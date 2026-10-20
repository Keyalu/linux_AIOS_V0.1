"""
real_tools.py — 真实行为工具（L1 系统动作 + L2 邮件）

把"AI 真实行动"注册进 ToolRegistry 的第一批工具：
- open_url   : 打开浏览器访问网址（PUBLIC）
- send_email : 通过 SMTP 发送邮件（ADMIN，默认干跑模式）

设计原则：
1. 与 default_tools 同一套返回契约：{"success": bool, "result"/"error": ...}
2. 零第三方依赖：webbrowser / smtplib / email 全部标准库
3. send_email 默认 dry_run=True——只构建邮件并返回预览，不碰网络；
   真实发送需要显式 dry_run=False + SMTP 凭据（参数或环境变量），
   且工具本身是 ADMIN 级，必须提权后才能调用
4. 可选注册：不进默认工具集（保持 8 工具契约不变），用法：

    from src.real_tools import register_real_tools
    register_real_tools(registry)

环境变量（真实发送时可用，凭据参数优先）：
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS
"""

from __future__ import annotations

import hashlib
import os
import re
import smtplib
import time
import webbrowser
from pathlib import Path
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .interfaces import PermissionLevel, ToolParam, ToolParamType, ToolSchema
from .default_tools import MAX_ATTACH_TOTAL, _is_protected_path

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _mask_email(addr) -> str:
    """邮箱打码（错误信息里不回显完整地址，避免凭据/隐私落进日志）。"""
    local, _, domain = str(addr).strip().partition("@")
    return (local[:1] + "***@" + domain) if domain else "***"


# ============================================================
# L1: 打开浏览器
# ============================================================

def open_url(url: str, new: int = 0) -> dict:
    """用系统默认浏览器打开网址。仅允许 http/https。"""
    if not isinstance(url, str) or not url.strip():
        return {"success": False, "error": "参数错误: url 不能为空"}

    url = url.strip()
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        return {
            "success": False,
            "error": f"参数错误: 仅允许 http/https 链接，收到: {url[:50]}",
        }
    try:                                    # new 只在 0/1/2 三档内取值
        new_i = max(0, min(int(new), 2))
    except (TypeError, ValueError):
        new_i = 0

    try:
        ok = webbrowser.open(url, new=new_i)
        if not ok:
            return {"success": False, "error": "执行异常: 系统没有可用的浏览器"}
        return {
            "success": True,
            "result": {"opened": url, "browser": webbrowser.get().name or "default"},
        }
    except Exception as e:
        return {"success": False, "error": f"执行异常: {type(e).__name__}: {e}"}


# ============================================================
# L1.5: 屏幕截图（X11/GNOME，Gdk 根窗口，零第三方安装）
# ============================================================

def _wait_screen_stable(grab, min_wait: float = 1.2, timeout: float = 6.0,
                        interval: float = 0.35) -> dict:
    """等"画面已经稳定显示"再截图：连续两帧像素摘要一致即认为渲染完成。

    grab() 返回当前屏幕的像素摘要（bytes/str）；抛异常则立即停止等待。
    拿不准（超时/采样失败）返回 `stable=False` 并在结果里如实标注，不阻断截图。

    背景：早期实现是固定睡眠两秒，完全不判断画面是否还在变化 —— 搜索结果页
    常在两秒后仍在渲染，于是拍出"半张白页"的截图。
    """
    t0 = time.monotonic()
    time.sleep(max(0.0, min_wait))
    prev, samples, same = None, 0, 0
    try:
        while time.monotonic() - t0 < timeout:
            cur = grab()
            samples += 1
            if prev is not None and cur == prev:
                same += 1
                if same >= 1:                 # 连续两帧一致 → 视为稳定
                    return {"stable": True, "samples": samples,
                            "waited_ms": round((time.monotonic() - t0) * 1000),
                            "reason": "连续两帧一致"}
            else:
                same = 0
            prev = cur
            time.sleep(interval)
        return {"stable": False, "samples": samples,
                "waited_ms": round((time.monotonic() - t0) * 1000),
                "reason": f"等待 {timeout}s 画面仍在变化（有时钟/动画？）"}
    except Exception as e:
        return {"stable": False, "samples": samples,
                "waited_ms": round((time.monotonic() - t0) * 1000),
                "reason": f"帧采样失败: {type(e).__name__}: {e}"}


