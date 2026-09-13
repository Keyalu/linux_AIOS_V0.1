---
name: agent-os-dev
description: Agent_OS_v1.0（五组 Agentic OS，UFO² 架构课程项目）的专属开发、调试、扩展与排障工作流。当用户要求在本项目上开发、调试、扩展、排查任何功能（组1 AI Shell/组2 规划/组3 执行/组4 工具注册/组5 协调安全RAG、编排链路、Web 控制台、AT-SPI 控件自动化、LLM/SMTP 配置、打包移植）或初始化新开发环境时使用——即使用户没有明说"Agent_OS_v1.0"。
---

# Agent_OS_v1.0 开发工作流（专属）

本目录即完整可交付系统：五组实现 + 集成编排 + Web 控制台 + 测试，全部相对本目录运行，无绝对路径依赖。

## 第一步：环境与命令速查（全部相对本目录）

```bash
# 系统控制台（8788；SMTP/LLM 配置从 system_out/ 自动加载，重启不丢）
python3 gui/server.py
# 任务书七场景联调矩阵（PASS/FAIL 结果表）
python3 main.py --demo
# 单指令编排（CLI）/ AI Shell 交互
python3 main.py --text "查询长沙天气"
python3 main.py --shell
# 测试（有 pytest 用 python -m pytest tests/ -v；无 pytest 用自带兼容层）
python3 .agents/skills/agent-os-dev/scripts/pytest_shim.py tests/test_integration_v10.py
```

服务健康检查：`python3 .agents/skills/agent-os-dev/scripts/health_check.py`（13 项端点扫描；会产生一次 LLM 测试调用和一封发给发件邮箱自身的测试邮件）。

行为异常、编排卡住、测试失败时，**先读 `references/pitfalls.md`**（失败特征 → 根因 → 修复的完整对照表，收录本项目全部实战踩坑）。

## 开发红线（每条都踩过真实坑，违反必出回归）

1. **接口契约不可破坏**。五组输出契约：intent_json / plan_json(steps+elements) / result_json(success+before_state+after_state) / tool_result_json / check_json。改动字段前先确认下游（组2 消费 intent、组3 消费 plan、组5 对账 session+stats）。
2. **组间解耦纪律**：组2 只看 schema JSON（禁止 import 组4）；组3 只走公开契约（`registry.call` / `skills.call_skill`）；组5 只读落盘 JSON（禁止 import 组4 任何代码）。违反会让"Mock 换真实零改动"的架构承诺失效。
3. **确定性归一**：LLM 输出有随机性，凡影响执行语义的字段必须在组1 后处理层强制归一，不信任 LLM 原样输出。已有范式：`send_email.dry_run`（出现发邮件动作=真发，起草词=干跑）、`attachment`（话语中的真实文件路径强制覆盖 LLM 的 null）、文件路径宿主环境解析。新增执行语义字段时照此办理。**动作选择本身也是执行语义字段**：规划后经 `group_2/route_policy.py` 决策表按计划图消费分析确定性改写通道（如裸搜索→web_search、数据链→mcp_search），改写留 route_reason/routes 审计——新增语义近邻工具（目标相同、通道不同）必须同步其歧义组，否则决策表会 blind spot。
4. **会话状态按运行隔离**：GUI 服务的 AppAgent 是常驻单例、跨运行复用，`escalated`/`self_healed` 会话列表必须在 `execute_plan` 开头重置——否则历史运行的提权/自愈记录泄进新会话，事件重复、审计对账失真。
5. **确认即执行所确认的方案**：`PENDING[token]` 存储完整 `{intent, plan, check}`，`/api/execute` 原样执行，执行阶段零 LLM 调用。**禁止确认后重新规划**——二次规划会导致确认方案≠执行方案（曾导致空附件邮件）、双倍延迟。
6. **敏感数据不外流**：`system_out/` 含 LLM Key 与 SMTP 授权码，打包/git 必须排除整个目录；文件整理类工具必须跳过系统账本文件。
7. **Mock/真实双轨降级链**：每个真实依赖（pyautogui/pyatspi/openai 类调用）都要有 Mock 降级路径且行为可测——任务书硬性要求，也是环境移植的保底。
8. **改前端 id 必须同步 JS 引用**：HTML 元素 id 改名后，全页扫描 JS 里所有 `$("#id")` 引用（曾因 `#pipe-tabs`→`#orch-tabs` 残留导致确认面板永远不显示、编排"卡住"）。

## 常见扩展任务

配方全文（含锚点与易错点）在 `references/recipes.md`：

- **组4 加新工具**：实现 + schema 注册 → 自动进 GUI 工具台。**若要被 LLM 编排使用**：同步 组1 提示词动作表 + 组2 `_TOOL_ACTIONS` 映射 + 参数类型防御核对。
- **加意图动作链**：组1 词典/提示词 + 组2 `_SKILL_ACTIONS`/`_TOOL_ACTIONS` 映射 + 实体/参数归一。
- **加 GUI 桌面动作**：组1 词典 + 组2 `_GUI_ACTIONS` 步骤序列 + 组3 handler。
- **加用户可调设置**：`SETTINGS_DEFAULT` + `apply_settings` + 设置页表单。
- **加 MCP 工具**：`mcp_connector.py` 注册 + schema（真实数据源优先，失败降级 Mock）。

## 验证清单（改动后必做）

1. 七场景矩阵：`python3 main.py --demo` → 7/7（真实 LLM 在环有输出波动，单场景偶发变体可重试一次再判）。
2. 测试全量：`tests/test_integration_v10.py`（13）+ 宿主工作区其余套件，当前基线全绿。
3. GUI 接口体检：`python3 .agents/skills/agent-os-dev/scripts/health_check.py`（23 项端点；会真实发一封 SMTP 测试邮件和一次 LLM 测试调用）。
4. 浏览器验证：`chromium --headless --dump-dom http://127.0.0.1:8788/` 后检查工具列表已渲染（JS 无语法错误的标志）。

## 宿主工作区说明

本目录可独立打包移植（打包排除 `system_out/`——内含 LLM Key 与 SMTP 授权码）。宿主 `/home/keyal/桌面/111` 还有：替身版联调（`pipeline_demo/`，与本系统并存的"Mock 证据"）、111 根的旧工具控制台（gui/，8765，已停用）。对照联调时参考，日常开发不需要。

## 参考文件

- `references/pitfalls.md` —— **排障先读**：失败特征 → 根因 → 修复对照表
- `references/recipes.md` —— 扩展配方
- `references/testing.md` —— 测试兼容层与服务管理
