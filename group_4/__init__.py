"""group_4 — 工具注册 + OS Skills（工具服务层，真实实现位于 src/）。

本包是分组四成果的正式包入口：src/ 里是经过三周迭代、48+ 单元测试
覆盖的真实实现，这里只做再导出与组装，方便其他组按公开契约使用：

    from group_4 import make_stack, schema_catalog

公开契约（任务书接口契约表 · tool_result_json）：
    ToolRegistry.call(name, params, user_level) -> ToolResult
    SkillLibrary.call_skill(name, params)       -> ToolResult
    两者返回统一形态 {"success": bool, "result"/"error": ...}

OS 原理关联（任务书）：持久性 —— 文件系统操作、统计逐笔原子落盘。
"""

from __future__ import annotations

import os
import sys

# 支持从任意工作目录导入 src/（包内自举）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tool_registry import ToolRegistry          # noqa: E402
from src.skill_library import SkillLibrary          # noqa: E402
from src.mcp_connector import MCPConnector          # noqa: E402
from src.stats import ToolStats                     # noqa: E402
from src.real_tools import register_real_tools      # noqa: E402
from src.interfaces import (                        # noqa: E402
    PermissionLevel, ToolSchema, ToolResult, ToolParam, ToolParamType,
)

__all__ = [
    "ToolRegistry", "SkillLibrary", "MCPConnector", "ToolStats",
    "PermissionLevel", "ToolSchema", "ToolResult", "ToolParam",
    "ToolParamType", "make_stack", "schema_catalog", "export_schemas",
]


def make_stack(stats: ToolStats | None = None,
               include_mcp: bool = True,
               include_real: bool = True,
               ):
    """组装一套组4 模块栈：ToolRegistry + SkillLibrary (+ MCP Mock + 真实动作)。

    返回 (stats, registry, skills)。传入自定义 stats 可把统计落到指定文件
    （ToolStats 会自动加载已有账本并追加，实现跨运行累积）。
    """
    stats = stats if stats is not None else ToolStats()
    registry = ToolRegistry(stats=stats)
    skills = SkillLibrary(registry=registry)
    if include_mcp:
        MCPConnector().register_mock_tools(registry)   # 幂等
    if include_real:
        register_real_tools(registry)                  # open_url / send_email（可选动作）
    return stats, registry, skills


def export_schemas(registry: ToolRegistry, skills: SkillLibrary) -> list[dict]:
    """把工具+技能的全部 schema 导出为纯 JSON（MCP inputSchema 同构）。

    这是组4 对外的"能力清单"：组2 规划器据此填参数名，组5 审计据此校验。
    """
    return ([s.to_dict() for s in registry.list_tools()]
            + [s.to_dict() for s in skills.list_skills()])


def schema_catalog(registry: ToolRegistry, skills: SkillLibrary) -> dict:
    """export_schemas 的字典版：{工具名: schema_dict}，供组2 O(1) 查询。"""
    return {s["name"]: s for s in export_schemas(registry, skills)}
