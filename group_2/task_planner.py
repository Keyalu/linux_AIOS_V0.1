"""task_planner.py — 任务规划器：intent_json → plan_json。

两类规划（对应任务书演示场景表）：
- 应用控制类（打开应用/切换窗口等）→ GUI 操作步骤（open_app/hotkey/
  click/type），配合 ControlDetector 的 elements 定位；
- 文件操作/信息查询类 → 工具步骤（走组4 公开契约），参数名一律按
  组4 schema 目录填写，不硬编码 —— 组4 改名自动跟随。

plan_json 契约：{"steps":[{step_id, kind, action, target, params,
requires_admin, description}], "elements": {...}, "policy": {...},
"unresolved": [...]}
"""

from __future__ import annotations

import re
import time
import uuid

# intent 动作 → (步骤类别, 组4 公开名)
_SKILL_ACTIONS = {
    "organize": "organize_downloads", "find_duplicates": "find_duplicate_files",
    "backup": "backup_directory", "find_large_files": "find_large_files",
    "disk_usage": "disk_usage", "system_check": "system_check",
    "cleanup_temp": "cleanup_temp", "find_files": "find_files",
}
# 应用控制类 GUI 动作 → GUI 步骤序列（任务书演示场景 1/2/3/5）
_GUI_ACTIONS = {
    "open_app": "打开应用",
    "navigate": "导航到路径",
    "switch_window": "切换应用窗口",
    "create_folder": "创建文件夹",
}

_TOOL_ACTIONS = {
    "delete_file": "delete_file", "send_email": "send_email",
    "open_url": "open_url", "weather": "mcp_weather",
    "search": "mcp_search", "translate": "mcp_translate",
    "write_file": "write_file", "run_command": "run_command",
    "take_screenshot": "take_screenshot",
}


