"""分组四：工具注册 + OS Skills 合并版。

融合了三个版本的优点：
- 版本1：接口抽象、Mock、统计
- 版本2：权限、备份/查重、JSON-RPC Mock
- 我搭建版本：默认工具、Skill 丰富度、MCP Server 管理
"""

from .tool_registry import ToolRegistry
from .skill_library import SkillLibrary
from .mcp_connector import MCPConnector
from .stats import ToolStats
from .stats_buffered import BufferedToolStats
from .batch_executor import BatchExecutor, RateLimiter, CircuitBreaker
from .mock_registry import MockToolRegistry
from .mock_skills import MockSkillLibrary
from .interfaces import (
    ToolSchema, ToolParam, ToolParamType, ToolResult, PermissionLevel, CallRecord
)

__all__ = [
    "ToolRegistry",
    "SkillLibrary",
    "MCPConnector",
    "ToolStats",
    "BufferedToolStats",
    "BatchExecutor",
    "RateLimiter",
    "CircuitBreaker",
    "MockToolRegistry",
    "MockSkillLibrary",
    "ToolSchema",
    "ToolParam",
    "ToolParamType",
    "ToolResult",
    "PermissionLevel",
    "CallRecord",
]
