"""
default_tools.py — 默认工具集

封装文件操作和系统命令工具，注册到 ToolRegistry。
AppAgent 通过 registry.call("copy_file", {...}) 调用。

来自版本1 的优点：安全机制（危险命令黑名单、受保护路径检查）。
"""

from __future__ import annotations
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from .interfaces import ToolResult, ToolSchema, ToolParam, ToolParamType, PermissionLevel


# ============================================================
# 安全常量
# ============================================================

DANGEROUS_KEYWORDS = [
    "rm -rf", "rm -r", "rm -fr",
    "> /dev/", "chmod 777", "chmod -r 777",
    ":(){",            # fork 炸弹
    "mv /*",           # 全盘移动
    "find / -delete",  # find 删除变体
    # 修复逻辑审计 #26：单词命令（dd/rmdir/mkfs/format/halt…）退出子串口径 ——
    # 整词匹配移入 DANGEROUS_PATTERNS，误伤（"add file" 撞 "dd "）与
    # 漏拦（rm --recursive --force）一并治理。与 group_5/security.py 同口径。
]

# 正则形态的"下载即执行 / 覆盖裸设备"变体（纯关键词表拦不住的部分）。
# 与 group_5/security.py 的 DANGEROUS_PATTERNS 必须保持一致
# （tests/test_regression_audit.py 交叉断言两份口径）。
DANGEROUS_PATTERNS = [
    r"(curl|wget|fetch)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|k)?sh\b",
    r"\bbase64\b[^|;&]*\|\s*(sudo\s+)?(ba|z|k)?sh\b",
    r"\btruncate\b[^|;&]*\s-\w*s\w*\s*\+?-?0\b",
    r">\s*/dev/(sd|nvme|disk|mmcblk)",
    r"\b(pkexec|sudo)\s+(ba|z|k)?sh\b",
    r"\bfind\b[^|;&]*\s-delete\b",
    r">\s*(~|\$HOME|/home/[^/\s|;&]+|/root)/[^\s|;&]*"
    r"\.(bashrc|bash_profile|profile|zshrc|bash_history)\b",
    # 审计 #26 ①：单词命令词界口径（替代旧的子串关键词，误伤/漏拦一并治理）
    r"(?<![\w./-])(rmdir|mkfs|wipefs|shred|useradd|userdel|poweroff|"
    r"shutdown|reboot|halt|init)(?![\w-])",
    r"(?<![\w./-])dd\s+[^\s|;&]+=",           # dd if=… / dd of=…
    r"(?<![\w./-])format\s+[a-z]:",          # Windows format 盘符
    # 审计 #26 ②：漏拦变体（长选项 rm、find -exec 删、解释器级递归删除）
    r"\brm\b[^|;&]*--recursive[^|;&]*--force|\brm\b[^|;&]*--force[^|;&]*--recursive",
    r"\brm\b[^|;&]*--no-preserve-root",
    r"\bfind\b[^|;&]*-exec\s+(rm|unlink|shred)\b",
    r"\bpython[23]?\b[^|;&]*\brmtree\b",
]
_DANGEROUS_RES = [re.compile(p, re.I) for p in DANGEROUS_PATTERNS]

PROTECTED_PATHS = [
    # Linux 系统目录
    "/etc", "/usr", "/root", "/boot", "/bin", "/sbin", "/lib",
    "/proc", "/sys", "/dev", "/var", "/opt", "/srv",
    # Windows 系统目录（大小写不敏感比较）
    "c:/windows", "c:/program files", "c:/program files (x86)", "c:/programdata",
]

# 主目录下必须保护的凭据/配置区（相对主目录，前缀匹配）
SENSITIVE_SUBPATHS = [
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker",
    ".config", ".local/share/keyrings", ".password-store",
    ".workbuddy", ".netrc", ".git-credentials",
    ".bashrc", ".bash_history", ".profile", ".zshrc",
    "appdata",
]

# 主目录根本身：**仅精确匹配**（允许其子目录作为工作区，禁止对整目录动手）
_HOME = os.path.expanduser("~")

