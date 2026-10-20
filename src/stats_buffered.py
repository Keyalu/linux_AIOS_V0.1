"""
stats_buffered.py — 高压场景优化版统计模块（Week3 方案演示）

针对原版 ToolStats 在高压下的三个瓶颈：
1. 每次 record 都全量重写 JSON → O(n²) 磁盘IO  → 改为缓冲区批量落盘
2. _records 列表无上限 → 内存无限增长          → 改为环形缓冲（只保留最近 N 条）
3. 多线程并发 record 存在竞态                  → threading.Lock 保护

接口与 ToolStats 完全一致，可无缝替换：
    stats = BufferedToolStats(log_path="stats.json")   # 替代 ToolStats

修复逻辑审计 #19：兑现"无缝替换"承诺 —— 原版与 ToolStats 行为分叉三处：
① 无 _load()（重启不从磁盘续账，"跨运行累积账本"语义丢失）；
② flush 用内存 counters **覆盖**文件（重启后归零再写回，历史统计被抹）；
③ clear() 后 _unsaved 为空，_flush_locked 早退**不落盘**（重置只改了内存，
   文件仍是旧账）。现全部对齐，并同步 ToolStats 的 set_redactor 脱敏钩子。
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
        # 与 ToolStats 同款的入库脱敏钩子（审计 #12/#19：无缝替换必须含它，
        # 否则换过来就是一个凭据明文旁路）。
        self._redactor = None
        # 审计 #19①：与 ToolStats 一致 —— 传入 log_path 即从磁盘续账
        if log_path:
            self._load()

    def set_redactor(self, fn) -> None:
        """注入入库脱敏回调（如组5 SecuritySandbox.mask_record）。"""
        self._redactor = fn if callable(fn) else None

    def _load(self) -> None:
        try:
            with open(self._log_path, encoding="utf-8") as f:
                data = json.load(f)
            self._records = data.get("records", [])[-self._max_records:]
            self._counters = data.get("counters", {})
        # 首次运行/账本损坏 → 从零开始（与 ToolStats 同口径，不拖垮调用方）
        except (FileNotFoundError, OSError, json.JSONDecodeError,
                UnicodeDecodeError):
            self._records = []
            self._counters = {}

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
        if self._redactor is not None:
            # 与 ToolStats 同口径：脱敏失败不拖垮记账
            try:
                entry = self._redactor(entry)
            except Exception:
                pass

        # 锁内完成：明细环形截断 + 聚合计数 + 待落盘缓冲 + 阈值判断
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

    def _flush_locked(self, force: bool = False) -> None:
        """增量落盘。注意：在持有锁的状态下调用。

        审计 #19③：force=True 时空缓冲也要落盘（clear 之后必须把空账本
        写回文件，否则"重置"只发生在内存，重启后旧账复活）。
        """
        if not self._unsaved and not force:
            self._last_flush = time.monotonic()
            return
        try:
            # 增量追加模式：只把未保存的条目写进去，不再全量重写。
            # counters 用内存值覆盖是安全的 —— __init__ 已 _load() 续账
            # （审计 #19② 的根因正是缺了续账）。
            existing = {"records": [], "counters": {}}
            if os.path.exists(self._log_path):
                try:
                    with open(self._log_path, encoding="utf-8") as f:
                        existing = json.load(f)
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    existing = {"records": [], "counters": {}}  # 坏账本自愈重写

            existing["records"] = (
                existing.get("records", []) + self._unsaved
            )[-self._max_records:]
            existing["counters"] = self._counters
            self._write_ledger_locked(existing)
        except Exception:
            pass  # 落盘失败不影响主流程
        finally:
            self._unsaved.clear()
            self._last_flush = time.monotonic()

    def _write_ledger_locked(self, data: dict) -> None:
        """原子写账本（临时文件 + os.replace，UTF-8 钉死 —— 审计 #18 同款）。
        调用方持锁。updated 时间戳在此统一补。"""
        data = {**data, "updated": datetime.now().isoformat()}
        dir_name = os.path.dirname(self._log_path) or "."
        with tempfile.NamedTemporaryFile(
            "w", dir=dir_name, suffix=".tmp", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f, ensure_ascii=False)
            tmp_path = f.name
        os.replace(tmp_path, self._log_path)

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
            # 审计 #19③：重置必须落盘 —— 旧实现 _flush_locked 因缓冲为空
            # 直接早退，文件仍是旧账，且下一次续账(_load)后旧统计复活。
            if self._log_path:
                try:
                    self._write_ledger_locked({"records": [], "counters": {}})
                except Exception:
                    pass
