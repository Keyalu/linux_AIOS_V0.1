"""restart_gui.py — 一键重启控制台服务（改了代码之后用这个，省得自己找 pid）。

为什么需要它：`gui/server.py` 是**启动时加载**的常驻服务，改了任何 `.py` 都必须
重启才生效；而"手动找端口占用进程 → kill → 重启"在 Windows/Linux 上命令还不一样。
本脚本纯标准库实现，跨平台：

    python3 scripts/restart_gui.py                # 默认 8788
    python3 scripts/restart_gui.py --port 8899
    python3 scripts/restart_gui.py --stop         # 只停不起
    python3 scripts/restart_gui.py --force        # 不确认直接停

安全约束：只停**监听该端口**的进程，且会校验它是不是 python 进程（不是就拒绝）。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_text(cmd: list[str]) -> str:
    """跑一条命令并**稳妥解码**输出。

    Windows 中文控制台的 netstat/tasklist 是 cp936，而本机环境把 Python 的默认
    文本编码设成了 utf-8 —— 直接用 text=True 会在读取线程里抛 UnicodeDecodeError
    （实测：脚本探测端口时崩在 netstat 输出解码上）。所以统一收 bytes 再逐个编码试。
    """
    try:
        raw = subprocess.run(cmd, capture_output=True).stdout or b""
    except (OSError, ValueError):
        return ""
    for enc in ("utf-8", "cp936", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _pids_on_port_windows(port: int) -> list[int]:
    out = _run_text(["netstat", "-ano", "-p", "TCP"])
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3] == "LISTENING":
            if re.search(rf":{port}$", parts[1]):
                if parts[4].isdigit():
                    pids.add(int(parts[4]))
    return sorted(pids)


def _pids_on_port_posix(port: int) -> list[int]:
    """不依赖 ss/lsof：读 /proc/net/tcp{,6} 拿 inode，再扫 /proc/*/fd 反查 pid。"""
    inodes = set()
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(name) as f:
                next(f, None)
                for line in f:
                    cols = line.split()
                    if len(cols) < 10 or cols[3] != "0A":       # 0A = LISTEN
                        continue
                    if int(cols[1].split(":")[1], 16) == port:
                        inodes.add(cols[9])
        except (OSError, ValueError):
            continue
    pids = set()
    if inodes:
        for pid in filter(str.isdigit, os.listdir("/proc")):
            fd_dir = f"/proc/{pid}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        target = os.readlink(os.path.join(fd_dir, fd))
                    except OSError:
                        continue
                    if target.startswith("socket:[") and \
                            target[8:-1] in inodes:
                        pids.add(int(pid))
                        break
            except OSError:
                continue
    if pids:
        return sorted(pids)
    # 兜底：解析 ss 输出
    out = _run_text(["ss", "-tlnp"])
    for line in out.splitlines():
        if re.search(rf":{port}\b", line):
            m = re.search(r"pid=(\d+)", line)
            if m:
                pids.add(int(m.group(1)))
    return sorted(pids)


def pids_on_port(port: int) -> list[int]:
    return (_pids_on_port_windows(port) if os.name == "nt"
            else _pids_on_port_posix(port))


def is_python(pid: int) -> bool:
    if os.name == "nt":
        out = _run_text(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"]).lower()
        return "python" in out
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().decode("utf-8", "replace")
        return "python" in cmd
    except OSError:
        return False


def kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True)
        return
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(20):
        time.sleep(0.2)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
    os.kill(pid, signal.SIGKILL)


def wait_port_free(port: int, timeout: float = 8.0) -> bool:
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        except OSError:
            return True
        time.sleep(0.3)
    return False


def source_markers() -> list[tuple[str, str, bool]]:
    """磁盘上关键源码的"版本标记"：用于确认**共享目录里的代码**是不是新版。

    典型踩坑：Windows 侧改了代码、Linux 侧共享目录也看得见，但**运行中的进程
    仍是旧代码**；反过来也会遇到"共享的是旧副本"。这里直接读文件判断，
    再配合 `/api/about`（新版才有）就能一眼定版本。
    """
    checks = [
        ("层3按注册对象核对", "group_5/security.py", '"_func"'),
        ("层3三档判定(不误拦)", "group_5/security.py", "suspect"),
        ("/mnt 已移出受保护表", "group_5/security.py", "'/mnt'"),
        ("/api/about 服务信息", "gui/server.py", "/api/about"),
        ("标识符可路由", "group_1/host_agent.py", "_ACTION_ALIASES"),
        ("能力参数说明", "src/capability_hints.py", "capability_hint"),
    ]
    out = []
    for label, rel, needle in checks:
        path = os.path.join(ROOT, rel)
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            hit = needle in text
        except OSError:
            hit = False
        # /mnt 那条是"不该出现"，语义反过来
        want = (needle != "'/mnt'")
        out.append((label, rel, hit == want))
    return out


def print_version(port: int) -> None:
    print("磁盘上的代码版本自检（共享目录以这份为准）：")
    bad = 0
    for label, rel, ok in source_markers():
        print(f"  {'✓' if ok else '✗'} {label:22} ({rel})")
        bad += 0 if ok else 1
    if bad:
        print(f"  ⚠ 有 {bad} 项不达标 → 这份目录不是新版（换个共享目录 / 重新同步）")
    else:
        print("  → 目录里是新版；若页面行为仍是旧的，说明**运行中的进程没换**")
    print(f"  提示：新版有 GET /api/about，浏览器打开 "
          f"http://127.0.0.1:{port}/api/about 能看到 pid/启动时间即新版\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="重启（或停止）控制台服务")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--stop", action="store_true", help="只停止，不重新启动")
    ap.add_argument("--force", action="store_true", help="不确认")
    args = ap.parse_args()

    print_version(args.port)

    pids = pids_on_port(args.port)
    if pids:
        print(f"端口 {args.port} 正在被监听：pid={pids}")
        for pid in pids:
            if not is_python(pid):
                print(f"  ✗ pid={pid} 看起来不是 python 进程 → 拒绝停止（请人工确认）")
                return 2
        if not args.force:
            ans = input("  停止它并重启？[y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("  已取消")
                return 1
        for pid in pids:
            print(f"  · 停止 pid={pid} …")
            kill(pid)
        if not wait_port_free(args.port):
            print(f"  ✗ 端口 {args.port} 仍被占用，请人工检查")
            return 2
        print("  ✓ 已停止")
    else:
        print(f"端口 {args.port} 当前没有监听进程")

    if args.stop:
        return 0

    print(f"启动：python gui/server.py --port {args.port}（前台，Ctrl+C 停止）")
    try:
        return subprocess.call([sys.executable, os.path.join("gui", "server.py"),
                                "--port", str(args.port)], cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
