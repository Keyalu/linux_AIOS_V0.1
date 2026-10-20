# Agent_OS_v1.0 优化方案（plan_优化）

> 依据：本仓库现有五组架构（组1 意图 / 组2 规划 / 组3 执行 / 组4 工具注册 / 组5 审计安全RAG）
> + 2026-09-14/15/19 三轮审计修复后的现状 + 20 条开发红线。
> 参考项目：OpenHands、UFO²、CoAct-1、browser-use、LangGraph、mem0/Letta/Zep、
> OS-Copilot、AppAgent、Open Interpreter、AgentBench/WebArena、Inspect AI 等。
> 每节标注【收益/成本/风险】，最后给三期落地路线图。

---

## 〇、现状一句话诊断

系统当前是"**契约驱动的静态流水线**"：intent → check → plan → execute → audit 一次走通，
诚实性、脱敏、复检在 919 审计后已基本闭环。瓶颈不在单点 bug，而在三个结构性约束：

1. **编排是"一轮一词"的直线**，没有观察-再规划回路（步骤失败只能自愈重试同一方案，不能改道）；
2. **知识只有"审计证据"没有"经验"**——系统每次运行学到的东西（哪个通道对哪个应用可靠、
   哪个控件树不可达）不会回流到下一次规划；
3. **安全与解耦靠纪律（红线 + 交叉断言测试）维持**，不是靠类型/协议结构维持，
   扩展面越大纪律越容易破（本轮 27 项发现里 8 项是"表没同步"类）。

以下方案都围绕解除这三个约束展开，且逐条核对与 20 条红线的兼容性。

---

## 一、架构翻新

### 1.1 事件流核心：五组契约改为"事件上的投影"【收益★★★★ / 成本★★★ / 风险中】

参考：OpenHands 的 EventStream（Agent 与 Environment 之间只有一条类型化事件流，
历史/回放/对账全部从流上重放得到）。

现状：`main.py`/`gui/server.py` 手工在编排节点上 `append(events)`，RUNS/audit/session_log
是三份"顺手攒出来的"字典。红线 5 要求"确认即执行所确认的方案"，红线 25 修复后才有
per-run 归档——本质上都是在**手工重建事件流**。

做法：
- 定义 8~10 个不可变事件类型（`IntentProposed / PlanCompiled / CheckPassed / CheckDenied /
  StepStarted / StepResult / EscalationRequested / EscalationDecided / RunFinished`），
  每个事件带 `seq/run_id/ts/payload`，JSONL 单调追加（复用 `runs/` 归档目录）。
- 五组契约**不动**（红线 1）：契约对象降格为"从事件流折叠出的快照"——
  `intent_json = fold(events, until=PlanCompiled)`。组5 对账（红线 13：只读落盘 JSON）
  天然升级为"重放事件流"，能回答"批准时刻看到了什么、执行时刻改了什么"逐字段 diff。
- GUI 前端从 500ms 轮询字典改为消费 `/api/events?since=<seq>`（游标式增量），
  时间线渲染直接吃事件类型。

红线兼容：红线 1 只约束"契约字段不许破坏"，事件是契约的**上游超集**；
红线 4（会话隔离）用 run_id 分片自然解决；红线 5 用 `PlanCompiled` 事件哈希锁死——
确认后重新规划会在事件流上出现第二个 `PlanCompiled`，测试可直接断言禁止。

### 1.2 执行期"观察-再规划"回路（受限 ReAct）【收益★★★★ / 成本★★★ / 风险高，须配闸门】

参考：CoAct-1（把"写代码调 API"作为原子动作，成功率远超纯 GUI 点击）、
OpenHands 的 agent-controller-iteration 循环、UFO² 的 Host-Agent 分解。

现状：组2 一次性编译全 plan，组3 失败只做元素级自愈（AppAgent self_healed），
不会"改道"。典型不可恢复场景：控件树不可达、dock 点击失效（现在靠 route_policy 预判）、
网页 DOM 改版。

做法（关键在"受限"二字，防止退化成黑盒 agentic 漫游）：
- 引入 `ReplanPolicy`：**只在步骤失败且失败类别 ∈ {element_not_found, degraded_channel,
  timeout} 时**，允许组2 基于"已完成步骤的结果摘要"做一次**增量补丁规划**
  （只允许替换失败步骤及其下游，已批准前缀不许动）；
