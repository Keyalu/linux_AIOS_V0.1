"""rag.py — RAG 知识库（零依赖向量检索）。

任务书要求：存储执行轨迹，支持向量检索。环境里没有 numpy/向量库，
这里用纯标准库实现一条可用的 RAG 最小闭环：
- 词级 TF 向量（中文按字 + 英文按词切分）+ 余弦相似度排序；
- 轨迹以 JSONL 追加落盘（持久性：重启不丢）；
- query(text, k) 返回最相关的 k 条历史轨迹。

这就是"检索增强生成"里的 R + R：真实系统把召回的轨迹拼进 LLM 上下文
即可成为完整 RAG —— 组1 的 HostAgent 已预留该扩展点。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections import Counter


def _tokenize(text: str) -> Counter:
    """极简切分：CJK 单字 + 连续英文/数字词。返回词频向量。"""
    text = (text or "").lower()
    tokens = re.findall(r"[a-z0-9_]+", text)
    tokens += [ch for ch in re.findall(r"[\u4e00-\u9fff]", text)]
    return Counter(tokens)


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class RAGKnowledgeBase:
    """执行轨迹知识库：add_trace 入库，query 余弦检索 top-k。"""

    def __init__(self, path: str | None = None, max_documents: int = 5000):
        """path：JSONL 落盘文件；不传则纯内存（单次演示用）。

        max_documents：内存中保留的轨迹上限（长期运行不无限膨胀；
        与落盘文件无关，文件仍是全量追加）。
        """
        self.path = path
        self.max_documents = max(1, int(max_documents))
        self.documents: list[dict] = []
        self._lock = threading.Lock()
        self._load()

    # ---------------------------------------------------------- --
    def add_trace(self, user_input: str, actions: list, result: str) -> dict:
        # 一条轨迹 = 输入话语 + 动作序列 + 结果摘要（trace_id 全局唯一）
        doc = {
            "trace_id": f"trace-{uuid.uuid4().hex[:8]}",
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "input": user_input,
            "actions": actions,
            "result": result,
        }
        self.documents.append(doc)
        if len(self.documents) > self.max_documents:
            self.documents = self.documents[-self.max_documents:]
        self._append(doc)
        return doc

    def query(self, query_text: str, k: int = 3) -> list[dict]:
        q = _tokenize(query_text)
        # 每条轨迹算 查询向量 vs (输入+动作) 向量 的余弦相似度，按相似度排序取 top-k
        scored = sorted(
            (( _cosine(q, _tokenize(d["input"] + " " + " ".join(
                d["actions"] if isinstance(d["actions"], list) else [str(d["actions"])]))), d)
             for d in self.documents),
            key=lambda pair: pair[0], reverse=True)
        hits = [d for score, d in scored if score > 0][:k]
        return hits

    def __len__(self) -> int:
        return len(self.documents)

    # ---------------------------------------------------------- --
    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        # 逐行容错：单行损坏只丢那一行，不让整个知识库加载失败
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self.documents.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return                     # 读不到就当空库，不拖垮编排主流程
        if len(self.documents) > self.max_documents:
            self.documents = self.documents[-self.max_documents:]

    def _append(self, doc: dict) -> None:
        """追加落盘。失败**不抛**：RAG 是旁路，写不进去也不能打断编排主流程。"""
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(doc, ensure_ascii=False) + "\n")
        except OSError:
            pass
