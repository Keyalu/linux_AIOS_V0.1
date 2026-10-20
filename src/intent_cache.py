"""intent_cache.py — LLM 意图结果缓存（优化方案 3.1 的第一刀）。

动机：LLM 是编排链上最慢、最不稳定的环节（1~3s/次 + 输出波动）。演示与联调
里同一句话常被连发多次；规则路径本就毫秒级，只有 `_llm_intent` 的原始产出
值得缓存。**只缓存"未归一的原始意图"**：target 锚定/dry_run/附件等
_finalize_intent 归一尾段每次都真跑（红线 3——归一依赖当轮 default_target
与文件系统实况，不许吃缓存）。

命中留痕：last_engine 追加 "(cache)" 后缀 → 事件流 intent 事件、GUI 引擎标签、
评测统计都如实可见，不冒充"刚算的"。
"""

from __future__ import annotations

import hashlib
import threading
import time


def cache_key(user_input: str, default_target: str, model: str,
              memory: list | None) -> str:
    """缓存键 = 话语 + 锚定目录 + 模型 + few-shot 记忆指纹。

    记忆（RAG 召回）参与 LLM 输出，必须进键 —— 否则知识库更新后吃的旧缓存
    就是"记忆失效"的伪装。"""
    mem = ",".join(str((m or {}).get("trace_id", "")) for m in (memory or []))
    raw = f"{user_input}\x1f{default_target}\x1f{model}\x1f{mem}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class TTLCache:
    """小容量 TTL+LRU（线程安全）。缓存的是意图 dict，取值方负责 deepcopy。"""

    def __init__(self, maxn: int = 128, ttl: float = 300.0):
        self.maxn = maxn
        self.ttl = ttl
        self._lock = threading.Lock()
        self._d: dict[str, tuple[float, object]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        with self._lock:
            item = self._d.get(key)
            if item is None:
                self.misses += 1
                return None
            exp, val = item
            if time.monotonic() > exp:
                self._d.pop(key, None)
                self.misses += 1
                return None
            self._d.pop(key)                 # LRU：重插到队尾
            self._d[key] = (exp, val)
            self.hits += 1
            return val

    def put(self, key: str, val) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            self._d[key] = (time.monotonic() + self.ttl, val)
            while len(self._d) > self.maxn:  # 满则逐出最旧（插入序≈LRU）
                self._d.pop(next(iter(self._d)))

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self.hits = self.misses = 0
