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


def _same_element(a: dict | None, b: dict | None) -> bool:
    """按 (name, role, bbox) 判断是否同一元素。

    整体 dict 相等会被 app/depth 等无关字段的波动破坏（两次遍历结果几乎
    不可能逐字段相同），导致"重定位重试一次"的判据失效。
    """
    if not a or not b:
        return False
    key = lambda d: (d.get("name"), d.get("role"), tuple(d.get("bbox") or ()))
    return key(a) == key(b)


class FileManagerAgent:
    """文件管理器特化动作（任务书要求的特化实现）。

    实现是"打开文件管理器 → 跳转/新建"的真实动作链。早期实现只发一个
    Ctrl+L / Ctrl+Shift+N 快捷键，既没有前置的打开应用，也没有结果确认，
    与类名/文档声称的"导航到路径 / 创建文件夹"语义不符。
    """

    def __init__(self, automator):
        self.automator = automator

    def navigate_to(self, path: str) -> dict:
        """打开文件管理器并跳转到 path（目录不存在则如实失败）。"""
        return self.automator.execute_step(
            {"action": "navigate", "params": {"path": path, "app": "文件管理器"}})

    def create_folder(self, name: str, parent: str = "") -> dict:
        """在文件管理器当前目录（或先跳转到 parent）新建文件夹。"""
        if parent:
            rec = self.navigate_to(parent)
            if not rec.get("success", True):
                return rec
        last = None
        for action, params in (
                ("hotkey", {"keys": ["ctrl", "shift", "n"]}),
                ("type", {"text": name}),
                ("hotkey", {"keys": ["enter"]})):
            last = self.automator.execute_step({"action": action, "params": params})
            if not last.get("success", True):
                break
        return last or {"success": False, "error": "create_folder 未执行任何步骤"}


