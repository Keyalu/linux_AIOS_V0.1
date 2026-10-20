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
        # 修复逻辑审计 #12：账本 params 全量落盘会把 send_email.password、
        # write_file.content 等明文写进 stats.json 并经 /api/stats 回前端。
        # 组4 不 import 组5 —— 由编排层把组5 的 mask_record 作为**函数对象**
        # 注入（红线 13 同款数据注入），入库前统一脱敏。
        self._redactor = None
        # 双结构：_records 逐笔流水（GUI 逐笔页），_counters 按工具聚合（报表）
        self._records: list[dict[str, Any]] = []
        self._counters: dict[str, dict[str, int]] = {}
        # 传入 log_path 即从磁盘续账（跨运行累积账本，持久性）
        if log_path:
            self._load()

    def set_redactor(self, fn) -> None:
        """注入入库脱敏回调（如组5 SecuritySandbox.mask_record）。"""
        self._redactor = fn if callable(fn) else None

    def record(self, record: CallRecord) -> None:
        """记录一次工具调用。"""
        # 逐笔条目：时间/工具/参数/成败/耗时；失败时附 error
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
            # 脱敏失败不拖垮记账：宁可少一层遮蔽，账本必须落得下
            try:
                entry = self._redactor(entry)
            except Exception:
                pass

        self._records.append(entry)

        # 聚合计数：每个工具维护 calls/success/fail 三元组
        c = self._counters.setdefault(
            record.tool_name, {"calls": 0, "success": 0, "fail": 0}
        )
        c["calls"] += 1
        if record.success:
            c["success"] += 1
        else:
            c["fail"] += 1

        # 每笔即落盘：进程崩溃最多丢当前一笔（原子写）。
        # 注：这是"逐笔持久性"换来的代价（每笔全量重写）；高压场景请改用
        #     src/stats_buffered.BufferedToolStats（增量批量 + 环形上限 + 锁）。
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
        # 原子写：先写临时文件再 os.replace 原子替换，写一半崩溃也不损坏账本
        # 修复逻辑审计 #18：显式 encoding="utf-8" —— tempfile 默认走 locale
        # 编码（Windows=GBK），与读取侧/其他写入口径不一致，中文参数一多
        # 账本就会混合编码损坏
        dir_name = os.path.dirname(self._log_path) or "."
        try:
            with tempfile.NamedTemporaryFile(
                "w", dir=dir_name, suffix=".tmp", delete=False,
                encoding="utf-8"
            ) as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                tmp_path = f.name
            os.replace(tmp_path, self._log_path)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            # 修复逻辑审计 #18：读取同样钉死 UTF-8（此前 locale=GBK 环境直接
            # UnicodeDecodeError 穿透，build() 被打死）；坏账本回落从零开始
            with open(self._log_path, encoding="utf-8") as f:
                data = json.load(f)
                self._records = data.get("records", [])
                self._counters = data.get("counters", {})
        # 首次运行/账本损坏 → 从零开始，不让历史问题拖垮当前编排
        except (FileNotFoundError, OSError, json.JSONDecodeError,
                UnicodeDecodeError):
            self._records = []
            self._counters = {}
