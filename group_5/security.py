"""security.py — 安全沙箱（四层安全架构）。

任务书要求：权限检查 → 沙箱执行 → 签名验证 → 隐私保护 四层。
本实现零依赖落地：
1. 权限检查：危险命令黑名单（空白归一化 + 下载即执行类正则）+ 受保护路径
   拦截（系统目录 / 用户主目录根 / 主目录下凭据敏感区），与组4 的闸门口径
   一致（口径漂移由 tests/test_regression_audit.py 交叉断言兜住）；
2. 沙箱执行：检测 bubblewrap 是否可用，给出执行沙箱建议；
3. 签名验证：对组4 工具函数的**运行时代码**（结构化字节码摘要）做摘要核对，检测
   进程内被替换/篡改；磁盘源码漂移（改了代码未重启）只提示不拦。
   由编排层 build() 通过 register_signatures() 注入待核对的工具函数；
4. 隐私保护：审计入库前对邮箱/密钥类敏感串打码（redact），编排层调用。

check_json 契约：{"approved": bool, "risk_level": "low|medium|high|critical",
                  "reason": str, "layer": 命中的层}
"""

from __future__ import annotations

import hashlib
import inspect
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- --
# 第 1 层：危险命令黑名单（与 src/default_tools.py 同口径，测试交叉断言）
# ---------------------------------------------------------------- --
DANGEROUS_KEYWORDS = [
    "rm -rf", "rm -r", "rm -fr",
    "> /dev/", "chmod 777", "chmod -r 777",
    ":(){",            # fork 炸弹
    "mv /*",           # 全盘移动
    "find / -delete",  # find 删除变体
    # 修复逻辑审计 #26：单词命令（dd/rmdir/mkfs/format/halt…）退出子串口径 ——
    # 裸 "dd " 命中 "add file"、"format" 命中 "--format="，而 user_text 也在
    # 扫描面上（话语里一个英文词就整条 critical）。整词匹配移入下方正则口径。
]

# 正则形态的"下载即执行 / 覆盖裸设备"变体（关键词表拦不住的部分）
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

