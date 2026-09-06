"""group_5 — 系统协调 + 安全 + RAG（系统协调层）。"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from .security import SecuritySandbox               # noqa: E402
from .rag import RAGKnowledgeBase                   # noqa: E402
from .coordinator import SystemCoordinator          # noqa: E402

__all__ = ["SecuritySandbox", "RAGKnowledgeBase", "SystemCoordinator"]
