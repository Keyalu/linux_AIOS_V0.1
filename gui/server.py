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
import sys
import threading
import uuid
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from group_1 import HostAgent                     # noqa: E402
from group_2 import TaskPlanner, ControlDetector  # noqa: E402
from group_3 import AppAgent                      # noqa: E402
from group_4 import make_stack, schema_catalog    # noqa: E402
from group_5 import SecuritySandbox, RAGKnowledgeBase, SystemCoordinator  # noqa: E402
from src.interfaces import PermissionLevel        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "system_out")
STATS_PATH = os.path.join(OUT, "stats.json")
INDEX_PATH = os.path.join(HERE, "index.html")

LEVELS = {"public": PermissionLevel.PUBLIC, "user": PermissionLevel.USER,
          "admin": PermissionLevel.ADMIN}

_LOCK = threading.Lock()             # 编排/调用串行化，避免并发踩账本


def build_system() -> SystemCoordinator:
    """与 main.build 相同的接线（gui 内自持一份，避免连带 argparse）。"""
    os.makedirs(OUT, exist_ok=True)
    from src.stats import ToolStats
    stats, registry, skills = make_stack(
        stats=ToolStats(log_path=STATS_PATH))
    catalog = schema_catalog(registry, skills)
    detector = ControlDetector()
    return SystemCoordinator(
        HostAgent(), TaskPlanner(schemas=catalog, detector=detector),
        AppAgent(registry, skills), detector=detector,
        security=SecuritySandbox(), rag=RAGKnowledgeBase(os.path.join(OUT, "rag_traces.jsonl")),
        audit_path=os.path.join(OUT, "audit_log.jsonl"),
        schemas_catalog=catalog, default_target=os.path.join(OUT, "demo_task"),
        artifact_dir=OUT)


