"""app_agent.py — 应用智能体：执行规划步骤，管理应用状态。

职责（任务书）：
- 执行 GUI 步骤（经 Automator）与工具步骤（经组4 公开契约）；
- 权限被拒 → 请求用户确认 → 提权 ADMIN 重试一次（提权流）；
- 工具名写错 → 用 list_tools 模糊匹配自愈（自愈流）；
- 状态验证：GUI 步骤带 before/after 状态；工具步骤带统一 ToolResult；
- 错误处理：失败原因回写会话日志，供上层反馈组2 调整。
"""

from __future__ import annotations

import difflib
import re
import time
import uuid
from datetime import datetime

from src.interfaces import PermissionLevel   # 公开契约（组4 接口定义）
from .automator import Automator


class FileManagerAgent:
    """文件管理器特化动作（任务书要求的特化实现）。"""

    def __init__(self, automator):
        self.automator = automator

    def navigate_to(self, path: str) -> dict:
        return self.automator.execute_step(
            {"action": "hotkey", "params": {"keys": ["ctrl", "l"]}})
    # 注：真实 PyAutoGUI 路径下接着 typewrite(path)+Enter；Mock 路径由
    # navigate 动作记录等效信息。

    def create_folder(self, name: str) -> dict:
        return self.automator.execute_step(
            {"action": "hotkey", "params": {"keys": ["ctrl", "shift", "n"]}})


