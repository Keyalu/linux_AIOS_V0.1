"""
mock_skills.py — Mock SkillLibrary 实现

用途：第3组在第4组完成真实 Skill 实现前，用此 Mock 进行独立开发。
特点：接口完全一致，返回值为模拟数据，不执行真实文件整理操作。

来自版本1 的优点：为其他组提供完整 Mock 接口。
"""

from __future__ import annotations
from typing import Any
from .interfaces import (
    ISkillLibrary, IToolRegistry, ToolFunc, ToolSchema, ToolResult, ToolParam, ToolParamType
)


class MockSkillLibrary(ISkillLibrary):
    """
    Mock OS Skills 库
    - 接口签名与真实 SkillLibrary 完全一致
    - call_skill() 返回模拟成功结果
    - 预置了核心 Skill 的 schema
    """

    def __init__(self, registry: IToolRegistry | None = None) -> None:
        self._registry = registry
        self._skills: dict[str, ToolFunc] = {}
        self._schemas: dict[str, ToolSchema] = {}
        self._register_defaults()

    def register_skill(self, name: str, func: ToolFunc, schema: ToolSchema | None = None) -> None:
        self._skills[name] = func
        if schema:
            self._schemas[name] = schema

    def call_skill(self, name: str, params: dict[str, Any] | None = None) -> ToolResult:
        """Mock 调用：返回模拟成功结果。"""
        params = params or {}
        if name not in self._skills:
            return ToolResult.fail(f"Skill 未注册: {name}")
        return ToolResult.ok(
            result={
                "skill": name,
                "status": "[Mock] 模拟执行成功",
                "params": params,
                "summary": self._mock_summary(name, params),
            }
        )

    def list_skills(self) -> list[ToolSchema]:
        return list(self._schemas.values())

    def _register_defaults(self) -> None:
        """注册核心 Skill 的 Mock 版本。"""

        def _noop(**kwargs: Any) -> dict:
            return {"success": True, "result": "[Mock] 模拟执行"}

        skills = [
            ("organize_downloads", "整理下载目录", ["path"]),
            ("system_check", "检查系统状态", []),
            ("find_files", "搜索文件", ["directory", "pattern"]),
            ("cleanup_temp", "清理临时文件", ["path"]),
            ("backup_directory", "目录备份", ["src", "dest"]),
            ("find_large_files", "查找大文件", ["path"]),
            ("find_duplicate_files", "查找重复文件", ["path"]),
            ("disk_usage", "磁盘使用", ["path"]),
        ]

        for name, desc, params in skills:
            self.register_skill(
                name, _noop,
                ToolSchema(
                    name=name,
                    description=f"{desc}（Mock：不执行真实操作）",
                    parameters=[
                        ToolParam(p, ToolParamType.STRING, f"参数: {p}", required=False)
                        for p in params
                    ],
                ),
            )

    def _mock_summary(self, name: str, params: dict) -> str:
        """生成模拟的执行摘要。"""
        summaries = {
            "organize_downloads": "整理完成：文档 5 个，图片 12 个，视频 3 个，其他 2 个",
            "system_check": "磁盘使用率 65%，内存使用 4.2/8.0 GB，运行进程 128 个",
            "find_files": "找到 8 个匹配文件",
            "cleanup_temp": "清理完成：删除临时文件 23 个，释放空间 156 MB",
            "backup_directory": "备份完成：已压缩为 zip 文件",
            "find_large_files": "找到 5 个大于 100MB 的文件",
            "find_duplicate_files": "找到 2 组重复文件，共 6 个文件",
            "disk_usage": "磁盘使用率 65%，剩余 120 GB",
        }
        return summaries.get(name, f"[Mock] {name} 执行完成")