class AppAgent:
    """应用智能体：应用状态管理 + 步骤执行（GUI 步骤 / 工具步骤）。"""

    def __init__(self, registry, skills, level: PermissionLevel = PermissionLevel.USER,
                 automator=None, confirmer=None, auto_escalate: bool = True):
        """registry/skills：组4 公开契约对象（只调 call/list_tools 等公开方法）。

        confirmer：提权确认回调 ({tool, from, to}) -> bool。
        缺省 None = **无人值守**：由 auto_escalate 决定是否自动同意，并在审计
        记录里标 confirm_mode="auto"，不再用"恒真的 lambda"把无人值守伪装成
        "用户已确认"。AI Shell 交互模式会注入 input() 版 → confirm_mode="human"。
        """
        self.registry = registry
        self.skills = skills
        self.level = level
        self.automator = automator or Automator()
        self.file_agent = FileManagerAgent(self.automator)
        self.confirmer = confirmer              # None = 无人值守
        self.auto_escalate = bool(auto_escalate)
        self.session_id = f"agent-{uuid.uuid4().hex[:8]}"
        self.escalated: list[dict] = []      # 必须是实例属性，否则跨会话累积
        self.denied_escalations: list[dict] = []   # 被拒绝的提权请求（单独记账）
        self.self_healed: list[dict] = []
        # 回填内容安全终审（修复逻辑审计 #7）：编排层注入的**函数对象**
        # （组5 security.check_step），组3 不 import 组5，遵守解耦纪律。
        # None = 不启用（单测/独立使用）。
        self.security_guard = None

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
        self.denied_escalations.clear()
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
            # 修复逻辑审计 #25：交**快照**而非实例活列表 —— execute() 开头
            # 会 clear()（红线 4 会话隔离），GUI 把 result 存进 RUNS 后，
            # 旧 bundle 的提权/自愈记录会被下一次运行清空（loadRun 回看恒 0 条）
            "escalations": list(self.escalated),
            "escalations_denied": list(self.denied_escalations),
            "self_healed": list(self.self_healed),
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

        # 修复逻辑审计 #7：{{prev_result}} 回填发生在安全闸门**之后**——
        # 上一步结果（如文件内容里夹带的指令文本）可能被注进本步参数。
        # 只对该步真的发生过占位替换的工具步骤做终审。
        had_ref = "{{" in str(step.get("params") or {})
        params = self._resolve_refs(params, ref_ctx, idx - 1)
        if (had_ref and kind != "gui" and self.security_guard is not None):
            try:
                blocked = self.security_guard(name, params)
            except Exception:
                blocked = None
            if blocked:
                return {"step": idx, "kind": kind, "name": name,
                        "params_sent": params,
                        "level_used": getattr(level, "value", str(level)),
                        "duration_ms": round((time.monotonic() - t0) * 1000, 2),
                        "tool_result": {"success": False,
                                        "error": f"回填内容被安全终审拦截: {blocked}",
                                        "blocked_by_guard": True}}
        if kind == "gui":
            el = params.get("element") or elements.get(step.get("target"))
            if el is None and params.get("element_query"):
                el = self._detector_find(params["element_query"])
            result = self.automator.execute_step(step, el)
            if step.get("action") == "click":
                result = self._verify_click(step, params, el, result)
            tool_result = {"success": result.get("success", True),
                           "result": result.get("message", ""),
                           # 修复逻辑审计 #11：GUI 分支失败原因不再丢失
                           # （"目录不存在,未执行导航"等 error 直达前端/审计）
                           "error": result.get("error"),
                           "before_state": result.get("before_state"),
                           "after_state": result.get("after_state"),
                           "simulated": result.get("simulated", True),
                           # mock/degraded 透传：让上层能区分"通道缺失降级"与
                           # "真实执行失败"（修复逻辑审计 #10/#11 的下游口径）
                           "mock": result.get("mock", False),
                           "degraded": result.get("degraded", False),
                           "effect": result.get("effect", "")}
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
    def _ask_escalation(self, info: dict) -> tuple[bool, str]:
        """征询提权许可。返回 (是否同意, 确认来源)。

        来源语义：human=真人确认（交互 Shell）/ auto=无人值守自动同意 /
        human-error=确认回调本身出错（按拒绝处理，安全优先）。
        """
        if callable(self.confirmer):
            try:
                return bool(self.confirmer(info)), "human"
            except Exception:
                return False, "human-error"
        return self.auto_escalate, "auto"

    def _call_contract(self, name: str, params: dict, level, idx: int):
        """经组4 公开契约调用；失败时依次尝试 提权流 / 自愈流。"""
        is_skill = self.skills and name in (s.to_dict()["name"]
                                            for s in self.skills.list_skills())
        call = (lambda n, p: self.skills.call_skill(n, p)) if is_skill \
            else (lambda n, p: self.registry.call(n, p, level))
        result = call(name, params)

        # 提权流：权限被拒 → 征询许可 → ADMIN 重试一次（工具步骤专属）
        if (not result.success and not is_skill
                and str(result.error).startswith("权限不足")):
            asked = {"tool": name, "from": str(level), "to": "admin"}
            confirmed, source = self._ask_escalation(asked)
            info = {"step": idx, "tool": name,
                    "from": getattr(level, "value", str(level)),
                    "to": "admin", "confirmed": confirmed,
                    "confirm_mode": source, "retried": False}
            if confirmed:
                info["retried"] = True
                self.escalated.append(info)      # 审计：只记"真的重试过"
                # 返回重试结果：成功自然返回；失败时其错误也比"权限不足"更具体
                return self.registry.call(name, params, PermissionLevel.ADMIN)
            # 拒绝的提权请求单独入账：早期实现也塞进 escalated，前端一律渲染成
            # "已确认 → ADMIN 重试"，等于界面上帮用户"代签"了
            self.denied_escalations.append(info)
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
    def _resolve_refs(params: dict, ctx: dict, prev_idx: int | None = None) -> dict:
        """把参数里的 {{prev_result}} / {{stepN.result}} 占位回填为真实结果。

        {{prev_result}} 严格指向**紧邻的上一步**：该步失败或无结果时返回
        "(上一步无结果/失败)"，不再退化成"取步骤号最大的成功结果"——
        早期实现会让下游拿到张冠李戴的更早结果，且没有任何提示。
        正则兼容单闭括号残缺形态 {{prev_result}（提示词示例曾长期带此错，
        LLM 照样输出，严格匹配会静默不替换）。
        """
        def sub(value):
            if isinstance(value, list):          # 附件列表等也要递归替换
                return [sub(v) for v in value]
            if isinstance(value, dict):
                return {k: sub(v) for k, v in value.items()}
            if not isinstance(value, str) or "{{" not in value:
                return value

            def rep(m):
                token = m.group(1).strip()
                if token == "prev_result":
                    if prev_idx is None:
                        return ctx[max(ctx)] if ctx else "(无上一步结果)"
                    if prev_idx in ctx:
                        return ctx[prev_idx]
                    return "(上一步无结果/失败)"
                m2 = re.fullmatch(r"step(\d+)(?:\.result)?", token)
                if m2:
                    return ctx.get(int(m2.group(1)), "(引用的步骤不存在)")
                return m.group(0)
            return re.sub(r"\{\{([^{}]+?)\}?\}", rep, value)
        return {k: sub(v) for k, v in params.items()}

    def _selfheal_name(self, wrong: str) -> str | None:
        menu = self.tool_menu()
        matches = difflib.get_close_matches(wrong, menu, n=1, cutoff=0.6)
        return matches[0] if matches else None

    def try_call_with_selfheal(self, name: str, params: dict):
        """边界演示入口：故意写错工具名 → 触发自愈。

        直接复用 _call_contract 的自愈流：早期实现把同一套逻辑抄了第二遍，
        已经与主链路分叉（不递归、不记录失败原因）。
        """
        return self._call_contract(name, params, self.level, 0)

    def _verify_click(self, step, params, el, result) -> dict:
        """点击效果标注 + 无可观测效果时重定位重试一次（最多一次）。

        窗口集合变化只是"可观测效果"的弱信号（页面内点击本就不动窗口），
        因此只标注 effect 不判失败；重试仅在 element_query 能再定位出
        不同元素时发生，避免无谓的重复树遍历。"""
        if result.get("mock") or result.get("api_action"):
            return result
        before = (result.get("before_state") or {}).get("windows") or []
        after = (result.get("after_state") or {}).get("windows") or []
        if before and after != before:
            result.setdefault("effect", "window-changed")
            return result
        result.setdefault("effect", "no-visible-change")
        query = params.get("element_query")
        if not query:
            return result
        el2 = self._detector_find(query)
        if el2 is None or _same_element(el2, el):
            return result
        retry = self.automator.execute_step(step, el2)
        retry["retried"] = True
        retry.setdefault("effect", "no-visible-change")
        return retry

    def _detector_find(self, query: dict):
        """延迟注入的控件查找（避免组3 硬依赖组2，运行期才用到）。

        element_query 来自"上一步操作后才出现的动态 UI"（如 Super 概览的
        搜索结果），异步渲染有延迟：这里自动短暂轮询，给结果项留出注册进
        AT-SPI 树的时间。"""
        detector = getattr(self, "_detector", None)
        if detector is None:
            return None
        q = dict(query)
        q.setdefault("retries", 3)
        q.setdefault("interval", 0.5)
        return detector.find_element(**q)

    def attach_detector(self, detector) -> None:
        self._detector = detector
