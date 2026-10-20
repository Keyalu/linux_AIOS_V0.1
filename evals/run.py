"""run.py — 评测跑分器（优化方案 2.3：Mock 通道、零 LLM、确定性，可进 CI）。

    python evals/run.py            # 通过率表 + 汇总（全过 exit 0，有 FAIL exit 1）
    python evals/run.py -v         # 附带每任务耗时

与回归测试的分工：test_*.py 锁"代码行为"，本评测集答"系统这周变好还是变坏"
—— 任务文本进、判分器出，全部走组间公开契约（红线 1），执行侧全 Mock（红线 7）。
artifact_dir=None：不落盘，不污染 system_out，也不吃经验/缓存之外的历史状态。
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from tasks import TASKS                                   # noqa: E402
from group_1 import HostAgent                             # noqa: E402
from group_2 import ControlDetector, TaskPlanner          # noqa: E402
from group_3 import AppAgent                              # noqa: E402
from group_4 import make_stack, schema_catalog            # noqa: E402
from group_5 import SecuritySandbox, SystemCoordinator    # noqa: E402
from src.stats import ToolStats                           # noqa: E402

DEF_TARGET = "/ws"          # 评测语义锚点（不存在也无妨：strict 动作原样保留）


def build():
    _stats, reg, skills = make_stack(stats=ToolStats())
    catalog = schema_catalog(reg, skills)
    sec = SecuritySandbox()
    host = HostAgent(use_llm=False)              # 评测走确定性规则路径
    planner = TaskPlanner(schemas=catalog, detector=ControlDetector())
    co = SystemCoordinator(host, planner, AppAgent(reg, skills),
                           security=sec, schemas_catalog=catalog,
                           default_target=DEF_TARGET, artifact_dir=None)
    return host, planner, sec, co


def run_task(t, host, planner, sec, co) -> str | None:
    """返回 None=PASS，字符串=失败原因。"""
    ctx: dict = {}
    mode = t["mode"]
    if mode == "flow":
        ctx["bundle"] = co.orchestrate(t["text"])
    elif mode == "check":
        ctx["intent"] = host.understand_intent(t["text"],
                                               default_target=DEF_TARGET)
        ctx["check"] = sec.check(ctx["intent"])
    elif mode == "check_intent":
        ctx["intent"] = t["intent"]
        ctx["check"] = sec.check(ctx["intent"])
    elif mode == "structure":
        ctx["intent"] = host.understand_intent(t["text"],
                                               default_target=DEF_TARGET)
        ctx["check"] = sec.check(ctx["intent"])
        if not ctx["check"]["approved"]:
            return f"意外被拦: {ctx['check'].get('reason')}"
        ctx["plan"] = planner.plan(ctx["intent"])
    elif mode == "structure_intent":
        ctx["intent"] = t["intent"]
        ctx["check"] = sec.check(ctx["intent"])
        if not ctx["check"]["approved"]:
            return f"意外被拦: {ctx['check'].get('reason')}"
        ctx["plan"] = planner.plan(ctx["intent"])
    else:
        return f"未知 mode {mode}"
    try:
        return t["score"](ctx)
    except KeyError as e:
        return f"判分缺字段 {e}（ctx keys={sorted(ctx)}）"
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def main() -> int:
    verbose = "-v" in sys.argv
    host, planner, sec, co = build()
    passed = 0
    print(f"评测基准集（{len(TASKS)} 任务，Mock 通道 / 规则意图 / 零 LLM）")
    print("-" * 76)
    for t in TASKS:
        t0 = time.monotonic()
        try:
            fail = run_task(t, host, planner, sec, co)
        except Exception as e:                      # 任务炸了也是评测结果
            fail = f"{type(e).__name__}: {e}"
        dt = (time.monotonic() - t0) * 1000
        if fail is None:
            passed += 1
            mark = "PASS"
        else:
            mark = f"FAIL → {fail}"
        print(f"  {t['id']:<26} {mark}" + (f"   [{dt:.0f}ms]" if verbose else ""))
    rate = passed / len(TASKS) * 100 if TASKS else 0.0
    print("-" * 76)
    print(f"通过率: {passed}/{len(TASKS)} = {rate:.1f}%")
    return 0 if passed == len(TASKS) else 1


if __name__ == "__main__":
    sys.exit(main())
