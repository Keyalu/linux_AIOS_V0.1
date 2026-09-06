"""group_3 — AppAgent + Automator（执行层）。"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)      # 组3 只依赖 src.interfaces 公开契约

from .automator import Automator                  # noqa: E402
from .app_agent import AppAgent, FileManagerAgent # noqa: E402

__all__ = ["Automator", "AppAgent", "FileManagerAgent"]
