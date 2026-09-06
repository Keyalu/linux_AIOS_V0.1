"""group_2 — 任务规划 + 控件检测（决策规划层，对应 UFO² Control Detection）。

对外入口：
    TaskPlanner().plan(intent, schemas=None) -> plan_json {"steps", "elements", ...}
    ControlDetector().detect_elements()      -> [{"role","name","bbox"}, ...]
    ControlDetector().find_element(role=, name=)

plan_json 契约（任务书接口契约表）：
    {"steps": [{"step_id": 1, "action": "...", "target": "..."}],
     "elements": {...}}

OS 原理关联（任务书）：并发 —— 规划出的步骤序列交由组3 异步调度执行，
规划器维护状态同步（每步带前置依赖与失败策略）。
"""

from .task_planner import TaskPlanner
from .control_detector import ControlDetector

__all__ = ["TaskPlanner", "ControlDetector"]