def take_screenshot(path: str = "", wait_page: bool = True) -> dict:
    """截取当前屏幕并保存为图片（X11 会话，Gdk 根窗口抓取）。

    截图前有两道"已正确显示"的闸门（替代早期写死的固定 2 秒等待）：
      1. **页面就绪**：若自动化浏览器正在跑，先 `Page.bringToFront` 把窗口提到
         最前，再等 `readyState=complete` + 图片齐全 + 两帧 rAF + 正文长度稳定
         —— 搜索结果这类异步渲染的页面，`complete` 之后还会继续填内容；
      2. **画面稳定**：连续两帧像素一致才抓屏。
    两道闸门的结果（是否就绪/稳定、等待毫秒数）都写进返回值，拿不准时如实标注
    而不是假装成功。目标路径仍先过受保护/敏感路径校验再等待。
    """
    # 未给路径则落到 /tmp 按时间戳命名，避免覆盖旧截图
    target = (path or "").strip() or f"/tmp/agent_os_screenshot_{int(time.time())}.png"
    if not target.lower().endswith((".png", ".jpg", ".jpeg")):
        target += ".png"
    target = os.path.expanduser(target)
    if not os.path.isabs(target):
        # 相对名锚定项目根，不随进程 CWD 漂移
        target = str(Path(__file__).resolve().parent.parent / target)
    prot = _is_protected_path(target)
    if prot:
        return {"success": False,
                "error": f"拒绝写入受保护/敏感路径: {target} ({prot})"}

    # ① 页面就绪闸门（浏览器没在跑就跳过，绝不为此启动浏览器）
    page_info: dict = {"ready": False, "skipped": True, "waited_ms": 0,
                       "reason": "wait_page=False"}
    if wait_page:
        try:
            from .web_automation import get_automation
            page_info = get_automation().wait_page_ready_if_active(8.0)
        except Exception as e:
            page_info = {"ready": False, "skipped": False, "waited_ms": 0,
                         "reason": f"{type(e).__name__}: {e}"}

    try:
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
        # 抓根窗口（整屏）→ 转 pixbuf → 按扩展名存 PNG/JPEG
        screen = Gdk.Screen.get_default().get_root_window()
        if screen is None:
            return {"success": False,
                    "error": "执行异常: 无可用屏幕（DISPLAY 未设置或 Wayland 会话）"}
        w, h = screen.get_width(), screen.get_height()

        def _grab():
            pb0 = Gdk.pixbuf_get_from_window(screen, 0, 0, w, h)
            if pb0 is None:
                raise RuntimeError("屏幕抓取失败")
            return hashlib.sha1(pb0.get_pixels()).hexdigest()

        # ② 画面稳定闸门
        stab = _wait_screen_stable(_grab, min_wait=1.2, timeout=6.0)

        pb = Gdk.pixbuf_get_from_window(screen, 0, 0, w, h)
        if pb is None:
            return {"success": False, "error": "执行异常: 屏幕抓取失败"}
        os.makedirs(os.path.dirname(target) or "/", exist_ok=True)
        fmt = "jpeg" if target.lower().endswith((".jpg", ".jpeg")) else "png"
        pb.savev(target, fmt, [], [])
        return {"success": True, "result": {
            "path": target, "width": w, "height": h,
            "size": os.path.getsize(target),
            "waited_ms": stab["waited_ms"] + int(page_info.get("waited_ms") or 0),
            "screen_stable": stab["stable"], "screen_note": stab["reason"],
            "page_ready": bool(page_info.get("ready")),
            "page_note": page_info.get("reason")}}
    except Exception as e:
        return {"success": False, "error": f"执行异常: {type(e).__name__}: {e}"}


