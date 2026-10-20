---
name: agent-os-dev
description: Agent_OS_v1.0（五组 Agentic OS，UFO² 架构课程项目）的专属开发、调试、扩展与排障工作流。当用户要求在本项目上开发、调试、扩展、排查任何功能（组1 AI Shell/组2 规划/组3 执行/组4 工具注册/组5 协调安全RAG、编排链路、Web 控制台、AT-SPI 控件自动化、LLM/SMTP 配置、打包移植）或初始化新开发环境时使用——即使用户没有明说"Agent_OS_v1.0"。
---

# Agent_OS_v1.0 开发工作流（专属）

本目录即完整可交付系统：五组实现 + 集成编排 + Web 控制台 + 测试，全部相对本目录运行，无绝对路径依赖。

## 第一步：环境与命令速查（全部相对本目录）

> **移植/上手指南**：`../简单跑起来_readme.md`（本目录的**上一级**）—— 目标机前置条件、干净拷贝步骤（必须排除 `system_out/` 的明文凭据）、5 步冒烟验证、7 页签手册、20 项功能勾选清单、13 条排障表、可粘贴自检脚本。给"换了台机器怎么跑起来"这类问题，直接引用它。

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
3. **确定性归一**：LLM 输出有随机性，凡影响执行语义的字段必须在组1 后处理层强制归一，不信任 LLM 原样输出。已有范式：`send_email.dry_run`（出现发邮件动作=真发，起草词=干跑；**覆盖方向必须保守单向**——规则与 LLM 任一判草稿即干跑，见 2026-09-19 审计 #21）、`attachment`（话语中的真实文件路径强制覆盖 LLM 的 null）、文件路径宿主环境解析。新增执行语义字段时照此办理。**动作选择本身也是执行语义字段**：规划后经 `group_2/route_policy.py` 决策表按计划图消费分析确定性改写通道（如裸搜索→web_search、数据链→mcp_search），改写留 route_reason/routes 审计——新增语义近邻工具（目标相同、通道不同）必须同步其歧义组，否则决策表会 blind spot。
4. **会话状态按运行隔离**：GUI 服务的 AppAgent 是常驻单例、跨运行复用，`escalated`/`self_healed` 会话列表必须在 `execute_plan` 开头重置——否则历史运行的提权/自愈记录泄进新会话，事件重复、审计对账失真。
5. **确认即执行所确认的方案**：`PENDING[token]` 存储完整 `{intent, plan, check}`，`/api/execute` 原样执行，执行阶段零 LLM 调用。**禁止确认后重新规划**——二次规划会导致确认方案≠执行方案（曾导致空附件邮件）、双倍延迟。
6. **敏感数据不外流**：`system_out/` 含 LLM Key 与 SMTP 授权码，打包/git 必须排除整个目录；文件整理类工具必须跳过系统账本文件。
7. **Mock/真实双轨降级链**：每个真实依赖（pyautogui/pyatspi/openai 类调用）都要有 Mock 降级路径且行为可测——任务书硬性要求，也是环境移植的保底。
8. **改前端 id 必须同步 JS 引用**：HTML 元素 id 改名后，全页扫描 JS 里所有 `$("#id")` 引用（曾因 `#pipe-tabs`→`#orch-tabs` 残留导致确认面板永远不显示、编排"卡住"）。
9. **安全检测面必须覆盖 goals/steps（2026-09-15 新增）**：`SecuritySandbox.check()` 的检测面由 `_surface()` 递归展开**整条意图**（顶层 + 每个 goal/step 的 action/target/params + user_text）。**只拼顶层 `params.cmd` 会让"危险命令藏在 `goals[].params.cmd`"整体绕过黑名单**（实测曾返回 `approved=True, risk_level=low`）。路径保护只作用于"会落盘/会外发/读敏感文件"的动作，且**不扫 user_text**（否则话语里提到 `/etc` 就整条被拒）。
10. **路径解析三模式，别用一条规则通吃（新增）**：`HostAgent._TARGET_MODE` / `_resolve_target(mode=…)` —— 目录级动作 `workspace`（找不到回落工作区）、产出型 `anchor`（相对名锚定工作区，不许落进程 CWD）、破坏/搜索 `strict`（**原样保留，交工具如实报"不存在"**）。配合组2 的 `_PATH_FALLBACK` 白名单：**缺参兜底只给"确实需要工作区目标"的动作**，`delete_file` 不在其中 —— 否则"删除一个不存在的路径"会变成"删除整个工作区"。
11. **Mock 降级必须判失败（新增）**：`web_*` 等通道不可用时返回 `success=False + simulated=True + degraded=True`。"动作没发生"就是失败；`simulated/degraded` 才是"降级而非真实故障"的标记。**禁止把降级写成 `success=True`**，也禁止在测试里把这种谎报断言成期望（曾把缺陷固化成契约）。
12. **确认/提权语义不许化妆（新增）**：`AppAgent.confirmer=None` 表示**无人值守**，由 `auto_escalate` 决定，审计记录 `confirm_mode="auto"/"human"`；被拒绝的提权请求进 `escalations_denied`，不得与"真的重试过"混为一谈。`confirm_mode="deny"`（"仅执行免提权步骤"）必须**先过滤 `requires_admin` 步骤再执行**，而不是只把 confirmer 换成恒 False。
13. **签名核对由编排层注入（新增）**：组5 不 import 组4 —— `main.build()` / `gui.build_system()` 调 `security.register_signatures(registry.functions() + skills.functions())`，把函数作为**数据**注入。`register_signature()` 记录 `module+qualname`，`_verify_signatures()` 按此反查对象（用 `__import__(工具名)` 是永远取不到的）。
14. **前端函数必须成对定义（新增）**：`gui/index.html` 里每个被调用的函数都要有定义（曾漏 `loadChatHistory`/`saveChatHistory`：首屏脚本中断 → 其后 `loadLLM/loadSMTP/loadSettings` 与 `#hash` 全不执行，且每轮对话都被 catch 改写成"请求失败"）。改前端后**必须** `node --check` 抽出的 `<script>` 并 grep 调用点/定义点计数。
15. **凭据类输出只报"已配置"（新增）**：掩码不得回显任何真实字符（`key[:4]+"****"+key[-4:]` 会泄露 8 位）。审计入库前统一走 `SecuritySandbox.mask_record()`（敏感**字段名**整条脱敏 + 字符串值内邮箱/密钥正则脱敏）。
16. **GUI 是本地高危接口，必须有同源防线（新增）**：所有 POST 走 `_same_origin_ok()`（`Host` 回环 + `Origin` **必带且同源** + `Content-Type: application/json`）。只绑 127.0.0.1 不构成防护 —— 浏览器对 127.0.0.1 的**跨站简单请求**不受同源策略阻止。前端所有 POST 必须带 JSON 头（回归测试有静态断言）。
17. **动作标识符是"一等输入"（新增）**：用户会直接抄 `system_check` / `read_file`（工具台、产物 JSON、对话里的"检测到系统操作：…"到处都印着它）。新增动作/工具时必须同步 ① 中文规则 或 ② `group_1/host_agent.py::_ACTION_ALIASES`（标识符→规范说法），否则"执行 system_check"这种输入会解析不出任何动作、静默落到 LLM 闲聊。没有中文规则或必填参数未给的名字，走 `src/capability_hints.py` 给**确定性参数说明**，不许让 LLM 猜参数。**2026-09-19 优化后**：类别/别名/组2 tool·skill 映射/提示词必备集已收敛为 `src/capabilities.py::CAPABILITIES` **一表派生**（别名表保持插入序=匹配行为），新增动作只需在表里登记一行；一致性由 `TestCapabilitySingleSource` 四条不变式机器守卫（别名必须能被规则解析回动作/映射目标必须已注册/提示词动作行无孤儿）。
18. **确定性路由的产物不许让 LLM 代言（新增）**：执行卡片只由服务端路由弹出，system prompt 里必须明令禁止"执行卡片已生成 / 正在等待确认 / 已开始执行"这类声称（模型会照着能力清单顺口编）。
19. **外部依赖要真握手探测，别只看 PATH（新增）**：`shutil.which("bwrap")` 只说明"装了"，不说明"能用"（Ubuntu 24.04 的 AppArmor 限制非特权 userns，bwrap 会报 `setting up uid map: Permission denied`）。沙箱/浏览器这类依赖要么真跑一次探针并缓存，要么如实降级并把原因写进结果。
20. **"磁盘上的源码"≠"进程里在跑的代码"（2026-09-19 修订）**：完整性/签名核对要盯**运行时代码的结构化摘要**（`_code_digest`：co_code 字节码+名称/变量表+行号+常量递归）。**禁止用 `marshal.dumps(func.__code__)` 当摘要** —— CPython 的 marshal 对 code 内字符串走引用表，**同一 code 对象在函数执行前后两次 dumps 的字节可能不同**，会造成间歇性"疑似被篡改"误判拦死全链路（实测 organize_downloads）。也不要拿 `inspect.getsource()` 的文件文本当判据（会把"改了代码没重启"报成篡改）。磁盘漂移只提示；核对以**注册表当前对象**为准（`register_live_source` 注入取数回调，仍是数据注入、组5 不 import 组4），登记后偷换注册表对象也要能查出。

