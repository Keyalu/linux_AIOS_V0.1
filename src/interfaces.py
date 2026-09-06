"""
interfaces.py — 工具注册 + OS Skills 接口定义

本文件定义所有模块的类型签名和接口契约，
是第3组 AppAgent 与第4组 Tools 模块的对接规范。

融合了三个版本的优点：
- 版本1 的完整接口抽象与类型系统
- 版本2 的权限级别概念
- 我搭建版本的统一返回格式
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# ============================================================
# 1. 基础类型定义
# ============================================================

class ToolParamType(str, Enum):
    """工具参数类型 — 参考 MCP / JSON Schema。"""
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"


class PermissionLevel(str, Enum):
    """工具权限级别。"""
    PUBLIC = "public"
    USER = "user"
    ADMIN = "admin"

    def rank(self) -> int:
        return {"public": 0, "user": 1, "admin": 2}[self.value]


@dataclass
class ToolParam:
    """单个参数的描述。"""
    name: str
    type: ToolParamType
    description: str = ""
    required: bool = True
    default: Any = None


@dataclass
class ToolSchema:
    """
    工具的完整描述（"说明书"）。
    参考 MCP 协议的 tool 定义格式。
    """
    name: str
    description: str
    parameters: list[ToolParam] = field(default_factory=list)
    returns: str = "dict"
    permission: PermissionLevel = PermissionLevel.USER

    def to_dict(self) -> dict:
        """转为 JSON 友好的字典格式（参考 MCP inputSchema）。"""
        props = {}
        required = []
        for p in self.parameters:
            props[p.name] = {
                "type": p.type.value,
                "description": p.description,
            }
            if p.default is not None:
                props[p.name]["default"] = p.default
            if p.required:
                required.append(p.name)
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.value,
            "inputSchema": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        }


# ============================================================
# 2. 工具函数类型
# ============================================================

# 工具函数签名：接收任意关键字参数，返回 dict
ToolFunc = Callable[..., dict[str, Any]]


# ============================================================
# 3. 统一返回格式
# ============================================================

@dataclass
class ToolResult:
    """
    工具调用的统一返回格式。
    AppAgent 调用工具后拿到的就是这个结构。
    """
    success: bool
    result: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"success": self.success}
        if self.success:
            d["result"] = self.result
        else:
            d["error"] = self.error
        return d

    @staticmethod
    def ok(result: Any = None) -> "ToolResult":
        return ToolResult(success=True, result=result)

    @staticmethod
    def fail(error: str) -> "ToolResult":
        return ToolResult(success=False, error=error)


# ============================================================
# 4. 调用记录（用于统计）
# ============================================================

@dataclass
class CallRecord:
    """一次工具调用的记录。"""
    tool_name: str
    params: dict[str, Any]
    success: bool
    duration_ms: float
    error: str | None = None


# ============================================================
# 5. 抽象接口 — ToolRegistry
# ============================================================

class IToolRegistry(ABC):
    """工具注册表接口。"""

    @abstractmethod
    def register(
        self,
        name: str,
        func: ToolFunc,
        schema: ToolSchema | None = None,
    ) -> None:
        """注册一个工具。"""
        ...

    @abstractmethod
    def unregister(self, name: str) -> bool:
        """注销一个工具，返回是否成功。"""
        ...

    @abstractmethod
    def call(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        user_level: PermissionLevel = PermissionLevel.USER,
    ) -> ToolResult:
        """调用一个工具，返回 ToolResult。"""
        ...

    @abstractmethod
    def list_tools(self) -> list[ToolSchema]:
        """列出所有已注册的工具。"""
        ...

    @abstractmethod
    def has(self, name: str) -> bool:
        """检查工具是否已注册。"""
        ...


# ============================================================
# 6. 抽象接口 — SkillLibrary
# ============================================================

class ISkillLibrary(ABC):
    """OS Skills 库接口。Skill = 多个 Tool 的编排组合。"""

    @abstractmethod
    def register_skill(
        self,
        name: str,
        func: ToolFunc,
        schema: ToolSchema | None = None,
    ) -> None:
        """注册一个 Skill。"""
        ...

    @abstractmethod
    def call_skill(
        self,
        name: str,
        params: dict[str, Any] | None = None,
    ) -> ToolResult:
        """调用一个 Skill。"""
        ...

    @abstractmethod
    def list_skills(self) -> list[ToolSchema]:
        """列出所有已注册的 Skill。"""
        ...


# ============================================================
# 7. 抽象接口 — ToolStats
# ============================================================

class IToolStats(ABC):
    """工具调用统计接口。"""

    @abstractmethod
    def record(self, record: CallRecord) -> None:
        """记录一次调用。"""
        ...

    @abstractmethod
    def report(self) -> dict[str, dict[str, int]]:
        """返回统计报告：{tool_name: {calls, success, fail}}。"""
        ...

    @abstractmethod
    def total_calls(self) -> int:
        """返回总调用次数。"""
        ...