class TaskPlanner:
    """把 intent_json 翻译成可执行 plan_json（组3 的唯一输入）。"""

    def __init__(self, schemas: dict | None = None,
                 detector=None):
        """schemas：组4 能力清单 {名称: schema_dict}（纯 JSON，不 import 组4）。

        detector：可选注入 ControlDetector，用于给 GUI 步骤预查元素。
        """
        self._schemas = schemas or {}
        self._detector = detector
        self.add_finale = True                # 用户可调：计划末尾是否附加提权演示收尾步

    # ---------------------------------------------------------- --
    def plan(self, intent: dict) -> dict:
        steps: list[dict] = []
        unresolved: list[str] = list(intent.get("unresolved", []))
        target = intent.get("target") or "."
        # 多目标：优先遍历组1 的 goals；兼容只填了顶层 action 的旧意图
        goals = intent.get("goals") or []
        if not goals and intent.get("action"):
            goals = [{"action": intent["action"]}]
        if not goals:
            unresolved.append("未能从话语中识别出任何目标动作")

        last_file_target = None
        for goal in goals:
            steps += self._steps_for(goal.get("action", ""), goal, intent,
                                     target, unresolved,
                                     last_file_target=last_file_target)
            if steps and steps[-1]["kind"] == "skill":
                p_last = (steps[-1].get("params") or {})
                last_file_target = p_last.get("path") or p_last.get(
                    "directory") or last_file_target

        if not steps and intent.get("action"):
            unresolved.append(f"动作 {intent.get('action')} 未能映射到组4 能力清单中的工具")

        # 收尾：用一条系统命令汇报完成（演示 USER→ADMIN 提权流）
        if steps and self.add_finale:
            steps.append({
                "step_id": len(steps) + 1, "kind": "tool", "action": "run_command",
                "target": "", "params": {"cmd": "echo [plan done]"},
                "requires_admin": True, "description": "收尾命令（演示提权流）",
            })
        steps = self.renumber(steps)

        return {
            "plan_id": f"plan-{uuid.uuid4().hex[:8]}",
            "based_on": intent.get("intent_id"),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "target": target,
            "steps": steps,
            "elements": self._elements_for(steps),
            "policy": {
                "on_permission_denied": "请求用户确认后提权重试一次",
                "on_tool_not_found": "用 list_tools 模糊匹配纠错",
                "on_other_fail": "记录并继续后续步骤",
            },
            "unresolved": unresolved,
        }

    # ---------------------------------------------------------- --
    def _steps_for(self, action: str, goal: dict, intent: dict,
                   target: str, unresolved: list[str],
                   last_file_target: str | None = None) -> list[dict]:
        # goal.params 是组1 LLM 为该目标抽取的专用参数，优先于顶层 params
        merged = dict(intent)
        merged["params"] = {**(intent.get("params") or {}),
                            **(goal.get("params") or {})}
        """单个目标动作 → 0 或多个计划步骤。"""
        if action == "open_app":
            return self._gui_open_app_steps(
                (intent.get("params") or {}).get("app") or target, unresolved)
        if action in _GUI_ACTIONS and action != "open_app":
            return self._gui_generic_steps(action, intent.get("params") or {},
                                           target, unresolved)
        name = _SKILL_ACTIONS.get(action) or _TOOL_ACTIONS.get(action)
        if name is None:
            # LLM 可能给出近义动作名（create_directory≈create_folder），模糊归一
            import difflib
            known = list(_SKILL_ACTIONS) + list(_TOOL_ACTIONS) + list(_GUI_ACTIONS)
            near = difflib.get_close_matches(action, known, n=1, cutoff=0.75)
            if near:
                action = near[0]
                name = _SKILL_ACTIONS.get(action) or _TOOL_ACTIONS.get(action)
        if name is None:
            unresolved.append(f"动作 {action} 未能映射到组4 能力清单中的工具")
            return []
        schema = self._schemas.get(name)
        if schema is None:
            unresolved.append(f"组4 能力清单中没有 {name}")
            return []
        params = self._fill_params(name, schema, target, merged, goal)
        missing = [k for k in schema.get("inputSchema", {}).get("required", [])
                   if k not in params or params[k] in ("", None)]
        if missing:
            unresolved.append(f"{name} 缺少必需参数: {', '.join(missing)}")
            return []
        kind = "skill" if name in _SKILL_ACTIONS.values() else "tool"
        return [{
            "step_id": 1,  # 由 plan() 统一重排
            "kind": kind, "action": name, "target": target, "params": params,
            "requires_admin": schema.get("permission") == "admin",
            "description": schema.get("description", name),
        }]

    def _fill_params(self, name: str, schema: dict, target: str,
                     intent: dict, goal: dict | None = None) -> dict:
        """schema 驱动填参，三层优先级：
        1) intent.params（组1 实体抽取的显式参数）直接采用；
        2) 有缺口时，用 schema 第一个字符串参数接 目标路径/实体；
        3) 其余参数（days/min_size_mb 等数值）留空，由技能默认值兜底。"""
        params_in = intent.get("params") or {}
        entity = (goal or {}).get("entity") or ""
        props = schema.get("inputSchema", {}).get("properties", {})
        required = schema.get("inputSchema", {}).get("required", [])

        params = {}
        for k, v in params_in.items():
            if k not in props or v in ("", None):
                continue
            # 类型防御：LLM 偶尔把上一步的整个结果对象塞进字符串参数
            # （如 {"opened": ...} 传给 url），字典/列表一律丢弃
            if props[k].get("type") == "string" and isinstance(v, (dict, list)):
                continue
            params[k] = v
        if name == "backup_directory":
            # goal.params 显式给出的 src/dest 优先，缺失才派生：
            # 顶层 target（须像路径）→ 计划内最近文件步骤的 target
            src_fb = params.get("src") or (
                target if str(target).startswith(("/", "~")) else "") \
                or last_file_target or ""
            params.setdefault("src", src_fb or target)
            params.setdefault("dest", (src_fb or target).rstrip("/\\") + "_backup")
        # send_email 兜底：话语中的全部真实文件路径 → attachment 列表（多附件）
        if name == "send_email" and not params.get("attachment"):
            m = re.findall(
                r"(/(?:[\w.\-\u4e00-\u9fa5]+)+[/\w.\-\u4e00-\u9fa5]*"
                r"\.(?:md|txt|pdf|docx|csv|json|py|log|xlsx|png|jpg))",
                intent.get("user_text", ""))
            if m:
                params["attachment"] = m if len(m) > 1 else m[0]
        # 显式参数已覆盖全部必需参数时不再兜底，避免污染无关参数
        if not params or any(k not in params for k in required):
            filler = entity or target
            if filler:
                for pname, meta in props.items():
                    if meta.get("type") == "string" and pname not in params:
                        params[pname] = filler
                        break
            if name == "backup_directory":
                src_fb = params.get("src") or (
                    target if str(target).startswith(("/", "~")) else "") \
                    or last_file_target or ""
                params.setdefault("src", src_fb or target)
                params.setdefault("dest", (src_fb or target).rstrip("/\\") + "_backup")
        return params

    # ---------------------------------------------------------- --
    def _gui_generic_steps(self, action: str, params_in: dict,
                           target: str, unresolved: list[str]) -> list[dict]:
        """导航/切换窗口/创建文件夹 的 GUI 步骤序列（任务书场景 2/3/5）。"""
        gui: list[tuple[str, dict, str]] = []
        if action == "navigate":
            path = params_in.get("path") or target
            gui = [("hotkey", {"keys": ["ctrl", "l"]}, "聚焦地址栏"),
                   ("type", {"text": path}, f"输入路径 {path}"),
                   ("hotkey", {"keys": ["enter"]}, "回车跳转")]
        elif action == "create_folder":
            name = params_in.get("name") or "new_folder"
            gui = [("hotkey", {"keys": ["ctrl", "shift", "n"]}, "新建文件夹"),
                   ("type", {"text": name}, f"输入名称 {name}"),
                   ("hotkey", {"keys": ["enter"]}, "确认")]
        elif action == "switch_window":
            keys = ["alt", "tab"]
            gui = [("hotkey", {"keys": keys}, "切换窗口")]
        if not gui:
            unresolved.append(f"GUI 动作 {action} 未定义步骤序列")
            return []
        steps = [{"step_id": 0, "kind": "gui", "action": a, "target": target,
                  "params": p, "requires_admin": False, "description": d}
                 for a, p, d in gui]
        # 收尾命令由 plan() 统一追加
        return steps

    def _gui_open_app_steps(self, app: str, unresolved: list[str]) -> list[dict]:
        """应用控制：打开应用的 GUI 步骤（Super 键 → 输入名称 → 点击图标）。"""
        if not app:
            unresolved.append("未识别要打开的应用名")
            return []
        el = self._detector.find_element(name=app) if self._detector else None
        return [
            {"step_id": 1, "kind": "gui", "action": "hotkey", "target": app,
             "params": {"keys": ["super"]}, "requires_admin": False,
             "description": "按下 Super 打开活动概览"},
            {"step_id": 2, "kind": "gui", "action": "type", "target": app,
             "params": {"text": app}, "requires_admin": False,
             "description": f"输入应用名 {app}"},
            {"step_id": 3, "kind": "gui", "action": "click", "target": app,
             "params": {"element": el} if el else {"element_query": {"name": app}},
             "requires_admin": False,
             "description": f"点击 {app} 图标" + ("（已定位）" if el else "（执行时定位）")},
        ]

    def _elements_for(self, steps: list[dict]) -> dict:
        """把 GUI 步骤引用的元素汇入 elements 字典（契约表第2组输出）。"""
        elements: dict = {}
        for s in steps:
            el = (s.get("params") or {}).get("element")
            if el and el.get("name"):
                elements[el["name"]] = el
        return elements

    @staticmethod
    def renumber(steps: list[dict]) -> list[dict]:
        for i, s in enumerate(steps, 1):
            s["step_id"] = i
        return steps