SETTINGS_PATH = os.path.join(OUT, "settings.json")
SETTINGS_DEFAULT = {
    "llm_engine": "llm",          # llm | rules
    "llm_temperature": 0,         # 0~1
    "default_level": "user",      # public | user | admin
    "escalation_confirm": "auto", # auto | deny
    "planner_finale": True,       # 计划末尾附加提权演示收尾步
    "action_delay": 0.3,          # GUI 动作间隔秒
    "protected_extra": [],        # 追加受保护路径
    "rag_k": 3,                   # RAG 默认返回条数
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
        agent.confirmer = (lambda info: True) if mode == "auto" else (lambda info: False)
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


SYSTEM = build_system()

# ── SMTP 配置持久化：重启后邮件服务默认可用 ──
SMTP_CONFIG_PATH = os.path.join(OUT, "smtp_config.json")


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
PENDING: dict[str, dict] = {}          # 待确认令牌 -> {text}


def _emit(run: dict, stage: str, **data) -> None:
    run["events"].append({"time": time.strftime("%H:%M:%S"), "stage": stage, **data})


def _start_run(text: str = None, confirm_mode: str = "auto",
               staged: dict | None = None) -> str:
    """启动一次编排运行。

    staged 给定（用户已确认的方案）时直接执行该方案，零重新规划、
    零 LLM 调用 —— 用户确认的方案 = 实际执行的方案；
    staged 为空（无需确认的低风险指令）时在线程内先 plan_only。
    """
    rid = uuid.uuid4().hex[:8]
    run = {"id": rid, "status": "running", "events": [], "bundle": None}
    with RUNS_LOCK:
        RUNS[rid] = run
    if len(RUNS) > 50:                     # 只保留最近 50 次运行
        for old in sorted(RUNS)[:len(RUNS) - 50]:
            if RUNS[old]["status"] != "running":
                RUNS.pop(old, None)

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
                        run["bundle"] = st
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
            bundle["stats_detail"] = stats_payload()
            run["bundle"] = bundle
            run["status"] = "done"
            _emit(run, "done", verdict=bundle["audit"]["verdict"])
        except Exception as e:
            run["status"] = "failed"
            run["error"] = f"{type(e).__name__}: {e}"
    threading.Thread(target=worker, daemon=True).start()
    return rid


SYSTEM = build_system() if False else SYSTEM   # 占位保持顺序
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
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    # ---------- GET ----------
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(INDEX_PATH, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/controls":
            with _LOCK:
                detector = SYSTEM.detector
                els = detector.detect_elements()
            self._json({"backend": detector.backend, "elements": els})
        elif self.path == "/api/tools":
            with _LOCK:
                tools = ([s.to_dict() for s in SYSTEM.modules[3].registry.list_tools()]
                         + [s.to_dict() for s in SYSTEM.modules[3].skills.list_skills()])
            self._json({"tools": tools})
        elif self.path == "/api/stats":
            with _LOCK:
                self._json(stats_payload())
        elif self.path.startswith("/api/rag"):
            q = ""
            k = 3
            if "?" in self.path:
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                q = (qs.get("q") or [""])[0]
                k = int((qs.get("k") or ["3"])[0])
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
                "api_key_masked": (key[:4] + "****" + key[-4:]) if len(key) > 8 else ("已配置" if key else ""),
                "use_llm": host.use_llm, "last_engine": host.last_engine,
            })
        elif self.path == "/api/smtp_config":
            key = os.environ.get("SMTP_PASS", "")
            self._json({"configured": bool(os.environ.get("SMTP_HOST")),
                        "host": os.environ.get("SMTP_HOST", ""),
                        "port": os.environ.get("SMTP_PORT", "465"),
                        "user": os.environ.get("SMTP_USER", ""),
                        "pass_masked": (key[:3] + "****" + key[-3:])
                                       if len(key) > 6 else ("已配置" if key else "")})
        elif self.path.startswith("/api/run"):
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            rid = (qs.get("id") or [""])[0]
            cursor = int((qs.get("cursor") or ["0"])[0])
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
    def do_POST(self):
        body = self._body()
        if body is None:
            self._json({"error": "请求体不是合法的 UTF-8 JSON"}, 400)
            return
        try:
            self._route_post(body)
        except Exception as e:                 # 任何异常都以 JSON 返回
            self._json({"error": f"服务端异常: {type(e).__name__}: {e}"}, 500)

    def _route_post(self, body: dict):
        if self.path == "/api/orchestrate":
            # 兼容路由：供未强刷的旧缓存页面使用（同步返回完整结果包）
            text = str(body.get("user_text") or "").strip()
            if not text:
                self._json({"ok": False, "rejected": "指令不能为空"})
                return
            with _LOCK:
                staged = SYSTEM.plan_only(text)
                if staged.get("rejected"):
                    bundle = {**staged, "ok": False}
                else:
                    bundle = SYSTEM.execute_approved(
                        staged["intent"], staged["plan"], confirm_mode="auto")
            bundle["user_text"] = text
            bundle["stats_detail"] = stats_payload()
            self._json(bundle)
        elif self.path == "/api/plan":
            text = str(body.get("user_text") or "").strip()
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
                token = uuid.uuid4().hex[:8]
                # 存储完整已批准方案：确认后原样执行，不再二次规划
                PENDING[token] = {"text": text, "time": time.strftime("%H:%M:%S"),
                                  "intent": staged["intent"], "plan": staged["plan"],
                                  "check": staged["check"]}
                self._json({"ok": True, "pending": True, "token": token,
                            "intent": staged["intent"], "plan": staged["plan"],
                            "check": staged["check"],
                            "admin_steps": staged["admin_steps"],
                            "real_send_steps": staged["real_send_steps"]})
                return
            rid = _start_run(text, confirm_mode="auto")
            self._json({"ok": True, "pending": False, "run_id": rid})
        elif self.path == "/api/execute":
            token = str(body.get("token", ""))
            if token not in PENDING:
                self._json({"error": "确认令牌无效或已过期"}, 404)
                return
            info = PENDING.pop(token)
            mode = "deny" if str(body.get("mode", "full")) == "safe" else "auto"
            rid = _start_run(confirm_mode=mode, staged=info)   # 原样执行已确认方案
            self._json({"ok": True, "run_id": rid, "mode": mode})
        elif self.path.startswith("/api/run"):
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            rid = (qs.get("id") or [""])[0]
            cursor = int((qs.get("cursor") or ["0"])[0])
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
        elif self.path.startswith("/api/run"):
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            rid = (qs.get("id") or [""])[0]
            cursor = int((qs.get("cursor") or ["0"])[0])
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
                        "pass_masked": (key[:3] + "****" + key[-3:])
                                       if len(key) > 6 else ("已配置" if key else "")})
        elif self.path == "/api/smtp_save":
            cfg = {}
            for k, body_key in (("SMTP_HOST", "host"), ("SMTP_PORT", "port"),
                                ("SMTP_USER", "user"), ("SMTP_PASS", "pass")):
                v = str(body.get(body_key, "")).strip()
                if v:
                    cfg[k] = v
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
                        "pass_masked": (key[:3] + "****" + key[-3:]) if len(key) > 6 else "已配置"})
        elif self.path == "/api/smtp_test":
            from src.real_tools import send_email
            to = os.environ.get("SMTP_USER", "")
            r = send_email(to=to, subject="Agent_OS_v1.0 SMTP 测试",
                           body="这是一封连通性测试邮件。", dry_run=False)
            self._json(r)
        elif self.path == "/api/call":
            name = str(body.get("name", ""))
            params = body.get("params") or {}
            level = LEVELS.get(str(body.get("level", "user")).lower())
            if level is None:
                self._json({"error": "level 必须是 public/user/admin"}, 400)
                return
            with _LOCK:
                result = SYSTEM.modules[3].registry.call(name, params, level)
            self._json({"result": result.to_dict()})
        elif self.path == "/api/skill":
            name = str(body.get("name", ""))
            params = body.get("params") or {}
            with _LOCK:
                result = SYSTEM.modules[3].skills.call_skill(name, params)
            self._json({"result": result.to_dict()})
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
                        "api_key_masked": (key[:4] + "****" + key[-4:]) if len(key) > 8 else "已配置",
                        "message": f"已切换到 {provider or '自定义'} · {model}（LLM 意图理解已启用）"})
        elif self.path == "/api/llm_test":
            with _LOCK:
                result = SYSTEM.modules[1].test_llm()
            self._json(result)
        elif self.path == "/api/reset_stats":
            with _LOCK:
                if os.path.exists(STATS_PATH):
                    os.remove(STATS_PATH)
            self._json({"ok": True, "message": "系统账本已清空（RAG 轨迹保留）"})
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
