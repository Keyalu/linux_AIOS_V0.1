"""server.py — Agent_OS_v1.0 系统控制台（纯标准库，零第三方依赖）。

给五组集成系统套一层本地 Web 控制台：
  - 编排控制台：自然语言 → 组5 SystemCoordinator 全链路编排，五阶段可视化
  - 工具台：组4 能力清单（工具+技能 schema 内省），选身份发起真实调用
  - 统计：系统账本（system_out/stats.json，累积式）逐笔/聚合展示
  - RAG 知识库：执行轨迹检索（TF 余弦）

运行：
    python gui/server.py                # 默认 http://127.0.0.1:8788
    python gui/server.py --port 9000
安全说明：编排与工具调用都是真实 OS 操作；服务只绑定 127.0.0.1，
权限闸门、黑名单、受保护路径拦截与生产代码完全一致。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
import uuid
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from group_1 import HostAgent                     # noqa: E402
from group_2 import TaskPlanner, ControlDetector  # noqa: E402
from group_3 import AppAgent                      # noqa: E402
from group_4 import make_stack, schema_catalog    # noqa: E402
from group_5 import SecuritySandbox, RAGKnowledgeBase, SystemCoordinator  # noqa: E402
from src.interfaces import PermissionLevel        # noqa: E402
from src.capability_hints import (                 # noqa: E402
    capability_hint, lookup_bare_capability, required_params)
from src.event_stream import load_events           # noqa: E402
from src.safe_io import write_json                 # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "system_out")
STATS_PATH = os.path.join(OUT, "stats.json")
INDEX_PATH = os.path.join(HERE, "index.html")

# 进程启动时间 + 第 3 层实现的"版本标签"（页面底与 /api/about 会显示）。
# 用途：判断"改了代码之后到底重启成功没"——启动时间早于源文件修改时间就是没生效。
BOOT_AT = time.strftime("%Y-%m-%d %H:%M:%S")
BOOT_EPOCH = time.time()
LAYER3_TAG = "层3=结构化字节码摘要+注册表当前对象核对(源文件漂移仅提示)"


def _source_mtime(rel: str) -> str:
    """磁盘上某个源文件的修改时间（用于和进程启动时间比对）。"""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(os.stat(os.path.join(ROOT, rel)).st_mtime))
    except OSError:
        return "?"


def _stale_sources() -> list[str]:
    """比进程启动时间**更新**的源文件 —— 即"改了但没重启"的那些。"""
    out = []
    for rel in ("gui/server.py", "group_5/security.py", "group_1/host_agent.py",
                "src/real_tools.py", "group_2/task_planner.py",
                "group_3/app_agent.py"):
        try:
            if os.stat(os.path.join(ROOT, rel)).st_mtime > BOOT_EPOCH:
                out.append(rel)
        except OSError:
            continue
    return out

LEVELS = {"public": PermissionLevel.PUBLIC, "user": PermissionLevel.USER,
          "admin": PermissionLevel.ADMIN}

_LOCK = threading.Lock()             # 编排/调用串行化，避免并发踩账本


def _safe_int(value, default: int = 0) -> int:
    """宽松取整：查询串里的坏值不该把 GET 打成连接中断（早期 int() 裸调用）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mask_secret(value) -> str:
    """凭据掩码：只报"已配置 + 长度"，不回显任何真实字符。

    早期实现回显了前 4 位与后 4 位真实字符（key 前缀 + 星号 + 后缀），
    等于把 8 位真实字符送给任何能访问 /api/llm、/api/smtp_config 的调用方。
    """
    s = str(value or "")
    return f"已配置（{len(s)} 字符，已隐藏）" if s else ""


STATS_OBJ = None                     # 在跑的统计实例（/api/reset_stats 需清内存态）


def build_system() -> SystemCoordinator:
    """与 main.build 相同的接线（gui 内自持一份，避免连带 argparse）。"""
    global STATS_OBJ
    os.makedirs(OUT, exist_ok=True)
    from src.stats import ToolStats
    stats, registry, skills = make_stack(
        stats=ToolStats(log_path=STATS_PATH))
    STATS_OBJ = stats
    catalog = schema_catalog(registry, skills)
    detector = ControlDetector()
    security = SecuritySandbox()
    # 第 3 层（签名验证）落地：组5 不 import 组4，由编排层把组4 的函数对象
    # 作为**数据**注入登记（早期 register_signature 全项目零调用点）。
    try:
        security.register_signatures(registry.functions())
        security.register_signatures(skills.functions())
        # 修复逻辑审计 #17：注入"注册表当前函数"取数回调（数据注入，不 import）
        security.register_live_source(
            lambda: {**registry.functions(), **skills.functions()})
    except Exception:
        pass
    # 修复逻辑审计 #12：统计账本逐笔落盘前过第 4 层脱敏（与 main.build 同款
    # 数据注入 —— stats 不 import 组5）。
    stats.set_redactor(security.mask_record)
    return SystemCoordinator(
        HostAgent(), TaskPlanner(schemas=catalog, detector=detector),
        AppAgent(registry, skills), detector=detector,
        security=security, rag=RAGKnowledgeBase(os.path.join(OUT, "rag_traces.jsonl")),
        audit_path=os.path.join(OUT, "audit_log.jsonl"),
        schemas_catalog=catalog, default_target=os.path.join(OUT, "demo_task"),
        artifact_dir=OUT)


SETTINGS_PATH = os.path.join(OUT, "settings.json")


def _mask_deep(data):
    """第 4 层脱敏（修复逻辑审计 #12）：run["bundle"] / 聊天记录等出口统一
    过 security.mask_record —— 内存态 bundle 原样携带 params_sent 与工具结果，
    经 /api/run 直接回显。脱敏失败时原样返回（不拖垮响应）。"""
    try:
        return SYSTEM.security.mask_record(data)
    except Exception:
        return data


SETTINGS_DEFAULT = {
    "llm_engine": "llm",          # llm | rules
    "llm_temperature": 0,         # 0~1
    "default_level": "user",      # public | user | admin
    "escalation_confirm": "auto", # auto | deny
    "planner_finale": True,       # 计划末尾附加提权演示收尾步
    "action_delay": 0.3,          # GUI 动作间隔秒
    "protected_extra": [],        # 追加受保护路径
    "rag_k": 3,                   # RAG 默认返回条数
    "workspace": "",              # 默认工作区（空 = system_out/demo_task）
}


def load_settings() -> dict:
    s = dict(SETTINGS_DEFAULT)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            user = json.load(f)
        for k in SETTINGS_DEFAULT:
            if k in user:
                s[k] = user[k]
    except (OSError, json.JSONDecodeError):
        pass
    return s


