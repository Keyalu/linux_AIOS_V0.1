"""
mock_registry.py — Mock ToolRegistry 实现

用途：第3组 AppAgent 在第4组完成真实实现前，用此 Mock 进行独立开发。
特点：接口完全一致，返回值为固定/模拟数据，不执行真实文件操作。

来自版本1 的优点：为其他组提供完整 Mock 接口。
"""

from __future__ import annotations
from typing import Any
from .interfaces import (
    IToolRegistry, ToolFunc, ToolSchema, ToolResult, ToolParam, ToolParamType, PermissionLevel
)


class MockToolRegistry(IToolRegistry):
    """
    Mock 工具注册表
    - 接口签名与真实 ToolRegistry 完全一致
    - call() 返回模拟成功结果，不执行任何真实操作
    - 预置了一组默认工具的 schema，方便第3组测试
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolFunc] = {}
        self._schemas: dict[str, ToolSchema] = {}
        self._register_defaults()

    def register(
        self,
        name: str,
        func: ToolFunc,
        schema: ToolSchema | None = None,
    ) -> None:
        self._tools[name] = func
        if schema:
            self._schemas[name] = schema

    def unregister(self, name: str) -> bool:
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
        """Mock 调用：不执行真实操作，返回模拟成功结果。"""
        params = params or {}
        if name not in self._tools:
            return ToolResult.fail(f"工具未注册: {name}")
        return ToolResult.ok(
            result=f"[Mock] {name} 执行成功（模拟） — 参数: {params}"
        )

    def list_tools(self) -> list[ToolSchema]:
        return list(self._schemas.values())

    def has(self, name: str) -> bool:
        return name in self._tools

    def _register_defaults(self) -> None:
        """注册一组默认工具的 Mock 版本。"""

        def _noop(**kwargs: Any) -> dict:
            return {"success": True, "result": "[Mock] 模拟执行"}

        defaults = [
            ("copy_file", "复制文件", ["src", "dest"]),
            ("move_file", "移动文件", ["src", "dest"]),
            ("delete_file", "删除文件", ["path"]),
            ("create_folder", "创建文件夹", ["path"]),
            ("list_directory", "列出目录", ["path"]),
            ("run_command", "执行系统命令", ["cmd"]),
            ("read_file", "读取文件", ["path"]),
            ("write_file", "写入文件", ["path", "content"]),
        ]

        for name, desc, params in defaults:
            self.register(
                name, _noop,
                ToolSchema(
                    name=name,
                    description=f"{desc}（Mock：不执行真实操作）",
                    parameters=[
                        ToolParam(p, ToolParamType.STRING, f"参数: {p}")
                        for p in params
                    ],
                ),
            )
