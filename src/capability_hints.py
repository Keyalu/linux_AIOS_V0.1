"""capability_hints.py — "只给了一个能力名"时的确定性应答。

动机：用户经常**直接抄标识符** —— 从工具台复制 `read_file`、从产物 JSON 里抄
`system_check`、或看到对话里"（检测到系统操作：disk_usage…）"后回一句
"执行 disk_usage"。而规则引擎只认**动作语义**（中文说法），不认名字：

    "执行 system_check" → 解析不出任何动作 → 不弹执行卡片
    → 落到 LLM 闲聊，模型还可能顺口编一句"执行卡片已生成，等待你确认"

本模块把这种情况变成**确定性的、可执行的下一步**：
  · 有对应中文规则且不缺必填参数 → 交给操作卡片路由（不在这里处理）；
  · 否则 → 直接回"这个能力要哪些参数、去哪儿点、怎么说"。

纯函数、无副作用，便于单测（GUI 服务端 import 时会真的起系统，不能直接单测）。
"""

from __future__ import annotations

import re

# 整条消息就是一个能力名（可带 执行/运行/启动… 前缀，可带括号参数）。
# 前缀表必须与 group_1/host_agent.py 的 _BARE_ACTION_RE 保持一致：两处都判
# "这是不是一个裸名字"，口径漂移会导致"提示说缺参数、路由却当成了别的动作"。
_BARE_CAP_RE = re.compile(
    r"^\s*(?:请|帮我|帮忙|麻烦|执行|运行|调用|启动|打开|跑|来一下|走一下|"
    r"start|run|exec)?\s*[：:，,]?\s*"
    r"([a-zA-Z][a-zA-Z0-9_]{3,})\s*(?:[（(].*[)）])?\s*$")

# 几个常见能力的"用自然语言怎么说"，用于给出可直接照抄的例子
EXAMPLES: dict[str, str] = {
    "read_file": "读一下 /tmp/aios_note.txt",
    "list_directory": "列出 /tmp 里的文件",
    "delete_file": "删除 /tmp/aios_note.txt",
    "copy_file": "把 /tmp/a.txt 复制到 /tmp/b.txt",
    "move_file": "把 /tmp/a.txt 移动到 /tmp/sub/",
    "write_file": "写一个文件 /tmp/a.txt 内容是 hello",
    "create_folder": "在 /tmp 下新建 project 文件夹",
    "send_email": "把 /tmp/a.txt 发给 someone@example.com",
    "run_command": "执行命令「ls -la」",
    "mcp_weather": "查询长沙天气",
    "mcp_search": "联网搜索湖南大学",
    "mcp_translate": "把 hello 翻译成中文",
    "web_open": "打开网页 https://example.com",
    "web_click": "点击网页里的“登录”",
    "web_state": "看看当前浏览器页面是什么",
    "web_extract": "提取当前网页内容",
    "take_screenshot": "截个屏保存到 /tmp/shot.png",
    "system_check": "检查系统状态",
    "disk_usage": "查一下磁盘使用情况",
}


def bare_capability_name(text: str) -> str:
    """整条消息就是一个能力标识符时返回它，否则返回 ""。"""
    m = _BARE_CAP_RE.match(text or "")
    return m.group(1) if m else ""


def lookup_bare_capability(text: str, catalog: dict | None):
    """消息就是某个已注册能力名时返回 (名字, 该能力的 schema 记录)，否则 None。"""
    name = bare_capability_name(text)
    if not name:
        return None
    rec = (catalog or {}).get(name)
    return (name, rec) if isinstance(rec, dict) else None


def required_params(rec: dict) -> list[str]:
    """该能力的必填参数名。"""
    return list(((rec.get("inputSchema") or {}).get("required")) or [])


def optional_params(rec: dict) -> list[str]:
    """该能力的可选参数名。"""
    sch = rec.get("inputSchema") or {}
    req = set(required_params(rec))
    return [p for p in (sch.get("properties") or {}) if p not in req]


def capability_hint(name: str, rec: dict) -> str:
    """把"给个名字就跑"变成可执行的下一步：缺什么参数、去哪儿点、怎么说。"""
    req = required_params(rec)
    opt = optional_params(rec)
    lines = [f"**{name}** —— {rec.get('description') or '（无描述）'}", ""]
    if req:
        lines.append("- **必填参数**：" + "、".join(f"`{p}`" for p in req))
    if opt:
        lines.append("- 可选参数：" + "、".join(f"`{p}`" for p in opt))
    example = EXAMPLES.get(name, "把要动的东西说清楚（例如路径或目标名）")
    lines.append(f"- **怎么跑**：① 到「工具台」左栏搜 `{name}`，按表单填参数执行"
                 f"（走权限闸门与统计）；② 或用一句自然语言说清目标 —— 例：`{example}`，"
                 f"说完整我会弹执行卡片，确认后才真实执行。")
    if req:
        lines.append("- 只给名字我没法开工：" + "、".join(f"`{p}`" for p in req)
                     + " 缺一不可，我不会替你猜。")
    return "\n".join(lines)
