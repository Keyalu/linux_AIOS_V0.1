"""
stats.py — 工具调用统计模块

功能：
- 记录每次工具调用的名称、参数、结果、耗时
- 按工具聚合统计（调用次数、成功/失败次数）
- 持久化到 JSON 文件（原子写入，防崩溃损坏）
- 提供 report() 查询统计报告

来自版本1 的优点：完整统计与持久化。
"""

from __future__ import annotations
import json
import os
import tempfile
from datetime import datetime
from typing import Any

from .interfaces import IToolStats, CallRecord


class ToolStats(IToolStats):
    """工具调用统计 — 真实实现。"""

    def __init__(self, log_path: str | None = None) -> None:
        self._log_path = log_path
        self._records: list[dict[str, Any]] = []
        self._counters: dict[str, dict[str, int]] = {}
        if log_path:
            self._load()

    def record(self, record: CallRecord) -> None:
        """记录一次工具调用。"""
        entry = {
            "time": datetime.now().isoformat(),
            "tool": record.tool_name,
            "params": record.params,
            "success": record.success,
            "duration_ms": round(record.duration_ms, 2),
        }
        if record.error:
            entry["error"] = record.error

        self._records.append(entry)

        c = self._counters.setdefault(
            record.tool_name, {"calls": 0, "success": 0, "fail": 0}
        )
        c["calls"] += 1
        if record.success:
            c["success"] += 1
        else:
            c["fail"] += 1

        if self._log_path:
            self._save()

    def report(self) -> dict[str, dict[str, int]]:
        """返回统计报告。"""
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
        self._records.clear()
        self._counters.clear()
        if self._log_path:
            self._save()

    def _save(self) -> None:
        """原子写入 JSON 文件。"""
        data = {
            "records": self._records,
            "counters": self._counters,
            "updated": datetime.now().isoformat(),
        }
        dir_name = os.path.dirname(self._log_path) or "."
        try:
            with tempfile.NamedTemporaryFile(
                "w", dir=dir_name, suffix=".tmp", delete=False
            ) as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                tmp_path = f.name
            os.replace(tmp_path, self._log_path)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            with open(self._log_path) as f:
                data = json.load(f)
                self._records = data.get("records", [])
                self._counters = data.get("counters", {})
        except (FileNotFoundError, json.JSONDecodeError):
            self._records = []
            self._counters = {}
