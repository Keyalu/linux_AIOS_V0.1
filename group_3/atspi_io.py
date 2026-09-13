"""atspi_io.py — AT-SPI 输入通道（API 级动作 + 合成键鼠，零第三方依赖）。

组3 Automator 的真实执行后端。与 PyAutoGUI（坐标级）相比：
- do_action      ：控件级 API 动作，无坐标、抗遮挡（任务书 "API Actions"）
- set_text       ：文本接口直接写内容（对 GTK 可编辑对象最可靠）
- generate_keyboard_event / generate_mouse_event：合成键鼠（坐标级兜底）

依赖：GNOME 预装的 Atspi GI 绑定（无需 pip/sudo）。getaddrinfo 解析在
本机偶发抖动不影响本模块 —— a11y 总线走 unix socket。
"""

from __future__ import annotations

import time

try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    _OK = True
except Exception:
    _OK = False

# evdev 键码 + 8 = X 键码（合成快捷键用）
_KEYCODE = {
    "ctrl": 37, "control": 37, "alt": 64, "shift": 50, "super": 133, "meta": 133,
    "tab": 23, "enter": 36, "return": 36, "space": 65, "backspace": 22,
    "esc": 9, "escape": 9, "delete": 119, "up": 111, "down": 116,
    "a": 38, "b": 56, "c": 54, "d": 40, "e": 26, "f": 41, "g": 42, "h": 43,
    "i": 31, "j": 44, "k": 45, "l": 46, "m": 58, "n": 57, "o": 32, "p": 33,
    "q": 24, "r": 27, "s": 39, "t": 28, "u": 30, "v": 55, "w": 25, "x": 53,
    "y": 29, "z": 52,
    "1": 10, "2": 11, "3": 12, "4": 13, "5": 14,
    "6": 15, "7": 16, "8": 17, "9": 18, "0": 19,
}

_KSYM_SPECIAL = {"/": "slash", ".": "period", "-": "minus", "_": "underscore",
                 "?": "question", ",": "comma", ":": "colon", ";": "semicolon",
                 "(": "parenleft", ")": "parenright", "!": "exclam",
                 "@": "at", "#": "numbersign", "%": "percent", "+": "plus",
                 "'": "apostrophe", "\\": "backslash"}

# 可交互角色（与 group_2/control_detector.py find_element 的清单一致；
# 组间解耦纪律：组3 不 import 组2，此处复制小常量）
_CLICKABLE_ROLES = ("push button", "button", "toggle button",
                    "menu item", "check box", "icon", "radio button",
                    "page tab", "link")

# 点击类动作名优先级（小写子串匹配）。语义选动作用——不再盲选 index 0
# （首个动作可能是 showContextMenu 等非点击语义）。
_CLICK_ACTION_KEYWORDS = ("press", "click", "activate", "open", "launch",
                          "toggle", "default", "expand")


def available() -> bool:
    return _OK


def _synth(name: str):
    """Atspi.KeySynthType 枚举兜底（GI 缺成员时回退整型值）。"""
    enum = getattr(Atspi, "KeySynthType", None)
    if enum is not None:
        member = getattr(enum, name, None)
        if member is not None:
            return member
    return {"press": 0, "release": 1, "pressrelease": 2,
            "keysym": 3, "string": 4}[name]


