"""Agent_OS_v1.0 — Linux 桌面 Agentic OS 原型（五组集成版）。

按《操作系统设计任务书》完成五组联调的系统总集：
    组1 AI Shell + HostAgent      (group_1/  用户交互层)
    组2 TaskPlanner + ControlDetector (group_2/  决策规划层)
    组3 AppAgent + Automator      (group_3/  执行层)
    组4 ToolRegistry + OS Skills  (group_4/ → src/  工具服务层)
    组5 SystemCoordinator + SecuritySandbox + RAG (group_5/ 系统协调层)

编排数据流（任务书 四.3）：
    用户 → 组1 → 组5(安全检查) → 组2 → 组3 → 组4 ←→ 组5(审计+RAG)

运行：
    python main.py                       # AI Shell 交互模式
    python main.py --demo                # 任务书演示场景表联调
    python main.py --text "查询长沙天气"  # 单指令
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "system_out")

from group_1 import HostAgent, AIShell            # noqa: E402
from group_2 import TaskPlanner, ControlDetector  # noqa: E402
from group_3 import AppAgent                      # noqa: E402
from group_4 import make_stack, schema_catalog    # noqa: E402
from group_5 import (                             # noqa: E402
    SecuritySandbox, RAGKnowledgeBase, SystemCoordinator,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 任务书 七.2 演示场景表（GUI 场景在 Mock 后端下模拟执行，文件场景真实执行）
DEMO_SCENARIOS = [
    ("场景1 基础 GUI", "打开文件管理器"),
    ("场景2 导航操作", "导航到 /tmp"),   # 任务书原路径 /home/user/Documents 本机不存在
    ("场景3 按钮点击", "创建 project 文件夹"),
    ("场景5 窗口切换", "切换到浏览器"),
    ("场景6 复杂任务", "帮我把「demo_task」文件夹里的文件整理一下，顺便看看有没有重复文件，最后备个份"),
    ("信息查询", "查询长沙天气"),
    ("安全拦截", "rm -rf /"),
]


def prepare_sandbox(out: str = OUT) -> str:
    """场景 6 的演示沙箱：含一对重复文件与多类型文件。"""
    sandbox = os.path.join(out, "demo_task")
    shutil.rmtree(sandbox, ignore_errors=True)
    os.makedirs(os.path.join(sandbox, "附件"), exist_ok=True)
    files = {"报告.docx": "A" * 300, "报告副本.docx": "A" * 300,
             "照片.jpg": "B" * 800, "notes.md": "# 笔记\n内容",
             "script.py": "print('hi')", "data.csv": "x,y\n1,2"}
    for name, content in files.items():
        with open(os.path.join(sandbox, name), "w", encoding="utf-8") as f:
            f.write(content)
    with open(os.path.join(sandbox, "附件", "报告.docx"), "w", encoding="utf-8") as f:
        f.write("A" * 300)
    return sandbox


def build(out: str = OUT) -> SystemCoordinator:
    """按任务书 四.3 数据流接线五组模块，返回就绪的系统协调器。"""
    os.makedirs(out, exist_ok=True)
    sandbox = prepare_sandbox(out)
    from src.stats import ToolStats
    stats, registry, skills = make_stack(
        stats=ToolStats(log_path=os.path.join(out, "stats.json")))
    catalog = schema_catalog(registry, skills)

    host = HostAgent()                       # 组1（配 DASHSCOPE_API_KEY 走 LLM）
    detector = ControlDetector()             # 组2 控件检测
    planner = TaskPlanner(schemas=catalog, detector=detector)
    agent = AppAgent(registry, skills)       # 组3
    coordinator = SystemCoordinator(
        host, planner, agent, detector=detector,
        security=SecuritySandbox(), rag=RAGKnowledgeBase(os.path.join(out, "rag_traces.jsonl")),
        audit_path=os.path.join(out, "audit_log.jsonl"),
        schemas_catalog=catalog, default_target=sandbox,
        artifact_dir=out)
    return coordinator


def save_bundle(out: str, bundle: dict) -> None:
    os.makedirs(out, exist_ok=True)
    for name, key in (("intent.json", "intent"), ("plan.json", "plan"),
                      ("session_log.json", "session"), ("check.json", "check"),
                      ("audit.json", "audit")):
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            json.dump(bundle.get(key, {}), f, ensure_ascii=False, indent=2)


def run_demo(out: str = OUT) -> int:
    """任务书演示场景表联调：逐场景跑通五组链路并打印结果矩阵。"""
    coordinator = build(out)
    print("=" * 74)
    print("  Agent_OS_v1.0 五组集成联调（组1→组5安全→组2→组3→组4←→组5审计/RAG）")
    print("=" * 74)
    print(f"{'场景':<14}{'意图':<10}{'安全':<6}{'步骤':<12}{'审计':<7}备注")
    print("-" * 74)
    passed = 0
    for name, text in DEMO_SCENARIOS:
        bundle = coordinator.orchestrate(text)
        intent = bundle["intent"].get("action") or "-"
        approved = "✓" if bundle["check"]["approved"] else "拦截"
        results = bundle.get("results", [])
        ok = sum(1 for r in results if r["tool_result"]["success"])
        steps = f"{ok}/{len(results)}" if results else "-"
        verdict = bundle.get("audit", {}).get("verdict", "-")
        note = bundle.get("rejected") or (
            bundle.get("rag", {}).get("trace_id", "") if bundle.get("rag") else "")
        good = (not bundle["check"]["approved"]) or (
            results and ok == len(results) and verdict == "PASS")
        passed += bool(good)
        print(f"{name:<14}{intent:<10}{approved:<6}{steps:<12}{verdict:<7}"
              f"{note[:34]}")
    print("-" * 74)
    print(f"联调结果: {passed}/{len(DEMO_SCENARIOS)} 场景通过 | 产物: {out}")
    print(f"知识库轨迹: {len(coordinator.rag)} 条 | 审计日志: {len(coordinator.audit_records)} 条")
    return 0 if passed == len(DEMO_SCENARIOS) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent_OS_v1.0 — 五组集成系统入口")
    ap.add_argument("--demo", action="store_true", help="运行任务书演示场景表联调")
    ap.add_argument("--text", default=None, help="单条自然语言指令")
    args = ap.parse_args()

    if args.demo:
        return run_demo()
    coordinator = build()
    if args.text:
        bundle = coordinator.orchestrate(args.text)
        print(json.dumps({k: bundle[k] for k in
                          ("intent", "check", "plan", "session", "audit")
                          if k in bundle}, ensure_ascii=False, indent=2))
        return 0
    AIShell(coordinator.modules[1], coordinator).run()   # 默认：AI Shell 交互
    return 0


if __name__ == "__main__":
    sys.exit(main())
