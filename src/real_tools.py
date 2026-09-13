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

import os
import re
import smtplib
import time
import webbrowser
from pathlib import Path
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .interfaces import PermissionLevel, ToolParam, ToolParamType, ToolSchema

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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

    try:
        ok = webbrowser.open(url, new=int(new))
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

def take_screenshot(path: str = "") -> dict:
    """截取当前屏幕并保存为图片（X11 会话，Gdk 根窗口抓取）。

    截图前固定等待 2s：GUI 操作（点击/开应用/切窗口）后画面通常还在
    渲染，立即抓取会拿到过渡帧；等两秒让画面稳定再截。"""
    time.sleep(2)
    try:
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
        screen = Gdk.Screen.get_default().get_root_window()
        if screen is None:
            return {"success": False,
                    "error": "执行异常: 无可用屏幕（DISPLAY 未设置或 Wayland 会话）"}
        w, h = screen.get_width(), screen.get_height()
        pb = Gdk.pixbuf_get_from_window(screen, 0, 0, w, h)
        if pb is None:
            return {"success": False, "error": "执行异常: 屏幕抓取失败"}
        target = path.strip() or f"/tmp/agent_os_screenshot_{int(time.time())}.png"
        if not target.lower().endswith((".png", ".jpg", ".jpeg")):
            target += ".png"
        os.makedirs(os.path.dirname(target) or "/", exist_ok=True)
        fmt = "jpeg" if target.lower().endswith((".jpg", ".jpeg")) else "png"
        pb.savev(target, fmt, [], [])
        return {"success": True, "result": {
            "path": target, "width": w, "height": h,
            "size": os.path.getsize(target)}}
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

    recips = _norm_recipients(to)
    if not recips:
        return {
            "success": False,
            "error": f"参数错误: 收件人格式不正确或为空（支持字符串/列表），收到: {to!r}",
        }

    # ---- 构建邮件（干跑与真实发送共用）----
    sender = user or os.environ.get("SMTP_USER", "agent@example.com")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recips)
    msg["Subject"] = subject
    # ---- 附件归一：字符串/列表 → 路径列表（支持多附件）----
    att_raw = attachment if isinstance(attachment, (list, tuple)) \
        else ([attachment] if attachment else [])
    att_paths = []
    for att in att_raw:
        apath = Path(str(att)).expanduser()
        if not apath.is_file():
            return {"success": False,
                    "error": f"参数错误: 附件不存在: {att}"}
        att_paths.append(apath)
    if att_paths and not body:
        body = f"详见附件 {att_paths[0].name}" + (" 等文件" if len(att_paths) > 1 else "")

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
    port = int(port or os.environ.get("SMTP_PORT", 465))
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
    """把真实行为工具注册进 registry（组3/演示按需调用）。"""
    registry.register(
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
    registry.register(
        "take_screenshot",
        take_screenshot,
        ToolSchema(
            name="take_screenshot",
            description="截取当前屏幕并保存为图片（X11/GNOME，隐私敏感）",
            parameters=[
                ToolParam("path", ToolParamType.STRING, "保存路径（留空自动生成）",
                          required=False, default=None),
            ],
            permission=PermissionLevel.USER,
        ),
    )
    registry.register(
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
