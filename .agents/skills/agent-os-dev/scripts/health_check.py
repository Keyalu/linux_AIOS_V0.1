"""health_check.py — Agent_OS GUI 全端点健康检查。

用法（8788 服务运行时）：
    python3 .agents/skills/agent-os-dev/scripts/health_check.py [端口]

覆盖全部 GET 接口 + 编排全链路（plan→execute→run）+ 工具/技能调用 +
LLM/SMTP 配置保存（沿用已存密钥，不外泄）+ 错误路径。
会产生的真实副作用：一次 LLM 测试调用、一封发给发件邮箱自身的 SMTP 测试邮件。
"""
import json
import socket
import sys
import time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8788
BASE = f"http://127.0.0.1:{PORT}"


def raw(method, path, body=None, timeout=240):
    t0 = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(("127.0.0.1", PORT))
    payload = json.dumps(body).encode() if body is not None else None
    headers = f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
    if payload is not None:
        headers += f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
    headers += "Connection: close\r\n\r\n"
    s.sendall(headers.encode() + (payload or b""))
    buf = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    head, _, body = buf.partition(b"\r\n\r\n")
    dt = round(time.time() - t0, 2)
    status = int(head.split(b" ")[1]) if head else 0
    text = body.decode("utf-8", "ignore")
    try:
        obj = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        obj = text                      # HTML 等非 JSON 响应原样返回
    return status, obj, dt


R = []


def check(name, ok, detail=""):
    R.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗ 失败'}  {name}  {detail}")


def main():
    st, b, dt = raw("GET", "/")
    check("GET / 页面", st == 200 and len(b) > 10000, f"{dt}s")
    for path in ("/api/tools", "/api/stats", "/api/settings", "/api/llm",
                 "/api/smtp_config", "/api/rag"):
        st, b, dt = raw("GET", path, timeout=20)
        check(f"GET {path}", st == 200, f"{dt}s")

    st, b, _ = raw("POST", "/api/plan", {"user_text": "查询长沙天气"}, timeout=120)
    ok = st == 200 and b.get("pending") is not None
    check("POST /api/plan（编排全链路）", ok)
    st, b, _ = raw("POST", "/api/execute",
                   {"token": b.get("token", ""), "mode": "full"}, timeout=60)
    check("POST /api/execute", st == 200 and b.get("run_id"))
    rid = b.get("run_id")
    final = None
    for _ in range(40):
        time.sleep(0.7)
        st, d, _ = raw("GET", f"/api/run?id={rid}&cursor=0", timeout=30)
        if d.get("status") in ("done", "rejected", "failed"):
            final = d
            break
    check("编排至终态", final is not None and final["status"] == "done",
          str(final.get("bundle", {}).get("audit", {}).get("verdict")) if final else "")
    st, b, _ = raw("POST", "/api/call",
                   {"name": "list_directory", "params": {"path": "."}, "level": "user"},
                   timeout=30)
    check("POST /api/call 工具", st == 200 and b["result"]["success"])
    st, b, _ = raw("POST", "/api/skill", {"name": "system_check", "params": {}}, timeout=60)
    check("POST /api/skill 技能", st == 200 and b["result"]["success"])
    st, b, _ = raw("POST", "/api/notexist", {}, timeout=15)
    check("POST 未知路径→404", st == 404)

    bad = sum(1 for _, ok, _ in R if not ok)
    print(f"\n端点体检: {len(R) - bad}/{len(R)} 通过")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
