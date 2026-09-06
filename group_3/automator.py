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
import time

try:                                   # 真实路径 A：坐标级
    import pyautogui
    pyautogui.FAILSAFE = False
    _REAL = True
except Exception:                      # 未安装
    _REAL = False

from .atspi_io import AtspiIO, available as _atspi_ok   # 真实路径 B：API 级（AT-SPI）


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
        self.atspi = AtspiIO() if _atspi_ok else None
        self.backend = ("pyautogui" if _REAL
                        else "atspi" if self.atspi else "mock")

    # ---------------------------------------------------------- --
    def execute_step(self, step: dict, element_info: dict | None = None) -> dict:
        """执行单个 GUI 步骤，返回 result_json（含执行前后状态）。"""
        before = self.capture_state()
        action = step.get("action")
        params = step.get("params") or {}
        handler = {
            "click": self.click, "type": self.type_text,
            "hotkey": self.hotkey, "drag": self.drag,
            "open_app": self.open_app, "navigate": self.navigate,
        }.get(action)
        if handler is None:
            after = self.capture_state()
            return {"success": False, "error": f"未知操作: {action}",
                    "before_state": before, "after_state": after,
                    "simulated": not _REAL}
        out = handler(step, element_info, params)
        time.sleep(self.action_delay)
        out.setdefault("success", True)
        out["before_state"] = before
        out["after_state"] = after = self.capture_state()
        out["simulated"] = not _REAL
        return out

    # ---------------------------------------------------------- --
    # 动作实现（真实路径调 PyAutoGUI；Mock 路径记录等效动作）
    # ---------------------------------------------------------- --
    def click(self, step, element_info, params):
        """点击：AT-SPI DoAction(API 级) → 坐标合成 → PyAutoGUI → Mock。"""
        el = element_info or params.get("element") or {}
        bbox = el.get("bbox")
        # 1) 控件级 API 动作：按名称找到可访问对象并触发其动作
        if self.atspi and el.get("name"):
            acc = self.atspi.find_accessible(name=el["name"])
            if acc is not None:
                try:
                    if self.atspi.do_action(acc, 0):
                        return {"message": f"API 点击 {el['name']!r}（DoAction）",
                                "api_action": True}
                except Exception:
                    pass
        # 2) 坐标点击：AT-SPI 合成 → PyAutoGUI
        if bbox:
            x, y = bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2
            if self.atspi:
                self.atspi.click_at(x, y)
                return {"message": f"合成点击({x},{y})", "synth": True}
            if _REAL:
                import pyautogui
                pyautogui.click(x, y)
                return {"message": f"点击({x},{y})"}
        return {"message": "[Mock] 点击（无可定位控件）", "simulated": True}

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

    def open_app(self, step, element_info, params):
        """打开应用：有桌面时交给系统启动器，否则用 xdg-open/nohup 等价物。"""
        app = step.get("target") or params.get("app", "")
        if shutil.which(app):
            os.system(f"nohup {app} >/dev/null 2>&1 &")
            return {"message": f"已启动 {app}"}
        return {"message": f"[Mock] 启动应用 {app}（应用不在 PATH）", "simulated": True}

    def navigate(self, step, element_info, params):
        path = params.get("path") or step.get("target", "")
        return {"message": f"[Mock] 导航到 {path}"}

    # ---------------------------------------------------------- --
    @staticmethod
    def capture_state() -> dict:
        """执行前后状态快照（状态验证的依据）。真实路径可扩展为截屏。"""
        return {"time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cwd_files": sorted(os.listdir("."))[:20]}