# ============================================================
# L2: 发送邮件
# ============================================================

def _norm_recipients(to) -> list[str]:
    """收件人支持字符串或列表，统一成列表并做格式校验。"""
    if isinstance(to, str):
        recips = [to]
    elif isinstance(to, (list, tuple)):
        recips = list(to)
    else:
        return []
    bad = [r for r in recips if not _EMAIL_RE.match(str(r).strip())]
    if bad:
        return []
    return [str(r).strip() for r in recips]


def send_email(
    to,
    subject: str = "",
    body: str = "",
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password: str | None = None,
    dry_run: bool = True,
    attachment: str | None = None,
) -> dict:
    """
    发送邮件。dry_run=True（默认）只构建邮件并返回预览，绝不联网。

    attachment：可选附件文件路径（文件必须存在；作为 MIME 附件发送）。

    真实发送（dry_run=False）需要 SMTP 凭据：
      - 显式参数 host/port/user/password，或
      - 环境变量 SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS
    端口 465 走 SMTP_SSL，其他端口走 SMTP + STARTTLS。
    """
    # ---- 收件人校验 ----
    # GUI 表单提交的都是字符串，"False" 是非空字符串（真值），
    # 必须先归一化，否则用户在网页上永远无法关闭干跑模式
    if isinstance(dry_run, str):
        dry_run = dry_run.strip().lower() not in ("false", "0", "no", "off")

    # 主题/正文可能被直调或 LLM 传成 None → 后面 len()/set_content 会崩
    subject = "" if subject is None else str(subject)
    body = "" if body is None else str(body)

    recips = _norm_recipients(to)
    if not recips:
        raw = to if isinstance(to, (list, tuple)) else ([to] if to else [])
        bad = ", ".join(_mask_email(x) for x in raw) or "(空)"
        return {
            "success": False,
            "error": f"参数错误: 收件人格式不正确或为空（支持字符串/列表），非法项: {bad}",
        }

    # ---- 附件归一与安全校验：字符串/列表 → 路径列表（支持多附件）----
    # 单次构建邮件（早期实现先建一版 msg 只为校验收件人、随后整对象丢弃）。
    att_raw = attachment if isinstance(attachment, (list, tuple)) \
        else ([attachment] if attachment else [])
    att_paths: list[Path] = []
    total_bytes = 0
    for att in att_raw:
        apath = Path(str(att)).expanduser()
        if not apath.is_file():
            return {"success": False,
                    "error": f"参数错误: 附件不存在: {att}"}
        # 附件即"外发通道"：受保护/敏感文件（/etc、~/.ssh、~/.workbuddy…）禁止外发
        prot = _is_protected_path(str(apath))
        if prot:
            return {"success": False,
                    "error": f"拒绝发送受保护/敏感文件: {apath} ({prot})"}
        try:
            total_bytes += apath.stat().st_size
        except OSError as e:
            return {"success": False, "error": f"附件不可读: {apath.name}: {e}"}
        if total_bytes > MAX_ATTACH_TOTAL:
            return {"success": False,
                    "error": f"附件总量超上限 "
                             f"{MAX_ATTACH_TOTAL // (1024 * 1024)}MB（拒绝整文件读入内存）"}
        att_paths.append(apath)
    if att_paths and not body:
        body = f"详见附件 {att_paths[0].name}" + (" 等文件" if len(att_paths) > 1 else "")

    sender = user or os.environ.get("SMTP_USER", "agent@example.com")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recips)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(body or "(空内容)")
    for apath in att_paths:
        try:
            msg.add_attachment(apath.read_bytes(), maintype="application",
                               subtype="octet-stream", filename=apath.name)
        except OSError as e:
            return {"success": False, "error": f"附件读取失败: {apath.name}: {e}"}

    # 干跑分支：返回邮件全貌预览，绝不联网
    if dry_run:
        return {
            "success": True,
            "result": {
                "dry_run": True,
                "from": sender,
                "to": recips,
                "subject": subject,
                "body_chars": len(body),
                "attachments": [p.name for p in att_paths],
                "message_id": msg["Message-ID"],
                "hint": "干跑模式未发送。真实发送需 dry_run=False 并提供 SMTP 凭据",
            },
        }

    # ---- 凭据解析（参数优先，环境变量兜底）----
    host = host or os.environ.get("SMTP_HOST")
    try:
        port = int(port or os.environ.get("SMTP_PORT", 465))
    except (TypeError, ValueError):
        return {"success": False,
                "error": f"参数错误: SMTP_PORT 必须是端口号，收到 {port!r}"}
    user = user or os.environ.get("SMTP_USER")
    password = password or os.environ.get("SMTP_PASS")

    if not (host and user and password):
        return {
            "success": False,
            "error": "参数错误: 真实发送需要 host/user/password（参数或 "
                     "SMTP_HOST/SMTP_USER/SMTP_PASS 环境变量）",
        }

    # ---- 真实发送 ----
    try:
        # 465=SMTP_SSL 直连；其他端口走 STARTTLS 明文升级
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls()
                s.login(user, password)
                s.send_message(msg)
        return {
            "success": True,
            "result": {
                "sent": True,
                "from": sender,
                "to": recips,
                "subject": subject,
                "attachments": [p.name for p in att_paths],
                "smtp": f"{host}:{port}",
            },
        }
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "执行异常: SMTP 认证失败（检查授权码，不是登录密码）"}
    except smtplib.SMTPException as e:
        return {"success": False, "error": f"执行异常: SMTP 错误: {e}"}
    except OSError as e:
        return {"success": False, "error": f"执行异常: 网络错误: {e}"}


