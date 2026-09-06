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


class SystemCoordinator:
    """注册所有模块，编排执行流程（任务书第5组职责 1）。"""

    def __init__(self, host, planner, agent, *,
                 detector=None, security=None, rag=None,
                 audit_path: str | None = None,
                 schemas_catalog: dict | None = None,
                 default_target: str = ".",
                 artifact_dir: str | None = None):
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
        self.register(5, self)                 # 组5 自身（协调 + 安全 + RAG）

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

        # 步骤 1：组1 意图理解
        intent = self.modules[1].understand_intent(user_input,
                                                   default_target=self.default_target)
        bundle["intent"] = intent

        # 步骤 2：组5 安全检查
        security = self.security or SecuritySandboxStub()
        check = security.check(intent)
        bundle["check"] = check
        self.audit({"stage": "security_check", "approved": check["approved"],
                    "risk_level": check.get("risk_level"), "intent": intent})
        if not check["approved"]:
            bundle["rejected"] = f"安全检查未通过: {check.get('reason')}"
            bundle["results"] = []
            bundle["needs_confirm"] = False
            bundle["admin_steps"] = []
            bundle["real_send_steps"] = []
            self._persist(bundle)
            return bundle

        # 步骤 3：组2 任务规划（+ 控件检测）
        plan = self.modules[2].plan(intent)
        bundle["plan"] = plan
        if self.detector is not None:
            try:
                detected = self.detector.detect_elements()
                elements = {el.get("name"): el for el in detected
                            if isinstance(el, dict) and el.get("name")}
                bundle["elements"] = elements
                plan["elements"] = elements
            except Exception:
                bundle["elements"] = plan.get("elements", {})

        if not plan.get("steps"):
            bundle["rejected"] = "；".join(plan.get("unresolved", [])) or "无可执行步骤"
            bundle["results"] = []
            bundle["needs_confirm"] = False
            bundle["admin_steps"] = []
            bundle["real_send_steps"] = []
            self.audit({"stage": "plan_empty", "intent": intent})
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
        return {"intent": intent, "check": check, "plan": plan,
                "elements": plan.get("elements", {}),
                "needs_confirm": bool(admin_steps or real_send),
                "admin_steps": admin_steps, "real_send_steps": real_send}

    def execute_approved(self, intent: dict, plan: dict,
                         event_sink=None, confirm_mode: str = "auto") -> dict:
        """阶段二：用户批准后执行计划（confirm_mode: auto=同意提权, deny=拒绝提权）。"""
        t0 = time.monotonic()
        bundle: dict = {"intent": intent, "plan": plan}

        agent = self.modules[3]
        old_confirmer = agent.confirmer
        if confirm_mode == "deny":
            agent.confirmer = lambda info: False

        def _sink(rec):
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

        ok = session["summary"]["success"]
        total = session["summary"]["total"]
        consistent = self._consistent(session)
        audit = {
            "report_id": f"orch-{time.strftime('%Y%m%d-%H%M%S')}",
            "generated_at": datetime.now().isoformat(),
            "totals": session["summary"],
            "consistent": consistent,
            "verdict": "PASS" if (total and ok == total and consistent) else
                       ("PASS" if total == 0 else "FAIL"),
        }
        bundle["audit"] = audit
        self.audit({"stage": "done", "verdict": audit["verdict"],
                    "total": total, "ok": ok})
        if self.rag is not None:
            actions = [f"{s['kind']}:{s['name']}" for s in session["steps"]]
            doc = self.rag.add_trace(intent.get("user_text", ""), actions,
                                     f"{ok}/{total} 步成功, 审计 {audit['verdict']}")
            bundle["rag"] = {"trace_id": doc["trace_id"],
                             "related": [h["trace_id"] for h in self.rag.query(
                                 intent.get("user_text", ""), 3)]}

        bundle["ok"] = True
        bundle["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 2)
        self._persist(bundle)
        return bundle

    # ---------------------------------------------------------- --
    # 旧版一步式编排（兼容保留：orchestrate_demo 等使用）
    # ---------------------------------------------------------- --
    def orchestrate(self, user_input: str) -> dict:
        t0 = time.monotonic()
        bundle: dict = {"ok": False, "user_text": user_input}

        # 步骤 1：组1 意图理解
        intent = self.modules[1].understand_intent(user_input,
                                                   default_target=self.default_target)
        bundle["intent"] = intent

        # 步骤 2：组5 安全检查（任务书：组1 之后必须过安全闸门）
        security = self.security or SecuritySandboxStub()
        check = security.check(intent)
        bundle["check"] = check
        self.audit({"stage": "security_check", "approved": check["approved"],
                    "risk_level": check.get("risk_level"), "intent": intent})
        if not check["approved"]:
            bundle["rejected"] = f"安全检查未通过: {check.get('reason')}"
            bundle["results"] = []
            self._persist(bundle)
            return bundle

        # 步骤 3：组2 任务规划（+ 控件检测）
        plan = self.modules[2].plan(intent)
        bundle["plan"] = plan
        if self.detector is not None:
            try:
                detected = self.detector.detect_elements()
                # 契约：plan["elements"] 是 {名称: 元素} 字典，供 GUI 步骤定位
                elements = {el.get("name"): el for el in detected
                            if isinstance(el, dict) and el.get("name")}
                bundle["elements"] = elements
                plan["elements"] = elements
            except Exception:
                bundle["elements"] = plan.get("elements", {})

        # 步骤 4：组3 执行（工具步骤内部经组4 公开契约调用）
        if not plan.get("steps"):
            bundle["rejected"] = "；".join(plan.get("unresolved", [])) or "无可执行步骤"
            bundle["results"] = []
            self.audit({"stage": "plan_empty", "intent": intent})
            self._persist(bundle)
            return bundle
        session = self.modules[3].execute_plan(plan)
        bundle["session"] = session
        bundle["results"] = session["steps"]

        # 步骤 5：组5 审计 + RAG 入库
        ok = session["summary"]["success"]
        total = session["summary"]["total"]
        consistent = self._consistent(session)
        audit = {
            "report_id": f"orch-{time.strftime('%Y%m%d-%H%M%S')}",
            "generated_at": datetime.now().isoformat(),
            "totals": session["summary"],
            "consistent": consistent,
            "verdict": "PASS" if (total and ok == total and consistent) else
                       ("PASS" if total == 0 and not intent.get("goals") else "FAIL"),
        }
        bundle["audit"] = audit
        self.audit({"stage": "done", "verdict": audit["verdict"],
                    "total": total, "ok": ok})
        if self.rag is not None:
            actions = [f"{s['kind']}:{s['name']}" for s in session["steps"]]
            doc = self.rag.add_trace(user_input, actions,
                                     f"{ok}/{total} 步成功, 审计 {audit['verdict']}")
            bundle["rag"] = {"trace_id": doc["trace_id"],
                             "related": [h["trace_id"] for h in self.rag.query(user_input, 3)]}

        bundle["ok"] = True
        bundle["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 2)
        self._persist(bundle)
        return bundle

    # ---------------------------------------------------------- --
    # 中间产物落盘（持久性：intent/plan/check/session/audit 五件套）
    # ---------------------------------------------------------- --
    def _persist(self, bundle: dict) -> None:
        if not self.artifact_dir:
            return
        os.makedirs(self.artifact_dir, exist_ok=True)
        for name, key in (("intent.json", "intent"), ("check.json", "check"),
                          ("plan.json", "plan"), ("session_log.json", "session"),
                          ("audit.json", "audit")):
            with open(os.path.join(self.artifact_dir, name), "w",
                      encoding="utf-8") as f:
                json.dump(bundle.get(key, {}), f, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------- --
    # 审计日志（持久性：JSONL 逐条追加）
    # ---------------------------------------------------------- --
    def audit(self, record: dict) -> None:
        rec = {"time": datetime.now().isoformat(), **record}
        self.audit_records.append(rec)
        if not self.audit_path:
            return
        os.makedirs(os.path.dirname(self.audit_path) or ".", exist_ok=True)
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @staticmethod
    def _consistent(session: dict) -> bool:
        """轻量对账：组3 自报成败计数与逐条步骤一致（组5 审计的契约检查点）。"""
        steps = session.get("steps", [])
        ok = sum(1 for s in steps if s["tool_result"]["success"])
        summary = session.get("summary", {})
        return summary.get("total", -1) == len(steps) and summary.get("success", -1) == ok


class SecuritySandboxStub:
    """兜底安全检查（未注入 SecuritySandbox 时使用，保证编排不中断）。"""

    @staticmethod
    def check(intent: dict) -> dict:
        return {"approved": True, "risk_level": "low",
                "reason": "未注入安全模块，放行（仅测试环境）", "layer": 0}