def save_settings(s: dict) -> None:
    os.makedirs(OUT, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def apply_settings(s: dict) -> None:
    """把设置安全地应用到在跑模块（逐项容错，坏值不影响系统）。"""
    # 目标是"在跑的模块实例"：热更新立即生效，无需重启服务
    host = SYSTEM.modules[1]
    agent = SYSTEM.modules[3]
    planner = SYSTEM.modules[2]
    security = SYSTEM.security
    try:
        host.use_llm = (s["llm_engine"] == "llm") and bool(host.api_key)
    except Exception:
        pass
    try:
        host.temperature = max(0.0, min(float(s["llm_temperature"]), 1.0))
    except Exception:
        pass
    try:
        agent.level = LEVELS.get(str(s["default_level"]).lower(), PermissionLevel.USER)
    except Exception:
        pass
    try:
        mode = s.get("escalation_confirm")
        # auto_escalate 走"无人值守自动同意"通道（审计里 confirm_mode="auto"）；
        # 设置页选 deny 时走拒绝通道。不再用两个恒定 lambda 把二者混为一谈。
        if getattr(agent, "auto_escalate", None) is not None:
            agent.auto_escalate = (mode == "auto")
            agent.confirmer = None
    except Exception:
        pass
    try:
        planner.add_finale = bool(s["planner_finale"])
    except Exception:
        pass
    try:
        agent.automator.action_delay = max(0.0, float(s["action_delay"]))
    except Exception:
        pass
    try:
        extra = [p for p in s["protected_extra"] if isinstance(p, str) and p.strip()]
        security.protected = list(security._base_protected)
        security.add_protected(extra)
    except Exception:
        pass
    try:
        # 默认工作区：文件操作（整理/备份/保存/打开目录）归一层的锚点。
        # 空值回落 system_out/demo_task；设置即时生效并自动创建目录
        ws = os.path.expanduser(str(s.get("workspace") or "").strip())
        if ws:
            os.makedirs(ws, exist_ok=True)
            SYSTEM.default_target = ws
        else:
            SYSTEM.default_target = os.path.join(OUT, "demo_task")
    except Exception:
        pass


SYSTEM = build_system()

# ── SMTP 配置持久化：重启后邮件服务默认可用 ──
SMTP_CONFIG_PATH = os.path.join(OUT, "smtp_config.json")
CHAT_HISTORY_PATH = os.path.join(OUT, "chat_history.json")


# 启动时把持久化的 SMTP 凭据灌进环境变量（send_email 真实发送时兜底读取）
def load_smtp_config() -> None:
    try:
        with open(SMTP_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in cfg.items():
            if v:
                os.environ[k] = str(v)
    except (OSError, json.JSONDecodeError):
        pass


load_smtp_config()

# ── 后台运行注册表：编排线程化，前端轮询事件流 ──
RUNS: dict[str, dict] = {}
RUNS_LOCK = threading.Lock()
PENDING: dict[str, dict] = {}          # 待确认令牌 -> {text, intent, plan, check}
PENDING_TTL = 1800                     # 待确认令牌有效期（秒）
PENDING_MAX = 32                       # 同时挂起的确认令牌上限
_CONTROLS_TTL = 3.0                    # /api/controls 桌面扫描缓存（审计 #16）
_CONTROLS_CACHE: dict = {"ts": 0.0, "data": None}


def _purge_pending() -> None:
    """清理过期/超量的待确认令牌（早期 PENDING 无过期、无上限，长期驻留内存）。"""
    now = time.time()
    for tok, info in list(PENDING.items()):
        if now - float(info.get("_ts") or now) > PENDING_TTL:
            PENDING.pop(tok, None)
    if len(PENDING) > PENDING_MAX:
        for tok in list(PENDING)[:len(PENDING) - PENDING_MAX]:
            PENDING.pop(tok, None)


def _emit(run: dict, stage: str, **data) -> None:
    run["events"].append({"time": time.strftime("%H:%M:%S"), "stage": stage, **data})


def _start_run(text: str = None, confirm_mode: str = "auto",
               staged: dict | None = None, source: str = "console") -> str:
    """启动一次编排运行。

    staged 给定（用户已确认的方案）时直接执行该方案，零重新规划、
    零 LLM 调用 —— 用户确认的方案 = 实际执行的方案；
    staged 为空（无需确认的低风险指令）时在线程内先 plan_only。
    """
    # 每次运行一个 run_id：前端凭它轮询事件流
    rid = uuid.uuid4().hex[:8]
    run = {"id": rid, "status": "running", "events": [], "bundle": None,
           "text": text or (staged or {}).get("text", ""),
           "source": source, "time": time.strftime("%H:%M:%S"),
           "verdict": None, "summary": None}
    with RUNS_LOCK:
        RUNS[rid] = run
    # 只保留最近 50 次运行：按**插入顺序**淘汰（dict 天然有序）。
    # 早期用 sorted(RUNS) —— run_id 是随机 hex，等于随机淘汰，可能把刚完成
    # 的那次丢掉、把最老的留下。
    if len(RUNS) > 50:
        finished = [k for k, v in list(RUNS.items())
                    if v.get("status") != "running"]
        for rid_old in finished[:max(0, len(RUNS) - 50)]:
            RUNS.pop(rid_old, None)

    # 编排线程：plan_only（或直接执行已确认的 staged 方案）→ execute_approved → 事件回传
    def worker():
        try:
            with _LOCK:
                if staged is not None and staged.get("intent") and staged.get("plan"):
                    intent = staged["intent"]
                    plan = staged["plan"]
                    _emit(run, "intent", action=intent.get("action"),
                          engine=getattr(SYSTEM.modules[1], "last_engine", "?"))
                    _emit(run, "security", approved=staged["check"]["approved"],
                          risk=staged["check"].get("risk_level"))
                    _emit(run, "plan", steps=len(plan.get("steps", [])))
                else:
                    st = SYSTEM.plan_only(text)
                    _emit(run, "intent", action=st["intent"].get("action"),
                          engine=getattr(SYSTEM.modules[1], "last_engine", "?"))
                    _emit(run, "security", approved=st["check"]["approved"],
                          risk=st["check"].get("risk_level"))
                    if st.get("rejected"):
                        run["status"] = "rejected"
                        run["bundle"] = _mask_deep(st)
                        run["verdict"] = "拦截"
                        _emit(run, "rejected", reason=st["rejected"])
                        return
                    _emit(run, "plan", steps=len(st["plan"].get("steps", [])))
                    intent, plan = st["intent"], st["plan"]
                bundle = SYSTEM.execute_approved(
                    intent, plan,
                    event_sink=lambda e: _emit(run, e["stage"],
                                               **{k: v for k, v in e.items()
                                                  if k != "stage"}),
                    confirm_mode=confirm_mode)
                # 计划安全复检拦截：execute_approved 返回的 bundle 没有
                # session/audit，直接取键会 KeyError（间歇性"failed"的根因）
                if bundle.get("rejected"):
                    bundle["stats_detail"] = stats_payload()
                    run["bundle"] = _mask_deep(bundle)
                    run["status"] = "rejected"
                    run["verdict"] = "拦截"
                    _emit(run, "rejected", reason=bundle["rejected"])
                    return
                # 读账本放进锁内：与编排内的工具调用串行，避免读到上一笔的旧账
                bundle["stats_detail"] = stats_payload()
            run["bundle"] = _mask_deep(bundle)
            run["status"] = "done"
            run["verdict"] = bundle["audit"]["verdict"]
            run["summary"] = bundle["session"]["summary"]
            _emit(run, "done", verdict=bundle["audit"]["verdict"])
        except Exception as e:
            run["status"] = "failed"
            run["error"] = f"{type(e).__name__}: {e}"
            # 编排线程异常必须留审计痕迹（早期只写 run["error"]，日志里什么都没有）
            try:
                SYSTEM.audit({"stage": "run_failed", "error": run["error"],
                              "text": run.get("text", "")})
            except Exception:
                pass
    threading.Thread(target=worker, daemon=True).start()
    return rid


SETTINGS = load_settings()
apply_settings(SETTINGS)


def stats_payload(max_records: int = 200) -> dict:
    """系统账本（system_out/stats.json）读取 + 聚合。"""
    payload: dict = {"records": [], "counters": {}, "updated": None,
                     "path": "system_out/stats.json"}
    try:
        with open(STATS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        payload["records"] = data.get("records", [])[-max_records:]
        payload["counters"] = data.get("counters", {})
        payload["updated"] = data.get("updated")
    except (OSError, json.JSONDecodeError):
        pass
    total = sum(c.get("calls", 0) for c in payload["counters"].values())
    ok = sum(c.get("success", 0) for c in payload["counters"].values())
    payload["summary"] = {"total": total, "ok": ok, "fail": total - ok,
                          "rate": round(100 * ok / total, 1) if total else 0.0}
    return payload


# ──────────────────────────────────────────────────────────────────────
# 对话助手：真实能力自述 + 真实数据问答
#
# 设计意图：让助手的回答**只来自本系统真实具备的能力与真实查询到的数据**。
# 早期实现只给了一句"你是控制台对话助手"的 system prompt，LLM 于是把通用
# 助手的能力（管理 Agent/任务/会话、检索日志…）套了过来 —— 那些业务本系统
# 一个都没有。现在把"运行时生成的能力清单"钉进上下文，并对能力类/状态类
# 提问走确定性通道。
# ──────────────────────────────────────────────────────────────────────
_CAP_CACHE: dict = {"sig": None, "text": ""}

# 控制台**真实可查**的数据（与 _system_facts 的实现一一对应）
_QUERYABLE = [
    ("调用统计", "组4 逐笔原子落账 stats.json：总调用/成功/失败/成功率/按接口聚合"),
    ("运行历史", "本进程最近 50 次编排：指令、来源、状态、审计 verdict、步数"),
    ("知识库轨迹", "组5 RAG rag_traces.jsonl：输入话语+动作序列+结果，支持余弦检索"),
    ("控件树后端", "组2 控件检测当前后端（pyatspi / atspi-gi / mock）"),
    ("系统设置", "LLM 引擎与温度、默认身份、提权确认策略、动作间隔、受保护路径、工作区、RAG 条数"),
    ("配置状态", "LLM Key / SMTP 凭据是否已配置（只报长度，不回显字符）"),
]

# 明确"本系统做不到"的事（防止把通用助手的能力套过来）
_NOT_SUPPORTED = [
    "多智能体/子智能体管理、任务队列与调度（没有任务调度器，也没有 Agent 管理接口）",
    "会话管理与会话检索（只有浏览器里的对话记录，落盘为 chat_history.json，无检索接口）",
    "日志检索（没有日志查询工具；只有审计 JSONL audit_log.jsonl 与运行历史，且在页面上查看）",
    "通用爬虫/批量抓取：网页能力仅限自动化浏览器（CDP）通道，未检测到浏览器时以 [Mock·未执行] 如实失败",
    "读取受保护/敏感路径（系统目录、主目录根、~/.ssh 等凭据区）",
    "绕过确认流执行提权操作（ADMIN 步骤需经确认；无人值守时按设置自动同意并在审计标注 confirm_mode=auto）",
]


def _browser_available() -> bool:
    """本机是否存在可用的 Chromium 系自动化浏览器（只查文件，不做端口探测）。"""
    env = os.environ.get("AIOS_WEB_BROWSER")
    if env and os.path.exists(env):
        return True
    return any(os.path.exists(p) or shutil.which(p)
               for p in ("/snap/bin/chromium", "chromium-browser", "chromium",
                         "google-chrome", "/usr/bin/google-chrome-stable"))


def _capability_brief() -> str:
    """按**当前真实装配**生成能力自述（不写死、不夸大）；带签名缓存。"""
    try:
        tools = sorted(s.to_dict()["name"]
                       for s in SYSTEM.modules[3].registry.list_tools())
        skills = sorted(s.to_dict()["name"]
                        for s in SYSTEM.modules[3].skills.list_skills())
    except Exception as e:
        return (f"（能力清单读取失败：{type(e).__name__}）"
                "请到「工具台」页签查看组4 schema 内省结果")
    use_llm = bool(getattr(SYSTEM.modules[1], "use_llm", False))
    browser = _browser_available()
    sig = (tuple(tools), tuple(skills), SETTINGS.get("llm_engine"),
           SETTINGS.get("default_level"), SETTINGS.get("escalation_confirm"),
           SETTINGS.get("workspace"), use_llm, browser)
    if _CAP_CACHE["sig"] == sig:
        return _CAP_CACHE["text"]

    engine = (f"LLM（{SYSTEM.modules[1].model}）" if use_llm
              else "规则引擎（未配置或未启用 LLM Key）")

    def _code(names) -> str:
        return " ".join(f"`{n}`" for n in names)

    lines = [
        f"**运行态**：意图引擎={engine}；默认执行身份={SETTINGS.get('default_level')}；"
        f"提权确认={SETTINGS.get('escalation_confirm')}；"
        f"工作区={SETTINGS.get('workspace') or 'system_out/demo_task'}",
        "",
        f"**可真实执行的工具（{len(tools)}）**：{_code(tools)}",
        "",
        f"**可真实执行的技能（{len(skills)}）**：{_code(skills)}",
        "",
        "**控制台可查的真实数据**：",
        *[f"- {n}：{d}" for n, d in _QUERYABLE],
        "",
        "**本系统明确做不到**（清单外的一律回答“不支持”，不会假装能做）：",
        *[f"- {x}" for x in _NOT_SUPPORTED],
        "",
        ("**浏览器自动化**：本机**未**检测到 Chromium 系浏览器 —— `web_*` 工具会以 "
         "`[Mock·未执行]` 如实失败，不会假装成功。"
         if not browser else
         "**浏览器自动化**：本机检测到 Chromium 系浏览器，`web_*` 走 CDP 真实通道。"),
        "",
        "**操作如何真正落地**：自然语言 → 编排控制台（组1 意图 → 组5 安全 → 组2 规划 "
        "→ 组3 执行 → 组4 工具 ←→ 组5 审计/RAG）；高影响动作需在确认面板批准。",
    ]
    text = "\n".join(lines)
    _CAP_CACHE.update({"sig": sig, "text": text})
    return text


_CAP_Q_RE = re.compile(
    r"你能做什么|能做什么|你会什么|你的功能|有什么功能|有哪些功能|能帮我做什么|"
    r"介绍一下你|自我介绍|你是谁|支持哪些|能力清单|可以做什么", re.I)


def _is_capability_question(text: str) -> bool:
    """"你能做什么 / 你是谁 / 有哪些功能" 类提问 → 走确定性清单（零 LLM）。"""
    return bool(_CAP_Q_RE.search(str(text or "").strip()))


def _system_facts(question: str) -> str:
    """按提问关键词取**真实数据**（只读、廉价）；无匹配返回 ""（= 普通闲聊）。

    每项独立 try/except：取不到就明说取不到，绝不编造数值。
    """
    q = str(question or "")

    def want(*keys) -> bool:
        return any(k in q for k in keys)

    blocks: list[str] = []
    if want("统计", "成功率", "调用", "账本", "落账", "次数"):
        try:
            st = stats_payload()
            s = st.get("summary", {})
            top = sorted((st.get("counters") or {}).items(),
                         key=lambda kv: -kv[1].get("calls", 0))[:8]
            blocks.append(
                "[组4 调用统计（stats.json 实账）] "
                f"总调用 {s.get('total')}，成功 {s.get('ok')}，失败 {s.get('fail')}，"
                f"成功率 {s.get('rate')}%；最近落盘 {st.get('updated')}\n按接口："
                + "、".join(f"{k} {v.get('calls')}次(败{v.get('fail')})"
                            for k, v in top))
        except Exception as e:
            blocks.append(f"[组4 调用统计] 读取失败：{type(e).__name__}: {e}")
    if want("运行", "编排", "审计", "verdict", "系统状态", "任务状态",
            "执行结果", "运行历史"):
        # 关键词刻意收窄：不收 "任务"/"最近"/"状态" 这类泛词，
        # 否则"你能帮我管理任务吗""最近有什么新闻"会被误导向运行清单
        try:
            with RUNS_LOCK:
                runs = list(RUNS.values())[-8:]
            if runs:
                rows = []
                for r in reversed(runs):
                    sm = r.get("summary") or {}
                    rows.append(
                        f"{r.get('time')} [{r.get('source')}] "
                        f"{str(r.get('text'))[:40]} → {r.get('status')}"
                        + (f"/审计 {r.get('verdict')}" if r.get("verdict") else "")
                        + (f"/{sm.get('success')}/{sm.get('total')} 步" if sm else ""))
                blocks.append("[本进程最近编排运行（与 /api/runs 同源）]\n"
                              + "\n".join(rows))
            else:
                blocks.append("[本进程最近编排运行] 尚无记录 —— 在编排控制台或本对话里"
                              "（说出系统操作）发起一次即可产生")
        except Exception as e:
            blocks.append(f"[运行历史] 读取失败：{type(e).__name__}: {e}")
    if want("知识库", "rag", "轨迹", "检索", "记忆"):
        try:
            n = len(SYSTEM.rag) if SYSTEM.rag else 0
            docs = SYSTEM.rag.documents[-5:] if SYSTEM.rag else []
            blocks.append(f"[组5 RAG 知识库] 共 {n} 条轨迹；最近："
                          + "；".join(str(d.get("input"))[:30] for d in docs))
        except Exception as e:
            blocks.append(f"[RAG 知识库] 读取失败：{type(e).__name__}: {e}")
    if want("控件", "元素", "桌面", "窗口", "atspi"):
        try:
            blocks.append(f"[组2 控件检测] 当前后端 = {SYSTEM.detector.backend}"
                          "（完整控件树在「编排控制台」的控件树区查看）")
        except Exception as e:
            blocks.append(f"[控件检测] 读取失败：{type(e).__name__}: {e}")
    if want("设置", "配置", "工作区", "受保护", "温度", "身份", "权限"):
        try:
            blocks.append(
                "[系统设置] " + json.dumps(SETTINGS, ensure_ascii=False)
                + f"；受保护路径 {len(getattr(SYSTEM.security, 'protected', []) or [])} 条"
                  "（含主目录根与敏感子路径）；"
                  f"LLM Key={'已配置' if SYSTEM.modules[1].api_key else '未配置'}；"
                  f"SMTP={'已配置' if os.environ.get('SMTP_HOST') else '未配置'}")
        except Exception as e:
            blocks.append(f"[系统设置] 读取失败：{type(e).__name__}: {e}")
    if want("能力", "工具", "技能", "支持"):
        blocks.append("[真实能力清单]\n" + _capability_brief())
    return "\n\n".join(blocks)


def _chat_system_prompt(reference: str = "") -> str:
    """对话助手的 system prompt：把"真实能力清单"钉进上下文，抑制能力幻觉。"""
    p = ("你是 Agent_OS_v1.0 系统控制台的内置对话助手。硬性规则：\n"
         "1) **能力类问题只能依据下面的《真实能力清单》回答**；清单里没有的功能，"
         "直接回答“本系统当前不支持”，并给出清单内的替代做法。禁止编造功能名、"
         "接口、数据、路径，或任务/会话/日志等本系统并不存在的对象。\n"
         "2) 状态类问题**只用给定的参考数据**；没有给到数据就如实说"
         "“我没有这项数据”，不要推测数值或时间。\n"
         "3) 真实操作（文件/命令/邮件/网页/桌面）不要承诺“我这就去做”，也**不得声称**"
         "“执行卡片已生成 / 正在等待你确认 / 已开始执行”—— 执行卡片只由服务端的"
         "确定性路由弹出，你无法生成它；没看到卡片时，就引导用户到「编排控制台」"
         "输入指令，或把指令补全（缺的参数一次问清）。\n"
         "4) 回答简洁（默认 3~6 行，可用短列表），中文。\n\n"
         "===== 真实能力清单（运行时生成，以此为准）=====\n"
         + _capability_brief() + "\n")
    if reference:
        p += ("\n===== 本次问答的参考数据（服务端真实查询结果，可直接引用）=====\n"
              + reference + "\n")
    return p


def _chunk_text(text: str, size: int = 100):
    """把确定性回答切块，复用 SSE 流式通道（前端渲染逻辑无需改动）。"""
    for i in range(0, len(text), size):
        yield text[i:i + size]


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentOS_v1.0_GUI/1.0"

    def log_message(self, fmt, *args):
        pass                          # 安静模式

    def end_headers(self):
        """所有响应禁止缓存：杜绝浏览器拿旧页面调已下线的接口。"""
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if not length:
            return {}
        if length > self.MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    # ---------- GET ----------
    def do_GET(self):
        """GET 统一兜底：参数异常/内部错误一律回 JSON，不再静默断连。

        早期 do_GET 没有 try/except（POST 侧有），`/api/rag?k=abc` 之类会
        异常穿出 handler → 连接被直接关闭，前端只看到"偶发空白"。
        """
        try:
            self._route_get()
        except Exception as e:
            try:
                self._json({"error": f"服务端异常: {type(e).__name__}: {e}"}, 500)
            except Exception:
                pass

    def _route_get(self):
        if self.path in ("/", "/index.html"):
            with open(INDEX_PATH, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/controls":
            # 修复逻辑审计 #16：GET 不过同源闸门（跨站 <img src> 也能触发）——
            # 旧实现每次都持全局锁跑全桌面扫描，反复触发可饿死编排。
            # 加 3 秒短缓存：扫描风暴最多每 3 秒真扫一次，其余吃缓存。
            now = time.time()
            data = _CONTROLS_CACHE.get("data")
            if data is None or now - _CONTROLS_CACHE["ts"] > _CONTROLS_TTL:
                with _LOCK:
                    detector = SYSTEM.detector
                    els = detector.detect_elements()
                data = {"backend": detector.backend, "elements": els}
                _CONTROLS_CACHE["ts"] = now
                _CONTROLS_CACHE["data"] = data
            self._json(data)
        elif self.path == "/api/tools":
            with _LOCK:
                tools = ([s.to_dict() for s in SYSTEM.modules[3].registry.list_tools()]
                         + [s.to_dict() for s in SYSTEM.modules[3].skills.list_skills()])
            self._json({"tools": tools})
        elif self.path == "/api/about":
            # "我到底重启成功没？" —— 服务是启动时加载的，改了代码不重启就等于没改。
            # 这里把（进程启动时间 / 磁盘上关键源文件的修改时间）一起给出来，页面
            # 底部也会显示；**启动时间早于源文件修改时间 = 你跑的还是旧代码**。
            self._json({
                "pid": os.getpid(),
                "started_at": BOOT_AT,
                "python": sys.version.split()[0],
                "root": ROOT,
                "sources": {rel: _source_mtime(rel) for rel in (
                    "gui/server.py", "group_5/security.py",
                    "group_1/host_agent.py", "src/real_tools.py")},
                "layer3": LAYER3_TAG,
                "stale": _stale_sources(),
            })
        elif self.path == "/api/stats":
            with _LOCK:
                self._json(stats_payload())
        elif self.path.startswith("/api/rag"):
            q = ""
            k = int(SETTINGS.get("rag_k") or 3)   # 设置项真正生效（原为死旋钮）
            if "?" in self.path:
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                q = (qs.get("q") or [""])[0]
                k = _safe_int((qs.get("k") or [""])[0], k)
            k = max(1, min(k, 50))
            with _LOCK:
                hits = SYSTEM.rag.query(q, k) if q else SYSTEM.rag.documents[-k:][::-1]
                self._json({"total": len(SYSTEM.rag), "hits": hits})
        elif self.path == "/api/settings":
            with _LOCK:
                s = dict(SETTINGS)
                s["llm_available"] = bool(SYSTEM.modules[1].api_key)
            self._json({"settings": s, "defaults": SETTINGS_DEFAULT})
        elif self.path == "/api/llm":
            host = SYSTEM.modules[1]
            key = host.api_key or ""
            self._json({
                "configured": bool(key),
                "provider": getattr(host, "provider_name", ""),
                "base_url": host.base_url, "model": host.model,
                "api_key_masked": _mask_secret(key),
                "use_llm": host.use_llm, "last_engine": host.last_engine,
            })
        elif self.path == "/api/runs":
            # 运行历史：编排控制台与对话助手发起的执行都在 RUNS 登记过
            with RUNS_LOCK:
                runs = [{k: r.get(k) for k in
                         ("id", "time", "source", "text", "status",
                          "verdict", "summary")}
                        for r in reversed(list(RUNS.values()))]
            self._json({"runs": runs})
        elif self.path == "/api/chat_history":
            try:
                with open(CHAT_HISTORY_PATH, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                data = {"messages": []}
            self._json({"messages": data.get("messages", [])[-100:],
                        "updated": data.get("updated")})
        elif self.path == "/api/smtp_config":
            key = os.environ.get("SMTP_PASS", "")
            self._json({"configured": bool(os.environ.get("SMTP_HOST")),
                        "host": os.environ.get("SMTP_HOST", ""),
                        "port": os.environ.get("SMTP_PORT", "465"),
                        "user": os.environ.get("SMTP_USER", ""),
                        "pass_masked": _mask_secret(key)})
        elif self.path.startswith("/api/replays"):
            # 优化方案 1.1/2.4：归档重放 —— 每次编排的事件账本（events.json）
            # 落在 system_out/runs/<tag>/ 下，这里只读枚举/取单份（tag 严格
            # 白名单字符集，杜绝路径穿越）。
            from urllib.parse import parse_qs as _pq, urlparse as _up
            qs = _pq(_up(self.path).query)
            base = os.path.join(OUT, "runs")
            tag = (qs.get("tag") or [""])[0]
            if tag:
                if not re.fullmatch(r"[\w.\-]+", tag):
                    self._json({"error": "非法 tag"}, 400)
                    return
                self._json({"tag": tag,
                            "events": load_events(
                                os.path.join(base, tag, "events.json"))})
                return
            try:
                tags = sorted((d for d in os.listdir(base)
                               if os.path.isfile(
                                   os.path.join(base, d, "events.json"))),
                              reverse=True)[:30]
            except OSError:
                tags = []
            self._json({"replays": tags})
        elif self.path.startswith("/api/run"):
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            rid = (qs.get("id") or [""])[0]
            cursor = max(0, _safe_int((qs.get("cursor") or ["0"])[0], 0))
            run = RUNS.get(rid)
            if run is None:
                self._json({"error": "运行不存在", "rid": rid,
                            "existing": list(RUNS.keys())}, 404)
                return
            with RUNS_LOCK:
                self._json({"id": rid, "status": run["status"],
                            "events": run["events"][cursor:],
                            "cursor": len(run["events"]),
                            "error": run.get("error"),
                            "bundle": run["bundle"] if run["status"] in (
                                "done", "rejected") else None})
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._json({"error": "not found"}, 404)

    # ---------- POST ----------
    MAX_BODY = 1 << 20                 # 1MB 请求体上限（防超长体撑爆内存）

    def _sandbox_gate(self, action: str, params) -> str | None:
        """修复逻辑审计 #14：工具台直调端点（/api/call、/api/skill、
        /api/smtp_test）统一过 SecuritySandbox —— 旧实现完全绕开四层检查，
        "与生产共用同一套权限闸门"的文件头承诺不成立。

        与编排链路同口径：把直调拼成一条动作意图过 check()（黑名单/路径
        保护/签名核对全生效）。放行返回 None，否则返回拒绝原因。
        沙箱异常按 fail-closed 处理（宁可拒调，不可放行未检请求）。
        """
        try:
            chk = SYSTEM.security.check({
                "intent": "工具台直调", "action": str(action),
                "params": params if isinstance(params, dict) else {},
                "target": "", "user_text": f"[工具台]直接调用 {action}"})
        except Exception as e:
            return f"安全沙箱异常（拒绝直调）: {type(e).__name__}: {e}"
        if not chk.get("approved"):
            return f"{chk.get('reason') or '安全判定未通过'}（risk={chk.get('risk_level')}）"
        return None

    def _same_origin_ok(self) -> bool:
        """CSRF / DNS-rebinding 防线（本地高危接口的必需品）。

        只绑 127.0.0.1 并不等于安全：浏览器对 127.0.0.1 的**跨站简单请求**
        不受同源策略阻止，任意网页都能 POST /api/call(level=admin) 执行
        run_command。因此要求：
          1) Content-Type 必须是 application/json —— 跨站简单请求只能用
             text/plain / form-urlencoded / multipart，这一条即挡掉；
          2) Origin 必须存在且同源（回环地址 + 同端口）——浏览器对本地 GUI
             的非 GET fetch 必带 Origin，缺失即非法来源（审计修复 #16）；
          3) Host 必须是回环地址（防 DNS rebinding；端口只在纯数字时剥离）。
        """
        host = (self.headers.get("Host") or "").strip().lower()
        # 修复逻辑审计 #16：只有"冒号后全是数字"才当端口剥掉 ——
        # 旧实现无脑 rsplit(":",1)，`Host: 127.0.0.1:8788.evil.com`
        # 取前段也判"回环"，防线形同虚设。
        if host.startswith("["):
            hostname = host[1:host.find("]")] if "]" in host else host
        elif ":" in host and host.rsplit(":", 1)[1].isdigit():
            hostname = host.rsplit(":", 1)[0]
        else:
            hostname = host
        if hostname not in ("127.0.0.1", "localhost", "::1"):
            return False
        origin = self.headers.get("Origin")
        # 修复逻辑审计 #16：Origin 缺失不再放行。本地 GUI 的全部 POST 都来自
        # 浏览器 fetch（非 GET 必带 Origin），体检脚本已同步带上；不带 Origin
        # 的请求没有任何合法来源，一律拒绝。
        if not origin:
            return False
        port = self.server.server_port
        ok = {f"http://127.0.0.1:{port}", f"http://localhost:{port}",
              f"http://[::1]:{port}"}
        if origin.rstrip("/") not in ok:
            return False
        ctype = (self.headers.get("Content-Type")
                 or "").split(";")[0].strip().lower()
        return ctype == "application/json"

    def do_POST(self):
        if not self._same_origin_ok():
            self._json({"error": "拒绝：仅接受本地同源的 application/json 请求"
                                 "（CSRF / 同源校验未通过）"}, 403)
            return
        body = self._body()
        if body is None:
            self._json({"error": "请求体不是合法的 UTF-8 JSON（或超过体积上限）"}, 400)
            return
        try:
            self._route_post(body)
        except Exception as e:                 # 任何异常都以 JSON 返回
            self._json({"error": f"服务端异常: {type(e).__name__}: {e}"}, 500)

    def _route_post(self, body: dict):
        if self.path == "/api/chat":
            # 对话助手：输入直连 LLM 厂商，SSE 逐段转发给前端（流式优先，
            # 厂商异常时降级为一次性返回）。不占编排锁 —— 长对话不阻塞编排。
            msgs = [{"role": str(m.get("role", "user")),
                     "content": str(m.get("content", ""))[:4000]}
                    for m in (body.get("messages") or [])
                    if isinstance(m, dict) and m.get("content")][-16:]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()

            def emit(obj):
                self.wfile.write(
                    f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()

            last_user = next((m["content"] for m in reversed(msgs)
                              if m.get("role") == "user"), "")

            def stream_answer(system: str, fallback_facts: str = "") -> None:
                """按给定 system prompt 流式回答；失败时降级（必要时直接给事实）。"""
                try:
                    from src.llm_client import chat_stream
                    for delta in chat_stream(
                            [{"role": "system", "content": system}] + msgs):
                        emit({"delta": delta})
                    emit({"done": True})
                    return
                except Exception as e:
                    if fallback_facts:
                        # 无 Key / 网络不可用：如实给服务端事实，不做 LLM 解读
                        emit({"delta": "\n【LLM 不可用，以下为服务端真实数据】\n"
                                       + fallback_facts})
                        emit({"done": True})
                        return
                    try:                      # 流式失败 → 一次性降级
                        from src.llm_client import chat_once
                        text = chat_once([{"role": "system", "content": system}]
                                         + msgs)
                        if text:
                            emit({"delta": text})
                            emit({"done": True})
                        else:
                            emit({"error": f"LLM 调用失败: {type(e).__name__}: {e}"})
                            emit({"done": True})
                    except Exception as e2:
                        emit({"error": f"对话失败: {type(e2).__name__}: {e2}"})
                        emit({"done": True})

            # ① 能力自述类提问 → 零 LLM 的真实清单（确定性，永远不会夸大）
            if _is_capability_question(last_user):
                emit({"meta": {"source": "capability",
                               "label": "本地能力清单 · 按组4 schema 现场生成"}})
                for chunk in _chunk_text(_capability_brief()):
                    emit({"delta": chunk})
                emit({"done": True})
                return
            # ② 问"本系统自己的数据"（统计/运行/审计/知识库/控件/设置）→ 先取真实
            #    事实再回答。**优先于下面的"操作卡片"判定**：问"当前系统状态与最近
            #    的编排结果"不该被规则引擎解析成"打开应用/检查系统"这类操作。
            try:
                facts = _system_facts(last_user)
            except Exception:
                facts = ""
            if facts:
                emit({"meta": {"source": "facts",
                               "label": "真实数据 + LLM 解读"
                               if getattr(SYSTEM.modules[1], "use_llm", False)
                               else "真实数据（LLM 未启用，直接返回事实）"}})
                stream_answer(_chat_system_prompt(facts), fallback_facts=facts)
                return
            # ③ 整条消息就是一个能力名（用户常从工具台/产物里抄 read_file、list_directory…）
            #    → 有中文规则且不缺必填参数，才交给下面的操作卡片；否则直接回"要哪些参数、
            #    去哪儿点、怎么说"（确定性，零 LLM，避免模型编一句"卡片已生成"）。
            try:
                caught = lookup_bare_capability(last_user, SYSTEM.schemas_catalog)
            except Exception:
                caught = None
            if caught:
                cap_name, cap_rec = caught
                try:
                    routable = bool(SYSTEM.modules[1].detect_operation(last_user))
                except Exception:
                    routable = False
                if required_params(cap_rec) or not routable:
                    emit({"meta": {"source": "capability",
                                   "label": "能力参数说明 · 按组4 schema 现场生成"}})
                    for chunk in _chunk_text(capability_hint(cap_name, cap_rec)):
                        emit({"delta": chunk})
                    emit({"done": True})
                    return
            # ④ 操作路由：确定性规则识别系统操作意图（零 token）。命中则发
            #    route 事件，由前端出执行卡片、用户点击后才走完整编排管线。
            try:
                op = SYSTEM.modules[1].detect_operation(last_user)
            except Exception:
                op = None
            if op:
                emit({"route": "operation", "user_text": last_user,
                      "actions": [g.get("action") for g in op.get("goals", [])]})
                emit({"done": True})
                return
            # ④ 规则命中了操作词但被保守门拦下（长/复杂指令）→ 引导去编排
            #    控制台：那里的 LLM 规划 + 确认流才是复杂任务的正确入口
            try:
                rule_goals = SYSTEM.modules[1]._rule_intent(last_user).get("goals", [])
            except Exception:
                rule_goals = []
            if rule_goals:
                emit({"route": "complex",
                      "actions": [g.get("action") for g in rule_goals]})
                emit({"done": True})
                return
            # ⑤ 普通对话：仍然把"真实能力清单"钉进 system prompt，抑制能力幻觉
            emit({"meta": {"source": "llm", "label": "LLM · 受真实能力清单约束"}})
            stream_answer(_chat_system_prompt())
        elif self.path == "/api/orchestrate":
            # 兼容路由：供未强刷的旧缓存页面使用（同步返回完整结果包）
            text = str(body.get("user_text") or "").strip()
            if not text:
                self._json({"ok": False, "rejected": "指令不能为空"})
                return
            with _LOCK:
                staged = SYSTEM.plan_only(text)
                if staged.get("rejected"):
                    bundle = {**staged, "ok": False}
                elif staged.get("needs_confirm"):
                    # 兼容路由**不得绕过确认流**：需要确认的方案只回计划+令牌，
                    # 由前端走 /api/execute（原实现直接 auto 执行 = 零交互执行）
                    _purge_pending()
                    token = uuid.uuid4().hex[:8]
                    PENDING[token] = {"text": text,
                                      "time": time.strftime("%H:%M:%S"),
                                      "intent": staged["intent"],
                                      "plan": staged["plan"],
                                      "check": staged["check"],
                                      "source": "orchestrate-compat",
                                      "_ts": time.time()}
                    bundle = {**staged, "ok": True, "pending": True, "token": token}
                else:
                    bundle = SYSTEM.execute_approved(
                        staged["intent"], staged["plan"], confirm_mode="auto")
            bundle["user_text"] = text
            if not bundle.get("pending"):
                bundle["stats_detail"] = stats_payload()
            self._json(bundle)
        elif self.path == "/api/plan":
            text = str(body.get("user_text") or "").strip()
            source = str(body.get("source") or "console")
            if not text:
                self._json({"ok": False, "rejected": "指令不能为空"})
                return
            with _LOCK:
                staged = SYSTEM.plan_only(text)
            if staged.get("rejected"):
                self._json({"ok": False, "rejected": staged["rejected"],
                            "check": staged.get("check", {})})
                return
            if staged.get("needs_confirm"):
                _purge_pending()
                token = uuid.uuid4().hex[:8]
                # 存储完整已批准方案：确认后原样执行，不再二次规划
                PENDING[token] = {"text": text, "time": time.strftime("%H:%M:%S"),
                                  "intent": staged["intent"], "plan": staged["plan"],
                                  "check": staged["check"], "source": source,
                                  "_ts": time.time()}
                self._json({"ok": True, "pending": True, "token": token,
                            "intent": staged["intent"], "plan": staged["plan"],
                            "check": staged["check"],
                            "degraded": staged.get("degraded", False),
                            "admin_steps": staged["admin_steps"],
                            "real_send_steps": staged["real_send_steps"]})
                return
            rid = _start_run(text, confirm_mode="auto", source=source)
            self._json({"ok": True, "pending": False, "run_id": rid})
        elif self.path == "/api/execute":
            # /api/execute：凭确认令牌取出 PENDING 里存好的完整方案原样执行，
            token = str(body.get("token", ""))
            # 修复逻辑审计 #15：pop(token, None) **一步原子消费** ——
            # 旧实现 `if token not in PENDING: … PENDING.pop(token)` 在并发
            # 双发下第二发抛 KeyError → 500 且异常文本回显令牌（可被再次利用）。
            info = PENDING.pop(token, None)
            if info is None:
                self._json({"error": "确认令牌无效或已过期"}, 404)
                return
            # 审计修复 #15：执行前**不重新规划**（红线 5），但 execute_approved
            # 内部会跑 _plan_gate 计划级安全复检 —— 改受保护路径等设置变更后，
            # 旧令牌也要按**执行时刻**的策略重新过闸。
            SYSTEM.audit({"stage": "confirm_execute", "text": info.get("text", ""),
                          "mode": str(body.get("mode", "full"))})
            mode = "deny" if str(body.get("mode", "full")) == "safe" else "auto"
            rid = _start_run(confirm_mode=mode, staged=info,
                             source=str(body.get("source")
                                        or info.get("source") or "console"))
            self._json({"ok": True, "run_id": rid, "mode": mode})
        elif self.path == "/api/cancel":
            # 修复逻辑审计 #15："取消"必须是**服务端**消费令牌 ——
            # 旧实现纯前端藏面板，令牌在 30 分钟 TTL 内仍可被任意同源
            # JSON POST 复用执行。
            token = str(body.get("token", ""))
            gone = PENDING.pop(token, None)
            SYSTEM.audit({"stage": "confirm_cancel", "token_consumed": gone is not None,
                          "text": (gone or {}).get("text", "")})
            self._json({"ok": True, "cancelled": gone is not None})
        # /api/run：前端每 800ms 带 cursor 增量拉事件流（只回新增事件）
        elif self.path.startswith("/api/run"):
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            rid = (qs.get("id") or [""])[0]
            cursor = max(0, _safe_int((qs.get("cursor") or ["0"])[0], 0))
            run = RUNS.get(rid)
            if run is None:
                self._json({"error": "运行不存在", "rid": rid,
                            "existing": list(RUNS.keys())}, 404)
                return
            with RUNS_LOCK:
                self._json({"id": rid, "status": run["status"],
                            "events": run["events"][cursor:],
                            "cursor": len(run["events"]),
                            "error": run.get("error"),
                            "bundle": run["bundle"] if run["status"] in (
                                "done", "rejected") else None})
        elif self.path == "/api/smtp_config":
            key = os.environ.get("SMTP_PASS", "")
            self._json({"configured": bool(os.environ.get("SMTP_HOST")),
                        "host": os.environ.get("SMTP_HOST", ""),
                        "port": os.environ.get("SMTP_PORT", "465"),
                        "user": os.environ.get("SMTP_USER", ""),
                        "pass_masked": _mask_secret(key)})
        elif self.path == "/api/smtp_save":
            cfg = {}
            for k, body_key in (("SMTP_HOST", "host"), ("SMTP_PORT", "port"),
                                ("SMTP_USER", "user"), ("SMTP_PASS", "pass")):
                v = str(body.get(body_key, "")).strip()
                if v:
                    cfg[k] = v
            # 授权码留空视为沿用旧值（前端不回显明文）
            if not cfg.get("SMTP_HOST") or not cfg.get("SMTP_USER"):
                self._json({"ok": False, "error": "host 与 user 必填"})
                return
            if not body.get("pass") and os.environ.get("SMTP_PASS"):
                cfg["SMTP_PASS"] = os.environ["SMTP_PASS"]   # 留空沿用旧授权码
            if not cfg.get("SMTP_PASS"):
                self._json({"ok": False, "error": "SMTP_PASS（授权码）首次必填"})
                return
            with open(SMTP_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            load_smtp_config()
            key = os.environ.get("SMTP_PASS", "")
            self._json({"ok": True, "message": "邮件服务已保存并启用（重启后默认可用）",
                        "pass_masked": _mask_secret(key)})
        elif self.path == "/api/smtp_test":
            # 修复逻辑审计 #14：测试发信同样走闸门+锁+统计+审计 —— 旧实现
            # 绕开注册表直接 send_email(dry_run=False)：不落账、不写审计、
            # 可与编排并发。改走 registry.call 后权限/统计口径与生产一致。
            to = os.environ.get("SMTP_USER", "")
            params = {"to": to, "subject": "Agent_OS_v1.0 SMTP 测试",
                      "body": "这是一封连通性测试邮件。", "dry_run": False}
            gate = self._sandbox_gate("send_email", params)
            if gate:
                SYSTEM.audit({"stage": "api_call", "endpoint": "/api/smtp_test",
                              "tool": "send_email", "params": params,
                              "approved": False, "error": gate})
                self._json({"ok": False,
                            "error": f"安全沙箱拦截: {gate}"}, 403)
                return
            with _LOCK:
                d = SYSTEM.modules[3].registry.call(
                    "send_email", params, LEVELS["admin"]).to_dict()
            SYSTEM.audit({"stage": "api_call", "endpoint": "/api/smtp_test",
                          "tool": "send_email", "params": params,
                          "approved": True, "success": bool(d.get("success")),
                          "error": d.get("error")})
            # 前端契约：testSMTP() 读 r.ok / r.error（旧版回 success 恒显 ✗）
            self._json({"ok": bool(d.get("success")),
                        "error": d.get("error"), "result": d.get("result")})
        elif self.path == "/api/chat_save":
            # 聊天记录持久化（system_out/chat_history.json，保留最近 100 条）
            msgs = [m for m in (body.get("messages") or [])
                    if isinstance(m, dict)
                    and str(m.get("content", "")).strip()][-100:]
            # 修复逻辑审计 #12：对话内容可能内联邮箱/密钥（模型会把工具结果
            # 整段复述进回复）—— 落盘前与审计同口径过第 4 层脱敏。
            msgs = _mask_deep(msgs)
            # 优化方案 3.3：落盘走 safe_io 统一出口（原子替换，不留半截 JSON）
            if write_json(CHAT_HISTORY_PATH,
                          {"messages": msgs,
                           "updated": datetime.now().isoformat()},
                          mask=lambda d: d):
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "写入失败"})
        # /api/call：工具台以指定身份直接调用单个工具（真实闸门+统计）
        elif self.path == "/api/call":
            name = str(body.get("name", ""))
            params = body.get("params") or {}
            level = LEVELS.get(str(body.get("level", "user")).lower())
            if level is None:
                self._json({"error": "level 必须是 public/user/admin"}, 400)
                return
            # 修复逻辑审计 #14：直调前先过安全沙箱（与编排同四层），
            # 结果统一写审计 —— 拦截与放行都留痕。
            gate = self._sandbox_gate(name, params)
            if gate:
                SYSTEM.audit({"stage": "api_call", "endpoint": "/api/call",
                              "tool": name, "params": params,
                              "approved": False, "error": gate})
                self._json({"result": {"success": False,
                                       "error": f"安全沙箱拦截: {gate}",
                                       "blocked_by_guard": True}})
                return
            with _LOCK:
                result = SYSTEM.modules[3].registry.call(name, params, level)
            d = result.to_dict()
            SYSTEM.audit({"stage": "api_call", "endpoint": "/api/call",
                          "tool": name, "params": params, "approved": True,
                          "success": bool(d.get("success")),
                          "error": d.get("error")})
            self._json({"result": d})
        # /api/skill：工具台调用 Skill（技能与工具分接口）
        elif self.path == "/api/skill":
            name = str(body.get("name", ""))
            params = body.get("params") or {}
            gate = self._sandbox_gate(name, params)   # 审计修复 #14 同款
            if gate:
                SYSTEM.audit({"stage": "api_call", "endpoint": "/api/skill",
                              "tool": name, "params": params,
                              "approved": False, "error": gate})
                self._json({"result": {"success": False,
                                       "error": f"安全沙箱拦截: {gate}",
                                       "blocked_by_guard": True}})
                return
            with _LOCK:
                result = SYSTEM.modules[3].skills.call_skill(name, params)
            d = result.to_dict()
            SYSTEM.audit({"stage": "api_call", "endpoint": "/api/skill",
                          "tool": name, "params": params, "approved": True,
                          "success": bool(d.get("success")),
                          "error": d.get("error")})
            self._json({"result": d})
        # /api/settings：以持久文件为底吸收提交字段 → 逐项钳位 → 热更新应用
        elif self.path == "/api/settings":
            with _LOCK:
                s = load_settings()          # 以持久文件为底，吸收本次提交的字段
                for k, default in SETTINGS_DEFAULT.items():
                    if k not in body:
                        continue
                    v = body[k]
                    if k == "llm_temperature":
                        v = max(0.0, min(float(v), 1.0))
                    elif k == "action_delay":
                        v = max(0.0, float(v))
                    elif k == "rag_k":
                        v = max(1, min(int(v), 20))
                    elif k == "protected_extra":
                        v = [str(x).strip() for x in v if str(x).strip()]
                    elif k in ("planner_finale",):
                        v = bool(v)
                    s[k] = v
                save_settings(s)
                SETTINGS.clear()             # 原地变更（避免重绑定 global）
                SETTINGS.update(s)
                apply_settings(SETTINGS)
            self._json({"ok": True, "settings": dict(SETTINGS),
                        "message": "设置已保存并即时生效"})
        elif self.path == "/api/settings/reset":
            with _LOCK:
                SETTINGS.clear()
                SETTINGS.update(dict(SETTINGS_DEFAULT))
                save_settings(SETTINGS)
                apply_settings(SETTINGS)
            self._json({"ok": True, "settings": dict(SETTINGS), "message": "已恢复默认设置"})
        elif self.path == "/api/rag_clear":
            with _LOCK:
                n = len(SYSTEM.rag)
                SYSTEM.rag.documents.clear()
                if SYSTEM.rag.path and os.path.exists(SYSTEM.rag.path):
                    os.remove(SYSTEM.rag.path)
            self._json({"ok": True, "message": f"已清空 {n} 条 RAG 轨迹"})
        elif self.path == "/api/llm_config":
            # 保存供应商配置并即时应用到组1 HostAgent（api_key 留空 = 沿用旧值）
            host = SYSTEM.modules[1]
            provider = str(body.get("provider", ""))
            base_url = str(body.get("base_url", "")).strip()
            model = str(body.get("model", "")).strip()
            api_key = str(body.get("api_key", "")).strip()
            if not base_url or not model or (not api_key and not host.api_key):
                self._json({"ok": False, "error": "base_url、model 必填；api_key 首次必填"})
                return
            with _LOCK:
                host.apply_config(base_url, api_key, model,
                                  provider=provider, use_llm=True)
                host.provider_name = provider
            key = host.api_key
            self._json({"ok": True,
                        "api_key_masked": _mask_secret(key),
                        "message": f"已切换到 {provider or '自定义'} · {model}（LLM 意图理解已启用）"})
        elif self.path == "/api/llm_test":
            with _LOCK:
                result = SYSTEM.modules[1].test_llm()
            self._json(result)
        elif self.path == "/api/reset_stats":
            with _LOCK:
                # 必须清**在跑实例**的内存账本：只删文件的话，下一次工具调用
                # 会把全量历史照原样写回去（"已清空"只是假象）
                if STATS_OBJ is not None:
                    STATS_OBJ.clear()
                if os.path.exists(STATS_PATH):
                    os.remove(STATS_PATH)
            self._json({"ok": True, "message": "系统账本已清空（内存+文件；RAG 轨迹保留）"})
        else:
            self._json({"error": "not found"}, 404)


def main() -> None:
    ap = argparse.ArgumentParser(description="Agent_OS_v1.0 系统控制台")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    menu = len(SYSTEM.modules[3].tool_menu())
    print("=" * 60)
    print("  Agent_OS_v1.0 系统控制台已启动")
    print(f"  地址：http://{args.host}:{args.port}")
    print(f"  已装载：{menu} 项组4能力 | 安全沙箱 | RAG 知识库 {len(SYSTEM.rag)} 条")
    print("  注意：编排会执行真实 OS 操作，Ctrl+C 停止")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