MAX_READ_SIZE = 10 * 1024 * 1024  # read_file 最大读取 10MB，防止大文件撑爆内存
MAX_WRITE_SIZE = 5 * 1024 * 1024   # write_file 单次写入上限 5MB
MAX_ATTACH_TOTAL = 25 * 1024 * 1024  # send_email 附件总量上限 25MB
# 软删除（回收站）目录：项目根 .aios_trash/，时间戳+随机后缀，可人工找回
TRASH_DIR = Path(__file__).resolve().parent.parent / ".aios_trash"


# ============================================================
# 安全检查函数
# ============================================================

def _norm_path(p) -> str:
    """统一路径形态：反斜杠→正斜杠、去尾斜杠、小写（跨平台比较用）。"""
    return str(p).replace(chr(92), "/").rstrip("/").lower()


def _trash_dest(target: Path) -> Path:
    """回收站落点：<项目根>/.aios_trash/<时间戳>_<随机>_<原名>。"""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return TRASH_DIR / f"{stamp}_{uuid.uuid4().hex[:6]}_{target.name}"


def _move_to_trash(target: Path) -> tuple[bool, str]:
    """把目标移入回收站。返回 (是否成功, 落点或错误信息)。"""
    dest = _trash_dest(target)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(dest))
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    return True, str(dest)


def _is_dangerous_command(cmd: str) -> str | None:
    """危险命令检查。先做空白归一化，防止用多余空格/制表符绕过黑名单；
    再匹配"下载即执行/覆盖裸设备"类正则形态。"""
    cmd_lower = " ".join(str(cmd).lower().split())
    for kw in DANGEROUS_KEYWORDS:
        if kw in cmd_lower:
            return f"包含危险关键词: {kw}"
    for rx in _DANGEROUS_RES:
        if rx.search(cmd_lower):
            return f"命中危险模式: {rx.pattern}"
    return None


def _is_protected_path(path: str) -> str | None:
    """受保护路径检查。同时匹配原始路径与解析后路径，大小写不敏感。

    三层判定：
      1) 用户主目录根 —— **仅精确匹配**（允许其子目录作为工作区）；
      2) 主目录下凭据敏感区（.ssh/.gnupg/.aws/.config/AppData…）—— 前缀匹配；
      3) 系统目录（/etc /usr /var /opt… 与 Windows 系统目录）—— 前缀匹配。
    """
    candidates = {str(Path(path)).replace("\\", "/").lower()}
    try:
        candidates.add(str(Path(path).resolve()).replace("\\", "/").lower())
    except OSError:
        pass
    # 修复逻辑审计 #3（第二道防线）：'/' 与盘符根归一后是空串/裸盘符，
    # 前缀匹配对所有保护目录都不命中 —— 目录级批量动作（organize/cleanup/
    # delete）拿到根目录等于对整盘动手，必须显式拦下。
    for cand in candidates:
        c = cand.rstrip("/")
        if c in ("", "/") or re.fullmatch(r"[a-zA-Z]:", c or ""):
            return "文件系统根目录"
    home_norm = _norm_path(_HOME)
    for sp in SENSITIVE_SUBPATHS:
        sp_norm = _norm_path(os.path.join(_HOME, sp))
        for cand in candidates:
            if cand == sp_norm or cand.startswith(sp_norm + "/"):
                return f"敏感路径: {sp}"
    for cand in candidates:
        if cand == home_norm:
            return f"用户主目录: {_HOME}"
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


