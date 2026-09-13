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
        # 1) 焦点对象的 Text 接口（若能定位到可编辑对象）
        if self.atspi:
            acc = self.atspi.find_accessible(role="text")
            if acc is not None:
                try:
                    if self.atspi.set_text(acc, text):
                        return {"message": f"setTextContents {text!r}",
                                "api_action": True}
                except Exception:
                    pass
            # 2) 合成键盘输入（KEY_STRING 整串 → 逐键 keysym）
            self.atspi.type_text(text)
            return {"message": f"合成键入 {text!r}", "synth": True}
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
        app = step.get("target") or params.get("app", "")
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

    def navigate(self, step, element_info, params):
        path = params.get("path") or step.get("target", "")
        return {"message": f"[Mock] 导航到 {path}"}

    # ---------------------------------------------------------- --
    @staticmethod
    def capture_state() -> dict:
        """执行前后状态快照（状态验证的依据）。真实路径可扩展为截屏。"""
        return {"time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cwd_files": sorted(os.listdir("."))[:20]}