# ============================================================
# 注册入口（可选，不进默认工具集）
# ============================================================

def register_real_tools(registry) -> None:
    """把真实行为工具注册进 registry（组3/演示按需调用）。

    幂等：同名工具已注册则跳过（早期实现无检查，二次调用直接抛 ValueError，
    与 mcp_connector 的自检口径不一致）。
    """
    def _once(name, func, schema):
        if not registry.has(name):
            registry.register(name, func, schema)

    _once(
        "open_url",
        open_url,
        ToolSchema(
            name="open_url",
            description="用系统默认浏览器打开网址（仅 http/https）",
            parameters=[
                ToolParam("url", ToolParamType.STRING, "要打开的网址"),
                ToolParam("new", ToolParamType.NUMBER, "0=当前窗口 1=新窗口 2=新标签页",
                          required=False, default=0),
            ],
            permission=PermissionLevel.PUBLIC,
        ),
    )
    _once(
        "take_screenshot",
        take_screenshot,
        ToolSchema(
            name="take_screenshot",
            description="截取当前屏幕并保存为图片（X11/GNOME，隐私敏感）。"
                        "截图前会等页面就绪（浏览器在跑时）与画面稳定，"
                        "确保拍到的是『已经正确显示』的画面",
            parameters=[
                ToolParam("path", ToolParamType.STRING, "保存路径（留空自动生成）",
                          required=False, default=None),
                ToolParam("wait_page", ToolParamType.BOOLEAN,
                          "True=截图前等页面就绪+画面稳定（默认）",
                          required=False, default=True),
            ],
            permission=PermissionLevel.USER,
        ),
    )
    _once(
        "send_email",
        send_email,
        ToolSchema(
            name="send_email",
            description="发送邮件（ADMIN；默认 dry_run 只预览不发送）",
            parameters=[
                ToolParam("to", ToolParamType.ARRAY, "收件人（字符串或列表）"),
                ToolParam("subject", ToolParamType.STRING, "邮件主题", required=False, default=""),
                ToolParam("body", ToolParamType.STRING, "邮件正文", required=False, default=""),
                ToolParam("dry_run", ToolParamType.BOOLEAN, "True=只预览不发送",
                          required=False, default=True),
                ToolParam("attachment", ToolParamType.ARRAY,
                          "附件路径（字符串=单附件，列表=多附件）",
                          required=False, default=None),
            ],
            permission=PermissionLevel.ADMIN,
        ),
    )
