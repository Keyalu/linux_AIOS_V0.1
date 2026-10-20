"""experience.py — 经验记忆回流（优化方案 2.1 的最小闭环）。

现状的"记忆"只有审计证据：每轮运行的 intent/plan/session 归档后就是死档案，
规划器从不消费。本模块把运行结果提炼成**通道级经验条目**（脱敏后 JSONL 追加），
供组2 决策表在改写时做"经验警示"注记 —— 参考 mem0 的抽取-更新-检索三段式，
但恪守红线 3：经验**不推翻硬规则**（改写目标仍由决策表决定），只在
route_reason 里附上近期失败率，让确认面板/事件流如实呈现"这条通道最近不稳"。

条目形态（一行一条，全部过 mask_record 后落盘）：
    {ts, action, route, outcome: ok|fail|degraded, failure_kind, elapsed_ms}
failure_kind ∈ {element_not_found, degraded_channel, timeout, other} ——
正是"受限再规划"（优化方案 1.2）将来分派改道策略需要的分类学，先落数据。
"""

from __future__ import annotations

import json
import time

from src.safe_io import append_jsonl


def _failure_kind(error, degraded) -> str:
    if degraded:
        return "degraded_channel"
    e = str(error or "").lower()
    if ("element" in e or "控件" in e or "not found" in e
            or "未找到" in e or "定位" in e):
        return "element_not_found"
    if "timeout" in e or "超时" in e:
        return "timeout"
    return "other"


def record_bundle(mask, bundle: dict, path: str) -> int:
    """从执行 bundle 抽取经验条目并追加落盘。返回写入条数（失败=0）。

    bundle 需含 plan.steps + results（execute_approved/orchestrate 的产物形态）；
    拦截/空计划轮次没有逐条结果，不产生经验（不谎记）。
    """
    plan = bundle.get("plan") or {}
    steps = {s.get("step_id"): s for s in (plan.get("steps") or [])}
    results = bundle.get("results") or (bundle.get("session") or {}).get("steps") or []
    written = 0
    for r in results:
        if not isinstance(r, dict):
            continue
        s = steps.get(r.get("step"), {})
        tr = r.get("tool_result") or {}
        ok = bool(tr.get("success"))
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "action": s.get("action") or r.get("name"),
                 "route": bool(s.get("route_reason")),
                 "outcome": "ok" if ok else (
                     "degraded" if tr.get("degraded") else "fail"),
                 "failure_kind": None if ok else _failure_kind(
                     tr.get("error"), tr.get("degraded")),
                 "elapsed_ms": r.get("duration_ms")}
        if append_jsonl(path, entry, mask):
            written += 1
    return written


def channel_stats(path: str, lookback: int = 300) -> dict[str, dict]:
    """action → {n, fail, degraded, fail_rate}（最近 lookback 条经验的截面）。

    读盘失败/文件不存在返回 {} —— 经验是增强项，缺席时决策表按原硬规则走。
    """
    stats: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-lookback:]
    except OSError:
        return {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        a = e.get("action")
        if not a or "outcome" not in e:
            continue
        st = stats.setdefault(a, {"n": 0, "fail": 0, "degraded": 0})
        st["n"] += 1
        if e.get("outcome") == "fail":
            st["fail"] += 1
        elif e.get("outcome") == "degraded":
            st["degraded"] += 1
    for st in stats.values():
        st["fail_rate"] = (round((st["fail"] + st["degraded"]) / st["n"], 3)
                           if st["n"] else 0.0)
    return stats
