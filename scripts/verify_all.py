"""verify_all.py — 一条命令跑全套离线验证（优化方案 五："验证清单"机器化）。

    python scripts/verify_all.py            # 5 项全查，任一 FAIL 退出码 1
    python scripts/verify_all.py --quick    # 跳过集成套（最快回路）

项目：① 全仓 py_compile ② 回归审计套 ③ 集成套 ④ 评测基准集 evals/
⑤ 前端 <script> node --check + 函数成对静态断言（红线 14）。
不启动服务、不真实发信 —— 全部离线确定性（真实端到端仍按 SKILL.md 清单：
main.py --demo + health_check.py）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# Windows 控制台 GBK 会炸中文/符号输出 —— 统一 UTF-8（老坑，见 pitfalls.md）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

QUICK = "--quick" in sys.argv


def _run(name: str, argv: list[str]) -> tuple[str, bool, str]:
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stderr or "") + (p.stdout or "")
    # 失败现场完整落盘（只打尾行会丢 FAIL 用例名 —— 排障踩过的坑）
    if p.returncode != 0:
        log_dir = os.path.join(ROOT, "logs", "verify")
        os.makedirs(log_dir, exist_ok=True)
        slug = re.sub(r"\W+", "_", name)[:40]
        with open(os.path.join(log_dir, slug + ".log"), "w",
                  encoding="utf-8") as f:
            f.write(out)
    tail = out.strip().splitlines()
    ok = p.returncode == 0
    return name, ok, (tail[-1] if tail else "")


def main() -> int:
    results = []

    # ① 全仓字节码编译（语法级防线）
    targets = [os.path.join(ROOT, d) for d in
               ("src", "group_1", "group_2", "group_3", "group_4",
                "group_5", "gui", "evals", "tests", "scripts")]
    results.append(_run("py_compile 全仓",
                        [sys.executable, "-m", "compileall", "-q",
                         *targets]))

    # ② 回归审计套
    results.append(_run("回归审计套 test_regression_audit",
                        [sys.executable, "-m", "unittest",
                         "tests.test_regression_audit"]))
    # ③ 集成套（--quick 可跳）
    if not QUICK:
        results.append(_run("集成套 test_integration_v10",
                            [sys.executable, "-m", "unittest",
                             "tests.test_integration_v10"]))
    # ④ 评测基准集
    results.append(_run("评测基准集 evals",
                        [sys.executable, "evals/run.py"]))

    # ⑤ 前端离线校验：node --check + 调用/定义成对（红线 14）
    html = open(os.path.join(ROOT, "gui", "index.html"),
                encoding="utf-8").read()
    js = "\n;\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
    tmp = os.path.join(ROOT, "tests", ".tmp_js_check.js")
    open(tmp, "w", encoding="utf-8").write(js)
    try:
        results.append(_run("前端 node --check", ["node", "--check", tmp]))
    except (FileNotFoundError, OSError):
        results.append(("前端 node --check", True, "node 不可用，跳过"))
    # 只查"裸调用"：`(` 前的标识符不得是 `.method(`（那是对象方法，不是页面函数）
    called = set(re.findall(r"(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(", js))
    builtin = {"if", "for", "while", "switch", "catch", "function", "return",
               "typeof", "new", "in", "of", "do", "else", "await", "fetch",
               "String", "Number", "Boolean", "Array", "Object", "Set", "Map",
               "Date", "JSON", "Promise", "RegExp", "Error", "parseInt",
               "parseFloat", "isNaN", "encodeURIComponent",
               "decodeURIComponent", "setTimeout", "setInterval", "clearTimeout",
               "console", "alert", "confirm", "prompt", "requestAnimationFrame",
               "structuredClone", "TextDecoder", "TextEncoder", "clearInterval",
               "var", "let", "const", "void", "yield", "delete", "ArrayBuffer"}
    missing = sorted(n for n in called - builtin
                     if re.search(rf"function\s+{re.escape(n)}\s*\(", js) is None
                     and re.search(rf"(?:const|let|var)\s+{re.escape(n)}\s*=",
                                   js) is None)
    dup_defined = sorted({fn for fn in re.findall(r"function\s+(\w+)", js)
                          if len(re.findall(rf"function\s+{re.escape(fn)}\s*\(", js)) > 1})
    ok5 = not missing and not dup_defined
    results.append(("前端函数成对断言", ok5,
                    f"缺定义={missing[:5]} 重定义={dup_defined[:5]}"
                    if not ok5 else "OK"))

    fails = 0
    print("=" * 70)
    for name, ok, tail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<34} {tail[:56]}")
        fails += 0 if ok else 1
    print("=" * 70)
    print(f"verify_all: {len(results) - fails}/{len(results)} 项通过"
          + ("（--quick）" if QUICK else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
