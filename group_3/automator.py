"""automator.py — GUI 操作执行器（PyAutoGUI 真实路径 + Mock 路径双轨）。

任务书要求：使用 PyAutoGUI 执行点击、输入、快捷键、拖拽等操作。
真实依赖不可用时（未安装 pyautogui / 无显示器）自动切换 Mock 路径：
按相同入参"模拟执行"，返回结构一致的结果并带 simulated 标记 ——
这正是任务书"每组必须提供 Mock 实现"的落地形式。

result_json 契约：{"success": bool, "before_state", "after_state", ...}
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

try:                                   # 真实路径 A：坐标级
    import pyautogui
    pyautogui.FAILSAFE = False
    _REAL = True
except Exception:                      # 未安装
    _REAL = False

from .atspi_io import AtspiIO, available as _atspi_ok   # 真实路径 B：API 级（AT-SPI）
from .coord_check import SENTINEL as _ATSPI_SENTINEL    # 方案二：X 真值坐标校正
from . import coord_check


def _valid_bbox(bbox) -> bool:
    """bbox 有效性：哨兵只认 AT-SPI 的 INT32_MIN 占位值，1x1 占位拒绝。
    合法负坐标（多显示器布局下主屏左侧/上方的屏）不再误杀 —— 旧判定
    bbox[0] >= 0 会把整块屏幕的控件全部打成无效、静默降级 Mock。"""
    return (bool(bbox) and len(bbox) >= 4
            and bbox[0] > _ATSPI_SENTINEL and bbox[1] > _ATSPI_SENTINEL
            and bbox[2] > 1 and bbox[3] > 1)


class Automator:
    """GUI 操作执行器：click / type / hotkey / drag 四类动作。

    真实输入后端优先级：
      1. AT-SPI API 级（控件 DoAction / setTextContents —— 无坐标、抗遮挡）
      2. AT-SPI 合成键鼠（generate_mouse_event / keyboard_event）
      3. PyAutoGUI 坐标级（若安装）
      4. Mock 模拟（任务书要求的降级实现）
    """

    def __init__(self, action_delay: float = 0.3):
        self.action_delay = action_delay
        self.atspi = AtspiIO() if _atspi_ok() else None
        self.backend = ("pyautogui" if _REAL
                        else "atspi" if self.atspi else "mock")

    # ---------------------------------------------------------- --
    def execute_step(self, step: dict, element_info: dict | None = None) -> dict:
        """执行单个 GUI 步骤，返回 result_json（含执行前后状态）。"""
        before = self.capture_state()
        if self.atspi:
            before["windows"] = self.atspi.window_names()
        action = step.get("action")
        params = step.get("params") or {}
        handler = {
            "click": self.click, "type": self.type_text,
            "hotkey": self.hotkey, "drag": self.drag,
            "open_app": self.open_app, "navigate": self.navigate,
            "wait": self.wait,
        }.get(action)
        if handler is None:
            after = self.capture_state()
            if self.atspi:
                after["windows"] = self.atspi.window_names()
            return {"success": False, "error": f"未知操作: {action}",
                    "before_state": before, "after_state": after,
                    "simulated": not _REAL}
        out = handler(step, element_info, params)
        time.sleep(self.action_delay)
        out.setdefault("success", True)
        out["before_state"] = before
        out["after_state"] = after = self.capture_state()
        if self.atspi:
            after["windows"] = self.atspi.window_names()
        out.setdefault("simulated", not _REAL)
        return out

    # ---------------------------------------------------------- --
    # 动作实现（真实路径调 PyAutoGUI；Mock 路径记录等效动作）
    # ---------------------------------------------------------- --
    def click(self, step, element_info, params):
        """点击：AT-SPI DoAction(语义选动作) → 校正坐标合成 → PyAutoGUI → Mock。

        1) API 级优先：按 角色约束+应用限定 精确锁定可交互控件（找不到再
           逐步放宽），动作按语义优先级挑选——免疫坐标失真与时机漂移；
        2) 坐标兜底：优先控件 live extents（执行期新鲜数据）经 coord_check
           仿射校正，无 acc 才退回规划期烘焙 bbox（可能陈旧）；
        3) 哨兵判定只拒 INT32_MIN，不再误杀多显示器合法负坐标。"""
        el = element_info or params.get("element") or {}
        bbox = el.get("bbox")
        acc = None
        # 1) API 级：精确锁定（角色 + 应用），失败逐步放宽避免漏点
        if self.atspi and el.get("name"):
            acc = self.atspi.find_accessible(
                name=el["name"], role=el.get("role") or "",
                app_name=el.get("app") or "", clickable_only=True)
            if acc is None and (el.get("role") or el.get("app")):
                acc = self.atspi.find_accessible(name=el["name"])
        if acc is not None:
            idx = self.atspi.action_index(acc)
            if idx is not None:
                try:
                    if self.atspi.do_action(acc, idx):
                        try:
                            used = acc.get_action_name(idx)
                        except Exception:
                            used = f"#{idx}"
                        return {"message": f"API 点击 {el['name']!r}（{used}）",
                                "api_action": True}
                except Exception:
                    pass
        # 2) 坐标点击：live extents（校正后）优先于规划期烘焙 bbox
        use = list(bbox) if isinstance(bbox, (list, tuple)) and bbox else []
        fix_info: dict = {}
        if acc is not None:
            live = self.atspi.extents(acc)
            if live:
                use, fix_info = coord_check.corrected(acc, live)
            else:
                use, fix_info = coord_check.corrected(acc, use)
        elif use:
            use = list(use[:4])
        if _valid_bbox(use):
            x, y = use[0] + use[2] // 2, use[1] + use[3] // 2
            mark = " [已校正]" if fix_info.get("coord_fix") else ""
            if self.atspi:
                self.atspi.click_at(x, y)
                return {"message": f"合成点击({x},{y}){mark}", "synth": True,
                        "coord_fix": fix_info}
            if _REAL:
                import pyautogui
                pyautogui.click(x, y)
                return {"message": f"点击({x},{y}){mark}", "coord_fix": fix_info}
        return {"message": "[Mock] 点击（无可定位控件）", "simulated": True,
                "mock": True}

    def type_text(self, step, element_info, params):
        text = params.get("text", "")
        if self.atspi:
            # 1) 纯 ASCII（路径/命令等）优先合成按键：打进"当前聚焦"的
            #    输入框（如 Ctrl+L 聚焦的地址栏）——语义正确的目标；
            #    set_text 任意 text 角色对象会把路径写进不相干控件
            if text and all(ord(c) < 128 for c in text):
                self.atspi.type_text(text)
                return {"message": f"合成键入 {text!r}", "synth": True}
            # 2) 非 ASCII（中文文件名等，keysym 合成不可靠）→ 焦点对象的
            #    Text 接口直接写内容
            acc = self.atspi.find_accessible(role="text")
            if acc is not None:
                try:
                    if self.atspi.set_text(acc, text):
                        return {"message": f"setTextContents {text!r}",
                                "api_action": True}
                except Exception:
                    pass
            # 3) 兜底：仍尝试合成（非 ASCII 部分可能丢失，如实注明）
            self.atspi.type_text(text)
            return {"message": f"合成键入 {text!r}（非 ASCII 可能不完整）",
                    "synth": True}
        if _REAL:
            import pyautogui
            pyautogui.typewrite(text)
            return {"message": f"输入 {text!r}"}
        return {"message": f"[Mock] 输入 {text!r}", "simulated": True}

    def hotkey(self, step, element_info, params):
        keys = params.get("keys", [])
        if self.atspi:
            self.atspi.hotkey(*keys)
            return {"message": f"合成快捷键 {'+'.join(keys)}"}
        if _REAL:
            import pyautogui
            pyautogui.hotkey(*keys)
            return {"message": f"快捷键 {'+'.join(keys)}"}
        return {"message": f"[Mock] 快捷键 {'+'.join(keys)}", "simulated": True}

    def drag(self, step, element_info, params):
        if self.atspi:
            x, y = params.get("x", 400), params.get("y", 400)
            self.atspi.click_at(x, y)
            return {"message": f"AT-SPI 点击({x},{y})"}
        if _REAL:
            import pyautogui
            pyautogui.dragTo(params.get("x", 400), params.get("y", 400), duration=0.4)
            return {"message": "拖拽完成"}
        return {"message": "[Mock] 拖拽", "simulated": True}

    # 中文口语名 → .desktop 名（gtk-launch 参数）。AT-SPI 坐标点击 dock 在
    # X11/Wayland 下对 GNOME Shell 不可靠，命令行启动最稳。
    _APP_DESKTOP = {
        "firefox": "firefox", "火狐": "firefox",
        "文件管理器": "nautilus", "文件": "nautilus", "files": "nautilus",
        "终端": "gnome-terminal", "terminal": "gnome-terminal",
        "应用中心": "gnome-software", "软件": "gnome-software",
        "设置": "gnome-control-center", "settings": "gnome-control-center",
        "文本编辑器": "gnome-text-editor", "text editor": "gnome-text-editor",
        "图片": "eog", "帮助": "yelp",
        "chrome": "google-chrome", "google-chrome": "google-chrome",
        "zcode": "zcode",
        "code": "code", "vscode": "code",
    }

    @classmethod
    def _resolve_desktop(cls, app: str) -> str:
        """应用口语名 → .desktop 名。精确匹配优先；否则长关键词优先子串
        匹配（避免 zcode 被 code 抢走）；都不中就原样返回。"""
        a = (app or "").strip().lower()
        if a in cls._APP_DESKTOP:
            return cls._APP_DESKTOP[a]
        for kw in sorted(cls._APP_DESKTOP.keys(), key=len, reverse=True):
            if kw in a:
                return cls._APP_DESKTOP[kw]
        return (app or "").strip()

    def open_app(self, step, element_info, params):
        """打开应用：优先 gtk-launch（走 .desktop，X11/Wayland 通用，
        检查返回码），失败则直接执行二进制；都不可用则 Mock 记录。"""
        # params.app 优先：navigate 链里步骤的 target 是"要打开的路径"，
        # 若用它当应用名，gtk-launch 必然失败 → 假成功 Mock，文件管理器根本没开
        app = str(params.get("app") or "").strip() or step.get("target", "")
        desktop = self._resolve_desktop(app)
        # 1) gtk-launch：找不到 .desktop 会返回非 0，必须检查返回码，
        #    否则 Popen 不等待会谎报"已启动"。
        if shutil.which("gtk-launch"):
            try:
                r = subprocess.run(["gtk-launch", desktop],
                                   capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    return {"message": f"已启动 {app}（gtk-launch {desktop}）",
                            "launched": True, "simulated": False}
            except Exception:
                pass
        # 2) fallback：直接执行二进制（zcode 等非 .desktop 应用）
        if shutil.which(desktop):
            os.system(f"nohup {desktop} >/dev/null 2>&1 &")
            return {"message": f"已启动 {desktop}", "simulated": False}
        return {"message": f"[Mock] 启动应用 {app}（{desktop}）",
                "simulated": True}

    def _nautilus_showing(self, base: str) -> bool:
        """任一文件管理器窗口标题包含 base（Nautilus 标题=当前文件夹名）。"""
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
            desk = Atspi.get_desktop(0)
            for i in range(desk.get_child_count()):
                app = desk.get_child_at_index(i)
                if "nautilus" not in (app.get_name() or "").lower():
                    continue
                for j in range(app.get_child_count()):
                    f = app.get_child_at_index(j)
                    if f.get_role_name() == "frame" \
                            and base.lower() in (f.get_name() or "").lower():
                        return True
        except Exception:
            pass
        return False

    def navigate(self, step, element_info, params):
        """打开文件管理器并跳转到 path。

        三段式：GUI 键鼠链（Ctrl+L→键入→回车）优先 —— 这是对任务书
        "GUI 自动化"的展示；随后 AT-SPI 验证窗口标题，未生效自动 CLI
        兜底（nautilus path）—— 键鼠落点受焦点影响不可靠，结果优先。"""
        path = (params.get("path") or step.get("target", "")).strip()
        if not path:
            return {"success": False, "error": "缺少要打开的路径"}
        base = os.path.basename(path.rstrip("/")) or path

        # 1) 拉起/聚焦文件管理器（params.app 优先，缺省文件管理器）
        app = str(params.get("app") or "").strip() or "文件管理器"
        self.open_app(step, element_info, {"app": app})
        time.sleep(2)

        # 2) GUI 键鼠链
        if self.atspi:
            self.atspi.hotkey("ctrl", "l")
            time.sleep(0.3)
            self.atspi.type_text(path)
            time.sleep(0.3)
            self.atspi.hotkey("enter")
        elif _REAL:
            import pyautogui
            pyautogui.hotkey("ctrl", "l")
            pyautogui.typewrite(path, interval=0.02)
            pyautogui.press("enter")
        time.sleep(1.5)

        # 3) 验证 + 兜底：目标目录不存在时先创建（"打开目录"的合理语义），
        #    再 CLI 兜底打开；创建被拒（如 /home/user 需 root）则如实失败
        verified = self._nautilus_showing(base)
        fallback = False
        if not verified:
            try:
                os.makedirs(path, exist_ok=True)
            except OSError:
                pass
        if not verified and shutil.which("nautilus"):
            subprocess.Popen(["nautilus", path], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             start_new_session=True)
            fallback = True
            time.sleep(2.5)
            verified = self._nautilus_showing(base)
        note = "GUI 键鼠链生效" if verified and not fallback else \
               ("CLI 兜底生效" if verified else "未能确认（路径可能不存在）")
        return {"success": verified,
                "message": f"导航到 {path}（{note}）",
                "verified": verified, "fallback": fallback}

    def wait(self, step, element_info, params):
        """等待若干秒（打开应用后等窗口就绪，再执行后续键鼠链）。"""
        try:
            seconds = max(0.0, float(params.get("seconds", 1) or 1))
        except (TypeError, ValueError):
            seconds = 1.0
        time.sleep(seconds)
        return {"message": f"等待 {seconds}s（窗口/页面就绪）", "waited": seconds}

    # ---------------------------------------------------------- --
    @staticmethod
    def capture_state() -> dict:
        """执行前后状态快照（状态验证的依据）。真实路径可扩展为截屏。"""
        return {"time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cwd_files": sorted(os.listdir("."))[:20]}
