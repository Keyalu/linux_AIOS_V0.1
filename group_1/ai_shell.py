"""ai_shell.py — AI Shell（类 cosh 交互界面）。

任务书要求：实现类 cosh 的交互界面，支持自然语言输入和历史记录。
两种工作模式：
- 只接 HostAgent：输入 → 意图理解 → 展示（不执行）
- 注入 SystemCoordinator：输入 → 完整编排执行 → 展示结果
"""

from __future__ import annotations


class AIShell:
    """AI Shell —— Agentic OS 的用户交互入口（类似微软 cosh）。"""

    BANNER = "Linux Agentic OS Shell (cosh) —— 输入自然语言指令，help 查看 help，exit 退出"

    def __init__(self, host_agent, coordinator=None):
        self.host_agent = host_agent
        self.coordinator = coordinator
        self.history: list[dict] = []          # 持久性：会话历史（可落盘）

    # ---------------------------------------------------------- --
    def run(self) -> None:
        print(self.BANNER)
        while True:
            try:
                line = input("\ncosh> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.lower() in ("exit", "quit"):
                break
            if line == "help":
                self._help()
                continue
            if line == "history":
                self._show_history()
                continue
            self.handle(line)

    def handle(self, line: str) -> dict:
        """处理一条输入：有协调器走完整编排，否则只做意图理解。"""
        if self.coordinator is not None:
            bundle = self.coordinator.orchestrate(line)
            self.history.append({"user": line, "bundle": bundle})
            verdict = (bundle.get("audit") or {}).get("verdict", "-")
            ok = sum(1 for r in bundle.get("results", [])
                     if (r.get("tool_result") or {}).get("success",
                                                         r.get("success", False)))
            print(f"[编排] 结果 {ok}/{len(bundle.get('results', []))} 步成功 | 审计 {verdict}")
            return bundle
        intent = self.host_agent.understand_intent(line)
        self.history.append({"user": line, "intent": intent})
        print(f"[Intent] {intent['intent']} | action={intent['action']}"
              f" | target={intent.get('target', '')} | params={intent.get('params')}")
        return intent

    # ---------------------------------------------------------- --
    def _help(self) -> None:
        print("exit/quit 退出 | history 历史 | 其他任意自然语言指令")
        print("示例：帮我把「demo_task」整理一下并查重，最后备份")

    def _show_history(self) -> None:
        if not self.history:
            print("(空)")
            return
        for i, h in enumerate(self.history, 1):
            tag = h.get("intent", {}).get("action") or f"{len(h.get('bundle', {}).get('results', []))} 步"
            print(f"  {i}. {h['user']}  →  {tag}")
