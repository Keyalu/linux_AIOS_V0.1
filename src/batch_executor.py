"""
batch_executor.py — 高压批处理执行器（Worker Pool + 有界队列 + 背压 + 限流 + 熔断）

解决"同时处理上万个文件"类高压请求的核心编排层：

    高压请求(1万个任务) → 有界队列(背压) → N 个 Worker 线程 → ToolRegistry.call

五个预设旋钮：
- workers    并行度（磁盘 IO 密集建议 8~16）
- max_queue  队列容量；满时 submit 阻塞等待（背压），等待超时则拒绝
- block_timeout 背压等待上限，防止提交方被无限卡死
- rate_limiter    令牌桶限速，防止打爆磁盘/系统
- circuit_breaker 失败率熔断，系统性故障时快速失败

三档预设：BatchExecutor.preset(registry, "light" | "medium" | "high")

用法：
    with BatchExecutor(registry, workers=8, max_queue=2000) as ex:
        for task in tasks:
            ex.submit(task["tool"], task.get("params", {}))
        results = ex.drain()          # 阻塞等全部完成，收割结果
    print(ex.stats())
"""

from __future__ import annotations
import queue
import threading
import time
from typing import Any

from .interfaces import PermissionLevel, ToolResult


class RateLimiter:
    """令牌桶限速器（线程安全）。

    预设执行速率上限 rate_per_sec，防止高压批处理把磁盘/系统打爆。
    worker 每执行一个任务前必须 acquire() 一个令牌，拿不到就睡眠等待。
    """

    def __init__(self, rate_per_sec: float) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec 必须 > 0")
        self._interval = 1.0 / rate_per_sec
        self._lock = threading.Lock()
        self._next_slot = 0.0   # 下一个可用令牌的单调时钟时刻

    def acquire(self) -> None:
        """阻塞直到获得一个执行令牌。"""
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._interval
        wait = slot - now
        if wait > 0:
            time.sleep(wait)


class CircuitBreaker:
    """熔断器：失败率超阈值后快速失败，避免在系统性故障时继续浪费资源。

    - 至少累计 min_samples 个结果后才开始评估（避免小样本误判）
    - 失败率 > failure_threshold 时熔断打开：后续任务不再真正执行，
      直接返回"熔断"错误，直到 reset() 或冷却 cooldown 秒后自动半开
    """

    def __init__(
        self,
        failure_threshold: float = 0.5,
        min_samples: int = 20,
        cooldown: float = 30.0,
    ) -> None:
        self._threshold = failure_threshold
        self._min_samples = min_samples
        self._cooldown = cooldown
        self._lock = threading.Lock()
        self._total = 0
        self._failures = 0
        self._opened_at: float | None = None

    def record(self, success: bool) -> None:
        with self._lock:
            if self._opened_at is not None:
                return                      # 已熔断期间不计入样本
            self._total += 1
            if not success:
                self._failures += 1
            if (self._total >= self._min_samples
                    and self._failures / self._total > self._threshold):
                self._opened_at = time.monotonic()

    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            # 冷却结束 → 半开：放行试探，并重置计数
            if time.monotonic() - self._opened_at >= self._cooldown:
                self._opened_at = None
                self._total = 0
                self._failures = 0
                return False
            return True

    def reset(self) -> None:
        with self._lock:
            self._opened_at = None
            self._total = 0
            self._failures = 0