# 受保护系统目录（前缀匹配）
PROTECTED_PATHS = [
    # Linux 系统目录
    "/etc", "/usr", "/root", "/boot", "/bin", "/sbin", "/lib",
    "/proc", "/sys", "/dev", "/var", "/opt", "/srv",
    # Windows 系统目录
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

# 路径保护只作用于"会改动磁盘 / 会外发数据 / 读敏感文件"的动作；
# 只读浏览类动作（navigate/list_directory/find_* 等）不受限，否则
# "导航到主目录""找找 home 下的大文件"这类合法只读需求会被误拦。
_MUTATING_ACTIONS = {
    "delete_file", "trash_delete", "move_file", "copy_file", "write_file",
    "create_folder", "cleanup_temp", "organize_downloads",
    "backup_directory", "take_screenshot", "send_email",
    # 修复逻辑审计 #6：check() 跑在组1 意图阶段，动作名用的是组1 口径
    # （organize/backup/find_duplicates）。此前只列组4 公开名，实测
    # "backup src=/etc" 因名字对不上而整体漏过路径保护。两套名字都收。
    "organize", "backup", "find_duplicates",
}
_SENSITIVE_READ_ACTIONS = {"read_file"}
_PROTECTED_SCOPE_ACTIONS = _MUTATING_ACTIONS | _SENSITIVE_READ_ACTIONS | {"run_command"}

# 哪些动作的 `target` 字段**真的是一个会被触碰的路径**。
# send_email 的 target 是收件人语义（LLM 有时还会塞进主题/草稿名），
# 真正的文件只有附件 —— 把它算进去会让"工作区正好在受保护目录下"的正常发信被拦。
_TARGET_IS_PATH = _PROTECTED_SCOPE_ACTIONS - {"send_email"}

# 作为"操作路径"参与判定的参数名
_PATH_KEYS = ("path", "src", "dest", "directory", "file", "target",
              "attachment", "backup")
# 命令串里可能内嵌路径 → run_command 需要一并当路径候选检查
_CMD_KEYS = ("cmd", "command")

# 修复逻辑审计 #5：整条 cmd 当一个"路径"做前缀匹配等于不查 ——
# "tee -a /root/.ssh/authorized_keys" 以程序名开头永不命中。
# 这组正则从命令串里抠出路径形态子串（~/…、$HOME/…、绝对路径、盘符路径），
# 逐个过受保护判定。lookbehind 排除 URL（"http://x/y" 不抠 "//x/y"）。
_CMD_PATH_RES = [
    re.compile(r"~(?:[/\\][\w.\-\u4e00-\u9fa5.]+)+"),
    re.compile(r"(?<![\w/~$%])(?:[a-zA-Z]:)?[/\\][\w.\-\u4e00-\u9fa5.*]+(?:[/\\][\w.\-\u4e00-\u9fa5.*]+)*"),
]


def _paths_in_command(cmd: str) -> list[str]:
    """从命令串中提取路径候选（先把 $HOME/%USERPROFILE% 换成真实主目录）。"""
    s = str(cmd or "")
    s = re.sub(r"\$\{?(?:HOME|USERPROFILE)\}?", _HOME.replace("\\", "/"), s)
    hits: list[str] = []
    for rx in _CMD_PATH_RES:
        for m in rx.finditer(s):
            tok = m.group(0).rstrip(".,;:)]>\"'")
            if tok and tok not in hits:
                hits.append(tok)
    return hits

_SENSITIVE_RE = re.compile(
    r"(?P<key>(api[_-]?key|token|password|passwd|secret|authorization|授权码)"
    r"['\"]?\s*[:=]\s*['\"]?\S+)"
    r"|(?P<email>[\w.+-]+@[\w-]+\.[\w.]+)", re.I)

# 敏感**字段名**：命中时整条值打码（用于审计记录递归打码 —— 只看值的话
# "裸的 sk-xxx" 没有 api_key 上下文，正则认不出来）。
# 修复逻辑审计 #13：补齐中文键名（用户表单/LLM 参数常写"密码/密钥/令牌"），
# 只匹配英文的老口径会把 {"密码": "hunter2"} 原样写进所有落盘出口。
SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|apikey|token|secret|password|passwd|授权码|"
    r"credential|private[_-]?key"
    r"|密码|口令|密钥|令牌|凭据|私钥)", re.I)


def _norm(p) -> str:
    """统一路径形态：反斜杠→正斜杠、去尾斜杠、小写。"""
    return str(p).replace("\\", "/").rstrip("/").lower()


