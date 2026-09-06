"""group_1 — AI Shell + HostAgent（用户交互层，对应 UFO² HostAgent）。

对外入口：
    HostAgent().understand_intent(user_text) -> intent_json
    AIShell(host_agent, coordinator=None).run()   # 类 cosh 交互界面

intent_json 契约（任务书接口契约表，扩展 Multi-goal 字段）：
    {"intent": "文件操作|应用控制|系统设置|信息查询",
     "target": 工作目录/对象, "action": 动作名, "params": {...},
     "user_text": 原话, "confidence": float, "unresolved": [...],
     "goals": [多目标时的完整清单]}

OS 原理关联（任务书）：虚拟化 —— HostAgent 解析并分发任务给下游组，
类似进程调度器把作业提交到就绪队列。
"""

from .host_agent import HostAgent
from .ai_shell import AIShell

__all__ = ["HostAgent", "AIShell"]