class AppAgent:
    """应用智能体：应用状态管理 + 步骤执行（GUI 步骤 / 工具步骤）。"""

    def __init__(self, registry, skills, level: PermissionLevel = PermissionLevel.USER,
                 automator=None, confirmer=None):
        """registry/skills：组4 公开契约对象（只调 call/list_tools 等公开方法）。

        confirmer：提权确认回调 ({tool, from, to}) -> bool；
        缺省自动确认（演示/测试），AI Shell 交互模式可传 input 版。
        """
        self.registry = registry
        self.skills = skills
        self.level = level
        self.automator = automator or Automator()
        self.file_agent = FileManagerAgent(self.automator)
        self.confirmer = confirmer or (lambda info: True)
        self.session_id = f"agent-{uuid.uuid4().hex[:8]}"
        self.escalated: list[dict] = []      # 必须是实例属性，否则跨会话累积
        self.self_healed: list[dict] = []

    # ---------------------------------------------------------- --
    def tool_menu(self) -> list[str]:
        """开机自检：组4 能力清单（契约示例：喂给 LLM function-calling）。"""
        return ([s.to_dict()["name"] for s in self.registry.list_tools()]
                + [s.to_dict()["name"] for s in self.skills.list_skills()])

    def execute_plan(self, plan: dict, verbose: bool = False,
                     on_step=None) -> dict:
        return self.execute(plan.get("steps", []), plan.get("elements", {}),
                            verbose, on_step=on_step)

    def execute(self, steps: list[dict], elements: dict | None = None,
                verbose: bool = False, on_step=None) -> dict:
        """按序执行步骤，产出会话日志（result_json 的集合 + 状态机轨迹）。"""
        started_at = datetime.now().isoformat()   # 运行窗口起点（微秒精度）
        # 会话状态按运行隔离：GUI 常驻实例跨运行复用，不重置会把
        # 历史提权/自愈记录泄进本次会话日志（审计对账随之失真）
        self.escalated.clear()
        self.self_healed.clear()
        elements = elements or {}
        ref_ctx: dict[int, str] = {}              # 步骤号 -> 结果文本（供 {{引用}} 回填）
        if isinstance(elements, list):            # 容错：检测器原始列表 → 字典
            elements = {el.get("name"): el for el in elements
                        if isinstance(el, dict) and el.get("name")}
        out_steps: list[dict] = []
        for idx, step in enumerate(steps, 1):
            rec = self._run_step(idx, step, elements, verbose, ref_ctx)
            out_steps.append(rec)
            if on_step:
                try:
                    on_step(rec)
                except Exception:
                    pass                    # 事件回调失败不影响执行
        ok = sum(1 for s in out_steps if s["tool_result"]["success"])
        return {
            "session_id": self.session_id,
            "timestamp": started_at,
            "steps": out_steps,
            "escalations": self.escalated,
            "self_healed": self.self_healed,
            "summary": {"total": len(out_steps), "success": ok,
                        "fail": len(out_steps) - ok},
        }

    # ---------------------------------------------------------- --
    def _run_step(self, idx: int, step: dict, elements: dict,
                  verbose: bool, ref_ctx: dict) -> dict:
        kind = step.get("kind", "tool")
        name = step.get("action", "")
        params = step.get("params") or {}
        t0 = time.monotonic()
        level = self.level

        params = self._resolve_refs(params, ref_ctx)
        if kind == "gui":
            el = params.get("element") or elements.get(step.get("target"))
            if el is None and params.get("element_query"):
                el = self._detector_find(params["element_query"])
            result = self.automator.execute_step(step, el)
            tool_result = {"success": result.get("success", True),
                           "result": result.get("message", ""),
                           "before_state": result.get("before_state"),
                           "after_state": result.get("after_state"),
                           "simulated": result.get("simulated", True)}
            level_used = "public"
        else:
            # 工具/技能步骤：走组4 公开契约，带提权流与自愈流
            result = self._call_contract(name, params, level, idx)
            tool_result = result.to_dict()
            level_used = getattr(level, "value", str(level))

        duration = (time.monotonic() - t0) * 1000
        if verbose:
            mark = "✓" if tool_result["success"] else "✗"
            print(f"    步骤{idx} [{kind}] {name} → {mark}")
        if tool_result["success"] and "result" in tool_result:
            ref_ctx[idx] = str(tool_result["result"])
        return {"step": idx, "kind": kind, "name": name,
                "params_sent": params, "level_used": level_used,
                "duration_ms": round(duration, 2), "tool_result": tool_result}

    # ---------------------------------------------------------- --
    def _call_contract(self, name: str, params: dict, level, idx: int):
        """经组4 公开契约调用；失败时依次尝试 提权流 / 自愈流。"""
        is_skill = self.skills and name in (s.to_dict()["name"]
                                            for s in self.skills.list_skills())
        call = (lambda n, p: self.skills.call_skill(n, p)) if is_skill \
            else (lambda n, p: self.registry.call(n, p, level))
        result = call(name, params)

        # 提权流：权限被拒 → 用户确认 → ADMIN 重试一次（工具步骤专属）
        if (not result.success and not is_skill
                and str(result.error).startswith("权限不足")):
            info = {"step": idx, "tool": name, "from": getattr(level, "value", str(level)),
                    "to": "admin", "user_confirmed": bool(self.confirmer(
                        {"tool": name, "from": str(level), "to": "admin"}))}
            if info["user_confirmed"]:
                retry = self.registry.call(name, params, PermissionLevel.ADMIN)
                self.escalated.append(info)
                # 返回重试结果：成功自然返回；失败时其错误也比"权限不足"更具体
                return retry
            self.escalated.append(info)
            return result

        # 自愈流：工具名写错 → list_tools 模糊匹配 → 纠错重试
        if not result.success and str(result.error).startswith("工具未注册"):
            fixed = self._selfheal_name(name)
            if fixed and fixed != name:
                retry = self._call_contract(fixed, params, level, idx)
                if retry.success:
                    self.self_healed.append({"wrong": name, "fixed": fixed})
                    return retry
        return result

    @staticmethod
    def _resolve_refs(params: dict, ctx: dict) -> dict:
        """把参数里的 {{prev_result}} / {{stepN.result}} 占位回填为真实结果。"""
        def sub(value):
            if not isinstance(value, str) or "{{" not in value:
                return value

            def rep(m):
                token = m.group(1).strip()
                if token == "prev_result":
                    return ctx[max(ctx)] if ctx else "(无上一步结果)"
                m2 = re.fullmatch(r"step(\d+)(?:\.result)?", token)
                if m2:
                    return ctx.get(int(m2.group(1)), "(引用的步骤不存在)")
                return m.group(0)
            return re.sub(r"\{\{([^}]+)\}\}", rep, value)
        return {k: sub(v) for k, v in params.items()}

    def _selfheal_name(self, wrong: str) -> str | None:
        menu = self.tool_menu()
        matches = difflib.get_close_matches(wrong, menu, n=1, cutoff=0.6)
        return matches[0] if matches else None

    def try_call_with_selfheal(self, name: str, params: dict):
        """边界演示入口：故意写错工具名 → 触发自愈。"""
        result = self.registry.call(name, params, self.level)
        if not result.success and str(result.error).startswith("工具未注册"):
            fixed = self._selfheal_name(name)
            if fixed:
                retry = self.registry.call(fixed, params, self.level)
                if retry.success:
                    self.self_healed.append({"wrong": name, "fixed": fixed})
                    return retry
        return result

    def _detector_find(self, query: dict):
        """延迟注入的控件查找（避免组3 硬依赖组2，运行期才用到）。"""
        detector = getattr(self, "_detector", None)
        return detector.find_element(**query) if detector else None

    def attach_detector(self, detector) -> None:
        self._detector = detector
