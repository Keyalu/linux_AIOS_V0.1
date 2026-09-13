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
import urllib.request
from pathlib import Path

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
            return                               # 已连接命令通道
        if not self._reachable():
            binary = self._binary()
            if not binary:
                raise CDPError("未找到 Chromium 系浏览器（chromium/chrome）")
            _PROFILE_DIR.parent.mkdir(parents=True, exist_ok=True)
            args = [binary, f"--remote-debugging-port={self._port}",
                    f"--user-data-dir={_PROFILE_DIR}",
                    "--no-first-run", "--no-default-browser-check"]
            if url:
                args.append(url)
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

    def click(self, text: str = "", selector: str = "", retries: int = 3,
              interval: float = 0.6) -> dict:
        """定位并点击页面元素（真实输入事件）。

        retries：页面异步渲染时元素可能晚到，未命中短暂轮询再判失败。"""
        self._ensure_locked()
        el = None
        for attempt in range(max(1, retries)):
            el = self.find(text=text, selector=selector)
            if el is not None:
                break
            if attempt + 1 < retries:
                time.sleep(interval)
        if el is None:
            raise _NotFoundError(
                f"网页中未找到元素: text={text!r} selector={selector!r}")
        common = {"x": el["x"], "y": el["y"], "button": "left"}
        for params in ({"type": "mouseMoved", **common},
                       {"type": "mousePressed", "clickCount": 1,
                        "buttons": 1, **common},
                       {"type": "mouseReleased", "clickCount": 1, **common}):
            self._cmd("Input.dispatchMouseEvent", params)
        return el

    def state(self) -> dict:
        """当前页面 {url, title}（效果验证与审计用）。"""
        self._ensure_locked()
        r = self._cmd("Runtime.evaluate", {
            "expression": "JSON.stringify({url:location.href,"
                          "title:document.title})",
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


def web_click(text: str = "", selector: str = "") -> dict:
    """点击网页元素（按可见文本子串或 CSS 选择器定位）。"""
    if not (isinstance(text, str) and text.strip()) \
            and not (isinstance(selector, str) and selector.strip()):
        return {"success": False,
                "error": "参数错误: text / selector 至少提供一个"}
    try:
        auto = get_automation()
        before = auto.state()
        el = auto.click(text=text.strip(), selector=selector.strip())
        time.sleep(0.6)                       # 等页面响应跳转/渲染
        after = auto.state()
        return {"success": True,
                "result": {"clicked": el.get("text") or selector,
                           "tag": el.get("tag", ""),
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


WEB_SCHEMAS: dict[str, ToolSchema] = {
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
    """把 web_open/web_click/web_state 注册进 ToolRegistry（幂等）。"""
    for name, func in (("web_open", web_open), ("web_click", web_click),
                       ("web_state", web_state)):
        registry.register(name, func, WEB_SCHEMAS.get(name))