class AtspiIO:
    """AT-SPI 读写与合成输入的薄封装。"""

    def __init__(self):
        if not _OK:
            raise RuntimeError("Atspi 绑定不可用")
        self._desktop = None

    # -------------------------------------------------- 查找 --
    @staticmethod
    def _name(obj) -> str:
        return obj.get_name() or ""

    @staticmethod
    def _role(obj) -> str:
        return obj.get_role_name()

    @staticmethod
    def _child_count(obj) -> int:
        if hasattr(obj, "get_child_count"):
            return obj.get_child_count()
        return obj.childCount

    @staticmethod
    def _child(obj, index: int):
        if hasattr(obj, "get_child_at_index"):
            return obj.get_child_at_index(index)
        return obj.getChildAtIndex(index)

    def _desk(self):
        if self._desktop is None:
            self._desktop = Atspi.get_desktop(0)
        return self._desktop

    def find_accessible(self, name: str = "", role: str = "",
                        contains: bool = True, app_name: str = "",
                        clickable_only: bool = False):
        """按名称/角色查找可访问对象。

        app_name：先定位到指定应用（如 gnome-text-editor），避免全桌面
        广度搜索在大型应用树上耗尽预算。
        clickable_only：优先可交互角色（与组2 find_element 的 clickable
        清单保持一致）——同名元素浅层常是不可点的文本 label，深层才是
        真按钮/图标；找齐全树仍无可交互者才退回首个名字匹配。"""
        desk = self._desk()
        roots = []
        if app_name:
            for i in range(self._child_count(desk)):
                try:
                    app = self._child(desk, i)
                    if app_name.lower() in (self._name(app) or "").lower():
                        roots.append((app, 0))
                except Exception:
                    continue
            if not roots:
                return None
        else:
            roots = [(desk, 0)]
        queue = list(roots)
        seen = 0
        want = (name or "").lower()
        fallback = None
        while queue and seen < 8000:
            obj, depth = queue.pop(0)
            seen += 1
            if depth > 0:                     # 根节点不查名（应用名已过滤）
                try:
                    nm = (obj.get_name() or "").lower()
                    rl = obj.get_role_name()
                except Exception:
                    continue
                ok_name = (not want) or (want in nm if contains else nm == want)
                ok_role = (not role) or (role in rl)
                if ok_name and ok_role:
                    if not clickable_only:
                        return obj
                    if rl in _CLICKABLE_ROLES:
                        return obj
                    if fallback is None:
                        fallback = obj
            try:
                n = self._child_count(obj)
            except Exception:
                continue
            if depth < 24:
                for i in range(min(n, 80)):
                    queue.append((self._child(obj, i), depth + 1))
        return fallback

    # -------------------------------------------------- API 级动作 --
    # 注意：Atspi GI 的接口方法扁平化在 Accessible 上（无 queryText/queryAction 包装）
    @staticmethod
    def actions(acc) -> list[str]:
        try:
            return [acc.get_action_name(i) for i in range(acc.get_n_actions())]
        except Exception:
            return []

    @staticmethod
    def do_action(acc, index: int = 0) -> bool:
        """控件级 API 动作（无坐标点击）。"""
        try:
            if index >= acc.get_n_actions():
                return False
            return bool(acc.do_action(index))
        except Exception:
            return False

    @classmethod
    def action_index(cls, acc) -> int | None:
        """语义选动作：按点击类动作名优先级挑一个；拿不准返回 None。

        多个动作但无一匹配点击语义时不猜（index 0 可能是 showContextMenu），
        交回坐标路径；唯一动作即使名字陌生也用它（总比点错坐标强）。"""
        try:
            names = [(acc.get_action_name(i) or "").lower()
                     for i in range(acc.get_n_actions())]
        except Exception:
            return None
        if not names:
            return None
        for kw in _CLICK_ACTION_KEYWORDS:
            for i, n in enumerate(names):
                if kw in n:
                    return i
        return 0 if len(names) == 1 else None

    @staticmethod
    def extents(acc) -> list[int]:
        """屏幕坐标 extents（CoordType.SCREEN）；失败返回空列表。"""
        try:
            e = acc.get_extents(1)
            return [int(e.x), int(e.y), int(e.width), int(e.height)]
        except Exception:
            return []

    def window_names(self) -> list[str]:
        """当前所有顶层窗口标题（非递归、浅遍历，~O(应用数) 次总线调用）。

        用途：execute_step 的 before/after 状态快照 —— API 动作"返回 True
        但界面无变化"的静默 no-op 由此留痕（如新窗口出现可对账）。"""
        out: list[str] = []
        try:
            desk = self._desk()
            for i in range(self._child_count(desk)):
                app = self._child(desk, i)
                for j in range(self._child_count(app)):
                    f = self._child(app, j)
                    nm = self._name(f)
                    if nm:
                        out.append(nm)
        except Exception:
            pass
        return out[:20]

    @staticmethod
    def set_text(acc, text: str) -> bool:
        """文本接口直接写内容（对可编辑对象）。"""
        try:
            return bool(acc.set_text_contents(text))
        except Exception:
            return False

    @staticmethod
    def get_text(acc) -> str:
        """读取文本：多方案适配不同 GI 版本的签名差异。"""
        try:
            n = acc.get_character_count()
        except Exception:
            n = 0
        for call in (lambda: acc.get_text(0, n),
                     lambda: acc.get_string_at_offset(0, n, 0),
                     lambda: acc.get_text(0, -1)):
            try:
                v = call()
                if isinstance(v, str):
                    return v
            except Exception:
                continue
        return ""

    # -------------------------------------------------- 合成输入 --
    @staticmethod
    def click_at(x: int, y: int) -> None:
        Atspi.generate_mouse_event(int(x), int(y), "b1c")   # 按下+释放

    @staticmethod
    def move_to(x: int, y: int) -> None:
        Atspi.generate_mouse_event(int(x), int(y), "abs")

    @staticmethod
    def press_keycode(code: int) -> None:
        Atspi.generate_keyboard_event(int(code), "", _synth("press"))

    @staticmethod
    def release_keycode(code: int) -> None:
        Atspi.generate_keyboard_event(int(code), "", _synth("release"))

    @classmethod
    def hotkey(cls, *keys: str) -> None:
        """合成快捷键：修饰键按下 → 末键敲击 → 修饰键逆序释放。"""
        codes = [_KEYCODE[k.lower()] for k in keys if k.lower() in _KEYCODE]
        for c in codes[:-1]:
            cls.press_keycode(c)
        if codes:
            Atspi.generate_keyboard_event(codes[-1], "", _synth("pressrelease"))
        for c in reversed(codes[:-1]):
            cls.release_keycode(c)

    @classmethod
    def type_text(cls, text: str) -> None:
        """逐字符 keysym 合成（ASCII 可靠；中文建议走 setTextContents）。"""
        for ch in text:
            sym = _KSYM_SPECIAL.get(ch, ch)
            Atspi.generate_keyboard_event(0, sym, _synth("keysym"))
        time.sleep(0.05)
