"""event_stream.py — 类型化运行事件流（优化方案 1.1：架构翻新的最小落地）。

动机（参考 OpenHands EventStream）：现状的"事件"是三份手工攒出来的字典
（GUI run["events"] 只活在内存、coordinator.audit 是散点记录、RUNS bundle 事后拼），
所以"这一轮到底按什么顺序发生了什么"无法从落盘产物重放——2026-09-19 审计 #25
的"回看失真"正是这个结构问题的症状。

本模块给每次编排一个**只追加、带 seq 游标**的事件账本：
- 事件不可变：emit() 返回副本，外部改不动账本；
- 线程安全：内部锁收口（簇7 ⏸"锁外共享态"在新代码路径上就地解决）；
- 可持久化：绑定 jsonl 路径后逐条落盘（runs/<tag>/events.jsonl），
  load_events() 可完整重放；
- 它是五组契约的**超集而非替代**（红线 1）：intent/plan/session 等契约对象
  原样产出，事件只是"编排过程"的第一等记录。

用法（组5 coordinator 内）：
    stream = EventStream(run_id="orch-...")
    stream.emit("intent", intent=...)          # stage 无关的键直接进 payload
    ...
    stream.dump_jsonl(path)                    # 或构造时给 jsonl_path 实时落盘
"""

from __future__ import annotations

import copy
import json
import threading
import time
from datetime import datetime

# 事件类型常量（只追加不改名；新增阶段在这里登记，避免散字符串漂移）
EV_INTENT = "intent"
EV_CHECK = "check"
EV_PLAN = "plan"
EV_PLAN_GATE = "plan_gate"
EV_STEP = "step"
EV_REFLECT = "reflect"
EV_RAG = "rag"
EV_DONE = "done"


class EventStream:
    """一次运行的不可变事件账本（append-only，seq 单调）。"""

    __slots__ = ("_lock", "_events", "_run_id", "_t0", "_jsonl_path")

    def __init__(self, run_id: str = "", jsonl_path: str | None = None):
        self._lock = threading.Lock()
        self._events: list[dict] = []
        self._run_id = run_id
        self._t0 = time.monotonic()
        self._jsonl_path = jsonl_path

    # ---------------------------------------------------------- --
    def emit(self, stage: str, **data) -> dict:
        """追加一条事件并返回其**副本**。写盘失败不阻断编排（如实降级）。"""
        with self._lock:
            ev = {"seq": len(self._events), "run_id": self._run_id,
                  "time": datetime.now().isoformat(timespec="milliseconds"),
                  "elapsed_ms": round((time.monotonic() - self._t0) * 1000, 1),
                  "stage": stage}
            ev.update(copy.deepcopy(data))
            self._events.append(ev)
            path = self._jsonl_path
        if path:
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            except OSError:
                # 账本落盘失败必须可见：降级标记进事件本身（不静默吞）
                with self._lock:
                    self._events[-1]["jsonl_write_failed"] = True
        return dict(ev)

    # ---------------------------------------------------------- --
    def snapshot(self, since: int = 0) -> list[dict]:
        """seq > since 的事件副本列表（游标式增量读，GUI 轮询/重放共用）。"""
        with self._lock:
            return [dict(e) for e in self._events[since:]]

    def tail(self) -> dict | None:
        with self._lock:
            return dict(self._events[-1]) if self._events else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


def load_events(path: str) -> list[dict]:
    """从 events.jsonl 重放事件流（坏行跳过——账本宁可残缺不可炸）。"""
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if isinstance(e, dict) and "seq" in e:
                        out.append(e)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out
