"""
default_tools.py — 默认工具集

封装文件操作和系统命令工具，注册到 ToolRegistry。
AppAgent 通过 registry.call("copy_file", {...}) 调用。

来自版本1 的优点：安全机制（危险命令黑名单、受保护路径检查）。
"""

from __future__ import annotations
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from .interfaces import ToolResult, ToolSchema, ToolParam, ToolParamType, PermissionLevel


# ============================================================
# 安全常量
# ============================================================

DANGEROUS_KEYWORDS = [
    "rm -rf", "rm -r", "rm -fr", "rmdir", "mkfs", "dd ", "wipefs",
    "shred", "format", "shutdown", "reboot", "halt", "poweroff",
    "> /dev/", "chmod 777", "chmod -r 777", "useradd", "userdel",
    ":(){",            # fork 炸弹
    "mv /*",           # 全盘移动
    "find / -delete",  # find 删除变体
]

PROTECTED_PATHS = [
    # Linux 系统目录
    "/etc", "/usr", "/root", "/boot", "/bin", "/sbin", "/lib",
    "/proc", "/sys", "/dev",
    # Windows 系统目录（大小写不敏感比较）
    "c:\\windows", "c:\\program files", "c:\\program files (x86)",
]

# 供 AppAgent 参考的"已知安全"命令清单（informational，不强制白名单）
SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "find", "grep",
    "df", "du", "free", "uname", "whoami", "pwd", "date",
    "echo", "which", "file", "stat", "tree", "nproc", "ps",
}

MAX_READ_SIZE = 10 * 1024 * 1024  # read_file 最大读取 10MB，防止大文件撑爆内存


# ============================================================
# 安全检查函数
# ============================================================

def _is_dangerous_command(cmd: str) -> str | None:
    """危险命令检查。先做空白归一化，防止用多余空格/制表符绕过黑名单。"""
    cmd_lower = " ".join(cmd.lower().split())
    for kw in DANGEROUS_KEYWORDS:
        if kw in cmd_lower:
            return f"包含危险关键词: {kw}"
    return None


def _is_protected_path(path: str) -> str | None:
    """受保护路径检查。同时匹配原始路径与解析后路径，大小写不敏感。"""
    candidates = {str(Path(path)).replace("\\", "/").lower()}
    try:
        candidates.add(str(Path(path).resolve()).replace("\\", "/").lower())
    except OSError:
        pass
    for pp in PROTECTED_PATHS:
        pp_norm = pp.replace("\\", "/").lower().rstrip("/")
        for cand in candidates:
            if cand == pp_norm or cand.startswith(pp_norm + "/"):
                return f"受保护路径: {pp}"
    return None


# ============================================================
# 文件操作工具
# ============================================================

def copy_file(src: str, dest: str) -> dict:
    """复制文件。"""
    src_path = Path(src).expanduser().resolve()
    dest_path = Path(dest).expanduser()

    if not src_path.exists():
        return ToolResult.fail(f"源文件不存在: {src_path}").to_dict()

    if dest_path.is_dir():
        dest_path = dest_path / src_path.name

    # 防止向系统目录写入（覆盖 /etc/passwd 等系统文件）
    prot = _is_protected_path(str(dest_path))
    if prot:
        return ToolResult.fail(f"拒绝复制到受保护路径: {dest_path} ({prot})").to_dict()

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if src_path.is_dir():
        shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
    else:
        shutil.copy2(str(src_path), str(dest_path))
    return ToolResult.ok(f"已复制: {src_path} → {dest_path}").to_dict()


def move_file(src: str, dest: str) -> dict:
    """移动文件。"""
    src_path = Path(src).expanduser().resolve()
    dest_path = Path(dest).expanduser()

    if not src_path.exists():
        return ToolResult.fail(f"源文件不存在: {src_path}").to_dict()

    if dest_path.is_dir():
        dest_path = dest_path / src_path.name

    # 防止向系统目录写入
    prot = _is_protected_path(str(dest_path))
    if prot:
        return ToolResult.fail(f"拒绝移动到受保护路径: {dest_path} ({prot})").to_dict()

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_path), str(dest_path))
    return ToolResult.ok(f"已移动: {src_path} → {dest_path}").to_dict()


def delete_file(path: str) -> dict:
    """删除文件或目录。"""
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return ToolResult.fail(f"文件不存在: {target}").to_dict()

    if _is_protected_path(str(target)):
        return ToolResult.fail(f"拒绝删除受保护路径: {target}").to_dict()

    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return ToolResult.ok(f"已删除: {target}").to_dict()


