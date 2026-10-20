"""capabilities.py — 动作能力单一命名空间（优化方案 1.3：把"四处同步"变"一表派生"）。

背景：一个动作要被 LLM 编排使用，历史上必须同步四张表 —— 组1 类别表
(_CATEGORIES)、组1 标识符别名表 (_ACTION_ALIASES)、组1 提示词动作表、
组2 动作映射表 (_TOOL_ACTIONS/_SKILL_ACTIONS)。2026-09-19 审计 #23/#24 的
根因正是这种手工同步（copy_file/move_file 等四个工具注册了却全链路失联）。
红线 17 说"新增动作必须同步"，本模块把它变成**结构上只需要同步一处**。

CAPABILITIES 每行一个标识符：
  cat    意图类别（组1 _CATEGORIES 派生源；纯别名行留空）
  alias  "动作标识符一等输入"的规范说法（组1 _ACTION_ALIASES 派生源）；
         收录标准不变：**换成说法后必须能被组1 规则表解析回来**（守卫测试断言）
  kind   规划归属：tool/skill=组2 映射表派生；gui=GUI 步骤序列动作；
         ""=纯别名行（工具标识符的口语桥，不新增规划映射）
  to     映射到的组4 工具/技能注册名（kind=tool/skill 时必填）
  prompt True=必须出现在组1 提示词"可用 action"行（守卫测试核对，双向：
         提示词里出现的动作名也必须在本表登记 —— 防"先写提示词后补表"）

注意：组1 中文规则 _RULES、参数抽取、GUI 步骤序列仍留在各组 —— 它们是
组内行为的实现细节，不是跨组契约；本表只收敛"跨组口径"（类别/别名/映射）。
"""

from __future__ import annotations

from typing import NamedTuple


class Cap(NamedTuple):
    cat: str = ""          # 意图类别
    alias: str = ""        # 标识符 → 规范说法（空=不收）
    kind: str = ""         # tool | skill | gui | ""
    to: str = ""           # 映射到的注册工具/技能名
    prompt: bool = False   # 是否要求出现在组1 提示词动作表


# 顺序 = 原 _ACTION_ALIASES 的迭代顺序（_canonical_phrase 首个命中即返回，
# 顺序即行为，勿重排）。纯规则动作行（weather 等）追加在后，无行为影响。
CAPABILITIES: dict[str, Cap] = {
    "system_check":          Cap("系统设置", "检查系统状态", "skill", "system_check", True),
    "disk_usage":            Cap("系统设置", "查看磁盘使用情况", "skill", "disk_usage", True),
    "organize_downloads":    Cap(alias="整理文件"),
    "find_duplicate_files":  Cap(alias="查找重复文件"),
    "backup_directory":      Cap(alias="备份目录"),
    "find_large_files":      Cap("文件操作", "查找大文件", "skill", "find_large_files", True),
    "find_files":            Cap("文件操作", "查找文件", "skill", "find_files", True),
    "cleanup_temp":          Cap("文件操作", "清理临时文件", "skill", "cleanup_temp", True),
    "delete_file":           Cap("文件操作", "删除文件", "tool", "delete_file", True),
    "write_file":            Cap("文件操作", "写入文件", "tool", "write_file", True),
    "copy_file":             Cap("文件操作", "复制文件", "tool", "copy_file", True),
    "move_file":             Cap("文件操作", "移动文件", "tool", "move_file", True),
    "read_file":             Cap("文件操作", "读取文件", "tool", "read_file", True),
    "list_directory":        Cap("文件操作", "列出目录", "tool", "list_directory", True),
    "web_state":             Cap("信息查询", "查看网页状态", "tool", "web_state", True),
    "create_folder":         Cap("应用控制", "新建文件夹", "gui", prompt=True),
    "take_screenshot":       Cap("文件操作", "截屏", "tool", "take_screenshot", True),
    "send_email":            Cap("应用控制", "发送邮件", "tool", "send_email", True),
    "run_command":           Cap("系统设置", "执行命令", "tool", "run_command", True),
    "open_url":              Cap("应用控制", "打开网页", "tool", "open_url", True),
    "open_app":              Cap("应用控制", "打开应用", "gui", prompt=True),
    "navigate":              Cap("应用控制", "导航到目录", "gui", prompt=True),
    "switch_window":         Cap("应用控制", "切换窗口", "gui", prompt=True),
    "click_element":         Cap("应用控制", "点击", "gui", prompt=True),
    "web_search":            Cap("信息查询", "联网搜索", "tool", "web_search", True),
    "web_click":             Cap("应用控制", "点击网页元素", "tool", "web_click", True),
    "web_extract":           Cap("信息查询", "提取网页内容", "tool", "web_extract", True),
    "web_close_browser":     Cap("应用控制", "关闭浏览器", "tool", "web_close_browser", True),
    "mcp_weather":           Cap(alias="查询天气"),
    "mcp_translate":         Cap(alias="翻译"),
    "llm_answer":            Cap(alias="介绍一下"),
    # —— 以下为规则/提示词侧动作名（无独立别名行需求，kind 决定规划归属）——
    "organize":              Cap("文件操作", kind="skill", to="organize_downloads", prompt=True),
    "find_duplicates":       Cap("文件操作", kind="skill", to="find_duplicate_files", prompt=True),
    "backup":                Cap("文件操作", kind="skill", to="backup_directory", prompt=True),
    "weather":               Cap("信息查询", kind="tool", to="mcp_weather", prompt=True),
    "search":                Cap("信息查询", kind="tool", to="mcp_search", prompt=True),
    "mcp_search":            Cap("信息查询", kind="tool", to="mcp_search"),
    "translate":             Cap("信息查询", kind="tool", to="mcp_translate", prompt=True),
    "web_open":              Cap("应用控制", kind="tool", to="web_open", prompt=True),
    "answer":                Cap("信息查询", kind="tool", to="llm_answer", prompt=True),
}

# ---------------------------------------------------------------
# 派生视图（组1/组2 直接以这些对象为表，不再是各自的手抄字面量）
# ---------------------------------------------------------------

CATEGORIES: dict[str, str] = {
    name: c.cat for name, c in CAPABILITIES.items() if c.cat}

ACTION_ALIASES: dict[str, str] = {
    name: c.alias for name, c in CAPABILITIES.items() if c.alias}

# 组2 规划映射表：intent 动作名 → 组4 注册名
TOOL_ACTION_MAP: dict[str, str] = {
    name: c.to for name, c in CAPABILITIES.items() if c.kind == "tool"}
SKILL_ACTION_MAP: dict[str, str] = {
    name: c.to for name, c in CAPABILITIES.items() if c.kind == "skill"}
GUI_ACTION_NAMES: frozenset[str] = frozenset(
    name for name, c in CAPABILITIES.items() if c.kind == "gui")

PROMPT_REQUIRED: frozenset[str] = frozenset(
    name for name, c in CAPABILITIES.items() if c.prompt)


def rule_resolvable(action: str) -> bool:
    """守卫测试用：alias 的规范说法要能被组1 规则表解析出动作。"""
    return action in {c.to for c in CAPABILITIES.values() if c.to} \
        or action in CAPABILITIES
