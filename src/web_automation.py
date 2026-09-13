"""web_automation.py — 网页自动化通道（CDP，零第三方依赖）。

为什么不用 AT-SPI 点网页：本机浏览器全是 snap 包，AppArmor 把网页内容
挡在 a11y 总线之外（应用注册为无名、树 child_count=-1，2026-09 实测）。
CDP（Chrome DevTools Protocol）是 Chromium 系自带的调试通道，AppArmor
不拦 localhost：
- HTTP  /json/version /json        —— 浏览器发现/就绪探测（urllib）
- WebSocket devtools/page/<id>     —— 命令通道（手写 RFC6455 客户端）
- Runtime.evaluate                 —— 按文本/选择器定位元素、读 URL/标题
- Input.dispatchMouseEvent         —— 派发真实鼠标事件（页面收到可信输入）

浏览器策略：自动化专用 Chromium + 独立 profile（~/.local/share/agent_os/
chromium-profile），不碰用户日常浏览器数据；端口仅绑 127.0.0.1。
自动化浏览器不可用时工具返回 [Mock] 标记（任务书"每组必须提供 Mock
实现"），绝不把没发生的点击谎报成真实成功。

环境变量：AIOS_CDP_PORT（默认 9222）、AIOS_WEB_BROWSER（强制指定浏览器
可执行文件，测试用）。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import llm_client
from .interfaces import (PermissionLevel, ToolParam, ToolParamType,
                         ToolSchema)

_CDP_PORT_DEFAULT = 9222
_BROWSER_CANDIDATES = ("/snap/bin/chromium", "chromium-browser", "chromium",
                       "google-chrome", "/usr/bin/google-chrome-stable")
_PROFILE_DIR = Path.home() / ".local" / "share" / "agent_os" / "chromium-profile"


class CDPError(RuntimeError):
    """CDP 通道不可用（浏览器未装/启动超时/连接断开）。"""


class _CmdError(CDPError):
    """CDP 命令级错误（协议应答 error）——连接仍然健康，不断开。"""


class _NotFoundError(CDPError):
    """页面上没有匹配元素——诚实失败，不降级 Mock（浏览器是活的）。"""


def _http_json(url: str, timeout: float = 5) -> dict | list:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


# ============================================================
# 最小 RFC6455 WebSocket 客户端（客户端帧必须掩码；服务端帧不掩码）
# ============================================================

class _WebSocket:
    def __init__(self, url: str, timeout: float = 15.0):
        if not url.startswith("ws://"):
            raise CDPError(f"仅支持 ws:// 连接: {url[:50]}")
        host, _, path = url[5:].partition("/")
        h, _, p = host.partition(":")
        self._sock = socket.create_connection(
            (h, int(p or 80)), timeout=timeout)
        self._sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self._sock.sendall(
            (f"GET /{path} HTTP/1.1\r\nHost: {host}\r\n"
             "Upgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
             ).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise CDPError("WebSocket 握手被中断")
            buf += chunk
        head, self._buf = buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n")[0]
        if b"101" not in status:
            raise CDPError(f"WebSocket 握手失败: {status[:60]!r}")

    def send_text(self, payload: str) -> None:
        data = payload.encode()
        mask = os.urandom(4)
        header = bytes([0x81])                       # FIN + text
        n = len(data)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self._sock.sendall(header + mask
                           + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv_text(self) -> str:
        """读一条完整文本消息（处理分片；ping 自动 pong；close 视为断开）。

        注意 _need 是消费式缓冲：取走 N 字节后 self._buf 前移 N，
        因此帧头帧体要按顺序逐段取，不能再做绝对位置切片。"""
        payload = b""
        while True:
            header = self._need(2)
            opcode = header[0] & 0x0F
            fin = bool(header[0] & 0x80)
            ln = header[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._need(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._need(8))[0]
            chunk = self._need(ln)               # 服务端→客户端帧不掩码
            if opcode == 0x8:                    # close
                raise CDPError("浏览器关闭了 WebSocket 连接")
            if opcode == 0x9:                    # ping → pong
                self._sock.sendall(bytes([0x8A, len(chunk)]) + chunk)
                continue
            if opcode in (0x1, 0x0):             # text / continuation
                payload += chunk
                if fin:
                    return payload.decode(errors="replace")
            # 其余 opcode（binary 等）忽略

    def _need(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise CDPError("WebSocket 连接中断")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


# ============================================================
# WebAutomation：浏览器生命周期 + CDP 命令
# ============================================================

class WebAutomation:
    """自动化浏览器的唯一入口（进程内单例，见 get_automation）。"""

    def __init__(self, port: int | None = None):
        self._port = port or int(os.environ.get("AIOS_CDP_PORT",
                                                _CDP_PORT_DEFAULT))
        self._ws: _WebSocket | None = None
        self._target_id: str | None = None
        self._lock = threading.Lock()

    # -------------------------------------------------- 生命周期 --
    def _binary(self) -> str | None:
        forced = os.environ.get("AIOS_WEB_BROWSER")
        if forced:
            return forced if "/" in forced else shutil.which(forced)
        for cand in _BROWSER_CANDIDATES:
            if "/" in cand and os.path.exists(cand):
                return cand
            if shutil.which(cand):
                return shutil.which(cand)
        return None

    def _reachable(self) -> bool:
        try:
            _http_json(f"http://127.0.0.1:{self._port}/json/version", 2)
            return True
        except Exception:
            return False

    def ensure_browser(self, url: str = "") -> None:
        """确保调试浏览器在跑并就绪；需要时以独立 profile 拉起。"""
        with self._lock:
            self._ensure_locked(url)

    def _ensure_locked(self, url: str = "") -> None:
        if self._ws is not None:
            # 连接探活：浏览器被关闭/崩溃后 _ws 残留为死连接，操作必然失败
            # 降级 —— 探活失败即关闭死连接并重连/重启（自愈而非报错）
            try:
                self._cmd("Runtime.evaluate",
                          {"expression": "1", "returnByValue": True})
                return
            except CDPError:
                self.close()
                if self._reachable():
                    self._connect()
                    return
        if not self._reachable():
            binary = self._binary()
            if not binary:
                raise CDPError("未找到 Chromium 系浏览器（chromium/chrome）")
            _PROFILE_DIR.parent.mkdir(parents=True, exist_ok=True)
            # 启动参数不带 url：带 url 会先开一个"启动页"，随后自动化
            # 标签页再导航同一地址 —— 同一页面被打开两遍（实测 bug）。
            # 统一由 open_url 经 Page.navigate 在自动化标签页里打开。
            args = [binary, f"--remote-debugging-port={self._port}",
                    f"--user-data-dir={_PROFILE_DIR}",
                    "--no-first-run", "--no-default-browser-check"]
            try:
                subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            except OSError as e:
                raise CDPError(f"启动自动化浏览器失败: {e}") from e
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if self._reachable():
                    break
                time.sleep(0.5)
            else:
                raise CDPError("自动化浏览器启动超时（25s）")
        self._connect()

    def _connect(self) -> None:
        """创建自动化专用标签页并连接其命令通道。

        不复用既有标签页：用户旧标签可能被 Chromium 内存节省机制丢弃
        （后台 tab discarded），对它 Runtime.evaluate 会永远无响应；
        专用 tab 也让自动化与用户浏览互不干扰。"""
        req = urllib.request.Request(
            f"http://127.0.0.1:{self._port}/json/new?about:blank",
            method="PUT")
        with urllib.request.urlopen(req, timeout=5) as r:
            target = json.load(r)
        if target.get("type") != "page":
            raise CDPError(f"创建自动化标签页失败: {target}")
        self._target_id = target.get("id")
        self._ws = _WebSocket(target["webSocketDebuggerUrl"])

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None

    def close_browser(self) -> dict:
        """CDP Browser.close 优雅关闭自动化浏览器。

        这是关闭 snap Chromium 的唯一可靠方式：pkill 在本环境被
        AppArmor 拒绝（权限不够）。幂等：浏览器未运行也返回成功。"""
        if not self._reachable():
            self.close()
            return {"closed": True, "note": "浏览器本就未在运行"}
        v = _http_json(f"http://127.0.0.1:{self._port}/json/version", 3)
        ws = _WebSocket(v["webSocketDebuggerUrl"])
        try:
            ws.send_text(json.dumps({"id": 1, "method": "Browser.close"}))
            try:
                ws.recv_text()                      # 应答可能随断连丢失
            except Exception:
                pass
        finally:
            ws.close()
        self.close()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if not self._reachable():
                return {"closed": True}
            time.sleep(0.3)
        return {"closed": False, "note": "关闭命令已发出但端口仍在，稍后自动退出"}

    # -------------------------------------------------- CDP 命令 --
    def _cmd(self, method: str, params: dict | None = None,
             _id: list[int] = [0]) -> dict:
        if self._ws is None:
            raise CDPError("命令通道未连接")
        _id[0] += 1
        try:
            self._ws.send_text(json.dumps(
                {"id": _id[0], "method": method, "params": params or {}}))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                msg = json.loads(self._ws.recv_text())
                if msg.get("id") == _id[0]:
                    if "error" in msg:
                        raise _CmdError(f"{method}: {msg['error']}")
                    return msg.get("result") or {}
            raise socket.timeout(f"{method}: 响应超时")
        except _CmdError:
            raise                                # 命令级错误：连接仍健康
        except (socket.timeout, OSError, json.JSONDecodeError,
                struct.error) as e:
            self.close()                         # 传输层故障：断连待重建
            raise CDPError(f"CDP 通信异常: {type(e).__name__}: {e}") from e

    # -------------------------------------------------- 高层操作 --
    def open_url(self, url: str) -> dict:
        """在自动化浏览器中打开网页并等待加载完成。"""
        self._ensure_locked(url)
        r = self._cmd("Page.navigate", {"url": url})
        if r.get("errorText"):
            raise CDPError(f"导航失败: {r['errorText']}")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                ready = self._cmd("Runtime.evaluate", {
                    "expression": "document.readyState",
                    "returnByValue": True})["result"]["value"]
                if ready == "complete":
                    break
            except CDPError:
                raise
            except Exception:
                pass
            time.sleep(0.4)
        # 关掉启动残留的空白标签页（浏览器冷启动默认页 + 历史自动化页），
        # 保持窗口里只有正在使用的页面
        try:
            for t in _http_json(f"http://127.0.0.1:{self._port}/json", 3):
                if (t.get("type") == "page" and t.get("id") != self._target_id
                        and str(t.get("url", ""))
                        .startswith(("chrome://newtab", "about:blank"))):
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{self._port}/json/close/{t['id']}",
                        timeout=2)
        except Exception:
            pass                        # 清理失败不影响打开页面本身
        return self.state()

    def find(self, text: str = "", selector: str = "") -> dict | None:
        """按可见文本（子串）/CSS 选择器定位页面元素，返回描述。

        返回 {tag, text, href, x, y}（视口坐标中心点），未找到返回 None。"""
        self._ensure_locked()
        js = (
            "JSON.stringify((()=>{"
            f"const sel={json.dumps(selector or '')};"
            f"const txt={json.dumps((text or '').lower())};"
            "const els=sel?document.querySelectorAll(sel)"
            ":document.querySelectorAll('a,button,[role=button],input,summary,label');"
            "for(const el of els){"
            "const t=String(el.innerText||el.value||el.textContent||'').trim();"
            "if(txt?!t.toLowerCase().includes(txt):!t)continue;"
            "el.scrollIntoView({block:'center'});"
            "const r=el.getBoundingClientRect();"
            "if(r.width<2&&r.height<2)continue;"
            "return {tag:el.tagName.toLowerCase(),text:t.slice(0,80),"
            "href:el.href||'',x:r.x+r.width/2,y:r.y+r.height/2};}"
            "return null;})())"
        )
        r = self._cmd("Runtime.evaluate",
                      {"expression": js, "returnByValue": True})
        raw = r.get("result", {}).get("value")
        return json.loads(raw) if raw else None

    def _nth_result(self, n: int) -> dict | None:
        """取搜索结果页的第 n 条自然结果链接（各引擎容器逐级探测）。"""
        js = (
            "JSON.stringify((()=>{"
            f"const n={max(1, int(n))};"
            "const sels=['#b_results li a[href]','#search a[href]',"
            "'.results a[href]','main a[href]'];"
            "let links=[];"
            "for(const s of sels){"
            "links=[...document.querySelectorAll(s)].filter(a=>{"
            "const h=a.href||'';return h.startsWith('http')"
            "&&!h.includes(location.hostname)&&(a.innerText||'').trim().length>3;});"
            "if(links.length>=n)break;}"
            "const a=links[n-1];if(!a)return null;"
            "a.scrollIntoView({block:'center'});"
            "const r=a.getBoundingClientRect();"
            "return {tag:'a',text:(a.innerText||'').trim().slice(0,60),"
            "href:a.href,x:r.x+r.width/2,y:r.y+r.height/2};})())"
        )
        r = self._cmd("Runtime.evaluate", {"expression": js,
                                           "returnByValue": True})
        raw = r.get("result", {}).get("value")
        return json.loads(raw) if raw else None

    def click(self, text: str = "", selector: str = "", nth: int | None = None,
              retries: int = 3, interval: float = 0.6) -> dict:
        """定位并点击页面元素（真实输入事件）。

        nth：点击搜索结果页的第 n 条自然结果（"点击第一条结果"专用）；
        text/selector：按可见文本/CSS 选择器定位。
        retries：页面异步渲染时元素可能晚到，未命中短暂轮询再判失败。"""
        self._ensure_locked()
        el = None
        if nth is not None:
            for attempt in range(max(1, retries)):
                el = self._nth_result(int(nth))
                if el is not None:
                    break
                if attempt + 1 < retries:
                    time.sleep(interval)
        else:
            for attempt in range(max(1, retries)):
                el = self.find(text=text, selector=selector)
                if el is not None:
                    break
                if attempt + 1 < retries:
                    time.sleep(interval)
        if el is None:
            raise _NotFoundError(
                f"网页中未找到元素: text={text!r} selector={selector!r} nth={nth!r}")
        before_ids = self._page_ids()
        common = {"x": el["x"], "y": el["y"], "button": "left"}
        for params in ({"type": "mouseMoved", **common},
                       {"type": "mousePressed", "clickCount": 1,
                        "buttons": 1, **common},
                       {"type": "mouseReleased", "clickCount": 1, **common}):
            self._cmd("Input.dispatchMouseEvent", params)
        # 链接常以 target=_blank 新标签打开：新页面出现后把自动化目标
        # 切过去（后续操作与状态读取跟随实际页面）
        el["opened_new_tab"] = self._adopt_new_tab(before_ids)
        return el

    def _page_ids(self) -> set:
        try:
            return {t.get("id") for t in
                    _http_json(f"http://127.0.0.1:{self._port}/json", 3)
                    if t.get("type") == "page"}
        except Exception:
            return set()

    def _adopt_new_tab(self, before_ids: set, tries: int = 4,
                       interval: float = 0.5) -> bool:
        """点击打开的新标签页出现后，把自动化命令通道切到该页。"""
        for _ in range(tries):
            try:
                pages = [t for t in _http_json(
                    f"http://127.0.0.1:{self._port}/json", 3)
                    if (t.get("type") == "page"
                        and t.get("id") not in before_ids
                        and t.get("webSocketDebuggerUrl"))]
            except Exception:
                pages = []
            if pages:
                self.close()
                self._target_id = pages[0]["id"]
                self._ws = _WebSocket(pages[0]["webSocketDebuggerUrl"])
                return True
            time.sleep(interval)
        return False

    def state(self) -> dict:
        """当前页面 {url, title}（效果验证与审计用）。"""
        self._ensure_locked()
        r = self._cmd("Runtime.evaluate", {
            "expression": "JSON.stringify({url:location.href,"
                          "title:document.title})",
            "returnByValue": True})
        return json.loads(r["result"]["value"])

    def web_extract(self, max_chars: int = 3500) -> dict:
        """抽取当前页面：URL/标题/正文（截断）/前 20 个链接，供 LLM 解读。"""
        self._ensure_locked()
        js = (
            "JSON.stringify((()=>{"
            f"const max={int(max_chars)};"
            "const t=(document.body?document.body.innerText:'')"
            ".replace(/\\n{3,}/g,'\\n\\n');"
            "const links=[...document.querySelectorAll('a[href]')]"
            ".map(a=>({text:(a.innerText||'').trim().slice(0,60),href:a.href}))"
            ".filter(l=>l.text&&l.text.length>1).slice(0,20);"
            "return {url:location.href,title:document.title,"
            "text:t.slice(0,max),links:links};})())"
        )
        r = self._cmd("Runtime.evaluate", {"expression": js,
                                           "returnByValue": True})
        return json.loads(r["result"]["value"])


_AUTO: WebAutomation | None = None
_AUTO_LOCK = threading.Lock()


def get_automation() -> WebAutomation:
    global _AUTO
    with _AUTO_LOCK:
        if _AUTO is None:
            _AUTO = WebAutomation()
        return _AUTO


# ============================================================
# 组4 工具封装（统一契约 {"success": bool, "result"/"error": ...}）
# ============================================================

def web_open(url: str) -> dict:
    """在自动化浏览器中打开网页（后续可用 web_click 操作页面）。"""
    if not isinstance(url, str) or not url.strip():
        return {"success": False, "error": "参数错误: url 不能为空"}
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return {"success": False,
                "error": f"参数错误: 仅允许 http/https 链接，收到: {url[:50]}"}
    try:
        st = get_automation().open_url(url)
        return {"success": True, "result": {"opened": url, **st}}
    except CDPError as e:
        return {"success": True, "result": f"[Mock] web_open({url}) — {e}",
                "simulated": True}
    except Exception as e:
        return {"success": False,
                "error": f"web_open 失败: {type(e).__name__}: {e}"}


def web_click(text: str = "", selector: str = "", nth=None) -> dict:
    """点击网页元素：按可见文本/CSS 选择器定位，或点搜索结果第 n 条。"""
    nth_v = int(nth) if str(nth or "").strip() else None
    if not (isinstance(text, str) and text.strip()) \
            and not (isinstance(selector, str) and selector.strip()) \
            and nth_v is None:
        return {"success": False,
                "error": "参数错误: text / selector / nth 至少提供一个"}
    try:
        auto = get_automation()
        before = auto.state()
        el = auto.click(text=text.strip(), selector=selector.strip(), nth=nth_v)
        time.sleep(0.6)                       # 等页面响应跳转/渲染
        after = auto.state()
        return {"success": True,
                "result": {"clicked": el.get("text") or selector,
                           "tag": el.get("tag", ""), "href": el.get("href", ""),
                           "before": before, "after": after}}
    except _NotFoundError as e:
        return {"success": False, "error": str(e)}   # 浏览器活着，是真没找到
    except CDPError as e:
        return {"success": True,
                "result": f"[Mock] web_click({text!r}) — {e}",
                "simulated": True}
    except Exception as e:
        return {"success": False,
                "error": f"web_click 失败: {type(e).__name__}: {e}"}


def web_state() -> dict:
    """读取自动化浏览器当前页面的 URL 与标题。"""
    try:
        st = get_automation().state()
        return {"success": True, "result": st}
    except CDPError as e:
        return {"success": True,
                "result": {"url": "", "title": f"[Mock] {e}"},
                "simulated": True}
    except Exception as e:
        return {"success": False,
                "error": f"web_state 失败: {type(e).__name__}: {e}"}


def web_extract(max_chars: int = 3500) -> dict:
    """抽取自动化浏览器当前页面的正文与链接（供 llm_answer 解读）。"""
    try:
        data = get_automation().web_extract(max_chars=int(max_chars or 3500))
        return {"success": True, "result": data}
    except CDPError as e:
        return {"success": True, "result": f"[Mock] web_extract — {e}",
                "simulated": True}
    except Exception as e:
        return {"success": False,
                "error": f"web_extract 失败: {type(e).__name__}: {e}"}


def web_close_browser() -> dict:
    """优雅关闭自动化浏览器（CDP Browser.close；幂等）。"""
    try:
        r = get_automation().close_browser()
        note = r.get("note", "")
        return {"success": True,
                "result": "自动化浏览器已关闭" + (f"（{note}）" if note else "")}
    except Exception as e:
        return {"success": False,
                "error": f"关闭浏览器失败: {type(e).__name__}: {e}"}


def llm_answer(question: str = "", text: str = "") -> dict:
    """把工具输出/文本交给 LLM 解读：回答问题或总结（配置与组1 同源）。"""
    q = (question or "").strip()
    t = (text or "").strip()
    if not q and not t:
        return {"success": False,
                "error": "参数错误: question / text 至少提供一个"
                         "（总结上一步输出时 text 通常写 {{prev_result}}）"}
    if t in ("(无上一步结果)", "(引用的步骤不存在)"):
        t = ""                                # 占位空值 → 纯问题模式
    sys_p = ("你是 Linux Agentic OS 的结果解读助手。基于给出的参考数据"
             "用简洁中文回答；参考数据里没有的信息不要编造。")
    user_p = (f"用户问题：{q}\n\n参考数据：\n{t[:6000]}") if t else q
    answer = llm_client.chat(user_p, sys_p)
    if answer is None:
        return {"success": False,
                "error": "LLM 调用失败：未配置 Key 或网络不可用（LLM 设置页检查）"}
    return {"success": True,
            "result": {"question": q or "(总结)", "answer": answer}}


def web_search(query: str, engine: str = "bing") -> dict:
    """在自动化浏览器中打开搜索引擎结果页（要拿结果内容当数据用 mcp_search）。"""
    if not isinstance(query, str) or not query.strip():
        return {"success": False, "error": "参数错误: query 不能为空"}
    q = urllib.parse.quote(query.strip())
    url = {"baidu": f"https://www.baidu.com/s?wd={q}"}.get(
        (engine or "bing").lower(), f"https://www.bing.com/search?q={q}")
    try:
        st = get_automation().open_url(url)
        return {"success": True,
                "result": {"query": query.strip(), "engine": engine, **st}}
    except CDPError as e:
        return {"success": True,
                "result": f"[Mock] web_search({query!r}) — {e}",
                "simulated": True}
    except Exception as e:
        return {"success": False,
                "error": f"web_search 失败: {type(e).__name__}: {e}"}


WEB_SCHEMAS: dict[str, ToolSchema] = {
    "web_extract": ToolSchema(
        name="web_extract",
        description="抽取自动化浏览器当前页面的正文与链接（配合 llm_answer "
                    "实现\"打开网页并总结/回答\"类任务）",
        parameters=[ToolParam("max_chars", ToolParamType.NUMBER,
                              "正文最多抽取字符数", required=False, default=3500)],
        permission=PermissionLevel.PUBLIC,
    ),
    "web_close_browser": ToolSchema(
        name="web_close_browser",
        description="优雅关闭自动化浏览器（CDP Browser.close，幂等）。"
                    "不要用 run_command 跑 pkill 关浏览器——snap 进程会被拒绝",
        parameters=[],
        permission=PermissionLevel.PUBLIC,
    ),
    "llm_answer": ToolSchema(
        name="llm_answer",
        description="把工具输出交给 LLM 解读：回答问题或生成摘要。"
                    "总结上一步输出时 text 写 {{prev_result}}",
        parameters=[
            ToolParam("question", ToolParamType.STRING, "用户的问题（可为空=纯总结）",
                      required=False, default=""),
            ToolParam("text", ToolParamType.STRING, "待解读的文本（通常 {{prev_result}}）",
                      required=False, default=""),
        ],
        permission=PermissionLevel.PUBLIC,
    ),
    "web_search": ToolSchema(
        name="web_search",
        description="在自动化浏览器中打开搜索引擎结果页（默认 Bing，可 baidu）；"
                    "要拿搜索结果的内容当数据用 mcp_search",
        parameters=[
            ToolParam("query", ToolParamType.STRING, "搜索关键词"),
            ToolParam("engine", ToolParamType.STRING, "搜索引擎：bing（默认）/baidu",
                      required=False, default="bing"),
        ],
        permission=PermissionLevel.PUBLIC,
    ),
    "web_open": ToolSchema(
        name="web_open",
        description="在自动化浏览器（独立 profile 的 Chromium）中打开网页并等待"
                    "加载完成；后续要在页面里点击元素就先用它",
        parameters=[ToolParam("url", ToolParamType.STRING, "完整网址（http/https）")],
        permission=PermissionLevel.PUBLIC,
    ),
    "web_click": ToolSchema(
        name="web_click",
        description="点击网页元素：按元素可见文本子串定位（如按钮/链接文字），"
                    "点击前自动滚动到可见",
        parameters=[
            ToolParam("text", ToolParamType.STRING, "元素可见文本（子串匹配）"),
            ToolParam("selector", ToolParamType.STRING, "CSS 选择器（可选，提供时优先）",
                      required=False, default=""),
            ToolParam("nth", ToolParamType.NUMBER, "点击搜索结果第 n 条（与 web_search 配套）",
                      required=False, default=None),
        ],
        permission=PermissionLevel.PUBLIC,
    ),
    "web_state": ToolSchema(
        name="web_state",
        description="读取自动化浏览器当前页面的 URL 与标题（点击效果验证用）",
        parameters=[],
        permission=PermissionLevel.PUBLIC,
    ),
}


def register_web_tools(registry) -> None:
    """把 web/answer 系工具注册进 ToolRegistry（幂等）。"""
    for name, func in (("web_search", web_search), ("web_open", web_open),
                       ("web_click", web_click), ("web_state", web_state),
                       ("web_extract", web_extract), ("llm_answer", llm_answer),
                       ("web_close_browser", web_close_browser)):
        registry.register(name, func, WEB_SCHEMAS.get(name))