- 补丁 plan 重新过组2→组5 复检（复用簇2 已建好的"执行前复跑 check"通道，`plan 复检` 函数
  现成），risk_level 升一档则回确认面板等待；
- 每次 replan 计数（上限 2），全量落事件流（`PlanPatched`），确认面板显示
  "原方案 vs 补丁方案" diff。

红线兼容：**这是对红线 5 的有意修订**——"禁止确认后重新规划"的动机是"二次规划导致
批准方案≠执行方案"。解法是把它变成"显式可见、可拦截的补丁事件"而不是隐式重规划：
前缀锁定 + diff 呈现 + 复检过闸。修订措辞建议进 SKILL.md 红线 5。

### 1.3 工具接入协议化：内部 MCP 化【收益★★★ / 成本★★ / 风险低】

参考：Model Context Protocol（Anthropic 主导，已成事实标准；`modelcontextprotocol/servers`
数官方 server）、OpenHands 的 tool plugin 化。

现状：组4 `tool_registry` + `mcp_connector`（mock）双轨；动作名单一命名空间靠
`_TOOL_ACTIONS`/`_ACTION_ALIASES`/中文规则/提示词**四处手工同步**（红线 17，本轮 #24
就是漏同步产物）。

做法：
- 把"注册一个能力"收敛为唯一入口 `CapabilitySpec{name, description, inputSchema, risk_hint,
  examples, channels}`，注册时**自动生成**：组1 提示词动作表片段、`_ACTION_ALIASES` 项、
  组2 `_TOOL_ACTIONS` 映射、`capability_hints` 参数说明——四处同步从"纪律"变"编译产物"；
- `mcp_connector` 从 mock 升级为真 stdio/SSE MCP client，外部 MCP server 的工具以
  `mcp:<server>/<tool>` 命名空间进注册表，schema 原样透传给组2（红线 2：组2 只看 schema JSON，
  天然满足）；
- 交叉断言测试（两份黑名单表口径）推广为"注册表派生一致性"测试：从 CapabilitySpec 生成的
  四张表永远一致，新增工具零手工同步点。

这是**根治"决策表/别名盲区"类回归**（报告 #23/#24 同构病灶）的结构性方案。

### 1.4 沙箱执行层独立成"Runtime"服务【收益★★★ / 成本★★★ / 风险中】

参考：OpenHands Runtime（Docker/local 双实现，Agent 不感知）、
bubblewrap/gVisor、Firejail。

现状：`run_command` 在宿主进程内 subprocess，安全靠黑名单+受保护路径（事后拦截）；
红线 19 已核实 bwrap 依赖要真握手探测，但探测结果没有接入执行路由。

做法：
- 抽 `IRuntime` 两实现：`LocalRuntime`（现状）与 `BwrapRuntime`（`--unshare-net` 或
  按动作授权的 minimal profile）；启动时探针（真跑一次 `echo ok`）决定默认路由，
  探针失败如实降级并把原因写进每个 result（红线 11 口径）；
- **危险动作强制走沙箱**：`DANGEROUS_PATTERNS` 命中但用户确认放行的命令，改在 bwrap 内执行
  （只读挂载工作区 + 可写 tmpfs），把黑名单从"唯一防线"降为"第一道防线"（纵深防御）；
- 浏览器通道同理：CDP 进程由 Runtime 管生管死，配合 1.5 的并发治理。

---

## 二、功能点设计

### 2.1 经验记忆层：从"审计证据"到"可回流的知识"【收益★★★★ / 成本★★ / 风险低】

参考：mem0（抽取-更新-检索三段式）、Letta/MemGPT（核心记忆块 + 归档记忆分层）、
Zep（时间知识图谱，事实带有效期）。

现状：组5 RAG 只做"历史会话文本入库→相似检索"，规划器从不消费它——记忆是**死档案**。

做法（不加新组，扩展组5→组2 的一条只读通道）：
- 每次运行结束，`Coordinator` 抽取结构化**经验条目**（JSON）：
  `{app, action, channel_used, outcome, failure_kind, elapsed_ms, patched:bool}`；
- 存 `system_out/experience.jsonl`（过 `mask_record`，红线 15 现成）；
- 组2 `route_policy` 消费：同类 (app, action) 最近 N 次某通道失败率高 → 决策表**软性降权**
  （不改硬规则，只影响并列候选的排序，理由写进 `route_reason`，前端可见）；
