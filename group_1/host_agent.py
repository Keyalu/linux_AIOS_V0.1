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

from src import llm_client   # 修复/生成类 LLM 调用共用同一配置源（便于测试替身）

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- --
# 规则词典（Mock 路径）：意图类别 / 动作 / 参数抽取
# ---------------------------------------------------------------- --

_CATEGORIES = {
    "organize": "文件操作", "find_duplicates": "文件操作", "backup": "文件操作",
    "find_large_files": "文件操作", "find_files": "文件操作",
    "cleanup_temp": "文件操作", "delete_file": "文件操作",
    "disk_usage": "系统设置", "system_check": "系统设置",
    "open_url": "应用控制", "open_app": "应用控制", "click_element": "应用控制",
    "send_email": "应用控制",
    "navigate": "应用控制", "switch_window": "应用控制", "create_folder": "应用控制",
    "weather": "信息查询", "search": "信息查询", "translate": "信息查询",
    "web_search": "信息查询", "web_open": "应用控制", "web_click": "应用控制",
    "web_state": "信息查询", "web_extract": "信息查询", "answer": "信息查询",
    "web_close_browser": "应用控制",
}

# (正则, 动作, 描述) —— 顺序即优先级，靠前的更具体
_RULES: list[tuple[str, str, str]] = [
    (r"天气", "weather", "查询城市天气"),
    (r"翻译", "translate", "翻译文本"),
    (r"联网搜索|上网查|搜个|搜一下|搜索(?!文件)", "web_search",
     "浏览器打开搜索结果页（数据链走 mcp_search）"),
    (r"网页(的)?内容|页面(的)?内容", "web_extract", "抽取当前网页内容"),
    (r"关闭(自动化)?浏览器|退出(自动化)?浏览器", "web_close_browser", "优雅关闭自动化浏览器"),
    (r"告诉我|说说|介绍一下", "answer", "回答问题（LLM 解读数据）"),
    (r"总结|概括", "answer", "总结上一步结果（LLM）"),
    (r"整理|归类|分类", "organize", "把目录里的文件按类型分类到子文件夹"),
    (r"重复|查重|冗余", "find_duplicates", "找出内容重复的文件"),
    (r"备份|备个份", "backup", "把目标目录压缩备份"),
    (r"大文件", "find_large_files", "找出目录里最大的若干文件"),
    (r"磁盘|空间", "disk_usage", "查询磁盘使用情况"),
    (r"系统状态|体检|检查系统", "system_check", "检查系统状态"),
    (r"清理|清空", "cleanup_temp", "清理临时文件"),
    (r"删除|删掉", "delete_file", "删除文件或目录"),
    (r"发(一封)?邮件|发送邮件", "send_email", "发送电子邮件"),
    (r"(发给|发送给|发到)\S*@\S+", "send_email", "把文件/内容发送给指定邮箱"),
    (r"打开(网址|网页)|访问", "open_url", "打开网页"),
    (r"导航|前往|去到", "navigate", "在文件管理器中导航到路径"),
    (r"切换到|切换窗口|切到", "switch_window", "切换应用窗口"),
    (r"创建|新建", "create_folder", "创建文件夹"),
    (r"点击(?:网页|页面|浏览器)|(?:网页|页面|浏览器)(?:里|中|上)(?:的)?点击",
     "web_click", "在网页中点击元素（自动化浏览器）"),
    (r"打开(文件)?(路径|目录)|打开文件夹", "navigate",
     "在文件管理器中打开该路径（先开文件管理器再跳转）"),
    (r"点击|点一下|单击|双击", "click_element", "点击界面控件（按名称/角色定位）"),
    (r"打开|启动|运行", "open_app", "打开应用"),
    (r"找(?!到)|查找", "find_files", "按条件搜索文件"),
]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
_QUOTE_RE = re.compile(r"[「'\"]([^」'\"]+)[」'\"]")


