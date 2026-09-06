"""
skill_library.py — OS 技能库真实实现

Skill = 多个 Tool 的编排组合，实现高级操作。
例如：organize_downloads 会调用 list_directory + create_folder + move_file。

融合了三个版本的优点：
- 版本1 的接口抽象、schema 定义、通过 Registry 调用工具
- 版本2 的 backup_directory / clean_temp_files / 查重技能
- 我搭建版本的 find_large_files / disk_usage / system_info
"""

from __future__ import annotations
import shutil
import hashlib
import time
from pathlib import Path
from typing import Any
from .interfaces import ISkillLibrary, IToolRegistry, ToolFunc, ToolSchema, ToolResult, ToolParam, ToolParamType


class SkillLibrary(ISkillLibrary):
    """
    OS Skills 库 — 真实实现。
    内部通过 IToolRegistry 调用底层工具。
    """

    # 文件分类规则（综合三个版本）
    FILE_CATEGORIES: dict[str, list[str]] = {
        "文档": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                 ".txt", ".md", ".csv", ".odt", ".rtf"],
        "图片": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico", ".tiff"],
        "视频": [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"],
        "音频": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"],
        "压缩包": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"],
        "代码": [".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".go",
                 ".rs", ".rb", ".sh", ".bash", ".json", ".yaml", ".yml",
                 ".xml", ".html", ".css", ".toml", ".ini", ".cfg"],
        "安装包": [".exe", ".msi", ".deb", ".rpm", ".dmg", ".apk", ".appimage"],
    }

    def __init__(self, registry: IToolRegistry | None = None) -> None:
        self._registry = registry
        self._skills: dict[str, ToolFunc] = {}
        self._schemas: dict[str, ToolSchema] = {}
        self._register_defaults()

    # ----------------------------------------------------------
    # 接口实现
    # ----------------------------------------------------------

    def register_skill(self, name: str, func: ToolFunc, schema: ToolSchema | None = None) -> None:
        self._skills[name] = func
        if schema:
            self._schemas[name] = schema
        else:
            self._schemas[name] = ToolSchema(name=name, description=f"Skill: {name}")

    def call_skill(self, name: str, params: dict[str, Any] | None = None) -> ToolResult:
        params = params or {}
        if name not in self._skills:
            return ToolResult.fail(f"Skill 未注册: {name}")
        if not isinstance(params, dict):
            return ToolResult.fail(
                f"参数错误: params 必须是字典，收到 {type(params).__name__}"
            )
        try:
            raw = self._skills[name](**params)
            if isinstance(raw, dict) and "success" in raw:
                return ToolResult(**raw) if raw["success"] else ToolResult.fail(raw.get("error", ""))
            return ToolResult.ok(raw)
        except Exception as e:
            return ToolResult.fail(f"Skill 执行异常: {type(e).__name__}: {e}")

    def list_skills(self) -> list[ToolSchema]:
        return list(self._schemas.values())

    # ----------------------------------------------------------
    # 预置默认 Skill
    # ----------------------------------------------------------

    def _register_defaults(self) -> None:
        """注册核心 Skill。"""
        skills = [
            ("organize_downloads", "整理目录：按文件扩展名分类到子文件夹", [
                ToolParam("path", ToolParamType.STRING, "要整理的目录路径", required=False, default="~/Downloads"),
                ToolParam("workers", ToolParamType.NUMBER, "并行搬运线程数(>1 启用)", required=False, default=1),
            ]),
            ("system_check", "检查系统状态：磁盘使用率、内存、进程数", []),
            ("find_files", "按条件搜索文件", [
                ToolParam("directory", ToolParamType.STRING, "搜索目录", required=False, default="~"),
                ToolParam("pattern", ToolParamType.STRING, "glob 匹配模式", required=False, default="*"),
            ]),
            ("cleanup_temp", "清理系统临时文件", [
                ToolParam("path", ToolParamType.STRING, "临时目录路径", required=False, default="/tmp"),
                ToolParam("max_age_days", ToolParamType.NUMBER, "最大保留天数", required=False, default=7),
            ]),
            ("backup_directory", "目录压缩备份", [
                ToolParam("src", ToolParamType.STRING, "源目录"),
                ToolParam("dest", ToolParamType.STRING, "备份目标（不含扩展名）", required=False, default=None),
            ]),
            ("find_large_files", "查找大文件", [
                ToolParam("path", ToolParamType.STRING, "搜索目录", required=False, default="~"),
                ToolParam("min_size_mb", ToolParamType.NUMBER, "最小大小(MB)", required=False, default=100),
                ToolParam("top_n", ToolParamType.NUMBER, "返回数量", required=False, default=20),
            ]),
            ("find_duplicate_files", "查找重复文件", [
                ToolParam("path", ToolParamType.STRING, "搜索目录", required=False, default="~"),
            ]),
            ("disk_usage", "查询磁盘使用情况", [
                ToolParam("path", ToolParamType.STRING, "路径", required=False, default="/"),
            ]),
        ]
        for name, desc, params in skills:
            func = getattr(self, f"_{name}")
            self.register_skill(name, func, ToolSchema(name=name, description=desc, parameters=params))

    # ==============================================================
    # Skill 实现
    # ==============================================================

    def _organize_downloads(self, path: str = "~/Downloads", workers: int = 1) -> dict:
        """整理目录：按文件扩展名分类。

        workers > 1 时启用并行搬运（高压优化）：规划阶段单线程完成
        分类与重名消解（保证目标路径唯一），执行阶段用线程池并行
        shutil.move，万级文件场景显著提速。默认 workers=1 行为不变。
        """
        base = Path(path).expanduser().resolve()
        if not base.is_dir():
            return {"success": False, "error": f"目录不存在: {base}"}

        # Week3 加固：禁止整理系统目录（防止把系统文件搬走）
        from .default_tools import _is_protected_path
        prot = _is_protected_path(str(base))
        if prot:
            return {"success": False, "error": f"拒绝整理受保护路径: {base} ({prot})"}

        # ---- 规划阶段（单线程）：分类 + 重名消解，产出 (源, 目标) 清单 ----
        plan: list[tuple[Path, Path, str]] = []   # (src, dest, category)
        skipped: list[str] = []
        reserved: set[Path] = set()               # 已占用的目标路径

        for f in base.iterdir():
            if not f.is_file():
                continue
            if f.name.startswith("."):
                skipped.append(f.name)
                continue

            category = self._classify(f.suffix.lower())
            dest_dir = base / category
            dest = dest_dir / f.name
            # Week3 加固：重名 _dup1/_dup2 递增；reserved 防止并行规划撞名
            if dest.exists() or dest in reserved:
                counter = 1
                while True:
                    dest = dest_dir / f"{f.stem}_dup{counter}{f.suffix}"
                    if not dest.exists() and dest not in reserved:
                        break
                    counter += 1
            reserved.add(dest)
            plan.append((f, dest, category))

        # ---- 执行阶段：workers>1 时线程池并行搬运 ----
        moved: dict[str, list[str]] = {}
        errors: list[str] = []

        def _do_move(item: tuple[Path, Path, str]) -> tuple[str, str, str | None]:
            src, dest, category = item
            try:
                dest.parent.mkdir(exist_ok=True)
                shutil.move(str(src), str(dest))
                return (category, src.name, None)
            except OSError as e:
                return (category, src.name, f"{src.name}: {e}")

        if workers > 1 and plan:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=workers) as pool:
                outcomes = list(pool.map(_do_move, plan))
        else:
            outcomes = [_do_move(item) for item in plan]

        for category, name, err in outcomes:
            if err is None:
                moved.setdefault(category, []).append(name)
            else:
                errors.append(err)

        total = sum(len(v) for v in moved.values())
        return {
            "success": True,
            "result": {
                "path": str(base),
                "total_moved": total,
                "categories": moved,
                "skipped": skipped,
                "workers": workers,
                **({"errors": errors[:10], "error_count": len(errors)} if errors else {}),
            },
        }

    def _system_check(self) -> dict:
        """检查系统状态：磁盘使用率、内存、进程数。"""
        import os
        import subprocess
        import platform

        usage = shutil.disk_usage("/")
        disk = {
            "total_gb": round(usage.total / (1024**3), 1),
            "used_gb": round(usage.used / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
            "percent": round(usage.used / usage.total * 100, 1),
        }

        mem = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        mem["total_mb"] = int(line.split()[1]) // 1024
                    elif line.startswith("MemAvailable"):
                        mem["available_mb"] = int(line.split()[1]) // 1024
        except Exception:
            mem = {"error": "无法读取 /proc/meminfo"}

        try:
            proc_count = len(os.listdir("/proc"))
        except Exception:
            proc_count = -1

        return {
            "success": True,
            "result": {
                "disk": disk,
                "memory": mem,
                "process_count": proc_count,
                "hostname": platform.node(),
            },
        }

    def _find_files(self, directory: str = "~", pattern: str = "*") -> dict:
        """按条件搜索文件。"""
        base = Path(directory).expanduser().resolve()
        if not base.is_dir():
            return {"success": False, "error": f"目录不存在: {base}"}

        matches = []
        try:
            for p in base.rglob(pattern):
                if p.is_file():
                    matches.append({
                        "path": str(p),
                        "name": p.name,
                        "size": p.stat().st_size,
                        "suffix": p.suffix,
                    })
                if len(matches) >= 100:
                    break
        except PermissionError:
            return {"success": False, "error": f"权限不足: {base}"}

        return {
            "success": True,
            "result": {
                "directory": str(base),
                "pattern": pattern,
                "count": len(matches),
                "files": matches[:50],
            },
        }

    def _cleanup_temp(self, path: str = "/tmp", max_age_days: int = 7) -> dict:
        """清理临时文件。"""
        base = Path(path).expanduser()
        if not base.is_dir():
            return {"success": False, "error": f"目录不存在: {base}"}

        # Week3 加固：禁止清理系统目录
        from .default_tools import _is_protected_path
        prot = _is_protected_path(str(base.resolve()))
        if prot:
            return {"success": False, "error": f"拒绝清理受保护路径: {base} ({prot})"}

        cutoff = time.time() - max_age_days * 86400
        removed = []
        errors = []

        for f in base.iterdir():
            if not f.is_file():
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed.append(f.name)
            except (PermissionError, OSError) as e:
                errors.append(f"{f.name}: {e}")

        return {
            "success": True,
            "result": {
                "path": str(base),
                "removed_count": len(removed),
                "error_count": len(errors),
                "removed_files": removed[:20],
                "errors": errors[:10],
            },
        }

    def _backup_directory(self, src: str, dest: str | None = None) -> dict:
        """目录压缩备份。"""
        src_p = Path(src).expanduser().resolve()
        if not src_p.exists():
            return {"success": False, "error": f"源路径不存在: {src_p}"}
        if dest is None:
            ts = time.strftime("%Y%m%d_%H%M%S")
            dest = f"{src_p}_{ts}_backup"
        try:
            shutil.make_archive(dest, "zip", str(src_p))
            return {"success": True, "result": f"已备份到 {dest}.zip"}
        except Exception as e:
            return {"success": False, "error": f"备份失败: {e}"}

    def _find_large_files(self, path: str = "~", min_size_mb: int = 100, top_n: int = 20) -> dict:
        """查找大文件。"""
        target = Path(path).expanduser()
        if not target.exists():
            return {"success": False, "error": f"目录不存在: {path}"}

        min_bytes = min_size_mb * 1024 * 1024
        large_files = []

        try:
            for item in target.rglob("*"):
                if item.is_file():
                    try:
                        size = item.stat().st_size
                        if size >= min_bytes:
                            large_files.append({
                                "path": str(item),
                                "size_mb": round(size / (1024 * 1024), 2),
                            })
                    except (PermissionError, OSError):
                        continue
        except PermissionError:
            return {"success": False, "error": f"无权限访问部分目录: {path}"}

        large_files.sort(key=lambda x: x["size_mb"], reverse=True)
        top = large_files[:top_n]

        return {
            "success": True,
            "result": {
                "summary": f"找到 {len(large_files)} 个大文件，显示前 {len(top)} 个",
                "files": top,
            },
        }

    def _find_duplicate_files(self, path: str = "~") -> dict:
        """查找重复文件（两级过滤：先按 size 分组，再只对嫌疑组算 MD5）。

        高压优化：内容不同的文件 size 几乎必不相同，因此第一级纯 stat
        分组即可排除绝大多数文件，只有 size 撞车的"嫌疑组"才真正读盘
        算哈希——万级文件场景下可减少 90%+ 的磁盘读取。
        """
        target = Path(path).expanduser()
        if not target.exists():
            return {"success": False, "error": f"目录不存在: {path}"}

        # ---- 第一级：按文件大小分组（纯 stat，不读文件内容）----
        by_size: dict[int, list[Path]] = {}
        scanned = 0
        try:
            for item in target.rglob("*"):
                if not item.is_file():
                    continue
                scanned += 1
                try:
                    by_size.setdefault(item.stat().st_size, []).append(item)
                except (PermissionError, OSError):
                    continue
        except PermissionError:
            return {"success": False, "error": f"无权限访问部分目录: {path}"}

        # ---- 第二级：只对 size 撞车的嫌疑文件算分块 MD5 ----
        suspects = [f for g in by_size.values() if len(g) > 1 for f in g]
        hashes: dict[str, list[str]] = {}
        for item in suspects:
            try:
                h = hashlib.md5()
                with open(item, "rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(chunk)
                hashes.setdefault(h.hexdigest(), []).append(str(item))
            except (PermissionError, OSError):
                continue

        duplicates = {h: paths for h, paths in hashes.items() if len(paths) > 1}
        return {
            "success": True,
            "result": {
                "scanned": scanned,                     # 扫描的文件总数
                "hashed": len(suspects),                # 实际算哈希的嫌疑文件数
                "duplicate_groups": len(duplicates),
                "total_duplicated_files": sum(len(v) for v in duplicates.values()),
                "groups": list(duplicates.values())[:20],
            },
        }

    def _disk_usage(self, path: str = "/") -> dict:
        """查询磁盘使用情况。"""
        try:
            usage = shutil.disk_usage(str(Path(path).expanduser()))
            return {
                "success": True,
                "result": {
                    "total_gb": round(usage.total / (1024**3), 1),
                    "used_gb": round(usage.used / (1024**3), 1),
                    "free_gb": round(usage.free / (1024**3), 1),
                    "percent": round(usage.used / usage.total * 100, 1),
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ----------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------

    def _classify(self, ext: str) -> str:
        """根据文件扩展名判断分类。"""
        for category, exts in self.FILE_CATEGORIES.items():
            if ext in exts:
                return category
        return "其他"