- 组1 意图阶段消费：高频成功动作链（"备份 reports 再发邮件"跑通过 5 次）→ 在对话助手
  Tab 给"一键复用"建议卡。

这等于给系统加了"用得越准"的飞轮，而所有回流都走既有的 route_reason 审计口径，
确定性可解释（不是让 LLM 自由发挥）。

### 2.2 跨平台 Grounding 后端：Windows UIA 适配 + 截图 VLM 兜底【收益★★★ / 成本★★★ / 风险低】

参考：UFO²（UIA 树 + 标号截图多模态grounding）、AppAgent（纯视觉+OCR 也可用）、
ScreenAgent、TuriX-CUA（Win/Mac 双平台）。

现状：组3 只写了 X11/AT-SPI + pyautogui，Windows 副本上场景1/2/3/5 诚实降级
（demo 3/7 的全部原因）。课程交付如果在 Linux 靶机演示没问题，但"移植性"叙事缺 Windows 半边。

做法：
- `group_3/grounding/` 拆后端接口：`list_windows/find_element/click_input`，
  Linux=AT-SPI（现有），Windows=`uiautomation` 库（pywinauto 后继，pip 即装）；
  Mock 后端保留（红线 7）；
- 兜底通道：AT-SPI/UIA 都拿不到目标时，截取窗口 + 元素画号（Set-of-Mark 提示），
  交给视觉 LLM 出坐标——正好复用现有 `llm_client`，点击精度用现有 DPI 感知修正；
- 收益：demo 从"Windows 3/7"拉到 6~7/7，课程报告"跨桌面框架"多一章实证。

### 2.3 场景评测基准集（Eval Harness）【收益★★★★ / 成本★ / 风险低】

参考：AgentBench、WebArena、OSWorld（369 个真实桌面任务）、Inspect AI（评测即代码：
任务定义 + scorer + 日志聚合）。

现状：验证手段是"7 场景 demo + 136 回归测试"，前者依赖真实 LLM 波动、后者锁代码行为，
**没有一个指标能回答"这周系统变好了还是变差了"**。

做法：
- 建 `evals/` 目录：20~30 个任务（现有 7 场景 + 每轮修 bug 时构造的对抗样本，如
  "goals[].params.cmd 藏 rm -rf"、"删除不存在路径"），每个任务带**自动判分器**
  （文件存在/邮件收到 mock/plan 结构断言，复用测试里的 `make_stack`）；
- `python evals/run.py --mock` 出通过率表（Mock 通道跑，确定性、零成本、可进 CI）；
  `--real` 抽样跑真 LLM；
- 每次改动跑 `--mock`，通过率写进 commit message——把"两天基线全绿"变成有数字的历史曲线。

### 2.4 回放调试器（Replayer）【收益★★★ / 成本★★ / 风险低】

参考：OpenHands 的 conversation replay、LangSmith 的 run tree。

做法（1.1 事件流的第一个自然衍生品）：GUI 加"回放"页签：选一次 `runs/<tag>`，
按事件 seq 拖动时间轴，逐步展示 意图/计划/改写留痕/步骤结果/截图。
排障时"为什么这一步点了这里"从翻 JSON 变成拖进度条。纯前端 + 读现有归档，零架构风险。

### 2.5 对话助手的"多轮意图栈"【收益★★★ / 成本★★ / 风险低】

现状：AI Shell/对话助手每轮独立编排，"把刚才那个文件再备份一下"的"刚才"靠 LLM 猜。
做法：会话级维护最近 K 个实体的短栈（文件路径/应用名/URL，来自 intent 归一产物），
注入组1 提示词的"对话上下文"块并在 `_extract_params` 里作为 `anaphora` 兜底源——
同样走确定性归一口径（红线 3），不依赖模型自觉。

---

## 三、性能优化

### 3.1 LLM 调用层【收益★★★ / 成本★】
- **意图缓存**：`hash(user_text + 附件摘要) → intent_json` 的进程内 LRU（TTL 5min）。
  同句连发（演示场景高发）直接秒回；缓存命中写入事件流（`cache_hit` 标记），审计不瞎。
- **规划分级**：规则路径（`_RULES` 已能覆盖多数）命中的意图**不再全量送 LLM 规划**，
  仅 risk≥medium 或缺参时才进 LLM 分支——实测可把平均延迟砍 1/3（规则路径当前也要过
  一次 LLM 确认动作）。