def delete_file(path: str, to_trash: bool = True) -> dict:
    """删除文件或目录。

    to_trash=True（默认，软删除）：先移入项目根 `.aios_trash/`（时间戳+随机后缀
    命名）可人工找回；移动失败**不**降级为永久删除，如实报错。
    to_trash=False：永久删除（目录递归删除）。
    两条路径都拒绝受保护/敏感路径，且目标必须存在。
    """
    if not path or not str(path).strip():
        return ToolResult.fail("参数错误: 删除目标为空").to_dict()
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as e:
        return ToolResult.fail(f"参数错误: 无法解析路径 {path!r}: {e}").to_dict()

    if not target.exists():
        return ToolResult.fail(f"文件不存在: {target}").to_dict()
    if target.parent == target:                     # 文件系统根
        return ToolResult.fail(f"拒绝删除文件系统根目录: {target}").to_dict()

    prot = _is_protected_path(str(target))
    if prot:
        return ToolResult.fail(f"拒绝删除受保护路径: {target} ({prot})").to_dict()

    if to_trash:
        ok, info = _move_to_trash(target)
        if ok:
            return ToolResult.ok(
                f"已移入回收站: {target} → {info}（如需永久删除传 to_trash=False）"
            ).to_dict()
        return ToolResult.fail(f"移入回收站失败，未执行删除: {info}").to_dict()

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as e:
        return ToolResult.fail(f"删除失败: {type(e).__name__}: {e}").to_dict()
    return ToolResult.ok(f"已永久删除: {target}").to_dict()


def create_folder(path: str) -> dict:
    """创建文件夹（递归创建）。拒绝在受保护/敏感路径下创建。"""
    if not path or not str(path).strip():
        return ToolResult.fail("参数错误: 目录路径为空").to_dict()
    target = Path(path).expanduser()

    prot = _is_protected_path(str(target))
    if prot:
        return ToolResult.fail(f"拒绝在受保护路径下创建目录: {target} ({prot})").to_dict()

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return ToolResult.fail(f"创建目录失败: {type(e).__name__}: {e}").to_dict()
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
    """写入文件内容（原子写：临时文件 + os.replace）。

    拒绝写入受保护/敏感路径；拒绝超长内容；拒绝非字符串 content。
    原子写保证"写一半崩溃不留半截文件"。
    """
    if not path or not str(path).strip():
        return ToolResult.fail("参数错误: 文件路径为空").to_dict()
    if not isinstance(content, str):
        return ToolResult.fail(
            f"参数错误: content 必须是字符串，收到 {type(content).__name__}").to_dict()
    if len(content.encode("utf-8")) > MAX_WRITE_SIZE:
        return ToolResult.fail(
            f"内容过大（>{MAX_WRITE_SIZE // (1024 * 1024)}MB），拒绝写入").to_dict()

    file_path = Path(path).expanduser()
    prot = _is_protected_path(str(file_path))
    if prot:
        return ToolResult.fail(f"拒绝写入受保护路径: {file_path} ({prot})").to_dict()

    tmp_path = None
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(file_path.parent),
                                        prefix=".aios_w_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, file_path)
    except OSError as e:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return ToolResult.fail(f"写入失败: {type(e).__name__}: {e}").to_dict()
    return ToolResult.ok(f"已写入: {file_path} ({len(content)} 字符)").to_dict()


# ============================================================
# 系统命令工具
# ============================================================

# ---------------------------------------------------------------- bwrap 状态
_BWRAP_STATE: dict = {"checked": False, "ok": False, "why": ""}

# bwrap **自身**失败（而不是它执行的命令失败）的特征串
_BWRAP_ERR_RE = re.compile(
    r"(^|\n)\s*bwrap[:\s]|setting up uid map|setting up gid map|"
    r"Failed to (?:setup|create)[^\n]*namespace|"
    r"No permissions to create new namespace|Creating new namespace failed",
    re.I)


