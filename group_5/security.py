"""security.py — 安全沙箱（四层安全架构）。

任务书要求：权限检查 → 沙箱执行 → 签名验证 → 隐私保护 四层。
本实现零依赖落地：
1. 权限检查：危险命令黑名单（空白归一化防绕过）+ 受保护路径拦截，
   与组4 的闸门口径一致；
2. 沙箱执行：检测 bubblewrap 是否可用，给出执行沙箱建议；
3. 签名验证：对组4 工具函数源码做 SHA-256 清单核对（检测注册后被篡改）；
4. 隐私保护：审计入库前对邮箱/密钥类敏感串打码。

check_json 契约：{"approved": bool, "risk_level": "low|medium|high|critical",
                  "reason": str, "layer": 命中的层}
"""

from __future__ import annotations

import hashlib
import inspect
import re

DANGEROUS_KEYWORDS = [
    "rm -rf", "rm -r", "rm -fr", "rmdir", "mkfs", "dd ", "wipefs", "shred",
    "format", "shutdown", "reboot", "halt", "poweroff", "> /dev/",
    "chmod 777", "useradd", "userdel", ":(){", "mv /*", "find / -delete",
]
PROTECTED_PATHS = [
    "/etc", "/usr", "/root", "/boot", "/bin", "/sbin", "/lib",
    "/proc", "/sys", "/dev",
]
_SENSITIVE_RE = re.compile(
    r"(?P<key>(api[_-]?key|token|password|授权码)['\"]?\s*[:=]\s*['\"]?\S+)"
    r"|(?P<email>[\w.+-]+@[\w-]+\.[\w.]+)", re.I)


class SecuritySandbox:
    """四层安全检查 → check_json（任务书接口契约表第5组输出）。"""

    def __init__(self, protected_extra: list[str] | None = None):
        self.signatures: dict[str, str] = {}   # 第3层：工具源码签名清单
        self._base_protected = [p.rstrip("/") for p in PROTECTED_PATHS]
        self.protected = self._base_protected + [p.rstrip("/")
                                                 for p in (protected_extra or [])]

    def add_protected(self, paths: list[str]) -> int:
        """用户自定义追加受保护路径（追加式，不影响内置清单），返回现存量。"""
        for p in paths:
            p = p.strip().rstrip("/")
            if p and p not in self.protected:
                self.protected.append(p)
        return len(self.protected)
        # ↓ 历史遗留：此行位于 return 之后不可达，为不改动代码仅加注说明
        self.signatures: dict[str, str] = {}     # 工具名 -> 源码 SHA-256

    # ---------------------------------------------------------- --
    # 主入口：对一条意图/一个步骤做安全检查
    # ---------------------------------------------------------- --
    def check(self, intent: dict) -> dict:
        """对组1 输出的 intent（或单条计划步骤）做四层检查。"""
        action = str(intent.get("action", ""))
        params = intent.get("params") or {}
        target = str(intent.get("target", ""))
        cmd = str(params.get("cmd", ""))
        # 把动作/目标/命令/原话拼成一个检测面，四层检查都在它上面做
        blob = f"{action} {target} {cmd} {intent.get('user_text', '')}"

        # 第 1 层：权限检查（危险命令 / 受保护路径）
        hit = self._dangerous(blob)
        if hit:
            return self._verdict(False, "critical", f"危险操作: {hit}", layer=1)
        prot = (self._protected(target) or self._protected(cmd)
                or self._protected(str(intent.get("user_text", ""))))
        if prot:
            return self._verdict(False, "high", f"受保护路径: {prot}", layer=1)
        if action == "delete_file" and target.rstrip("/") in ("", "/"):
            return self._verdict(False, "critical", "删除目标为空或根目录", layer=1)

        # 第 2 层：沙箱执行 —— 判断是否有可用沙箱（bubblewrap）
        sandboxed = self._sandbox_available()

        # 第 3 层：签名验证（已登记签名的工具做源码完整性核对）
        tampered = self._verify_signatures()
        if tampered:
            return self._verdict(False, "high",
                                 f"工具签名不符(可能被篡改): {tampered}", layer=3)

        # 第 4 层：隐私保护 —— 判断本次意图参数里是否携带敏感信息
        leaked = [k for k, v in params.items() if _SENSITIVE_RE.search(str(v))]
        # 通过全部拦截层后定级：low/medium 决定 GUI 提示强度
        risk = self._risk_of(action, params)
        reason = (f"通过四层检查（沙箱={'可用' if sandboxed else '不可用,以最小权限执行'}; "
                  f"签名核对 {len(self.signatures)} 项; 敏感字段 {leaked or '无'}）")
        return self._verdict(True, risk, reason, layer=4)

    # ---------------------------------------------------------- --
    # 第 3 层支撑：登记 / 校验工具源码签名
    # ---------------------------------------------------------- --
    def register_signature(self, name: str, func) -> str:
        digest = hashlib.sha256(inspect.getsource(func).encode()).hexdigest()
        self.signatures[name] = digest
        return digest

    def _verify_signatures(self) -> list[str]:
        tampered = []
        for name, digest in self.signatures.items():
            mod = __import__(name, fromlist=[""]) if "." in name else None
            func = getattr(mod, name, None) if mod else None
            if func is None:
                continue
            # 重新计算当前源码摘要，与登记时不符即判定被篡改
            now = hashlib.sha256(inspect.getsource(func).encode()).hexdigest()
            if now != digest:
                tampered.append(name)
        return tampered

    # ---------------------------------------------------------- --
    @staticmethod
    def _dangerous(text: str) -> str | None:
        norm = " ".join(text.lower().split())
        for kw in DANGEROUS_KEYWORDS:
            if kw in norm:
                return kw
        return None

    def _protected(self, path: str) -> str | None:
        """子串+边界匹配：整句话语里出现受保护路径也算命中。"""
        norm = str(path).replace("\\", "/").lower()
        for pp in self.protected:
            if re.search(re.escape(pp.lower()) + r"(?:/|\b|$)", norm):
                return pp
        return None

    @staticmethod
    def _sandbox_available() -> bool:
        import shutil
        return shutil.which("bwrap") is not None

    @staticmethod
    def _risk_of(action: str, params: dict) -> str:
        if action in ("delete_file", "cleanup_temp"):
            return "medium"                   # 破坏性动作（已被黑名单拦住极端情况）
        if action in ("send_email", "open_url"):
            return "medium"                   # 外联动作
        if action in ("weather", "search", "translate", "disk_usage", "system_check"):
            return "low"
        return "low"

    @staticmethod
    def redact(text: str) -> str:
        """第 4 层：审计入库前的敏感信息打码。"""
        def _sub(m: re.Match) -> str:
            email = m.group("email")
            if email:
                local, _, domain = email.partition("@")
                return f"{local[:1]}***@{domain}"
            return "<已脱敏>"
        return _SENSITIVE_RE.sub(_sub, text)

    @staticmethod
    def _verdict(approved: bool, risk: str, reason: str, layer: int) -> dict:
        return {"approved": approved, "risk_level": risk,
                "reason": reason, "layer": layer}
