"""
tool_registry.py — 工具注册表真实实现

融合了三个版本的优点：
- 版本1 的接口抽象、schema 校验、装饰器注册、统计集成
- 版本2 的 public/user/admin 权限控制
- 我搭建版本的 8 个默认工具与统一错误处理
"""

from __future__ import annotations
import time
from typing import Any, Callable
from .interfaces import (
    IToolRegistry, IToolStats, ToolFunc, ToolSchema, ToolResult,
    ToolParam, ToolParamType, PermissionLevel, CallRecord,
)
from . import default_tools


class ToolRegistry(IToolRegistry):
    """
    工具注册表 — 真实实现。
    支持 register / unregister / call / list_tools / has，
    可选集成 stats 进行调用统计，支持权限控制。
    """

    def __init__(self, stats: IToolStats | None = None) -> None:
        # 三张核心表：函数体 / schema 说明书 / 统计器（stats 可选注入）
        self._tools: dict[str, ToolFunc] = {}
        self._schemas: dict[str, ToolSchema] = {}
        self._stats = stats
        self._register_defaults()

    # ----------------------------------------------------------
    # 核心接口
    # ----------------------------------------------------------

    def register(
        self,
        name: str,
        func: ToolFunc,
        schema: ToolSchema | None = None,
    ) -> None:
        """注册一个工具。"""
        # 注册护栏：函数必须可调用；重名直接拒绝（防止静默覆盖已有工具）
        if not callable(func):
            raise TypeError(f"工具函数必须是 callable，收到: {type(func)}")
        if name in self._tools:
            raise ValueError(f"工具 '{name}' 已注册")
        self._tools[name] = func
        # schema 缺省时补一份最小说明书（GUI 表单与组2 规划依赖它）
        if schema:
            self._schemas[name] = schema
        else:
            self._schemas[name] = ToolSchema(name=name, description=f"工具: {name}")

    def unregister(self, name: str) -> bool:
        """注销一个工具。"""
        if name in self._tools:
            del self._tools[name]
            self._schemas.pop(name, None)
            return True
        return False

    def call(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        user_level: PermissionLevel = PermissionLevel.USER,
    ) -> ToolResult:
        """
        调用一个工具。
        1. 检查工具是否存在
        2. 检查权限是否足够
        3. 执行工具函数
        4. 记录调用统计（如果启用了 stats）
        5. 返回统一格式的 ToolResult
        """
        params = params or {}

        # 1) 存在性：统一失败形态返回（组3 自愈流依赖这个错误前缀）
        if name not in self._tools:
            return ToolResult.fail(f"工具未注册: {name}")

        # 2) 权限闸门：调用者级别不足即拒绝（提权流由此触发）
        schema = self._schemas.get(name)
        required_perm = schema.permission if schema else PermissionLevel.USER
        if user_level.rank() < required_perm.rank():
            return ToolResult.fail(
                f"权限不足: '{name}' 需要 {required_perm.value}, "
                f"当前为 {user_level.value}"
            )

        # ---- 参数合法性预检查（Week3 加固）----
        if not isinstance(params, dict):
            err = f"参数错误: params 必须是字典，收到 {type(params).__name__}"
            self._record_stats(name, {}, None, 0.0, error=err)
            return ToolResult.fail(err)

        if schema:
            missing = [
                p.name for p in schema.parameters
                if p.required and p.name not in params
            ]
            if missing:
                err = f"参数错误: 缺少必需参数: {', '.join(missing)}"
                self._record_stats(name, params, None, 0.0, error=err)
                return ToolResult.fail(err)

        # 3) 执行：统一计时；返回值规整为 ToolResult（裸 dict 也兼容）
        start = time.monotonic()
        try:
            raw = self._tools[name](**params)
            duration = (time.monotonic() - start) * 1000

            if isinstance(raw, dict) and "success" in raw:
                # 统一契约键 success/result/error 之外的键（如 simulated）
                # 收进 extra，避免 **raw 解包时 TypeError
                result = ToolResult(
                    success=bool(raw["success"]),
                    result=raw.get("result"),
                    error=raw.get("error"),
                    extra={k: v for k, v in raw.items()
                           if k not in ("success", "result", "error")},
                ) if raw["success"] else ToolResult.fail(raw.get("error", "未知错误"))
            else:
                result = ToolResult.ok(raw)

            self._record_stats(name, params, result, duration)
            return result

        # TypeError 单列：多为参数个数/名称不匹配 → 归类"参数错误"便于自愈流
        except TypeError as e:
            duration = (time.monotonic() - start) * 1000
            err = f"参数错误: {e}"
            self._record_stats(name, params, None, duration, error=err)
            return ToolResult.fail(err)

        # 其余异常统一兜底：任何工具崩溃都不允许炸掉编排主流程
        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            err = f"执行异常: {type(e).__name__}: {e}"
            self._record_stats(name, params, None, duration, error=err)
            return ToolResult.fail(err)

    def _record_stats(
        self,
        name: str,
        params: dict,
        result: ToolResult | None,
        duration_ms: float,
        error: str | None = None,
    ) -> None:
        """安全地记录统计——统计模块出错不能影响工具调用本身。"""
        if not self._stats:
            return
        try:
            self._stats.record(CallRecord(
                tool_name=name, params=params,
                success=result.success if result else False,
                duration_ms=duration_ms,
                error=error or (result.error if result else None),
            ))
        except Exception:
            pass  # 统计失败静默忽略，保证主流程不受影响

    # 能力清单：GUI 工具台与组2 规划的数据源
    def list_tools(self) -> list[ToolSchema]:
        return list(self._schemas.values())

    # 存在性快查（MCP 注册幂等检查、组3 自愈流用）
    def has(self, name: str) -> bool:
        return name in self._tools

    # 取单个工具的说明书
    def get_schema(self, name: str) -> ToolSchema | None:
        return self._schemas.get(name)

    # 已注册工具数（开机自检用）
    def count(self) -> int:
        return len(self._tools)

    # ----------------------------------------------------------
    # 装饰器注册
    # ----------------------------------------------------------

    def tool(self, name: str, description: str = "", parameters: list[ToolParam] | None = None):
        """装饰器注册工具。"""
        # 闭包捕获注册参数：被装饰函数定义时即完成注册
        def decorator(func: ToolFunc) -> ToolFunc:
            schema = ToolSchema(
                name=name,
                description=description or f"工具: {name}",
                parameters=parameters or [],
            )
            self.register(name, func, schema)
            return func
        return decorator

    # ----------------------------------------------------------
    # 默认工具
    # ----------------------------------------------------------

    def _register_defaults(self) -> None:
        """注册内置默认 OS 工具。"""
        default_tools.register_default_tools(self)