def _bwrap_usable(timeout: int = 5) -> tuple[bool, str]:
    """bwrap 是否**真的能用**（不只是"装了"）；结果按进程缓存。

    只看 `shutil.which("bwrap")` 会漏掉最常见的一种情况：bwrap 在，但**建不了
    用户命名空间** —— Ubuntu 24.04 默认 `kernel.apparmor_restrict_unprivileged_userns=1`，
    非特权进程建 userns 被 AppArmor 拒，bwrap 直接报
    `bwrap: setting up uid map: Permission denied`。这时仍按"有沙箱"去执行，
    命令会在沙箱层就死掉，用户只看到"命令失败"却不知道是沙箱环境问题。
    """
    if _BWRAP_STATE["checked"]:
        return _BWRAP_STATE["ok"], _BWRAP_STATE["why"]
    _BWRAP_STATE["checked"] = True
    exe = shutil.which("bwrap")
    if not exe:
        _BWRAP_STATE["why"] = "未安装 bwrap"
        return False, _BWRAP_STATE["why"]
    try:
        probe = subprocess.run(
            [exe, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"],
            capture_output=True, text=True, timeout=timeout)
        if probe.returncode == 0:
            _BWRAP_STATE["ok"] = True
        else:
            first = (probe.stderr.strip().splitlines() or ["未知错误"])[0]
            _BWRAP_STATE["why"] = f"bwrap 无法启动：{first[:120]}"
    except Exception as e:
        _BWRAP_STATE["why"] = f"bwrap 探测异常: {type(e).__name__}: {e}"
    return _BWRAP_STATE["ok"], _BWRAP_STATE["why"]


def reset_bwrap_cache() -> None:
    """清掉 bwrap 可用性缓存（测试用；环境变化后也可手工调用）。"""
    _BWRAP_STATE.update({"checked": False, "ok": False, "why": ""})


def run_command(cmd: str, timeout: int = 30, sandbox: bool = False) -> dict:
    """执行系统命令（带安全过滤）。

    sandbox=True 时优先用 bubblewrap 真沙箱（/etc /var /root 等映射为 tmpfs，
    写入即丢弃）。**沙箱不可用会"明说 + 降级"**，区分两种成因：
      · 没装 bwrap
      · 装了但启不动（Ubuntu 24.04 AppArmor 限制 userns，报 setting up uid map）
    两者都写进结果前缀；另外若**沙箱层**在运行时报错，会自动直接重试一次并把
    沙箱错误一并写进结果 —— 既不静默降级，也不让整条计划因为沙箱环境问题被判 FAIL。
    """
    danger = _is_dangerous_command(cmd)
    if danger:
        return ToolResult.fail(f"拒绝执行: {danger}").to_dict()

    wrapped, note = cmd, ""
    if sandbox:
        ok, why = _bwrap_usable()
        if ok:
            import shlex
            # 根文件系统只读 + 系统目录用空 tmpfs 覆盖：写入进入内存、命令结束即销毁
            wrapped = ("bwrap --ro-bind / / --bind /tmp /tmp --tmpfs /etc "
                       "--tmpfs /var --tmpfs /root --tmpfs /boot "
                       "--proc /proc --dev /dev bash -c " + shlex.quote(cmd))
            note = "[bwrap 沙箱] 系统目录只读+写入隔离 "
        else:
            note = f"[沙箱不可用:{why} → 直接执行] "

    try:
        result = subprocess.run(
            wrapped, shell=True,
            capture_output=True, text=True, timeout=timeout,
        )
        # 沙箱层失败（不是被执行的命令失败）→ 直接重试一次，并如实写明原因
        if (result.returncode != 0 and sandbox
                and _BWRAP_ERR_RE.search(result.stderr or "")):
            why = (result.stderr.strip().splitlines() or ["未知错误"])[0][:120]
            _BWRAP_STATE.update({"checked": True, "ok": False,
                                 "why": f"bwrap 无法启动：{why}"})
            retry = subprocess.run(cmd, shell=True, capture_output=True,
                                   text=True, timeout=timeout)
            out = retry.stdout.strip()
            if retry.returncode != 0:
                return ToolResult.fail(
                    f"命令失败 (code={retry.returncode}): {retry.stderr.strip()}"
                    f"（沙箱层先失败: {why}）").to_dict()
            return ToolResult.ok(
                f"[沙箱层失败 → 已直接执行] 沙箱错误: {why}；"
                + (out if out else "(无输出)")).to_dict()

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
        description="删除文件或目录（默认软删除：移入项目根 .aios_trash/ 可找回）",
        parameters=[
            ToolParam("path", ToolParamType.STRING, "要删除的路径"),
            ToolParam("to_trash", ToolParamType.BOOLEAN,
                      "True=软删除(移入 .aios_trash 可找回)，False=永久删除",
                      required=False, default=True),
        ],
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