- **流式回填 UI**：`chat_stream` 已存在，编排侧 `llm_answer` 步骤接流式，
  长文本先出字后校验（校验失败只标红，不回滚显示）。

### 3.2 并发与连接【收益★★ / 成本★】
- **步骤级依赖图并行**：组2 已产出 `elements`/线性 steps；给 plan 加可选 `deps` 字段，
  无依赖步骤（如"查天气"+"查邮件"）在组3 线程池并行——红线 1 兼容（deps 是新增可选字段，
  老消费方忽略即可）。
- **CDP 常驻 + 页签复用**：浏览器冷启 ~2s/次；改为 run 内保活、跨 run 闲置 60s 回收
  （配合 1.4 Runtime 管生命周期）。簇5 的锁已解决安全共享，剩下的是复用。
- **ToolStats 写放大**：每次调用全量重写 JSON 账本；已有 buffered 版对齐行为，
  进一步可上 SQLite（单文件、`WAL` 模式、崩溃原子）——同时解决并发读一致性问题
  （簇7 ⏸"锁外共享态"在 SQLite 事务下自然消解）。

### 3.3 审计与检索【收益★★ / 成本★】
- RAG 检索现为线性扫描 JSONL：语料过千条后建倒排/向量索引（sqlite-vec 或 faiss-lite，
  纯 pip）；`system_out/` 排除打包的纪律不变（红线 6）。
- `mask_record` 出口统一后，可把脱敏从"每出口调用"下沉到"落盘包装器"（一个
  `safe_write_json()`），漏挂出口类回归（#12 同构）从根上消失。

---

## 四、安全演进（在已修 27 项之上的下一层）

### 4.1 从"黑名单"到"能力授权"（capability tokens）【收益★★★★ / 成本★★★】
本轮 #26 词界修复证明：字符串特征匹配永远在误伤/漏拦之间摇摆（攻击面是文本，
执行面是语义）。中期方向：
- 组5 签发**运行级能力令牌**：`{fs_read:[...], fs_write:[工作区], net:bool,
  exec_profile: enum, ttl, run_id}`，确认面板**直接展示这份清单**（用户批准的是能力集，
  不是看不懂的 JSON plan）；
- 组4 每个工具入口校验令牌（装饰器一处生效），黑名单降级为"起草令牌时的风险提示"
  而非执行期裁决；
- 收益：命令串内嵌路径（#5）、间接注入（#7）、别名缺口（#24）整类问题**在结构上不可表达**
  ——因为执行面校验的是解析后的路径参数，不是文本。

### 4.2 提示词注入的第二战场：结果侧扫描
`{{prev_result}}` 复检（#7 已修）解决"闸门数据"，但**网页/邮件正文本身**可携带
"请执行 rm -rf"诱导 llm_answer 复述或下游步骤。做法：所有外部内容进 plan 参数前过
`_surface`+危险扫描（现成函数），命中则参数值替换为 `<UNTRUSTED:摘要>` 占位 + 事件留痕。

### 4.3 完整性：注册表即清单（manifest）
每轮运行把 `{registry.functions() + skills.functions()}` 的结构化摘要（#17 已实现
`marshal` 摘要）哈希成 manifest 写进 `runs/<tag>/manifest.json`，跨 run 比对——
"这轮用的工具和上轮是不是同一套"成为可对账事实（配合 2.3 评测可防"测试通过但工具漂移"）。

---

## 五、工程质量（让红线从文档变成机器）

- **契约类型化**：五份契约用 dataclass/pydantic 定义（`src/interfaces.py` 已有雏形），
  组间传递只走类型对象，`.get("params")` 返回 None 类缺陷（#22）在构造期即报错；
  JSON 落盘仍是 dict（红线 1 的输出契约不变）。
- **CI 化**：`python -m unittest`（双套）+ `node --check` + `py_compile` + 2.3 的
  `evals/run.py --mock` 捆成一个 `scripts/verify_all.py`（`verify_commands.py` 已有雏形），
  一条命令进 pre-push/GitHub Actions。
- **热重载清单修正**：`gui/server.py` 只列 `group_*` 的问题（审计遗留）——1.3 注册表
  收敛后按 CapabilitySpec 模块自动派生清单。
- 死副本三文件删除（人工）、`session_id` 语义、锁外共享态三个 ⏸ 项：1.1 事件流（run_id
  分片）与 3.2 SQLite（事务）分别**顺带根治前两个**，剩锁外共享态可关闭。