def create_folder(path: str) -> dict:
    """创建文件夹（递归创建）。"""
    target = Path(path).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    return ToolResult.ok(f"已创建目录: {target.resolve()}").to_dict()


def list_directory(path: str = "~", offset: int = 0, limit: int = 100) -> dict:
    """列出目录内容（高压优化版）。

    - os.scandir：文件类型/大小随目录项免费返回，免去每文件 2~3 次 stat 系统调用
    - 分页游标：offset/limit 可选参数，默认行为与旧版一致（前 100 条）
    - 只对当前页的条目取 size，扫描成本从 O(全目录) 降为 O(页大小)
    """
    base = Path(path).expanduser().resolve()
    if not base.is_dir():
        return ToolResult.fail(f"目录不存在: {base}").to_dict()

    try:
        offset = max(int(offset), 0)
        limit = min(max(int(limit), 1), 1000)  # 单页上限 1000，防止一次吞下万级结果
    except (TypeError, ValueError):
        return ToolResult.fail("参数错误: offset/limit 必须是数字").to_dict()

    entries = []
    scanned = 0
    with os.scandir(base) as it:
        for e in sorted(it, key=lambda x: x.name):
            scanned += 1
            # 页外的条目只计数、不做 stat（保持 count 语义，同时把
            # 系统调用限制在当前页内——遍历本身是廉价操作）
            if scanned <= offset or len(entries) >= limit:
                continue
            try:
                is_file = e.is_file()
                size = e.stat().st_size if is_file else None
            except OSError:
                # 符号链接损坏 / 无权限读取元数据 → 跳过大小信息
                is_file = not e.is_dir()
                size = None
            entries.append({
                "name": e.name,
                "type": "file" if is_file else "dir",
                "size": size,
                "suffix": Path(e.name).suffix if is_file else "",
            })

    has_more = (offset + len(entries)) < scanned
    return ToolResult.ok({
        "path": str(base),
        "count": scanned,                       # 目录总条目数（含未返回的）
        "returned": len(entries),               # 本页条目数
        "entries": entries,
        "has_more": has_more,
        "next_offset": offset + len(entries) if has_more else None,
    }).to_dict()


def read_file(path: str) -> dict:
    """读取文件内容（限制最大 10MB，拒绝受保护路径）。"""
    base = Path(path).expanduser().resolve()
    if not base.is_file():
        return ToolResult.fail(f"文件不存在: {base}").to_dict()

    # 防止读取 /etc/shadow 等敏感系统文件
    prot = _is_protected_path(str(base))
    if prot:
        return ToolResult.fail(f"拒绝读取受保护路径: {base} ({prot})").to_dict()

    # 防止大文件撑爆内存
    if base.stat().st_size > MAX_READ_SIZE:
        return ToolResult.fail(
            f"文件过大 ({base.stat().st_size // (1024*1024)}MB)，超过读取上限 "
            f"{MAX_READ_SIZE // (1024*1024)}MB"
        ).to_dict()

    try:
        with open(base, "r", encoding="utf-8") as f:
            return ToolResult.ok(f.read()).to_dict()
    except UnicodeDecodeError:
        return ToolResult.fail("无法以文本方式读取文件（可能是二进制）").to_dict()


def write_file(path: str, content: str) -> dict:
    """写入文件内容（拒绝向受保护路径写入）。"""
    file_path = Path(path).expanduser()

    prot = _is_protected_path(str(file_path))
    if prot:
        return ToolResult.fail(f"拒绝写入受保护路径: {file_path} ({prot})").to_dict()

    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return ToolResult.ok(f"已写入: {path} ({len(content)} 字符)").to_dict()


# ============================================================
# 系统命令工具
# ============================================================

