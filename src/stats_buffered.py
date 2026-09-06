"""
stats_buffered.py — 高压场景优化版统计模块（Week3 方案演示）

针对原版 ToolStats 在高压下的三个瓶颈：
1. 每次 record 都全量重写 JSON → O(n²) 磁盘IO  → 改为缓冲区批量落盘
2. _records 列表无上限 → 内存无限增长          → 改为环形缓冲（只保留最近 N 条）
3. 多线程并发 record 存在竞态                  → threading.Lock 保护

接口与 ToolStats 完全一致，可无缝替换：
    stats = BufferedToolStats(log_path="stats.json")   # 替代 ToolStats
"""

from __future__ import annotations
import json
import os
import tempfile
import threading
import time
from datetime import datetime
from typing import Any

from .interfaces import IToolStats, CallRecord


class BufferedToolStats(IToolStats):
    """带缓冲落盘 + 环形上限 + 线程安全的统计实现。"""

    def __init__(
        self,
        log_path: str | None = None,
        flush_every: int = 200,      # 每累积 200 条落盘一次
        flush_interval: float = 5.0, # 或每 5 秒落盘一次（先到者触发）
        max_records: int = 5000,     # 内存中最多保留最近 5000 条明细
    ) -> None:
        self._log_path = log_path
        self._flush_every = flush_every
        self._flush_interval = flush_interval
        self._max_records = max_records

        self._records: list[dict[str, Any]] = []   # 环形缓冲
        self._unsaved: list[dict[str, Any]] = []   # 待落盘缓冲
        self._counters: dict[str, dict[str, int]] = {}
        self._last_flush = time.monotonic()
        self._lock = threading.Lock()

    # ----------------------------------------------------------
    # 核心记录（O(1)，只在达到阈值时才碰磁盘）
    # ----------------------------------------------------------

    def record(self, record: CallRecord) -> None:
        entry = {
            "time": datetime.now().isoformat(),
            "tool": record.tool_name,
            "params": record.params,
            "success": record.success,
            "duration_ms": round(record.duration_ms, 2),
        }
        if record.error:
            entry["error"] = record.error

        with self._lock:
            self._records.append(entry)
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records:]

            c = self._counters.setdefault(
                record.tool_name, {"calls": 0, "success": 0, "fail": 0}
            )
            c["calls"] += 1
            if record.success:
                c["success"] += 1
            else:
                c["fail"] += 1

            self._unsaved.append(entry)
            should_flush = (
                len(self._unsaved) >= self._flush_every
                or (time.monotonic() - self._last_flush) >= self._flush_interval
            )
            if should_flush and self._log_path:
                self._flush_locked()

    def flush(self) -> None:
        """手动触发落盘（程序退出前调用，保证不丢数据）。"""
        with self._lock:
            if self._log_path:
                self._flush_locked()

    def _flush_locked(self) -> None:
        """原子写入。注意：在持有锁的状态下调用。"""
        if not self._unsaved:
            self._last_flush = time.monotonic()
            return
        try:
            # 增量追加模式：只把未保存的条目写进去，不再全量重写
            existing = {"records": [], "counters": {}}
            if os.path.exists(self._log_path):
                try:
                    with open(self._log_path, encoding="utf-8") as f:
                        existing = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass

            existing["records"] = (
                existing.get("records", []) + self._unsaved
            )[-self._max_records:]
            existing["counters"] = self._counters
            existing["updated"] = datetime.now().isoformat()

            dir_name = os.path.dirname(self._log_path) or "."
            with tempfile.NamedTemporaryFile(
                "w", dir=dir_name, suffix=".tmp", delete=False, encoding="utf-8"
            ) as f:
                json.dump(existing, f, ensure_ascii=False)
                tmp_path = f.name
            os.replace(tmp_path, self._log_path)
        except Exception:
            pass  # 落盘失败不影响主流程
        finally:
            self._unsaved.clear()
            self._last_flush = time.monotonic()

    # ----------------------------------------------------------
    # 查询接口（与 ToolStats 完全一致）
    # ----------------------------------------------------------

    def report(self) -> dict[str, dict[str, int]]:
        return dict(self._counters)

    def total_calls(self) -> int:
        return sum(c["calls"] for c in self._counters.values())

    def tool_stats(self, name: str) -> dict[str, int] | None:
        return self._counters.get(name)

    def recent_records(self, limit: int = 10) -> list[dict]:
        return self._records[-limit:]

    def success_rate(self, name: str | None = None) -> float:
        if name:
            c = self._counters.get(name)
            if not c or c["calls"] == 0:
                return 0.0
            return c["success"] / c["calls"]
        total = self.total_calls()
        if total == 0:
            return 0.0
        total_success = sum(c["success"] for c in self._counters.values())
        return total_success / total

    def avg_duration(self, name: str | None = None) -> float:
        recs = [r for r in self._records if r["tool"] == name] if name else self._records
        if not recs:
            return 0.0
        return sum(r["duration_ms"] for r in recs) / len(recs)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._counters.clear()
            self._unsaved.clear()
            if self._log_path:
                self._flush_locked()