class SecuritySandbox:
    """四层安全检查 → check_json（任务书接口契约表第5组输出）。"""

    def __init__(self, protected_extra: list[str] | None = None):
        # 第3层：工具名 → {"digest","module","qualname"}（register_signatures 注入）
        self.signatures: dict[str, dict] = {}
        # 修复逻辑审计 #17（对象口径）：可选注入"当前注册表快照"的取函数
        # （编排层用 lambda 注入，组5 仍不 import 组4，红线 13 同款）。
        # 核对时**优先取注册表当前对象** —— 真正被执行的是它；早期只核对
        # 注册瞬间存下的 rec["_func"]，注册表对象后来被人换掉反而查不出来。
        self._live_lookup = None
        self._base_protected = [p.rstrip("/") for p in PROTECTED_PATHS]
        self._sensitive = [_norm(os.path.join(_HOME, s))
                           for s in SENSITIVE_SUBPATHS]
        self.home_root = _norm(_HOME)
        self.protected = self._base_protected + [p.rstrip("/")
                                                 for p in (protected_extra or [])]

    def add_protected(self, paths: list[str]) -> int:
        """用户自定义追加受保护路径（追加式，不影响内置清单），返回现存量。"""
        for p in paths:
            p = str(p).strip().rstrip("/")
            if p and p not in self.protected:
                self.protected.append(p)
        return len(self.protected)

    # ---------------------------------------------------------- --
    # 主入口：对一条意图/一个步骤做安全检查
    # ---------------------------------------------------------- --
    def check(self, intent: dict) -> dict:
        """对组1 输出的 intent（含全部 goals）或单条计划步骤做四层检查。

        检测面**全量展开**：顶层 action/params/target/user_text + 每个 goal 与
        每个 step 的 action/target/params（递归收集全部字符串值）。
        早期实现只拼顶层 params.cmd，导致"危险命令藏在 goals[].params.cmd"
        可整体绕过黑名单 —— 这是本层最关键的不变量，勿再退回。
        """
        intent = intent or {}
        surface = self._surface(intent)

        # 第 1 层：权限检查（危险命令 / 受保护路径 / 空目标删除）
        hit = self._dangerous(surface)
        if hit:
            return self._verdict(False, "critical", f"危险操作: {hit}", layer=1)
        prot = self._protected_paths_in(intent)
        if prot:
            return self._verdict(False, "high", f"受保护路径: {prot}", layer=1)
        empty_target = self._empty_delete_target(intent)
        if empty_target:
            return self._verdict(False, "critical", empty_target, layer=1)

        # 第 2 层：沙箱执行 —— 判断是否有可用沙箱（bubblewrap）
        sandboxed = self._sandbox_available()

        # 第 3 层：签名验证
        #   · 运行时代码变了、源文件没变（tampered）→ 拦（真·内存里被动过）；
        #   · 运行时代码变了、源文件也变了（suspect）→ 可解释（热更/重导入），只提示；
        #   · 只有源文件变了（drifted）→ 进程仍跑旧版本，只提示。
        tampered, drifted, suspect = self._verify_signatures()
        if tampered:
            return self._verdict(False, "high",
                                 f"工具运行时代码与登记不一致(疑似被篡改): {tampered}",
                                 layer=3)

        # 第 4 层：隐私保护 —— 判断本次意图参数里是否携带敏感信息
        leaked = self._sensitive_fields(intent)
        risk = self._risk_of_intent(intent)
        notes = ""
        if drifted:
            notes += (f"; ⚠ 磁盘源码已变更: {drifted}（进程仍运行加载时的旧版本，"
                      f"重启后生效；不影响本次执行）")
        if suspect:
            notes += (f"; ⚠ 运行时代码与登记不一致但源文件同时变更: {suspect}"
                      f"（按可解释处理，未阻断；重启后恢复干净基线）")
        reason = (f"通过四层检查（沙箱={'可用' if sandboxed else '不可用,以最小权限执行'}; "
                  f"签名核对 {len(self.signatures)} 项; 敏感字段 {leaked or '无'}"
                  f"{notes}）")
        return self._verdict(True, risk, reason, layer=4)

    # ---------------------------------------------------------- --
    # 执行前终审（修复逻辑审计 #7）：单步参数在 {{占位符}} 回填之后、
    # 真正调用工具之前过一遍第 1 层判据。回填内容成形于意图/计划闸门
    # 之后，不受约束等于"检测面 ≠ 执行面"。由编排层把本方法作为**数据**
    # 注入 AppAgent.security_guard（组5↔组3 不互相 import，红线 13 同款）。
    # 返回 None = 放行；返回字符串 = 拦截原因。
    # ---------------------------------------------------------- --
    def check_step(self, action: str, params: dict) -> str | None:
        node = {"action": str(action or ""), "params": params
                if isinstance(params, dict) else {}}
        hit = self._dangerous(self._surface(node))
        if hit:
            return f"危险操作: {hit}"
        prot = self._protected_paths_in(node)
        if prot:
            return f"受保护路径: {prot}"
        empty = self._empty_delete_target(node)
        if empty:
            return empty
        return None

    # ---------------------------------------------------------- --
    # 检测面构建（关键不变量：必须覆盖 goals/steps）
    # ---------------------------------------------------------- --
    @classmethod
    def _surface(cls, intent: dict) -> str:
        """把整条意图（含 goals/steps 全部层级）的字符串值拼成检测面。"""
        parts: list[str] = []

        def walk(node, depth: int = 0) -> None:
            # 轻微审计：递归设深度上限 —— 异常深的嵌套意图（repair/恶意 LLM
            # 输出）不再能把 RecursionError 炸进整轮编排；真实契约层级 <6。
            if depth > 24:
                return
            if isinstance(node, str):
                parts.append(node)
            elif isinstance(node, dict):
                for k, v in node.items():
                    parts.append(str(k))
                    walk(v, depth + 1)
            elif isinstance(node, (list, tuple, set)):
                for v in node:
                    walk(v, depth + 1)

        walk(intent)
        return " ".join(parts)

    @classmethod
    def _iter_actions(cls, intent: dict):
        """产出 (action, params) —— 顶层 + 每个 goal/step。

        同动作 goal/step 未显式给 params 时按**组2 执行口径**继承顶层 params
        （planner._fill_params 以 intent.params 为第 1 层基底）—— 否则
        "闸门看到的数据 ≠ 执行用的数据"：规则路径的 delete_file 意图
        （goal 只有动作名）会被空目标防线误拦，而藏在 goal 继承参数里的
        危险路径又会漏检。"""
        top = cls._action_params(intent)
        if top:
            yield top
        for key in ("goals", "steps"):
            for node in (intent.get(key) or []):
                if isinstance(node, dict):
                    pair = cls._action_params(node)
                    if not pair:
                        continue
                    action, params = pair
                    if params == {} and top and action == top[0]:
                        yield action, top[1]
                    else:
                        yield pair

    @staticmethod
    def _action_params(node: dict):
        action = str(node.get("action", "") or "")
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        if not action and not params:
            return None
        return action, params

    @classmethod
    def _operation_paths(cls, intent: dict) -> list[tuple[str, str]]:
        """收集需要过保护判定的"操作路径"，返回 [(路径, 出处)]。

        出处形如 `write_file.path` / `send_email.attachment`，用来把拦截原因写清楚
        （旧实现只报"受保护路径: /mnt"，用户根本不知道是哪个字段触发的）。
        不看 user_text —— 否则"话语里提到 /etc"就会把整条指令误拦。
        """
        out: list[tuple[str, str]] = []

        def take(node: dict) -> None:
            action, params = (cls._action_params(node) or ("", {}))
            if action not in _PROTECTED_SCOPE_ACTIONS:
                return
            # send_email 的 target 不是"要触碰的路径"（收件人/主题都可能有 target），
            # 邮件真正会读的只有附件 —— 否则工作区落在受保护目录时，正常发信也被拦。
            if action != "run_command" and action in _TARGET_IS_PATH \
                    and node.get("target"):
                out.append((str(node["target"]), f"{action}.target"))
            keys = list(_PATH_KEYS) + (list(_CMD_KEYS)
                                       if action == "run_command" else [])
            for k, v in params.items():
                if k not in keys:
                    continue
                for item in (v if isinstance(v, (list, tuple)) else [v]):
                    if isinstance(item, str) and item.strip():
                        out.append((item, f"{action}.{k}"))
                        # 修复逻辑审计 #5：run_command 还要抠出命令串**内嵌**的
                        # 路径逐个过保护判定（整条命令前缀匹配恒不命中）
                        if action == "run_command" and k in _CMD_KEYS:
                            for tok in _paths_in_command(item):
                                out.append((tok, f"{action}.{k}内嵌"))

        take(intent)
        for key in ("goals", "steps"):
            for node in (intent.get(key) or []):
                if isinstance(node, dict):
                    take(node)
        return out

    def _protected_paths_in(self, intent: dict) -> list[str]:
        hits: list[str] = []
        for p, where in self._operation_paths(intent):
            hit = self._protected(p)
            if hit and hit not in hits:
                sug = f"{hit}（来自 {where}）"
                if sug not in hits:
                    hits.append(sug)
        return hits

    @classmethod
    def _sensitive_fields(cls, intent: dict) -> list[str]:
        """本次意图里携带敏感内容的字段名（实际打码由 redact 做）。"""
        out: list[str] = []
        for _, params in cls._iter_actions(intent):
            for k, v in params.items():
                if _SENSITIVE_RE.search(str(v)) and k not in out:
                    out.append(k)
        return out

    # 删除语义的动作：目标为空/根目录必须整条拒绝（修复逻辑审计 #4）。
    # 不再回退顶层 intent.target —— goal 级缺参会被顶层目标顶掉，正是泄漏路径；
    # 缺参在组2 会如实报"缺少必需参数"，但安全闸门是最后防线，宁拦勿误删。
    _EMPTY_TARGET_ACTIONS = {"delete_file", "trash_delete"}

    @classmethod
    def _empty_delete_target(cls, intent: dict) -> str | None:
        for action, params in cls._iter_actions(intent):
            if action not in cls._EMPTY_TARGET_ACTIONS:
                continue
            target = str(params.get("path") or "").strip()
            nt = target.replace("\\", "/").rstrip("/")
            if (nt in ("", "/", "~") or target == "~"
                    or re.fullmatch(r"[a-zA-Z]:", nt)):
                return f"删除类动作 {action} 的目标为空或根目录"
        return None

    # ---------------------------------------------------------- --
    # 第 3 层支撑：登记 / 校验工具源码签名
    # ---------------------------------------------------------- --
    def register_signature(self, name: str, func) -> str | None:
        """登记一个工具的**运行时代码摘要** + 源码摘要 + 源文件戳。取不到源码返回 None。

        三个信号各管一件事：
          · code  ：`_code_digest(func)` —— 进程内**真正在跑**的结构化字节码
                    摘要（不用 marshal.dumps：同一 code 对象执行前后两次
                    dumps 的字节可能不同，会造成间歇性误判，见审计 #17）；
                    有人把函数对象换掉/改掉，这里就变（第 3 层要防的就是这个）；
          · source_digest / file：磁盘上的源码文本与 (mtime,size) —— 用来识别
                    "文件改过、但进程仍在跑旧版本"，属于提示而非篡改。
        """
        try:
            src = inspect.getsource(func)
        except (OSError, TypeError):
            return None
        rec = {
            "digest": hashlib.sha256(src.encode("utf-8")).hexdigest(),
            "module": getattr(func, "__module__", "") or "",
            "qualname": getattr(func, "__qualname__", "") or "",
            "code": self._code_digest(func),
            "file": self._file_stamp(func),
            # 记下**文件路径**本身：核对"源文件是否变过"必须 stat 这个记录下来的
            # 路径，而不是当前 func.__code__.co_filename —— 后者可以随
            # `func.__code__ = ...` 一起被换掉，于是"文件也变了"这个解释条件
            # 会被伪造，真篡改反而被归到"可解释"档放行。
            "file_path": getattr(func.__code__, "co_filename", "") or "",
            # 直接持有**注册时的函数对象**（内存内引用，绝不序列化/外发）。
            # 只按 module+qualname 反查是不够的：同一个模块被两条路径导入过
            # （sys.path 混用、包名与顶层副本并存）时，反查回来的可能是**另一个
            # 同名对象**，于是摘要"必然不符" → 被误判成篡改（实测开发机上就这
            # 么误伤过：整条编排被拒，报 take_screenshot 等工具被篡改）。
            # 而真正决定行为的是**注册表持有的这个对象**，核对它就够了。
            "_func": func,
        }
        self.signatures[name] = rec
        return rec["digest"]

    @staticmethod
    def _code_digest(func) -> str:
        """运行中函数的**结构化摘要**（内存里被替换/改写就会变）。

        修复逻辑审计 #17（间歇性"疑似被篡改"的真实根因）：旧实现用
        `marshal.dumps(func.__code__)` 取字节 —— CPython 的 marshal 对 code 内
        字符串走"引用表"输出，**同一 code 对象在函数首次被执行后两次 dumps
        的字节都可能不同**（实测 organize_downloads：注册时一致、跑过一轮编排
        后翻转 → 整条链路被误判篡改拒掉）。因此不依赖 marshal 编码，显式展开
        不变结构：co_code 字节码、名称/变量表、行号、常量（嵌套 code 递归），
        依旧只反映"进程里真正在跑的代码"（红线 20 的语义不变）。
        """
        try:
            h = hashlib.sha256()
            code_cls = type((lambda: None).__code__)

            def walk(code):
                h.update(b"<c>")
                h.update(code.co_code)
                meta = "|".join((
                    str(code.co_name), str(getattr(code, "co_qualname", "")),
                    str(code.co_filename), str(code.co_argcount),
                    str(code.co_posonlyargcount), str(code.co_kwonlyargcount),
                    str(code.co_flags), str(code.co_firstlineno),
                    ",".join(code.co_names), ",".join(code.co_varnames),
                    ",".join(code.co_freevars), ",".join(code.co_cellvars)))
                h.update(meta.encode("utf-8", "replace"))
                for const in code.co_consts:
                    if isinstance(const, code_cls):
                        walk(const)
                    else:
                        h.update(repr(const).encode("utf-8", "replace"))
                h.update(b"</c>")

            walk(func.__code__)
            return h.hexdigest()
        except Exception:
            return ""                     # 取不到不算篡改，只是无法核对

    @staticmethod
    def _file_stamp(func) -> str:
        """函数所在文件的 (mtime_ns, size)，用于识别"磁盘源码已变更"。"""
        try:
            st = os.stat(func.__code__.co_filename)
            return f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            return ""

    @staticmethod
    def _stamp_at(path: str) -> str:
        """按**登记时的路径**取文件戳（不信任当前 co_filename，见 register_signature）。"""
        try:
            st = os.stat(path)
            return f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            return "已消失"

    def register_signatures(self, mapping: dict | None) -> int:
        """批量登记 {工具名: 函数}，返回成功登记数（编排层 build() 调用）。"""
        count = 0
        for name, func in (mapping or {}).items():
            if callable(func) and self.register_signature(str(name), func):
                count += 1
        return count

    def register_live_source(self, getter) -> None:
        """注入"取当前注册表 {名: 函数}"的回调（编排层 build() 调用）。

        getter 为无参函数，返回 dict。注入后 _verify_signatures 会同时核对
        **注册表当前对象**：有人绕过 register() 直接把 registry._tools 里的
        函数换成别的实现（真正被调用的就是它），单靠登记快照查不出来。
        """
        self._live_lookup = getter if callable(getter) else None

    def _verify_signatures(self) -> tuple[list[str], list[str], list[str]]:
        """核对签名，返回 **(被篡改的, 磁盘源码已漂移的, 存疑但可解释的)**。

        · **篡改（拦）**：运行时代码变了，而它所在源文件**没变** —— 进程内存里被
          换过，这是第 3 层真正要防的；
        · **存疑（不拦）**：运行时代码变了，但**源文件同时也变过** —— 进程很可能
          在加载后经历了重导入/热更，或"启动时的版本"与"现在的版本"本来就不同。
          能解释就不该拦（拦下来只会让开发期的每次改码都卡住整条链路）；
        · **漂移（不拦）**：只有源文件变了（进程仍跑加载时的旧版本），提示即可。

        早期实现把三种情况一律报"疑似被篡改"并拒执行，实测在开发机上频繁误伤。
        """
        tampered: list[str] = []
        drifted: list[str] = []
        suspect: list[str] = []
        for name, rec in list(self.signatures.items()):
            # 优先核对**注册时持有的那个对象**（注册表将来执行的就是它）；
            # 老记录没有 _func 时才退回按 module+qualname 反查。
            func = rec.get("_func") or self._resolve_object(rec)
            if func is None:
                continue
            cur = self._code_digest(func)
            # "源文件变过"要按**登记时的路径**核对（不能信当前 co_filename）
            file_changed = bool(rec.get("file")) and (
                self._stamp_at(rec.get("file_path") or "") != rec["file"])
            if rec.get("code") and cur and cur != rec["code"]:
                (suspect if file_changed else tampered).append(name)
                continue
            # 修复逻辑审计 #17（对象口径）：注册表**当前**对象若已被换成别的
            # 实现（真正会被调用的就是它），同样按篡改处理。结构化摘要对
            # "同源双导入"不敏感（同文件同行号同字节码 → 摘要一致），不会
            # 复发早期 module+qualname 反查的误伤问题。
            if self._live_lookup is not None:
                try:
                    live = dict(self._live_lookup() or {}).get(name)
                except Exception:
                    live = None
                if (live is not None and live is not func
                        and rec.get("code")
                        and self._code_digest(live) != rec["code"]):
                    tampered.append(name)
                    continue
            if file_changed:
                drifted.append(name)
        return tampered, drifted, suspect

    @staticmethod
    def _resolve_object(rec: dict):
        module = sys.modules.get(rec.get("module") or "")
        obj = module
        for part in (rec.get("qualname") or "").split("."):
            if not part or obj is None:
                return None
            obj = getattr(obj, part, None)
        return obj

    # ---------------------------------------------------------- --
    @staticmethod
    def _dangerous(text: str) -> str | None:
        norm = " ".join(str(text).lower().split())
        for kw in DANGEROUS_KEYWORDS:
            if kw in norm:
                return kw
        for rx in _DANGEROUS_RES:
            if rx.search(norm):
                return rx.pattern
        return None

    def _protected(self, path: str) -> str | None:
        """受保护判定：系统目录/敏感区前缀匹配 + 主目录根精确匹配。"""
        if not path:
            return None
        cands = {_norm(path)}
        try:
            cands.add(_norm(Path(str(path)).expanduser().resolve()))
        except (OSError, RuntimeError):
            pass
        for cand in cands:
            # 修复逻辑审计 #3：根目录/'/' 归一后是空串，前缀匹配恒不命中；
            # 盘符根（"c:"）同理。对根目录动手一律拦。
            if cand in ("", "/") or re.fullmatch(r"[a-zA-Z]:", cand):
                return "文件系统根目录"
            if cand == self.home_root:
                return f"用户主目录 {_HOME}"
            for sp in self._sensitive:
                if cand == sp or cand.startswith(sp + "/"):
                    return f"敏感路径 {sp}"
            for pp in self.protected:
                pp_norm = _norm(pp)
                if cand == pp_norm or cand.startswith(pp_norm + "/"):
                    return pp
        return None

    @staticmethod
    def _sandbox_available() -> bool:
        import shutil
        return shutil.which("bwrap") is not None

    @classmethod
    def _risk_of_intent(cls, intent: dict) -> str:
        """按所有 goal/step 的动作取最高风险（不再只看顶层 action）。"""
        levels = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        worst = "low"
        for action, params in cls._iter_actions(intent):
            r = cls._risk_of(action, params)
            if levels.get(r, 0) > levels[worst]:
                worst = r
        return worst

    @staticmethod
    def _risk_of(action: str, params: dict) -> str:
        # 系统命令执行：即使不在黑名单里也不是"低风险"
        if action == "run_command":
            return "high"
        if action in ("delete_file", "cleanup_temp", "move_file", "trash_delete"):
            return "medium"                   # 破坏性动作
        if action in ("write_file", "copy_file", "create_folder",
                      "take_screenshot", "backup_directory",
                      "organize_downloads", "backup"):
            return "medium"                   # 落盘动作
        if action in ("send_email", "open_url", "web_open", "web_search",
                      "web_click", "web_close_browser"):
            return "medium"                   # 外联动作
        return "low"

    @staticmethod
    def redact(text: str) -> str:
        """第 4 层：审计入库前的敏感信息打码（编排层 audit() 调用）。"""
        def _sub(m: re.Match) -> str:
            email = m.group("email")
            if email:
                local, _, domain = email.partition("@")
                return f"{local[:1]}***@{domain}"
            return "<已脱敏>"
        return _SENSITIVE_RE.sub(_sub, str(text))

    def mask_record(self, record):
        """递归打码一条审计记录（编排层 audit() 调用）。

        两条规则：① **敏感字段名**命中 → 整条值换成 <已脱敏>（值本身可能不带
        任何可识别上下文，如裸的 "sk-xxx"）；② 字符串值内的邮箱/内联密钥 → 正则打码。
        """
        return self._mask_obj(record)

    @classmethod
    def _mask_obj(cls, node):
        if isinstance(node, str):
            return cls.redact(node)
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                # 修复逻辑审计 #13：敏感字段名命中 → **整个子树**打码。
                # 旧实现只打码 str 值 —— {"password": {"work": "..."}} 或
                # 凭据对象挂在敏感键下时，内部内容原样外流。
                if v and SENSITIVE_KEY_RE.search(str(k)):
                    out[k] = "<已脱敏>"
                else:
                    out[k] = cls._mask_obj(v)
            return out
        if isinstance(node, (list, tuple)):
            return [cls._mask_obj(v) for v in node]
        return node

    @staticmethod
    def _verdict(approved: bool, risk: str, reason: str, layer: int) -> dict:
        return {"approved": approved, "risk_level": risk,
                "reason": reason, "layer": layer}