---

## 六、三期路线图

| 期 | 目标 | 内容 | 量级 |
|---|---|---|---|
| **P1 快赢**（1~2 周，零架构风险） | 可度量、不 regress | 2.3 评测基准集；3.1 意图缓存+规划分级；3.3 mask 落盘下沉 + RAG 索引；五、契约类型化 + verify_all；删除三个 ⏸ 死副本（人工） | 每个 ≤200 行改动 |
| **P2 结构性**（3~6 周） | 消除"表没同步"回归族、加经验飞轮 | 1.3 CapabilitySpec 单一入口；2.1 经验记忆回流 route_policy；2.5 多轮意图栈；1.4 Runtime 抽象 + bwrap 真沙箱；4.2 结果侧扫描；2.4 回放器（依赖 P2 事件流最小版） | 涉及五组接口审查 |
| **P3 换代**（学期项目可做叙事章） | 从流水线到 agentic OS | 1.1 事件流核心；1.2 受限再规划回路（红线 5 修订）；2.2 跨平台 grounding（Win UIA + VLM 兜底）；4.1 能力令牌授权；3.2 依赖图并行 | 架构级，需全量回归 + 新契约测试 |

**每期的退出判据**：P1=评测基线数字建立且双测试套全绿；P2=新增工具"零手工同步点"演示
（注册即全链路可用）+ 经验回流使某场景 Mock 通过率上升；P3=事件流重放==现行 audit、
demo 在 Windows 达 6/7、补丁规划在对抗场景（DOM 改版 mock）下成功率 > 一次性规划。

---

## 七、参考项目速览（按本方案引用点）

| 项目 | 借鉴点 | 对应本节 |
|---|---|---|
| All-Hands-AI/OpenHands | EventStream、AgentController 迭代循环、Runtime 抽象、replay | 1.1 / 1.2 / 1.4 / 2.4 |
| microsoft/UFO²（本项目架构母版） | Host-Agent 分解、UIA+标号截图多模态 grounding | 2.2 |
| CoAct-1 (arXiv 2508.03923) | 编码动作作为一等原子操作，降 GUI 长链 | 1.2 长期方向 |
| modelcontextprotocol/* | 工具接入协议、命名空间 | 1.3 |
| mem0ai/mem0、letta-ai/letta (MemGPT)、getzep/zep | 抽取-更新-检索记忆三段式 / 分层记忆 / 时间知识图谱 | 2.1 |
| langchain-ai/langgraph | 图化编排、检查点续跑 | 1.1 / 3.2 |
| browser-use/browser-use | CDP 复用、DOM 索引化点击、降级标记 | 3.2 |
| OS-Copilot/OS-Copilot、microsoft/AppAgent、niuzaisheng/ScreenAgent、TuriX-CUA | 跨平台桌面 agent 形态、视觉兜底 | 2.2 |
| THUDM/AgentBench、web-arena-x/webarena、OSWorld、UKGovernmentBEIS/inspect_ai | 任务基准 + 自动判分 + 评测即代码 | 2.3 |
| openinterpreter/open-interpreter | 沙箱内代码执行模式 | 1.4 / 4.1 |
| containerd/gVisor、bubblewrap、Firejail | 真隔离与探针 | 1.4 |

---

## 八、明确不建议做的

1. **不要换成重型编排框架**（LangGraph/CrewAI 整体引入）：五组解耦 + Mock 双轨 + 课程
   "白盒可控"是评分叙事核心，引入框架等于把红线 2/3/5 的自主保证交给第三方调度。
   借鉴其模式（1.1/1.2）而非引入其代码。
2. **不要让 LLM 在确认后再碰 plan**：红线 5 的教训（空附件邮件）在 1.2 的设计里已被
   前缀锁定+复检围死，若去掉这两个约束就回到已知事故现场。
3. **不要上常驻多进程服务集群**：单机课程系统，ThreadingHTTPServer + SQLite 够用；
   并发问题的正确解法是事件流与令牌，不是加机器。
4. **不要在脱敏未下沉（3.3）前扩任何新持久化出口**：#12 的教训——出口越多裸奔面越大。

---

*文档生成：2026-09-19；配套阅读：docs/bugFind_logic_20260919.md（27 项清单）、
docs/bugFix_logic_20260919.md（修复对照表）、.agents/skills/agent-os-dev/SKILL.md（红线）。*