class BatchExecutor:
    """有界队列 + 固定 Worker 线程池的批量执行器。

    可选预设：
    - rate_limiter    限速器，控制每秒最多执行多少个任务
    - circuit_breaker 熔断器，失败率过高时中止后续执行
    """

    # 三档预设方案（对应轻载 / 中载 / 高压场景）
    PRESETS: dict[str, dict[str, Any]] = {
        "light":  {"workers": 2,  "max_queue": 200,  "rate_per_sec": 50.0},
        "medium": {"workers": 8,  "max_queue": 2000, "rate_per_sec": 500.0},
        "high":   {"workers": 16, "max_queue": 10000, "rate_per_sec": None},
    }

    @classmethod
    def preset(cls, registry, name: str = "medium") -> "BatchExecutor":
        """按预设档位创建执行器。name: light / medium / high"""
        if name not in cls.PRESETS:
            raise ValueError(f"未知预设: {name}，可选: {list(cls.PRESETS)}")
        cfg = cls.PRESETS[name]
        limiter = (RateLimiter(cfg["rate_per_sec"])
                   if cfg["rate_per_sec"] else None)
        return cls(
            registry,
            workers=cfg["workers"],
            max_queue=cfg["max_queue"],
            rate_limiter=limiter,
            circuit_breaker=CircuitBreaker(),
        )

    def __init__(
        self,
        registry,
        workers: int = 8,
        max_queue: int = 2000,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        if workers < 1 or max_queue < 1:
            raise ValueError("workers 和 max_queue 必须 >= 1")
        self._registry = registry
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._results: list[ToolResult] = []
        self._lock = threading.Lock()
        self._rate_limiter = rate_limiter
        self._circuit_breaker = circuit_breaker
        self._tripped = 0      # 被熔断快速失败的任务数
        self._submitted = 0    # 成功入队的任务数
        self._rejected = 0     # 被背压拒绝的任务数
        self._threads = [
            threading.Thread(target=self._worker, daemon=True, name=f"batch-worker-{i}")
            for i in range(workers)
        ]
        for t in self._threads:
            t.start()

    # ----------------------------------------------------------
    # 提交（背压发生在这里）
    # ----------------------------------------------------------

    def submit(
        self,
        tool: str,
        params: dict | None = None,
        level: PermissionLevel = PermissionLevel.USER,
        block_timeout: float = 30.0,
    ) -> bool:
        """提交一个任务。

        队列满时阻塞等待（背压），最多等 block_timeout 秒；
        超时返回 False（任务被拒绝），调用方可降级/重试/记日志。
        """
        try:
            self._queue.put((tool, params or {}, level), timeout=block_timeout)
        except queue.Full:
            with self._lock:
                self._rejected += 1
            return False
        with self._lock:
            self._submitted += 1
        return True

    def submit_batch(self, calls: list[dict], block_timeout: float = 30.0) -> int:
        """批量提交，返回成功入队的数量。每个元素: {"tool":..., "params":..., "level":...}"""
        ok = 0
        for c in calls:
            if self.submit(
                c["tool"], c.get("params", {}),
                c.get("level", PermissionLevel.USER), block_timeout,
            ):
                ok += 1
        return ok

    # ----------------------------------------------------------
    # 收割
    # ----------------------------------------------------------

    def drain(self) -> list[ToolResult]:
        """阻塞等待队列中全部任务完成，返回所有结果（按完成顺序）。"""
        self._queue.join()
        with self._lock:
            return list(self._results)

    def stats(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "submitted": self._submitted,
                "completed": len(self._results),
                "succeeded": sum(1 for r in self._results if r.success),
                "failed": sum(1 for r in self._results if not r.success),
                "rejected": self._rejected,
                "tripped": self._tripped,
                "circuit_open": (self._circuit_breaker.is_open()
                                 if self._circuit_breaker else False),
                "queue_size": self._queue.qsize(),
            }

    # ----------------------------------------------------------
    # Worker 主循环
    # ----------------------------------------------------------

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:          # 关闭哨兵
                self._queue.task_done()
                break
            tool, params, level = item
            # 熔断：快速失败，不再真正执行，保护下游系统
            if self._circuit_breaker and self._circuit_breaker.is_open():
                result = ToolResult.fail("熔断: 失败率超阈值，任务被中止")
                with self._lock:
                    self._results.append(result)
                    self._tripped += 1
                self._queue.task_done()
                continue
            # 限速：拿到令牌才执行
            if self._rate_limiter:
                self._rate_limiter.acquire()
            try:
                result = self._registry.call(tool, params, level)
            except Exception as e:    # 双保险：任何异常都不能杀死 worker
                result = ToolResult.fail(f"执行异常: {type(e).__name__}: {e}")
            if self._circuit_breaker:
                self._circuit_breaker.record(result.success)
            with self._lock:
                self._results.append(result)
            self._queue.task_done()

    def shutdown(self) -> None:
        """发送关闭哨兵并等待 worker 退出（会先隐式等待队列清空）。"""
        for _ in self._threads:
            self._queue.put(None)
        for t in self._threads:
            t.join(timeout=10)

    def __enter__(self) -> "BatchExecutor":
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()
