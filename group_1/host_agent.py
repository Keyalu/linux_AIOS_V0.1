"""host_agent.py — 意图理解（LLM 主路径 + 规则 Mock 双轨）。

任务书要求：HostAgent 调用 LLM（通义 qwen-plus，OpenAI 兼容接口）做意图
理解，同时"提供 Mock 实现，便于其他组独立开发"。本模块两条路径都实现：

- LLM 路径：配置了 DASHSCOPE_API_KEY / OPENAI_API_KEY 环境变量时启用，
  用标准库 urllib 直连 OpenAI 兼容接口（项目保持零第三方依赖）；
- 规则路径（Mock）：纯正则词典 + 实体抽取，无任何网络与依赖，
  LLM 调用失败时也自动降级到这里。

两条路径输出同一种 intent_json（契约见 group_1/__init__.py）。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
import uuid

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- --
# 规则词典（Mock 路径）：意图类别 / 动作 / 参数抽取
# ---------------------------------------------------------------- --

_CATEGORIES = {
    "organize": "文件操作", "find_duplicates": "文件操作", "backup": "文件操作",
    "find_large_files": "文件操作", "find_files": "文件操作",
    "cleanup_temp": "文件操作", "delete_file": "文件操作",
    "disk_usage": "系统设置", "system_check": "系统设置",
    "open_url": "应用控制", "open_app": "应用控制", "send_email": "应用控制",
    "navigate": "应用控制", "switch_window": "应用控制", "create_folder": "应用控制",
    "weather": "信息查询", "search": "信息查询", "translate": "信息查询",
}

# (正则, 动作, 描述) —— 顺序即优先级，靠前的更具体
_RULES: list[tuple[str, str, str]] = [
    (r"天气", "weather", "查询城市天气"),
    (r"翻译", "translate", "翻译文本"),
    (r"联网搜索|上网查|搜个|搜一下", "search", "联网搜索资料"),
    (r"整理|归类|分类", "organize", "把目录里的文件按类型分类到子文件夹"),
    (r"重复|查重|冗余", "find_duplicates", "找出内容重复的文件"),
    (r"备份|备个份", "backup", "把目标目录压缩备份"),
    (r"大文件", "find_large_files", "找出目录里最大的若干文件"),
    (r"磁盘|空间", "disk_usage", "查询磁盘使用情况"),
    (r"系统状态|体检|检查系统", "system_check", "检查系统状态"),
    (r"清理|清空", "cleanup_temp", "清理临时文件"),
    (r"删除|删掉", "delete_file", "删除文件或目录"),
    (r"发(一封)?邮件|发送邮件", "send_email", "发送电子邮件"),
    (r"打开(网址|网页)|访问", "open_url", "打开网页"),
    (r"导航|前往|去到", "navigate", "在文件管理器中导航到路径"),
    (r"切换到|切换窗口|切到", "switch_window", "切换应用窗口"),
    (r"创建|新建", "create_folder", "创建文件夹"),
    (r"打开|启动|运行", "open_app", "打开应用"),
    (r"找(?!到)|查找", "find_files", "按条件搜索文件"),
]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
_QUOTE_RE = re.compile(r"[「'\"]([^」'\"]+)[」'\"]")


def _match_goals(text: str) -> list[dict]:
    """按词典扫描多个动作；span 重叠时保留更具体的先命中项。"""
    goals: list[dict] = []
    claimed: list[tuple[int, int]] = []
    for pattern, action, desc in _RULES:
        m = re.search(pattern, text)
        if not m:
            continue
        if any(s <= m.start() and m.end() <= e for s, e in claimed):
            continue                      # 命中位置被更具体的动作覆盖
        claimed.append((m.start(), m.end()))
        goals.append({"action": action, "description": desc})
    # 通用"找"只是兜底：已有更具体的文件目标时丢弃
    if len(goals) > 1 and any(g["action"] == "find_files" for g in goals):
        goals = [g for g in goals if g["action"] != "find_files"] or goals
    return goals


def _extract_params(action: str, text: str) -> dict:
    """按动作抽取参数（契约表 params 字段）。"""
    params: dict = {}
    if action == "weather":
        m = re.search(r"(?:查询|查|看看|看|一下|帮我)*(.*?)天气", text)
        params["city"] = (m.group(1) if m else "").strip(" 的「」，,。") or "北京"
    elif action == "search":
        params["query"] = re.sub(r"^(帮我|请|麻烦)?(联网搜索|上网查|搜个|搜一下)", "", text).strip(" ，。,") or text
    elif action == "translate":
        params["text"] = re.sub(r".*?(?:把|将)?(.*?)(?:翻译.*)?$", r"\1", text).strip(" 「」") or text
        params["to_lang"] = "en"
    elif action == "send_email":
        m = _EMAIL_RE.search(text)
        if m:
            params["to"] = m.group(0)
        s = re.search(r"主题(?:是|为)?[「'\"]?([^，,。\n「'\"]+)", text)
        params["subject"] = s.group(1).strip() if s else "(无主题)"
        b = re.search(r"(?:内容|正文)(?:是|为)?[「'\"]?([^，,。\n「'\"]+)", text)
        params["body"] = b.group(1).strip() if b else "(空)"
        # 明确说"发/发送"即真实发送（安全由 ADMIN 提权确认流把关）；
        # "起草/只预览/先别发"才进入 dry_run
        params["dry_run"] = bool(re.search(r"草稿|只预览|不要发送|先别发|别真发", text))
    elif action == "open_url":
        u = re.search(r"https?://\S+", text)
        params["url"] = u.group(0).rstrip("。，,") if u else ""
    elif action == "open_app":
        m = re.search(r"(?:打开|启动|运行)(?:一下)?[「'\"]?([\w\u4e00-\u9fa5.-]+)", text)
        params["app"] = m.group(1) if m else ""
    elif action == "navigate":
        u = re.search(r"[「'\"]?(/[/\w.\-~]+)[「'\"]?", text)
        params["path"] = u.group(1) if u else "~/Documents"
    elif action == "switch_window":
        m = re.search(r"切换到[「'\"]?([\w\u4e00-\u9fa5.-]+)", text)
        params["app"] = m.group(1) if m else ""
    elif action == "create_folder":
        m = re.search(r"(?:创建|新建)(?:一个)?(?:名为)?[「'\"]?([\w\u4e00-\u9fa5.-]+?)[」'\"]?(?:的)?文件夹", text)
        params["name"] = m.group(1) if m else "new_folder"
    return params


class HostAgent:
    """意图理解：LLM 主路径 + 规则 Mock 降级，输出结构化 intent_json。"""

    def __init__(self, model: str = "qwen-plus", use_llm: bool | None = None):
        self.model = model
        self.api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = os.environ.get(
            "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.temperature = 0                  # 用户可调（GUI 系统设置）
        self._lock = threading.Lock()
        self._load_config()                   # GUI 保存的供应商配置优先于环境变量
        self.use_llm = bool(self.api_key) if use_llm is None else use_llm
        self.last_engine = "rules"            # 供上层展示本次用了哪条路径

    # ---------------------------------------------------------- --
    # 供应商配置（GUI「LLM 设置」页签读写；明文存放于 system_out/llm_config.json）
    # ---------------------------------------------------------- --
    @staticmethod
    def config_path() -> str:
        custom = os.environ.get("AGENT_OS_LLM_CONFIG")
        if custom:
            return custom
        return os.path.join(_ROOT, "system_out", "llm_config.json")

    def _load_config(self) -> None:
        try:
            with open(self.config_path(), encoding="utf-8") as f:
                cfg = json.load(f)
            self.base_url = cfg.get("base_url") or self.base_url
            self.model = cfg.get("model") or self.model
            self.api_key = cfg.get("api_key") or self.api_key
        except (OSError, json.JSONDecodeError):
            pass

    def apply_config(self, base_url: str, api_key: str, model: str,
                     provider: str = "", use_llm: bool = True) -> None:
        """应用并在 GUI 会话内即时生效 + 持久化，重启后自动加载。"""
        with self._lock:
            if base_url:
                self.base_url = base_url
            if api_key:
                self.api_key = api_key
            if model:
                self.model = model
            self.use_llm = use_llm and bool(self.api_key)
            path = self.config_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"provider": provider, "base_url": self.base_url,
                           "model": self.model, "api_key": self.api_key},
                          f, ensure_ascii=False, indent=2)

    def test_llm(self, sample: str = "查询长沙天气") -> dict:
        """连通性测试：强制走一次 LLM 路径，返回耗时与解析结果。"""
        t0 = time.monotonic()
        if not (self.api_key and self.base_url):
            return {"ok": False, "error": "未配置 api_key / base_url", "latency_ms": 0}
        try:
            intent = self._llm_intent(sample)
            latency = round((time.monotonic() - t0) * 1000, 1)
            if intent and intent.get("action"):
                return {"ok": True, "latency_ms": latency,
                        "engine": f"llm:{self.model}",
                        "intent": {"intent": intent.get("intent"),
                                   "action": intent.get("action"),
                                   "params": intent.get("params")}}
            return {"ok": False, "error": "LLM 返回内容无法解析为意图",
                    "latency_ms": latency}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": round((time.monotonic() - t0) * 1000, 1)}

    # ---------------------------------------------------------- --
    # 对外唯一入口
    # ---------------------------------------------------------- --
    def understand_intent(self, user_input: str, default_target: str = ".") -> dict:
        user_input = (user_input or "").strip()
        intent = None
        if self.use_llm:
            try:
                intent = self._llm_intent(user_input)
                self.last_engine = f"llm:{self.model}"
            except Exception:
                intent = None                  # 网络/密钥/解析失败 → 降级
        if intent is None:
            intent = self._rule_intent(user_input)
            self.last_engine = "rules"
        intent.setdefault("target", default_target)
        if not intent.get("target_label"):
            intent["target_label"] = intent.get("target", "")
        # 宿主环境解析：文件类意图的目标称呼（「demo_task」等）映射为真实目录
        if intent.get("intent") == "文件操作" or intent.get("action") in self._FILE_ACTIONS:
            raw = intent.get("target") or ""
            resolved = self._resolve_target(raw, default_target)
            if raw and resolved != raw:
                intent.setdefault("unresolved", []).append(
                    f"目标 {raw!r} 未在工作环境找到，已落到默认工作目录")
            intent["target"] = resolved
        # send_email 的 dry_run 确定性归一（不依赖 LLM 随机性）：
        # 出现发邮件动作即视为真实发送（安全由 ADMIN 提权确认流把关）；
        # 仅当话语含起草/预览类词汇时才降为干跑
        draft_only = bool(re.search(r"草稿|只预览|先别发|不要发送|别真发", user_input))
        for g in intent.get("goals", []):
            if g.get("action") == "send_email":
                g.setdefault("params", {})["dry_run"] = draft_only
        if intent.get("action") == "send_email":
            intent.setdefault("params", {})["dry_run"] = draft_only

        # send_email 附件确定性归一：话语里出现的真实文件路径 → attachment
        m_file = re.search(
            r"(/[\w.\-\u4e00-\u9fa5]+)+[/\w.\-\u4e00-\u9fa5]*\."
            r"(md|txt|pdf|docx|csv|json|py|log|xlsx|png|jpg)",
            user_input)
        for g in intent.get("goals", []):
            if (g.get("action") == "send_email" and m_file
                    and not (g.get("params") or {}).get("attachment")):
                g.setdefault("params", {})["attachment"] = m_file.group(0)
        if (intent.get("action") == "send_email" and m_file
                and not (intent.get("params") or {}).get("attachment")):
            intent.setdefault("params", {})["attachment"] = m_file.group(0)

        # send_email 多目标合并：LLM 偶尔把"多文件发给同一人"拆成多条——
        # 合并为一条，附件收进列表（send_email 已支持数组多附件）
        send_goals = [g for g in intent.get("goals", [])
                      if g.get("action") == "send_email"]
        if len(send_goals) > 1:
            first = send_goals[0]
            fp = first.setdefault("params", {})
            for g in send_goals[1:]:
                for k, v in (g.get("params") or {}).items():
                    if v in ("", None):
                        continue
                    if k == "attachment":
                        atts = fp.get("attachment") or []
                        atts = atts if isinstance(atts, list) else [atts]
                        for item in (v if isinstance(v, list) else [v]):
                            if item and item not in atts:
                                atts.append(item)
                        fp["attachment"] = atts or None
                    elif not fp.get(k):
                        fp[k] = v
            intent["goals"] = [
                g for g in intent.get("goals", [])
                if not (g.get("action") == "send_email" and g is not first)]

        # 文件类动作的 goal.params 路径参数同样做宿主环境解析
        for g in intent.get("goals", []):
            if g.get("action") not in self._FILE_ACTIONS:
                continue
            for k in ("path", "src", "directory"):
                v = (g.get("params") or {}).get(k)
                if v:
                    r = self._resolve_target(v, default_target)
                    if r != v:
                        g["params"][k] = r
            d = (g.get("params") or {}).get("dest")
            if d:
                g["params"]["dest"] = self._resolve_target(d, default_target) \
                    if os.path.isabs(os.path.expanduser(d)) else d

        intent["user_text"] = user_input
        intent["intent_id"] = f"intent-{uuid.uuid4().hex[:8]}"
        intent["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        intent["language"] = "zh-CN"
        return intent

    _FILE_ACTIONS = {"organize", "find_duplicates", "backup", "find_large_files",
                     "find_files", "cleanup_temp", "delete_file"}

    @staticmethod
    def _resolve_target(raw: str, default_target: str) -> str:
        """把用户口中的目标称呼解析为工作环境里的真实目录。"""
        if not raw:
            return default_target
        expanded = os.path.expanduser(raw)
        if os.path.isabs(expanded):
            return expanded if os.path.exists(expanded) else default_target
        if os.path.exists(expanded):
            return expanded
        for base in (os.path.dirname(default_target), os.getcwd(),
                     os.path.expanduser("~")):
            cand = os.path.join(base, raw)
            if os.path.exists(cand):
                return cand
        return default_target            # 找不到 → 宿主默认工作目录

    # ---------------------------------------------------------- --
    # LLM 路径（urllib 直连 OpenAI 兼容接口，零第三方依赖）
    # ---------------------------------------------------------- --
    _SYSTEM_PROMPT = (
        "你是 Linux Agentic OS 的意图理解模块。把用户指令解析成 JSON，只输出 JSON 不要解释。\n"
        "指令可能包含多个动作（如：搜索XX并把结果写入文件）——每个动作一个 goal，按执行顺序排列。格式：\n"
        '{"intent":"类别","target":"","confidence":0.9,"goals":[{"action":"..","target":"..","params":{..}}]}\n'
        "可用 action：organize/find_duplicates/backup/find_large_files/find_files/"
        "cleanup_temp/delete_file/disk_usage/system_check/open_url/open_app/"
        "send_email/weather/search/translate/write_file/run_command/"
        "navigate/create_folder/switch_window/take_screenshot\n"
        "截图链路：截图保存类需求 → take_screenshot(path=显式路径，"
        "如 /home/keyal/桌面/111/Agent_OS_v1.0/out/截图.png)；"
        "随后 send_email(attachment=同路径)/write_file 引用同一文件\n"
        "桌面 GUI 场景优先用：打开/启动应用=open_app(app)；导航到路径=navigate(path)；"
        "创建文件夹=create_folder(name)；切换窗口=switch_window(app)；"
        "截屏=take_screenshot(path)\n"
        "浏览器边界：open_url 仅能打开网址；浏览器内点击/读取页面内容不支持 —— "
        "需要搜索结果的内容/链接时用 search(query)，不要用 open_url 变通\n"
        "常用参数名：write_file 用 path(含文件名的完整路径)+content，写报告/摘录用它；"
        "run_command 用 cmd；open_url 用 url；"
        "send_email 用 to,subject,body,attachment(把文件发给对方时给文件的完整路径),"
        "dry_run(用户明确要求发送=false，仅起草/预览=true)；"
        "示例:把/out/a.md发送给x@qq.com → {action:send_email,params:{to:x@qq.com,"
        "subject:a.md,attachment:/out/a.md,dry_run:false}}\n"
        "mcp_weather 用 city,days；mcp_search 用 query；mcp_translate 用 text,to_lang；"
        "文件技能：organize_downloads(path)/find_duplicate_files(path)/"
        "backup_directory(src,dest)/find_large_files(path,min_size_mb)\n"
        "数据依赖：后一步参数需要前一步输出时，该参数值写占位符 {{prev_result}}"
        "（表示上一步的结果文本）。示例：\n"
        '输入:搜索智能体并摘录写入 /data/out/a.md → {"intent":"信息查询","goals":['
        '{"action":"search","target":"","params":{"query":"智能体"}},'
        '{"action":"write_file","target":"/data/out/a.md","params":{'
        '"path":"/data/out/a.md","content":"{{prev_result}"}}]}\n'
        "intent 类别取第一个动作所属：文件操作/应用控制/系统设置/信息查询。"
    )

    def _llm_intent(self, text: str) -> dict | None:
        if not text:
            return None
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": self._SYSTEM_PROMPT},
                         {"role": "user", "content": text}],
            "temperature": getattr(self, "temperature", 0),
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=60) as resp:   # 复杂多步指令生成较慢
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(json)?|```$", "", content, flags=re.M).strip()
        parsed = json.loads(content)
        goals = parsed.get("goals")
        if not goals:                      # 旧式单动作输出 → 归一为 goals
            if not parsed.get("action"):
                return None
            goals = [{"action": parsed["action"],
                      "target": parsed.get("target", ""),
                      "params": parsed.get("params", {})}]
        goals = [g for g in goals if g.get("action")]
        if not goals:
            return None
        primary = goals[0]
        return {"intent": parsed.get("intent")
                or _CATEGORIES.get(primary["action"], "文件操作"),
                "action": primary["action"], "target": primary.get("target", ""),
                "params": primary.get("params", {}),
                "goals": goals, "confidence": parsed.get("confidence", 0.95),
                "unresolved": parsed.get("unresolved", [])}

    # ---------------------------------------------------------- --
    # 规则路径（Mock）
    # ---------------------------------------------------------- --
    def _rule_intent(self, text: str) -> dict:
        goals = _match_goals(text)
        quote = _QUOTE_RE.search(text)
        if not goals:
            return {"intent": "信息查询", "action": "", "target": quote.group(1) if quote else "",
                    "params": {}, "confidence": 0.3,
                    "unresolved": ["未能从话语中识别出任何目标动作"],
                    "goals": []}
        primary = goals[0]
        params = _extract_params(primary["action"], text)
        target = (quote.group(1) if quote
                  else params.get("path") or params.get("city") or params.get("url") or "")
        confidence = 0.9 if len(goals) == 1 else 0.8
        return {"intent": _CATEGORIES.get(primary["action"], "文件操作"),
                "action": primary["action"], "target": target,
                "target_label": quote.group(1) if quote else target,
                "params": params, "confidence": confidence,
                "unresolved": [], "goals": goals}
