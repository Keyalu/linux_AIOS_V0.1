"""coordinator.py — 系统协调器（整条链路的指挥棒，任务书 四.3 的数据流）。

编排数据流（严格对齐任务书）：
    用户 → 组1(意图) → 组5(安全检查) → 组2(规划+控件检测)
        → 组3(执行, 经组4 公开契约调工具) → 组5(审计 + RAG 入库)

SystemCoordinator 持有全部模块引用（register(group_id, module)），
orchestrate() 是外部世界的唯一入口；每一步的中间产物都回传，
与 pipeline_demo/out/ 的 JSON 形态保持一致。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

# 优化方案 1.1：编排过程升格为类型化事件流（EventStream 是五组契约的超集，
# intent/plan/session 契约原样产出；事件只是"这一轮按什么顺序发生了什么"的
# 第一等记录，随产物归档为 events.json，供事后重放对账）。
from src.event_stream import (EventStream, EV_INTENT, EV_CHECK, EV_PLAN,
                              EV_PLAN_GATE, EV_STEP, EV_REFLECT, EV_RAG,
                              EV_DONE)
from src.safe_io import write_json, append_jsonl


def _intent_event(intent: dict) -> dict:
    return {"action": intent.get("action"),
            "intent_id": intent.get("intent_id"),
            "user_text": intent.get("user_text", "")}


def _emit_step(stream: EventStream, rec: dict) -> None:
    """组3 步骤记录 → 精简 step 事件（账本不背完整 tool_result）。"""
    tr = rec.get("tool_result") or {}
    stream.emit(EV_STEP, step=rec.get("step"), kind=rec.get("kind"),
                name=rec.get("name"), success=tr.get("success"),
                degraded=tr.get("degraded"),
                error=str(tr.get("error") or "")[:200],
                duration_ms=rec.get("duration_ms"))


class SystemCoordinator:
    """注册所有模块，编排执行流程（任务书第5组职责 1）。"""

    def __init__(self, host, planner, agent, *,
                 detector=None, security=None, rag=None,
                 audit_path: str | None = None,
                 schemas_catalog: dict | None = None,
                 default_target: str = ".",
                 artifact_dir: str | None = None):
        # 模块登记表：组号 → 实例（组5 自身也注册进来）
        self.modules: dict[int, object] = {}
        self.register(1, host)                 # 组1 AI Shell + HostAgent
        self.register(2, planner)              # 组2 任务规划 + 控件检测
        self.register(3, agent)                # 组3 AppAgent + Automator
        self.detector = detector               # 组2 控件检测器（可空）
        self.security = security               # 组5 安全沙箱
        self.rag = rag                         # 组5 RAG 知识库
        self.schemas_catalog = schemas_catalog or {}
        self.audit_path = audit_path
        self.default_target = default_target   # 工作目录（宿主环境解析）
        self.artifact_dir = artifact_dir       # 中间产物落盘目录（持久性）
        self.audit_records: list[dict] = []
        self._stub = SecuritySandboxStub()      # 未注入安全模块时的常驻兜底
        self._stub_warned = False
        # 修复逻辑审计 #7：把组5 的"回填后终审"作为**数据**（方法对象）注入组3，
        # 组3 只按 getattr 调用 —— 不产生组5↔组3 的 import 依赖（红线 13 同款）。
        if security is not None and hasattr(agent, "security_guard"):
            agent.security_guard = security.check_step
        # 优化方案 2.1：经验回流双向接线（数据注入，不产组间 import）——
        # ① 组2 规划器拿到经验账本路径（改写注记用，getattr 时才读）；
        # ② 每轮执行后回写条目（_record_experience）。
        if artifact_dir:
            planner.experience_path = os.path.join(artifact_dir,
                                                   "experience.jsonl")
        self.register(5, self)                 # 组5 自身（协调 + 安全 + RAG）

    # ---------------------------------------------------------- --
    def _record_experience(self, bundle: dict) -> None:
        """从执行 bundle 抽取经验条目（旁路：失败不影响编排，不谎记）。"""
        if not self.artifact_dir:
            return
        try:
            from src.experience import record_bundle
            record_bundle(self._security().mask_record, bundle,
                          os.path.join(self.artifact_dir, "experience.jsonl"))
        except Exception:
            pass

    def _security(self):
        """取安全模块；未注入时返回常驻 stub 并**留痕一次**。

        早期三处各自 `self.security or SecuritySandboxStub()`：每次新建实例、
        安全层被静默跳过、审计里没有任何"安全未启用"的痕迹。
        """
        if self.security is None:
            if not self._stub_warned:
                self._stub_warned = True
                self.audit({"stage": "security_stub",
                            "warning": "未注入 SecuritySandbox，安全检查为放行 stub"})
            return self._stub
        return self.security

    # 任务书示例接口：注册模块
    def register(self, group_id: int, module) -> None:
        self.modules[group_id] = module

    # ---------------------------------------------------------- --
    # 拆分版编排：阶段一规划（不执行）→ 用户确认 → 阶段二执行
    # ---------------------------------------------------------- --
    def plan_only(self, user_input: str) -> dict:
        """阶段一：意图 + 安全检查 + 规划（不执行）。

        返回含 needs_confirm/admin_steps/real_send_steps——
        needs_confirm=True 时调用方应先征得用户同意再 execute_approved。
        """
        bundle: dict = {"user_input": user_input}
        stream = EventStream(run_id=f"plan-{int(time.time() * 1000)}")

        # 步骤 1：组1 意图理解（RAG 召回相似历史轨迹作 few-shot 记忆注入）
        memory = self._recall_memory(user_input)
        intent = self.modules[1].understand_intent(user_input,
                                                   default_target=self.default_target,
                                                   memory=memory)
        bundle["intent"] = intent
        # LLM 失败降级规则时如实标注：规则引擎对长/复杂指令只是粗略解析
        degraded = str(getattr(self.modules[1], "last_engine", "")).startswith("rules")
        bundle["degraded"] = degraded
        stream.emit(EV_INTENT, engine=getattr(self.modules[1], "last_engine", ""),
                    degraded=degraded, **_intent_event(intent))

        # 步骤 2：组5 安全检查
        security = self._security()
        check = security.check(intent)
        bundle["check"] = check
        self.audit({"stage": "security_check", "approved": check["approved"],
                    "risk_level": check.get("risk_level"), "intent": intent})
        stream.emit(EV_CHECK, approved=check["approved"],
                    risk_level=check.get("risk_level"),
                    reason=check.get("reason"))
        if not check["approved"]:
            bundle["rejected"] = f"安全检查未通过: {check.get('reason')}"
            bundle["results"] = []
            bundle["needs_confirm"] = False
            bundle["admin_steps"] = []
            bundle["real_send_steps"] = []
            bundle["events"] = stream.snapshot()
            self._persist(bundle)
            return bundle

        # 步骤 3：组2 任务规划。plan["elements"] 只含步骤引用的元素
        # （规划期预定位结果，通常 0~3 个）；全桌面快照走 /api/controls，
        # 不再随 plan 全量携带（响应体积从几十 KB 降至 KB 级）
        plan = self.modules[2].plan(intent)
        bundle["plan"] = plan
        bundle["elements"] = plan.get("elements", {})
        stream.emit(EV_PLAN, steps=len(plan.get("steps", [])),
                    routes=plan.get("routes") or [],
                    unresolved=plan.get("unresolved") or [])

        if not plan.get("steps"):
            bundle["rejected"] = "；".join(plan.get("unresolved", [])) or "无可执行步骤"
            bundle["results"] = []
            bundle["needs_confirm"] = False
            bundle["admin_steps"] = []
            bundle["real_send_steps"] = []
            self.audit({"stage": "plan_empty", "intent": intent})
            bundle["events"] = stream.snapshot()
            self._persist(bundle)
            return bundle

        # 需要用户确认的高影响动作清单（真实外发 / 需提权）
        admin_steps = [{"step": s["step_id"], "action": s["action"],
                        "params": s.get("params", {})}
                       for s in plan["steps"] if s.get("requires_admin")]
        real_send = [{"step": s["step_id"], "action": s["action"],
                      "params": s.get("params", {})}
                     for s in plan["steps"]
                     if s.get("action") == "send_email"
                     and not (s.get("params") or {}).get("dry_run", True)]
        # 成功路径同样落盘：用户在确认面板点"取消"时，这一轮的
        # intent/check/plan 必须留下痕迹（早期只有 rejected/plan_empty
        # 两条路径落盘，最需要审计的主流程反而零记录）。
        bundle["needs_confirm"] = bool(admin_steps or real_send)
        bundle["admin_steps"] = admin_steps
        bundle["real_send_steps"] = real_send
        bundle["events"] = stream.snapshot()
        self._persist(bundle)
        return {"intent": intent, "check": check, "plan": plan,
                "elements": plan.get("elements", {}),
                "needs_confirm": bundle["needs_confirm"],
                "admin_steps": admin_steps, "real_send_steps": real_send,
                # 早期 return 里漏了 degraded → 前端"已降级为规则引擎"的警告
                # 永远拿不到值（staged.get("degraded", False) 恒 False）
                "degraded": degraded}

    def _plan_gate(self, plan: dict, stream: EventStream | None = None) -> str | None:
        """执行前对**最终计划**复跑安全闸门（修复逻辑审计 #6/#7）。

        旧流程 check() 只跑在组1 意图上：组2 派生的 dest、改写到组4 动作名后
        的 step、注入的沙箱开关等都不在原始扫描面上；确认流存储的也是旧结论。
        返回 None=放行，字符串=拦截原因。
        """
        steps = (plan or {}).get("steps") or []
        if not steps:
            return None
        verdict = self._security().check({"action": "", "params": {},
                                          "steps": steps})
        self.audit({"stage": "plan_gate", "approved": verdict["approved"],
                    "risk_level": verdict.get("risk_level"),
                    "reason": verdict.get("reason")})
        if stream is not None:
            stream.emit(EV_PLAN_GATE, approved=verdict["approved"],
                        risk_level=verdict.get("risk_level"),
                        reason=verdict.get("reason"), steps=len(steps))
        return None if verdict["approved"] else verdict.get("reason")

    def execute_approved(self, intent: dict, plan: dict,
                         event_sink=None, confirm_mode: str = "auto") -> dict:
        """阶段二：用户批准后执行计划。

        confirm_mode：
          - auto：允许提权（agent 无 confirmer 时按 auto_escalate 自动同意，
            审计里 confirm_mode="auto" 如实标注"无人值守"）；
          - deny：**先过滤掉 requires_admin 步骤再执行** —— 这才是"仅执行
            免提权步骤"的字面语义。早期实现只把 confirmer 换成恒 False，
            提权步骤照常进执行循环、照常产生副作用，最后报"权限不足"。
        """
        t0 = time.monotonic()
        stream = EventStream(run_id=str((intent or {}).get("intent_id")
                                        or f"exec-{int(t0 * 1000)}"))
        plan = dict(plan or {})
        skipped_admin: list[dict] = []
        if confirm_mode == "deny":
            kept = []
            for s in (plan.get("steps") or []):
                if s.get("requires_admin"):
                    skipped_admin.append({"step": s.get("step_id"),
                                          "action": s.get("action")})
                else:
                    kept.append(s)
            plan = {**plan, "steps": kept}
        bundle: dict = {"intent": intent, "plan": plan}
        if skipped_admin:
            bundle["skipped_admin_steps"] = skipped_admin
        stream.emit(EV_INTENT, **_intent_event(intent or {}))
        stream.emit(EV_PLAN, steps=len(plan.get("steps") or []),
                    routes=plan.get("routes") or [],
                    confirm_mode=confirm_mode, skipped_admin=len(skipped_admin))

        # 修复逻辑审计 #6/#7：执行前对最终计划复跑闸门（含 deny 过滤后的形态）
        gate = self._plan_gate(plan, stream)
        if gate:
            bundle["rejected"] = f"计划安全复检未通过: {gate}"
            bundle["results"] = []
            bundle["ok"] = False
            bundle["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 2)
            bundle["events"] = stream.snapshot()
            self._persist(bundle)
            return bundle

        agent = self.modules[3]
        # deny 模式临时替换提权确认回调，执行完在 finally 恢复
        old_confirmer = agent.confirmer
        if confirm_mode == "deny":
            agent.confirmer = lambda info: False

        def _sink(rec):
            _emit_step(stream, rec)
            if event_sink:
                try:
                    event_sink({"stage": "step", "step": rec})
                except Exception:
                    pass                    # 事件回调失败不影响执行

        try:
            session = agent.execute_plan(plan, on_step=_sink)
        finally:
            agent.confirmer = old_confirmer
        bundle["session"] = session
        bundle["results"] = session["steps"]

        # 步骤 5：组5 审计对账（summary 与逐条结果一致才 PASS）+ RAG 入库
        ok = session["summary"]["success"]
        total = session["summary"]["total"]
        consistent = self._consistent(session, plan.get("steps"))
        audit = {
            "report_id": f"orch-{time.strftime('%Y%m%d-%H%M%S')}",
            "generated_at": datetime.now().isoformat(),
            "totals": session["summary"],
            "consistent": consistent,
            "verdict": self._verdict(total, ok, consistent),
            "plan_steps": len(plan.get("steps") or []),
        }
        bundle["audit"] = audit
        self.audit({"stage": "done", "verdict": audit["verdict"],
                    "total": total, "ok": ok})
        stream.emit(EV_DONE, verdict=audit["verdict"], total=total, ok=ok,
                    consistent=consistent)
        if self.rag is not None:
            actions = [f"{s['kind']}:{s['name']}" for s in session["steps"]]
            # 修复逻辑审计 #12：RAG 轨迹同样落盘（traces.jsonl）—— 用户话语里
            # 内联的邮箱/密钥必须先过第 4 层脱敏再入库，与审计/产物同口径。
            sec = self._security()
            safe_text = sec.mask_record(intent.get("user_text", ""))
            safe_result = sec.mask_record(f"{ok}/{total} 步成功, 审计 {audit['verdict']}")
            doc = self.rag.add_trace(safe_text, actions, safe_result)
            bundle["rag"] = {"trace_id": doc["trace_id"],
                             "related": [h["trace_id"] for h in self.rag.query(
                                 intent.get("user_text", ""), 3)]}
            stream.emit(EV_RAG, trace_id=doc["trace_id"])

        bundle["ok"] = True
        bundle["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 2)
        bundle["events"] = stream.snapshot()
        self._persist(bundle)
        self._record_experience(bundle)
        return bundle

    # ---------------------------------------------------------- --
    # 旧版一步式编排（兼容保留：orchestrate_demo 等使用）
    # ---------------------------------------------------------- --
    def orchestrate(self, user_input: str) -> dict:
        t0 = time.monotonic()
        bundle: dict = {"ok": False, "user_text": user_input}
        stream = EventStream(run_id=f"orch-{int(t0 * 1000)}")

        # 步骤 1：组1 意图理解（RAG 记忆注入）
        memory = self._recall_memory(user_input)
        intent = self.modules[1].understand_intent(user_input,
                                                   default_target=self.default_target,
                                                   memory=memory)
        bundle["intent"] = intent
        stream.emit(EV_INTENT, engine=getattr(self.modules[1], "last_engine", ""),
                    **_intent_event(intent))

        # 步骤 2：组5 安全检查（任务书：组1 之后必须过安全闸门）
        security = self._security()
        check = security.check(intent)
        bundle["check"] = check
        self.audit({"stage": "security_check", "approved": check["approved"],
                    "risk_level": check.get("risk_level"), "intent": intent})
        stream.emit(EV_CHECK, approved=check["approved"],
                    risk_level=check.get("risk_level"), reason=check.get("reason"))
        if not check["approved"]:
            bundle["rejected"] = f"安全检查未通过: {check.get('reason')}"
            bundle["results"] = []
            bundle["events"] = stream.snapshot()
            self._persist(bundle)
            return bundle

        # 步骤 3：组2 任务规划（elements 收敛同 plan_only，全桌面快照走 /api/controls）
        plan = self.modules[2].plan(intent)
        bundle["plan"] = plan
        bundle["elements"] = plan.get("elements", {})
        stream.emit(EV_PLAN, steps=len(plan.get("steps", [])),
                    routes=plan.get("routes") or [])
        if not plan.get("steps"):
            bundle["rejected"] = "；".join(plan.get("unresolved", [])) or "无可执行步骤"
            bundle["results"] = []
            self.audit({"stage": "plan_empty", "intent": intent})
            bundle["events"] = stream.snapshot()
            self._persist(bundle)
            return bundle
        # 修复逻辑审计 #6/#7：一步式编排同样对最终计划复检后再执行
        gate = self._plan_gate(plan, stream)
        if gate:
            bundle["rejected"] = f"计划安全复检未通过: {gate}"
            bundle["results"] = []
            bundle["events"] = stream.snapshot()
            self._persist(bundle)
            return bundle
        session = self.modules[3].execute_plan(
            plan, on_step=lambda rec: _emit_step(stream, rec))
        bundle["session"] = session
        bundle["results"] = session["steps"]

        # 步骤 4.5：失败反思重试（智能体闭环）——LLM 产出修正 intent，重新过
        # 安全闸门与决策表后自动重试一次（仅限一步式编排；GUI 确认流不变）
        if (session["summary"].get("fail", 0) > 0
                and getattr(self.modules[1], "use_llm", False)):
            fixed = None
            try:
                failed = [{"step": s.get("step"), "name": s.get("name"),
                           "params": s.get("params_sent", {}),
                           "error": (s.get("tool_result") or {}).get("error", "")}
                          for s in session["steps"]
                          if not (s.get("tool_result") or {}).get("success", True)]
                fixed = self.modules[1].repair_intent(
                    user_input, failed, self.modules[3].tool_menu(), intent)
            except Exception:
                fixed = None
            if fixed and fixed.get("goals"):
                check2 = self._security().check(fixed)
                self.audit({"stage": "reflect", "approved": check2["approved"],
                            "risk_level": check2.get("risk_level"),
                            "intent": fixed})
                stream.emit(EV_REFLECT, approved=check2["approved"],
                            risk_level=check2.get("risk_level"))
                plan2 = self.modules[2].plan(fixed) \
                    if check2["approved"] else {}
                gate2 = self._plan_gate(plan2, stream) if plan2.get("steps") else None
                if plan2.get("steps") and not gate2:
                    # 保留首轮失败证据（intent/plan/session），否则重试成功后
                    # 审计再也回看不到"第一次到底哪步失败、错误是什么"
                    first = {"intent": bundle["intent"], "plan": bundle["plan"],
                             "session": session,
                             "results": bundle.get("results", [])}
                    session = self.modules[3].execute_plan(
                        plan2, on_step=lambda rec: _emit_step(stream, rec))
                    intent, plan = fixed, plan2
                    bundle["intent"] = intent
                    bundle["plan"] = plan
                    bundle["session"] = session
                    bundle["results"] = session["steps"]
                    bundle["first_attempt"] = first
                    bundle["reflected"] = {
                        "reason": "首次执行存在失败步骤，已按 LLM 修复方案重试一次",
                        "fixed_goals": fixed.get("goals")}
                    stream.emit(EV_PLAN, steps=len(plan2.get("steps") or []),
                                routes=plan2.get("routes") or [], retry=True)
                else:
                    bundle["reflected"] = {
                        "reason": (f"反思产出的修正计划未通过计划安全复检: {gate2}"
                                   if gate2 else
                                   "反思产出的修正计划未通过安全检查或为空"),
                        "rejected": not check2["approved"]}

        # 步骤 5：组5 审计 + RAG 入库（检索增强扩展点）
        ok = session["summary"]["success"]
        total = session["summary"]["total"]
        consistent = self._consistent(session, plan.get("steps"))
        audit = {
            "report_id": f"orch-{time.strftime('%Y%m%d-%H%M%S')}",
            "generated_at": datetime.now().isoformat(),
            "totals": session["summary"],
            "consistent": consistent,
            "verdict": self._verdict(total, ok, consistent),
            "plan_steps": len(plan.get("steps") or []),
        }
        bundle["audit"] = audit
        self.audit({"stage": "done", "verdict": audit["verdict"],
                    "total": total, "ok": ok})
        stream.emit(EV_DONE, verdict=audit["verdict"], total=total, ok=ok,
                    consistent=consistent)
        if self.rag is not None:
            actions = [f"{s['kind']}:{s['name']}" for s in session["steps"]]
            # 修复逻辑审计 #12：同上 —— RAG 轨迹入库前先脱敏（与审计同口径）。
            sec = self._security()
            doc = self.rag.add_trace(sec.mask_record(user_input), actions,
                                     sec.mask_record(f"{ok}/{total} 步成功, 审计 {audit['verdict']}"))
            bundle["rag"] = {"trace_id": doc["trace_id"],
                             "related": [h["trace_id"] for h in self.rag.query(user_input, 3)]}
            stream.emit(EV_RAG, trace_id=doc["trace_id"])

        bundle["ok"] = True
        bundle["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 2)
        bundle["events"] = stream.snapshot()
        self._persist(bundle)
        self._record_experience(bundle)
        return bundle

    # ---------------------------------------------------------- --
    # 记忆召回：相似历史轨迹 → 意图层 few-shot 注入（越用越懂用户）
    # ---------------------------------------------------------- --
    def _recall_memory(self, user_input: str) -> list | None:
        if self.rag is None:
            return None
        try:
            return self.rag.query(user_input, 2) or None
        except Exception:
            return None                  # 记忆召回失败不影响编排主流程

    # ---------------------------------------------------------- --
    # 中间产物落盘（持久性：intent/plan/check/session/audit 五件套）
    # ---------------------------------------------------------- --
    def _persist(self, bundle: dict) -> None:
        if not self.artifact_dir:
            return
        os.makedirs(self.artifact_dir, exist_ok=True)
        # 修复逻辑审计 #12：产物五件套同样是凭据外流旁路 —— session_log.json
        # 原样携带 params_sent（send_email.password）与工具结果。统一与审计
        # 日志同口径：入库前过第 4 层脱敏（mask_record 为纯函数，失败原样写）。
        security = self._security()
        # 修复逻辑审计 #25（归档面）：固定文件名"最新五件套"逐轮覆盖，事后
        # 无法比对"批准方案 == 执行方案"。除最新副本外按运行时刻归档一份
        # （artifact_dir/runs/<时间戳>_<intent_id>/）；归档是旁路，失败不断流。
        run_tag = ""
        try:
            it = bundle.get("intent") or {}
            run_tag = (f"{datetime.now():%Y%m%dT%H%M%S}_"
                       f"{str(it.get('intent_id') or 'run')}")
        except Exception:
            run_tag = ""
        run_dir = os.path.join(self.artifact_dir, "runs", run_tag) if run_tag \
            else None
        for name, key in (("intent.json", "intent"), ("check.json", "check"),
                          ("plan.json", "plan"), ("session_log.json", "session"),
                          ("audit.json", "audit"), ("events.json", "events")):
            if key == "events" and "events" not in bundle:
                continue     # 无事件账本的路径不产空文件
            data = bundle.get(key, {})
            # 优化方案 3.3：脱敏与原子写下沉进 safe_io —— 出口本身不再可能"漏挂 mask"
            write_json(os.path.join(self.artifact_dir, name), data,
                       security.mask_record)
            if run_dir:
                write_json(os.path.join(run_dir, name), data,
                           security.mask_record)

    # ---------------------------------------------------------- --
    # 审计日志（持久性：JSONL 逐条追加）
    # ---------------------------------------------------------- --
    def audit(self, record: dict) -> None:
        """JSONL 追加：一行一条审计事件（重启不丢）。

        入库前做第 4 层隐私打码（邮箱/密钥类串）；落盘失败不得打断编排
        —— 审计是旁路，早期实现任何 IO 异常都会炸掉主流程。
        """
        rec = {"time": datetime.now().isoformat(), **record}
        if self.security is not None:
            try:
                rec = self.security.mask_record(rec)
            except Exception:
                rec = {"time": datetime.now().isoformat(), **record}
        self.audit_records.append(rec)
        if not self.audit_path:
            return
        # 内存 rec 已完成脱敏，落盘走统一出口（原子追加语义由 safe_io 保证）
        append_jsonl(self.audit_path, rec, mask=lambda d: d)

    @staticmethod
    def _verdict(total: int, ok: int, consistent: bool) -> str:
        """"审计 PASS"的唯一口径：对账一致 + 全部成功 + **确实执行了步骤**。

        早期两处 verdict 表达式不同（一处"0 步也判 PASS"，另一处额外加了
        goals 条件），同一系统两套审计语义；"什么都没跑"不该看起来像成功。
        """
        if not consistent or total <= 0:
            return "FAIL"
        return "PASS" if ok == total else "FAIL"

    @staticmethod
    def _consistent(session: dict, plan_steps=None) -> bool:
        """轻量对账：三份独立计数必须互相对上（组5 审计的契约检查点）。

        1) 组3 自报 summary.total  == 逐条 steps 条数；
        2) 组3 自报 summary.success == 逐条成功数；
        3) 计划步数 == 实际执行步数（新增）—— 早期只做"自证"，计划被丢掉
        步骤/多出步骤也照样判一致。
        """
        steps = session.get("steps", [])
        ok = sum(1 for s in steps
                 if (s.get("tool_result") or {}).get("success"))
        summary = session.get("summary", {})
        if summary.get("total", -1) != len(steps):
            return False
        if summary.get("success", -1) != ok:
            return False
        if plan_steps is not None and len(plan_steps) != len(steps):
            return False
        return True


class SecuritySandboxStub:
    """兜底安全检查（未注入 SecuritySandbox 时使用，保证编排不中断）。"""

    @staticmethod
    def check(intent: dict) -> dict:
        return {"approved": True, "risk_level": "low",
                "reason": "未注入安全模块，放行（仅测试环境）", "layer": 0}

    @staticmethod
    def check_step(action: str, params: dict) -> str | None:
        return None

    @staticmethod
    def mask_record(record):
        # 与 check 同口径的"放行"：stub 不做脱敏（仅测试环境）
        return record