def run_command(cmd: str, timeout: int = 30, sandbox: bool = False) -> dict:
    """执行系统命令（带安全过滤）。

    sandbox=True 时经 bubblewrap 真沙箱执行：/etc /usr /var /root 等系统
    目录映射为 tmpfs（写入即丢弃、不落真实磁盘），命令结束即销毁。
    bwrap 不可用时降级为直接执行并在结果中注明。
    """
    danger = _is_dangerous_command(cmd)
    if danger:
        return ToolResult.fail(f"拒绝执行: {danger}").to_dict()

    wrapped = cmd
    note = ""
    if sandbox:
        if shutil.which("bwrap"):
            import shlex
            # 根文件系统只读 + 系统目录用空 tmpfs 覆盖：写入进入内存、命令结束即销毁
            wrapped = ("bwrap --ro-bind / / --bind /tmp /tmp --tmpfs /etc "
                       "--tmpfs /var --tmpfs /root --tmpfs /boot "
                       "--proc /proc --dev /dev bash -c " + shlex.quote(cmd))
            note = "[bwrap 沙箱] 系统目录只读+写入隔离 "
        else:
            note = "[沙箱不可用,直接执行] "

    try:
        result = subprocess.run(
            wrapped, shell=True,
            capture_output=True, text=True,
            timeout=timeout,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            error = result.stderr.strip()
            return ToolResult.fail(f"命令失败 (code={result.returncode}): {error}").to_dict()
        return ToolResult.ok(note + (output if output else "(无输出)")).to_dict()

    except subprocess.TimeoutExpired:
        return ToolResult.fail("命令执行超时").to_dict()
    except Exception as e:
        return ToolResult.fail(f"执行异常: {type(e).__name__}: {e}").to_dict()


# ============================================================
# 工具 Schema 定义
# ============================================================

TOOL_SCHEMAS: dict[str, ToolSchema] = {
    "copy_file": ToolSchema(
        name="copy_file",
        description="复制文件到指定路径",
        parameters=[
            ToolParam("src", ToolParamType.STRING, "源文件路径"),
            ToolParam("dest", ToolParamType.STRING, "目标路径（可以是目录）"),
        ],
    ),
    "move_file": ToolSchema(
        name="move_file",
        description="移动文件到指定路径",
        parameters=[
            ToolParam("src", ToolParamType.STRING, "源文件路径"),
            ToolParam("dest", ToolParamType.STRING, "目标路径（可以是目录）"),
        ],
    ),
    "delete_file": ToolSchema(
        name="delete_file",
        description="删除文件或目录",
        parameters=[ToolParam("path", ToolParamType.STRING, "要删除的路径")],
    ),
    "create_folder": ToolSchema(
        name="create_folder",
        description="创建文件夹（递归创建）",
        parameters=[ToolParam("path", ToolParamType.STRING, "文件夹路径")],
    ),
    "list_directory": ToolSchema(
        name="list_directory",
        description="列出目录内容（支持分页：offset/limit 可选）",
        parameters=[
            ToolParam("path", ToolParamType.STRING, "目录路径", required=False, default="~"),
            ToolParam("offset", ToolParamType.NUMBER, "起始位置(分页游标)", required=False, default=0),
            ToolParam("limit", ToolParamType.NUMBER, "每页条数(1-1000)", required=False, default=100),
        ],
        permission=PermissionLevel.PUBLIC,
    ),
    "read_file": ToolSchema(
        name="read_file",
        description="读取文本文件内容",
        parameters=[ToolParam("path", ToolParamType.STRING, "文件路径")],
        permission=PermissionLevel.PUBLIC,
    ),
    "write_file": ToolSchema(
        name="write_file",
        description="写入文本文件",
        parameters=[
            ToolParam("path", ToolParamType.STRING, "文件路径"),
            ToolParam("content", ToolParamType.STRING, "文件内容"),
        ],
    ),
    "run_command": ToolSchema(
        name="run_command",
        description="执行系统命令（带安全过滤）",
        parameters=[
            ToolParam("cmd", ToolParamType.STRING, "要执行的命令"),
            ToolParam("timeout", ToolParamType.NUMBER, "超时秒数", required=False, default=30),
            ToolParam("sandbox", ToolParamType.BOOLEAN, "经 bubblewrap 沙箱执行（系统目录写入隔离）",
                      required=False, default=False),
        ],
        permission=PermissionLevel.ADMIN,
    ),
}


# ============================================================
# 注册函数
# ============================================================

def register_default_tools(registry) -> None:
    """将所有默认工具注册到 ToolRegistry。"""
    tools = {
        "copy_file": copy_file,
        "move_file": move_file,
        "delete_file": delete_file,
        "create_folder": create_folder,
        "list_directory": list_directory,
        "read_file": read_file,
        "write_file": write_file,
        "run_command": run_command,
    }
    for name, func in tools.items():
        schema = TOOL_SCHEMAS.get(name)
        registry.register(name, func, schema)