## 常见扩展任务

配方全文（含锚点与易错点）在 `references/recipes.md`：

- **组4 加新工具**：实现 + schema 注册 → 自动进 GUI 工具台。**若要被 LLM 编排使用**：同步 组1 提示词动作表 + 组2 `_TOOL_ACTIONS` 映射 + 参数类型防御核对。
- **加意图动作链**：组1 词典/提示词 + 组2 `_SKILL_ACTIONS`/`_TOOL_ACTIONS` 映射 + 实体/参数归一。
- **加 GUI 桌面动作**：组1 词典 + 组2 `_GUI_ACTIONS` 步骤序列 + 组3 handler。
- **加用户可调设置**：`SETTINGS_DEFAULT` + `apply_settings` + 设置页表单。
- **加 MCP 工具**：`mcp_connector.py` 注册 + schema（真实数据源优先，失败降级 Mock）。

## 验证清单（改动后必做）

0. **一条命令全套离线验证**：`python scripts/verify_all.py`（全仓编译 + 双测试套 + evals 评测集 + node --check + 前端函数成对断言；任一失败完整现场落 `logs/verify/`；`--quick` 跳集成套）。
1. 七场景矩阵：`python3 main.py --demo` → 7/7（真实 LLM 在环有输出波动，单场景偶发变体可重试一次再判）。
2. 测试全量：`tests/test_integration_v10.py`（51 项）**+ `tests/test_regression_audit.py`（157 项，锁定 2026-09-15 审计与 2026-09-19 优化的契约）**，两天基线全绿（Windows 副本上"场景2 导航"因无文件管理器必红，属平台差异）。
3. GUI 接口体检：`python3 .agents/skills/agent-os-dev/scripts/health_check.py`（13 项端点；会真实发一封 SMTP 测试邮件和一次 LLM 测试调用）。
4. **评测基准集**：`python evals/run.py`（16 任务零 LLM 确定性判分，Mock 通道）。**找到新缺陷 → 先把复现文本写成 evals/tasks.py 任务，再动手修**（2026-09-19 落地当天首轮 12/16 当场抓出 4 个真缺陷，见 docs/优化改动报告_20260919.md）。
5. 浏览器验证：`chromium --headless --dump-dom http://127.0.0.1:8788/` 后检查工具列表已渲染（JS 无语法错误的标志）。
6. **前端改动的离线校验**（无浏览器时）：抽出 `<script>` 交给 `node --check`；再断言调用点/定义点计数（`loadChatHistory` 这类漏定义曾让首屏脚本中断；现已收进 verify_all 的"函数成对断言"项）。
7. **GUI 改动后的同源防线校验**：POST 必须带 `Content-Type: application/json`；用原始 socket 发一次 `text/plain` 形态请求应得 403（见 `docs/bugFix.md` §4.1 的 7 项冒烟）。

## 宿主工作区说明

本目录可独立打包移植（打包排除 `system_out/`——内含 LLM Key 与 SMTP 授权码）。宿主 `/home/keyal/桌面/111` 还有：替身版联调（`pipeline_demo/`，与本系统并存的"Mock 证据"）、111 根的旧工具控制台（gui/，8765，已停用）。对照联调时参考，日常开发不需要。

## 参考文件

- `references/pitfalls.md` —— **排障先读**：失败特征 → 根因 → 修复对照表
- `references/recipes.md` —— 扩展配方
- `references/testing.md` —— 测试兼容层与服务管理