def _match_goals(text: str) -> list[dict]:
    """按词典扫描多个动作；span 重叠时保留更具体的先命中项。"""
    hits: list[tuple[int, dict]] = []
    claimed: list[tuple[int, int]] = []
    for pattern, action, desc in _RULES:
        m = re.search(pattern, text)
        if not m:
            continue
        if any(st <= m.start() and m.end() <= en for st, en in claimed):
            continue                      # 命中位置被更具体的动作覆盖
        claimed.append((m.start(), m.end()))
        hits.append((m.start(), {"action": action, "description": desc}))
    # 通用"找"只是兜底：已有更具体的文件目标时丢弃
    if len(hits) > 1 and any(g["action"] == "find_files" for _, g in hits):
        hits = [h for h in hits if h[1]["action"] != "find_files"] or hits
    # 目标按"话语中出现位置"排序 —— 用户说"先A再B再C"，计划就按 A→B→C
    # 排，而不是按规则表顺序（长指令曾因此步骤顺序全乱）
    return [g for _, g in sorted(hits, key=lambda x: x[0])]


def _extract_params(action: str, text: str) -> dict:
    """按动作抽取参数（契约表 params 字段）。"""
    params: dict = {}
    if action == "weather":
        m = re.search(r"(?:查询|查|看看|看|一下|帮我)*(.*?)天气", text)
        params["city"] = (m.group(1) if m else "").strip(" 的「」，,。") or "北京"
    elif action in ("search", "web_search"):
        params["query"] = re.sub(r"^(帮我|请|麻烦)?(联网搜索|上网查|搜个|搜一下|搜索)",
                                 "", text).strip(" ，。,「」\"'") or text
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
    elif action == "click_element":
        # 控件名：优先引号包裹；否则取"点击"后的剩余实体（剥离角色词前缀）
        q = _QUOTE_RE.search(text)
        if q:
            params["name"] = q.group(1).strip(" ，。、")
        else:
            rest = re.sub(r"^(?:请|帮我|麻烦)?(?:点击|点一下|单击|双击)(?:一下)?", "", text)
            rest = re.sub(r"^(?:label|button|icon|图标|按钮|控件|菜单项)[，,、\s]*", "",
                          rest, flags=re.I)
            rest = re.sub(r"[，。、；:：]+$", "", rest).strip()
            if rest:
                params["name"] = rest
        # 可选角色：label/按钮/图标/菜单项 → AT-SPI role 名（控件树精确定位用）
        m_role = re.search(r"(?:label|button|icon|图标|按钮|菜单项)", text, re.I)
        if m_role:
            r = m_role.group(0).lower()
            params["role"] = {"图标": "icon", "按钮": "push button",
                              "菜单项": "menu item"}.get(r, r)
    elif action == "web_click":
        # 网页元素文本：取"点击"之后的实体（规则命中形如"点击网页里的X"/
        # "在页面中点击X"）；LLM 路径直接给 params.text 时此处不覆盖
        m = re.search(r"(?:点击(?:网页|页面|浏览器)|(?:网页|页面|浏览器)"
                      r"(?:里|中|上)?(?:的)?点击)", text)
        if m:
            rest = text[m.end():]
            rest = re.sub(r"^(?:里|中|上)?(?:的)?(?:这个|那个)?(?:链接|按钮)?",
                          "", rest)
            rest = re.sub(r"[，。、；:：]+$", "", rest).strip()
            if rest:
                params["text"] = rest
    elif action == "answer":
        # 问题文本：去掉触发词后的剩余；纯"总结一下"则问题留空（纯总结模式）
        m = re.search(r"(告诉我|说说|介绍一下|总结|概括)(一下)?", text)
        if m:
            rest = text[m.end():].strip(" 的「」，,。：:。")
            if rest:
                params["question"] = rest
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
    def understand_intent(self, user_input: str, default_target: str = ".",
                          memory: list | None = None) -> dict:
        """意图理解入口。memory：RAG 召回的相似历史轨迹（few-shot 注入）。"""
        user_input = (user_input or "").strip()
        intent = None
        if self.use_llm:
            try:
                intent = self._llm_intent(user_input, memory=memory)
                self.last_engine = f"llm:{self.model}"
            except Exception:
                time.sleep(2)
                try:                           # 瞬时限流/抖动重试一次
                    intent = self._llm_intent(user_input, memory=memory)
                    self.last_engine = f"llm:{self.model}"
                except Exception:
                    intent = None              # 仍失败 → 降级规则
        if intent is None:
            intent = self._rule_intent(user_input)
            self.last_engine = "rules"
        return self._finalize_intent(intent, user_input, default_target)

    def _finalize_intent(self, intent: dict, user_input: str,
                          default_target: str) -> dict:
        """意图归一尾段（确定性归一，红线 3 的主战场）：target 解析 /
        dry_run / 附件 / dest 等。understand_intent 与 repair_intent
        共用，保证反思修复产出的 intent 与原始意图同一口径。"""
        if not intent.get("target"):         # setdefault 对"键存在值为空"无效
            intent["target"] = default_target
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

        # 文件类动作（含截图/写入）的路径统一归一到默认工作区：
        # - 缺路径的目录级动作 → 工作区（工具内置默认如 ~/Downloads 可能不存在）
        # - '.'/'..' 与 shell 风格 $变量占位 → 丢弃/映射工作区
        # - 相对路径/裸文件名（hnu_intro.md）→ 工作区内
        # - 不存在的绝对路径（LLM 编造的 /data/out/…）→ 工作区 + 文件名
        # - 已存在的绝对路径 → 保留（用户明确指定的真实位置）
        # - 目录级动作工作区外且话语无迹可循 → 工作区（证据防线）
        for g in intent.get("goals", []):
            action = g.get("action")
            if action not in self._FILE_ACTIONS and action != "take_screenshot":
                continue
            is_dir_scope = action in self._DIR_SCOPE_ACTIONS
            for k in ("path", "src", "directory"):
                v = (g.get("params") or {}).get(k)
                if v in (None, ""):
                    if is_dir_scope:
                        g.setdefault("params", {})[k] = default_target
                    continue                              # 缺路径 → 工作区
                v = str(v).strip()
                if re.fullmatch(r"\$\{?\w+\}?", v):
                    g["params"].pop(k, None)              # 变量占位 → 兜底填充
                    continue
                if v in (".", "./", "..", "../") and is_dir_scope:
                    g["params"][k] = default_target
                    continue
                v = os.path.expanduser(v)
                if os.path.isabs(v) and os.path.exists(v):
                    r = v                                 # 真实位置 → 保留
                elif os.path.isabs(v):
                    # 不存在的绝对路径：父目录真实 → 合法的计划输出文件，保留；
                    # 父目录也不存在（幻觉前缀如 /data/out）→ 工作区 + 文件名
                    parent = os.path.dirname(v.rstrip("/\\"))
                    r = v if os.path.isdir(parent) else os.path.join(
                        default_target, os.path.basename(v.rstrip("/\\")))
                else:
                    r = os.path.join(default_target, v)   # 相对 → 工作区内
                # 目录级动作的目标必须真实存在（organize 不建根目录）——
                # "整理 工作区文件夹"被拼成不存在的工作区/工作区 → 回落根目录
                if is_dir_scope and r != default_target \
                        and not os.path.isdir(r):
                    r = default_target
                if (is_dir_scope and r != default_target
                        and not r.startswith(default_target.rstrip("/") + "/")
                        and os.path.basename(r.rstrip("/\\"))
                        not in (user_input or "")):
                    r = default_target                    # 证据防线
                g["params"][k] = r
            d = (g.get("params") or {}).get("dest")
            if d and re.fullmatch(r"\$\{?\w+\}?", str(d).strip()):
                g["params"].pop("dest", None)     # 变量占位 → 组2 重派生
                d = None
            if d:
                src_v = (g.get("params") or {}).get("src") or default_target
                nd = self._normalize_dest(d, default_target, src_v)
                if nd is not None:
                    # LLM 幻觉目的地的最后防线：工作区外的绝对 dest 只有在
                    # 用户话语里有迹可循（父目录名/文件名被用户提及）才接受，
                    # 否则一律锚回工作区 —— 曾致备份 zip 落到桌面致测试失败
                    nd_parent = os.path.basename(os.path.dirname(nd.rstrip("/\\")))
                    nd_tail = os.path.basename(nd.rstrip("/\\"))
                    if not (nd_parent in (user_input or "")
                            or nd_tail in (user_input or "")):
                        nd = os.path.join(os.path.dirname(default_target),
                                          os.path.basename(nd.rstrip("/\\")))
                if nd is None:
                    g["params"].pop("dest", None)   # 幻觉路径 → 组2 重派生
                else:
                    g["params"]["dest"] = nd

        # send_email 附件确定性归一（在路径归一之后执行，三级来源）：
        # 1) 话语里出现的真实文件路径；2) 计划内其他目标将产出的文件
        # （截图路径/写入的报告/备份 zip）；3) LLM 给的真实存在路径。
        # 占位符（{{prev_result}}/$var）与不存在的路径一律丢弃 ——
        # 附件在执行期由 send_email 自行校验，缺文件时如实报错
        def _planned_files() -> list:
            files = []
            for g in intent.get("goals", []):
                gp = g.get("params") or {}
                if g.get("action") in ("take_screenshot", "write_file") \
                        and gp.get("path"):
                    files.append(str(gp["path"]))
                if g.get("action") in ("backup", "backup_directory"):
                    # 镜像组2 的派生口径：src → dest=src_backup → zip
                    d = str(gp.get("dest") or "").strip()
                    src = str(gp.get("src") or "").strip() or default_target
                    base = d or src.rstrip("/\\") + "_backup"
                    if not os.path.isabs(base):
                        base = os.path.join(os.path.dirname(default_target), base)
                    files.append(base.rstrip("/\\") + ".zip")
            return files

        planned = _planned_files()
        m_file = re.search(
            r"(/[\w.\-\u4e00-\u9fa5]+)+[/\w.\-\u4e00-\u9fa5]*\."
            r"(md|txt|pdf|docx|csv|json|py|log|xlsx|png|jpg)",
            user_input)
        # 1) 计划内产物优先（截图/报告/备份 zip —— 执行期依序产生）；
        # 2) LLM/话语给的额外真实文件按绝对路径补充，与计划产物同名者去重
        #    （裸文件名是进程 CWD 旧副本，会被计划产物的绝对路径取代）
        wanted: list = []
        seen_base: set = set()
        for f in planned:
            if f not in wanted:
                wanted.append(f)
                seen_base.add(os.path.basename(f))
        extra: list = []
        for g in intent.get("goals", []):
            if g.get("action") != "send_email":
                continue
            att = (g.get("params") or {}).get("attachment")
            for a in (att if isinstance(att, (list, tuple))
                      else ([att] if att else [])):
                extra.append(str(a).strip())
        if m_file:
            extra.append(m_file.group(0))
        for a in extra:
            if not a or "{{" in a or re.fullmatch(r"\$\{?\w+\}?", a):
                continue
            a_abs = os.path.abspath(os.path.expanduser(a))
            if os.path.isfile(a_abs) \
                    and os.path.basename(a_abs) not in seen_base:
                wanted.append(a_abs)
                seen_base.add(os.path.basename(a_abs))
        for g in intent.get("goals", []):
            if g.get("action") == "send_email":
                if wanted:
                    g.setdefault("params", {})["attachment"] = \
                        wanted[0] if len(wanted) == 1 else list(wanted)
                else:
                    g["params"].pop("attachment", None)   # 占位符全灭 → 不附
        if intent.get("action") == "send_email":
            if wanted:
                intent.setdefault("params", {})["attachment"] = \
                    wanted[0] if len(wanted) == 1 else list(wanted)
            else:
                intent["params"].pop("attachment", None)

        intent["user_text"] = user_input
        intent["intent_id"] = f"intent-{uuid.uuid4().hex[:8]}"
        intent["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        intent["language"] = "zh-CN"
        return intent

    _FILE_ACTIONS = {"organize", "find_duplicates", "backup", "find_large_files",
                     "find_files", "cleanup_temp", "delete_file",
                     "organize_downloads", "find_duplicate_files",
                     "backup_directory", "write_file", "take_screenshot"}
    # 作用域为整个目录的动作：'.'/'..' 必须映射到工作区，禁止落到进程 CWD
    _DIR_SCOPE_ACTIONS = {"organize", "find_duplicates", "backup",
                          "find_large_files", "cleanup_temp",
                          "organize_downloads", "find_duplicate_files",
                          "backup_directory"}

    def detect_operation(self, user_input: str) -> dict | None:
        """对话路由检测：规则引擎识别系统操作意图（确定性、零 token）。

        命中返回 intent（调用方交编排管线，须走确认流），未命中返回
        None（走纯对话）。answer 归对话本身的能力，不参与路由——
        避免日常聊天频繁弹出执行卡片。"""
        # 保守门：规则引擎只够格解析"短而单一"的操作指令。长/复杂文本
        # 会被关键词搅成垃圾计划（实战：9 目标长指令被解析成 query=全文）
        if len(user_input.strip()) > 60:
            return None
        intent = self._rule_intent(user_input)
        goals = [g for g in intent.get("goals", [])
                 if g.get("action") != "answer"]
        if not goals or len(goals) > 2:
            return None
        intent["goals"] = goals
        intent["action"] = goals[0]["action"]
        intent["user_text"] = user_input
        return intent

    def repair_intent(self, user_input: str, failed_steps: list[dict],
                      tool_menu: list[str],
                      original_intent: dict | None = None) -> dict | None:
        """失败反思（智能体闭环）：把失败步骤喂给 LLM 产出修正 goals。

        返回的 intent 与原始计划同等不可信——调用方必须重新过安全闸门、
        决策表与 schema 校验后再执行。无法修复（LLM 不可用/输出空计划）
        返回 None，调用方保持首次结果。"""
        if not (failed_steps and tool_menu):
            return None
        prompt = (
            "用户的任务计划里有步骤执行失败。请参考可用工具清单，输出修正后的计划。\n"
            '只输出 JSON：{"goals":[{"action":"..","target":"..","params":{..}}]}\n'
            "可以换工具、改参数、换路径；确实无法修复时输出 {\"goals\":[]}\n\n"
            f"原始指令: {user_input}\n"
            f"失败步骤: {json.dumps(failed_steps, ensure_ascii=False)}\n"
            f"可用工具: {', '.join(tool_menu)}"
        )
        raw = llm_client.chat(prompt, timeout=45, max_input_chars=5000)
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        goals = [g for g in (parsed.get("goals") or []) if g.get("action")]
        if not goals:
            return None
        intent = {"intent": "修复重试", "action": goals[0]["action"],
                  "target": (original_intent or {}).get("target", ""),
                  "params": goals[0].get("params", {}),
                  "confidence": 0.5, "user_text": user_input,
                  "goals": goals}
        # 与 understand_intent 共用归一尾段（dest/dry_run/附件/路径全口径）
        return self._finalize_intent(
            intent, user_input,
            (original_intent or {}).get("target", "."))

    @staticmethod
    def _normalize_dest(dest: str, default_target: str, src: str) -> str | None:
        """备份/输出的 dest 归一。返回 None = 丢弃（交由组2 重派生）。

        - 绝对 dest：父目录真实即接受（输出路径允许尚不存在）。此前对
          "绝对但不存在"无脑回退 default_target，会把 dest 改写回源目录，
          zip 名错位（test_s6 失败根因之一）；
        - 相对 dest：锚定到 src 同级（工作区内）。此前原样放行，
          make_archive 按 CWD 落盘，demo_task_backup.zip 曾被写进项目根。
        """
        d = os.path.expanduser(str(dest))
        if os.path.isabs(d):
            return d if os.path.isdir(os.path.dirname(d.rstrip("/\\"))) else None
        s = os.path.expanduser(str(src))
        base = os.path.dirname(s) if os.path.isabs(s) \
            else os.path.dirname(default_target)
        return os.path.join(base, d)

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
        '格式：{"intent":"类别","target":"","confidence":0.9,"goals":'
        '[{"action":"..","target":"..","params":{..}}]}，多动作按执行顺序排列。\n'
        "可用 action：organize/find_duplicates/backup/find_large_files/find_files/"
        "cleanup_temp/delete_file/disk_usage/system_check/open_url/open_app/"
        "send_email/weather/search/translate/write_file/run_command/"
        "navigate/create_folder/switch_window/take_screenshot/click_element/"
        "web_search/web_open/web_click/web_state/web_extract/llm_answer/"
        "web_close_browser\n"
        "工具参数：write_file(path,content) run_command(cmd[,timeout]) open_url(url) "
        "open_app(app) send_email(to,subject,body,attachment[文件路径],"
        "dry_run[用户明确要发=false]) take_screenshot([path]) mcp_weather(city[,days]) "
        "mcp_search(query) mcp_translate(text,to_lang) web_search(query[,engine]) "
        "web_open(url) web_click(text) web_state() web_extract() "
        "llm_answer([question,text]) click_element(name[,role])\n"
        "桌面 GUI：打开/启动应用=open_app(app)；点击界面控件=click_element(name[,role])；"
        "打开/跳转到某个文件路径或目录=navigate(path)（先打开文件管理器再跳转，不要用 run_command）；"
        "导航=navigate(path)；创建文件夹=create_folder(name)；切换窗口=switch_window(app)；"
        "截屏=take_screenshot(path，随后的 send_email/write_file 引用同一路径)\n"
        "网页与搜索：看搜索结果页/后续要点击=web_search(query)；"
        "要数据(写文件/回答)=mcp_search(query)；"
        "点击第N条搜索结果=web_search(query)后接 web_click(nth=N)；"
        "点击页面上指定文字的元素=web_click(text)；"
        "打开具体网址=web_open(url)或open_url(url)；"
        "读当前页面内容=web_extract()；回答问题/总结=llm_answer(question[,text])；"
        "关闭自动化浏览器=web_close_browser()（不要用 run_command 跑 pkill）\n"
        "边界：网页内容不在桌面控件树里，不要用 click_element 点网页元素\n"
        "数据依赖：后一步参数需要前一步输出时写占位符 {{prev_result}}\n"
        "示例1:搜索智能体并摘录写入 a.md → goals:[{action:mcp_search,"
        "params:{query:智能体}},{action:write_file,params:{path:a.md,"
        "content:{{prev_result}}}}]（裸文件名自动存入工作区）\n"
        "示例2:点击label，文件 → goals:[{action:click_element,"
        "params:{name:文件,role:label}}]\n"
        "intent 类别：文件操作/应用控制/系统设置/信息查询，取第一个动作所属\n"
    )

    def _llm_intent(self, text: str, memory: list | None = None) -> dict | None:
        if not text:
            return None
        system = self._SYSTEM_PROMPT
        if memory:
            # RAG 记忆注入：相似历史轨迹作 few-shot 参考（动作选择借鉴，
            # 参数不照抄）——用户的通道偏好与纠正由此沉淀生效
            refs = "\n".join(
                f"- 输入:{(m.get('input') or '')[:60]} → "
                f"动作:{m.get('actions')}（{(m.get('result') or '')[:40]}）"
                for m in memory[:2])
            system += ("\n相似历史任务的处理记录（供参考动作选择，不要照抄参数）:\n"
                       + refs)
        # OpenAI 兼容 chat/completions 请求体（urllib 直连，零 SDK 依赖）
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": text}],
            "temperature": getattr(self, "temperature", 0),
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        # 复杂多目标指令的 JSON 生成实测可超 60s（自由档模型 ~68s），
        # 超时会整体降级规则引擎 → 长指令被搅成垃圾计划
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"].strip()
        # 剥掉 LLM 可能包裹的 markdown 代码栏再解析
        content = re.sub(r"^```(json)?|```$", "", content, flags=re.M).strip()
        parsed = json.loads(content)
        goals = parsed.get("goals")
        if not goals:                      # 旧式单动作输出 → 归一为 goals
            if not parsed.get("action"):
                return None
            goals = [{"action": parsed["action"],
                      "target": parsed.get("target", ""),
                      "params": parsed.get("params", {})}]
        # 丢弃没有动作的空 goal
        goals = [g for g in goals if g.get("action")]
        if not goals:
            return None
        # intent/action/params 取第一个 goal（主目标），全量 goals 原样带出
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
                  else params.get("name") or params.get("path") or params.get("city")
                  or params.get("url") or "")
        confidence = 0.9 if len(goals) == 1 else 0.8
        return {"intent": _CATEGORIES.get(primary["action"], "文件操作"),
                "action": primary["action"], "target": target,
                "target_label": quote.group(1) if quote else target,
                "params": params, "confidence": confidence,
                "unresolved": [], "goals": goals}
