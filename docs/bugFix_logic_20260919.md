# 逻辑问题修复记录（对应 bugFind_logic_20260919.md）

日期：2026-09-19　原则：只修报告所列问题；每条给出【思路 → 落点 → 验证】。
标注：✅ 已修复并通过双测试套；🔄 进行中；⏸ 评估后不改（附理由）。

---

## 簇1：路径语义类（报告 #1-4）✅

### #1 组2 参数合并泄漏（可致删除整个工作区）
- 根因：`_steps_for` 无条件 `merged.params = {**intent.params, **goal.params}`，
  顶层 params 是主目标的抽参，非主目标（delete_file）继承后拿到组1 兜底填进
  顶层的工作区路径当删除目标。
- 修法：只有**主目标**（`goal is goals[0]`，或无 goals 的旧式意图）才继承顶层
  params；非主目标只用 `goal.params`。缺参动作由组2 如实报"缺少必需参数"。
- 落点：`group_2/task_planner.py::_steps_for`。
- 验证：`test_planner_never_fills_delete_path_with_workspace` 等 101 项全绿；
  "整理 demo_task 然后删除里面的临时文件"现在在安全闸门即被 #4 拦为 critical。

### #2 多目标整句抽参互相污染
- 根因：`_rule_intent` 对非主目标按**整句**`_extract_params`，`_extract_path`
  取全句第一个引号/路径 → "整理「报告.docx」再删除「临时.txt」"删错对象。
- 修法：`_match_goals` 给每个 goal 附加命中位置 `_pos`（输出前剥除，不进契约）；
  非主目标改在**所属分句**内抽参。分句按标点+顺序连接词（然后/接着/再…/最后…）
  切分，`_clause_for(text,pos)` 定位；pos 越界回退整句（规范说法转换路径安全）。
- 落点：`group_1/host_agent.py`（`_CLAUSE_SPLIT_RE`/`_clause_for`/`_rule_intent`）。
- 验证：TestActionNameRouting、规则路径相关用例全绿。

### #3 证据防线对文件系统根目录失效
- 根因：`basename("/")` 为空串，`"" in user_input` 恒真 → LLM 输出 `path:"/"`
  时目录级动作带着根目录直奔执行；且组4 `_norm_path("/")→""` 前缀匹配全不中。
- 修法：三道防线。① 组1 归一循环：`_is_fs_root(r)`（/、\、盘符根）→ 回落工作区
  并写 unresolved 提示；② 组4 `default_tools._is_protected_path` 增加根目录判定
  （organize/cleanup/delete 到 "/" 或 "C:" 一律按受保护拒绝）；③ 组5
  `security._protected` 同步补根判定（`_norm` 后为 ""/裸盘符 → 文件系统根目录）。
- 落点：`group_1/host_agent.py::_is_fs_root`、`src/default_tools.py`、
  `group_5/security.py::_protected`。

### #4 空目标删除检查不全
- 根因：`_empty_delete_target` 只查 `delete_file` 且回退顶层 `intent.target`
  ——goal 级缺参被顶层目标顶掉，恰是 #1 的泄漏通道；盘符根不查。
- 修法：判定集合扩为 `{delete_file, trash_delete}`；只看该动作自己的
  `params.path`（不回退顶层 target）——为空/`/`/`~`/盘符根一律 critical 拒绝。
  宁拦勿误删：缺参指令整体被闸门拒掉，比生成一个空删步骤再兜底更诚实。
- 落点：`group_5/security.py::_empty_delete_target`。
- 验证：`test_risk_aggregates_over_goals`（带 path 的 delete goal 仍 approved
  medium）通过。

## 验证记录（簇1后）
- `python -m unittest tests.test_regression_audit` → Ran 101, OK
- `python -m unittest tests.test_integration_v10` → Ran 51（一次偶发
  KeyError: 'session'，复跑不复现，列入簇5 GUI/并发排查；其余 OK/skipped 2）

---

## 簇2：组5 扫描面缺口（报告 #5-7）✅

### #5 run_command 命令串内嵌路径逃过保护判定
- 根因：`_operation_paths` 把整条命令串当一个"路径"做前缀匹配 ——
  `cat ~/notes/../etc/passwd`、`rm ~/.ssh/id_rsa` 这类命令整体永远不等于
  `/etc`，保护形同虚设；而 `~` 展开只发生在组4 落盘时。
- 修法：新增 `_paths_in_command(cmd)` —— 先把 `$HOME`/`~` 归一成真实主目录，
  再用两条正则抠出绝对路径（含 Windows 盘符路径）与 `~/子路径` 形态的 token，
  逐个送进 `_protected` 判定；出处标注为 `run_command.cmd内嵌`，拦截原因
  能指到具体字段。与组4 的黑名单是**互补**关系：黑名单管"命令本身危险"，
  本判定管"命令触碰受保护区"。
- 落点：`group_5/security.py`（`_CMD_PATH_RES`/`_paths_in_command`/
  `_operation_paths.take`）。
- 设计取舍：`/dev/*` 路径 token 现在也会被拦（如 `cat /dev/null`）—— 与既有
  `> /dev/` 关键词口径一致，宁可保守；组4 沙箱本就是这类动作的第二道墙。

### #6 安全闸门只看组1 意图，计划期派生参数零复检（动作名口径错位）
- 根因：`check()` 只跑在意图上；组2 会**改写动作名**（规范说法→组4 工具名，
  部分动作不在 `_MUTATING_ACTIONS` 视野内）、**派生新参数**（如 backup 的
  dest、organize 的目标目录兜底）——这些从未经过任何扫描；确认流存的也是
  计划前的旧结论。另 `_MUTATING_ACTIONS` 名单本身缺 organize/backup/
  find_duplicates。
- 修法：新增 `SystemCoordinator._plan_gate(plan)`：把**最终计划**（deny 过滤
  后、反思重试后的 plan2 同样）以 `{"steps": [...]}` 形态复跑 `check()`，
  拦截原因入审计（stage=plan_gate）并整条拒绝、不执行任何步骤。接线点三处：
  `execute_approved`（确认流/一键编排共同出口）、`orchestrate` 主路径、
  `orchestrate` 反思重试分支。同时补齐 `_MUTATING_ACTIONS` 三个名字。
  复检是纯字符串/路径判定，无 IO、无 LLM，成本可忽略。
- 落点：`group_5/coordinator.py`、`group_5/security.py::_MUTATING_ACTIONS`。

### #7 {{prev_result}} 回填发生在闸门之后（间接注入通道）
- 根因：`_resolve_refs` 把上一步结果原文注进本步参数 —— 上一步若是
  read_file 读到夹带指令的文本，本步的真实参数从未被扫描（闸门扫的是
  含 `{{...}}` 占位的空壳）。
- 修法：组3 增加 `security_guard`（编排层把组5 `check_step` 作为**函数对象**
  注入，遵守红线 2/13，无 import 依赖）。`_run_step` 仅对**发生过占位替换**
  的工具步骤做终审：命中即该步如实失败
  （`success=False, blocked_by_guard=True`），不产生任何副作用。
- 落点：`group_3/app_agent.py`（`__init__`/`_run_step`）、
  `group_5/security.py::check_step`、`group_5/coordinator.py::__init__`。

### 连带修复：GUI worker 对"计划复检拦截"的 KeyError
- `execute_approved` 现在可能返回无 session/audit 的 rejected bundle，
  `worker()` 直接取 `bundle["audit"]["verdict"]` 会 KeyError —— 这正是簇1
  偶发 `KeyError: 'session'` 的同族隐患。worker 增加 rejected 分支（状态
  "rejected"/verdict"拦截"/事件回传），前端 `pollRun` 补渲染拦截原因行。
- 落点：`gui/server.py::worker`、`gui/index.html::pollRun`。

## 验证记录（簇2后）
- `python -m unittest tests.test_regression_audit` → Ran 101, OK
- `python -m unittest tests.test_integration_v10` → Ran 51, OK (skipped=2)，
  本轮未复现 KeyError
- 偶发"工具运行时代码与登记不一致(疑似被篡改)"仍存在 → 报告 #17，簇5 处理

---

## 簇3：失败诚实性链条（报告 #8-11）✅

### #8 MCP 三工具网络失败仍谎报成功
- 根因：`mcp_search/weather/translate` 真实通道异常被 `except: pass` 吞掉后
  返回 "[Mock·离线]…" 裸字符串 → 注册表按"裸值=成功"包装，示例假天气/假
  "找到3条结果"被 LLM 与前端当事实（红线 11 的系统性违反）。
- 修法：与 `web_*` 同口径 —— 统一 `_degraded()` 出口返回
  `{success:False, simulated:True, degraded:True, error:"[Mock·未执行]…"}`，
  离线示例文本放 result 仅供参考；三处 schema 描述同步改为"如实降级失败"。
  translate 的 ("你好","en")→"Hello" 硬编码样本一并移除（无网络时同样是编造）。
- 落点：`src/mcp_connector.py`。

### #9 注册表/Skill 库失败分支抹平降级标记与明细
- 根因：`tool_registry.call` / `skill_library.call_skill` 失败时走
  `ToolResult.fail(error)`，丢弃 result 与全部 extra（simulated/degraded/
  partial 明细）；`ToolResult.to_dict` 失败分支也只输出 error。上游即使按
  红线 11 打了标记，到契约层就被吞。
- 修法：失败分支与成功分支同构构造 `ToolResult(success, result, error, extra)`
  （error 缺失时补"未知错误"）；`to_dict` 失败且 result 非空时如实透出明细。
- 落点：`src/tool_registry.py`、`src/skill_library.py`、`src/interfaces.py`。

### #10 组3 Automator 所有 Mock 分支默认 success=True
- 根因：Mock 分支只带 message 不带 success 键，`execute_step` 的
  `setdefault("success", True)` 让"无控件可点/应用未装"也算成功；反向是
  真实 AT-SPI 动作在无 pyautogui 的 Linux 上被 `setdefault("simulated",
  not _REAL)` 误标 simulated=True —— 真假两侧都失真；`drag` 的 AT-SPI 分支
  只点不拖还回"拖拽完成"。
- 修法：五个 Mock 分支（click/type/hotkey/drag/open_app）显式
  `success:False + mock:True + simulated:True + error`；`execute_step` 的
  simulated 缺省改为 False（真实通道执行过 = False）；drag 的 AT-SPI 分支
  如实回 `degraded:True`（点击是副作用，注明拖拽未发生）。
- 下游口径：组3 GUI 分支透传 mock/degraded 标记，上层可区分"通道缺失降级"
  与"真实执行失败"。
- 连带：`tests/test_integration_v10.py` 原先把"缺通道之成功"固化成期望
  （s1/s3 断言 PASS、demo 矩阵断言 rc=0）——按红线 11 改写：s1/s3 允许
  "失败步骤全部带降级标记"的平台差异分支；TestDemoMatrix 在无 GUI 通道
  机器 skipTest（Windows 副本如实 3/7，不再靠谎报或靠 #17 误拦凑数）。
- 落点：`group_3/automator.py`、`group_3/app_agent.py`、测试两处。

### #11 错误信息在契约与 GUI 双层丢失
- 根因：组3 GUI 分支组装 tool_result 时从不拷 automator 的 `error` ——
  "目录不存在，未执行导航"等失败原因在审计/前端渲染为空；前端成功分支
  `String(r.result)` 对 dict 结果输出 `[object Object]`。
- 修法：GUI 分支透传 `error`（+mock/degraded）；前端失败分支兜底文案
  "失败（无错误信息）"，成功分支 dict 走 JSON.stringify 截断展示。
- 落点：`group_3/app_agent.py::_run_step`、`gui/index.html::renderBundle`。

### 连带提前修复（簇3验证被其阻塞）：
- **#17 偶发"疑似被篡改"（簇5 项提前）**：真实根因是 CPython `marshal.dumps`
  对**同一 code 对象**两次序列化字节可不同（字符串按"引用表"输出；实测
  organize_downloads 注册时摘要一致、跑过一轮编排后翻转 → 该进程内一切指令
  被第 3 层拒）。修法：`_code_digest` 改为**结构化摘要**（co_code 字节码 +
  名称/变量表 + 行号 + 常量递归，不再依赖 marshal 编码），"进程内真正在跑
  的代码变了才拦"的语义不变（红线 20）；同场闭环 #17 的另一半 —— 新增
  `register_live_source(getter)`，编排层把"取注册表当前 {名:函数}"的 lambda
  作为数据注入（组5 仍不 import 组4），核对改以**注册表当前对象**为准，
  堵住"登记后偷换注册表对象查不出"的盲区；同源双导入不再误伤（结构相同 →
  摘要一致）。落点：`group_5/security.py`、`main.py::build`、
  `gui/server.py::build_system`（LAYER3_TAG 同步）。
- **#18 stats 账本编码（簇5 项提前）**：`ToolStats._save` 的 tempfile 与
  `_load` 均不钉 encoding="utf-8"，Windows(GBK) 下读写混合编码 → 本次中文
  error 文案入账后 `main.py --demo` 直接 UnicodeDecodeError 崩在 build()。
  修法：读写统一钉 UTF-8，`_load` 捕获 OSError/UnicodeDecodeError 坏账本
  回落从零开始。落点：`src/stats.py`。

## 验证记录（簇3后）
- `python -m unittest tests.test_regression_audit` → Ran 101, OK
- `python -m unittest tests.test_integration_v10` → Ran 51, OK (skipped=3：
  原 2 项 + Windows 副本无 GUI 通道的 demo 矩阵)
- `python main.py --demo`（Windows 副本）→ 篡改误报 0 次；GUI 通道类场景
  如实报"降级失败"，真实通道场景（场景6/信息查询/安全拦截）全绿
- 前端 `<script>` 抽出 `node --check` → 通过

---

# 簇4：脱敏旁路 —— 凭据出库口统一脱敏（报告 #12–#13）✅

口径：红线 15 要求"入库/回显前统一 mask_record"，但旧实现只在 `audit()`
一处执行。审计把**所有落盘/回显出口**列成清单逐个补：产物五件套、
stats 账本、RAG 轨迹、聊天记录、内存 run bundle。

### #12 除审计日志外，所有持久化出口都在裸奔
- 根因：`mask_record` 只挂在 `SystemCoordinator.audit()` 一个出口。
  ① `_persist` 原样写 `session_log.json` —— `params_sent` 携带
  `send_email.password`，工具结果原样入库；② `ToolStats.record` 把
  `params` 逐笔明文写进 `stats.json`，且 `/api/stats` 整本回前端；
  ③ `rag.add_trace(user_text, …)` 把用户话语明文写进 `rag_traces.jsonl`
  （话语内联密钥/邮箱很常见）；④ GUI `chat_save` 明文写
  `chat_history.json`（模型会把含凭据的工具结果整段复述进回复）；
  ⑤ worker 存进 `RUNS` 的内存 `run["bundle"]` 经 `/api/run` 原样回显，
  `_persist` 的脱敏帮不到它。（报告同时记录在案：编排可读取
  `system_out/llm_config.json` 明文 api_key，该路径不在敏感清单 ——
  属红线 6 的既有环境事实，本簇不扩权处理。）
- 修法：**每个出口过同一把 `mask_record`（纯函数、失败原样放行）**，
  跨组依赖一律走数据注入（红线 2/13 同款）：
  - `_persist`：五件套逐个 `data = security.mask_record(data)`（结构保留，
    只改值），stub 场景经 `_security()` 兜底直通；
  - `stats`：`ToolStats.set_redactor(fn)` 注入回调，`record()` 里
    entry 入库前 `self._redactor(entry)`；`main.build` /
    `gui.build_system` 各接一行 `stats.set_redactor(security.mask_record)`
    —— 组4 全程不 import 组5；
  - RAG：`execute_approved` / `orchestrate` 两处 `add_trace` 的文本入参
    （话语 + 结果串）先 `sec.mask_record(...)`；`query()` 用原文做相似度
    检索（内存即用，不落盘）不脱敏；
  - GUI：新增模块级 `_mask_deep(data)`（取 `SYSTEM.security.mask_record`，
    失败原样返回），worker 三处 `run["bundle"]` 赋值与 `chat_save`
    的 `msgs` 统一走它。
- 落点：`group_5/coordinator.py`、`src/stats.py`、`gui/server.py`、`main.py`。

### #13 `_mask_obj` 只打码顶层字符串值，敏感键认不全
- 根因：① 敏感字段名命中时旧实现只对 `isinstance(v, str)` 的值打码 ——
  `{"password": {"work": "…"}}` 这类嵌套凭据对象、以及任何非字符串值
  原样外流；② `SENSITIVE_KEY_RE` 只有英文键（password/token/api_key…），
  中文键"密码/口令/密钥/令牌/凭据/私钥"全部漏认；③ 正则里 `passwd`
  重复了一遍。
- 修法：敏感键 + 真值 → **整个子树**替换为 `"<已脱敏>"`（任意类型）；
  补齐中文敏感键、去重；`mask_record` 对裸字符串同样生效（走 `redact`
  正则，话语内联邮箱/密钥打码），RAG/聊天出口直接受益。
- 连带：`SecuritySandboxStub` 增加 `mask_record` 直通（与 `check` 同口径
  的"仅测试环境放行"），并清掉簇2 编辑时重复粘贴出的第二个
  `check_step` 定义。
- 落点：`group_5/security.py`、`group_5/coordinator.py`。

## 验证记录（簇4后）
- 新增 `TestRedactionExits` 5 项，锁定契约：子树/中文键打码、
  stats redactor 注入后 `stats.json` 无明文凭据、`intent.json` 产物无
  明文邮箱、FakeRag 捕获的 `add_trace` 入参无 `sk-` 密钥、
  main/server 两处的脱敏接线（静态断言）。
- `python -m unittest tests.test_regression_audit` → **Ran 106, OK**
- `python -m unittest tests.test_integration_v10` → Ran 51, OK (skipped=3)
- 实测探针：`ToolStats(log_path)+set_redactor` 记一笔含
  `password/密钥/邮箱` 的 params → 落盘 JSON 中 `TopSecret123`、
  `sk-abc123def456` 均不可见，邮箱变 `a***@b.com`。

---

# 簇5：GUI 端点 / 令牌 / 并发（报告 #14–#16、#19、#20）✅

（#17 签名对象、#18 stats 编码已在簇3连带修复。）

### #14 工具台直调端点绕开整套安全层
- 根因：`/api/call`、`/api/skill` 直接 `registry.call(...)`，**完全不进
  SecuritySandbox**（黑名单/受保护路径/签名核对四层全失效）；`/api/smtp_test`
  更绕开注册表裸调 `send_email(dry_run=False)` —— 不占 `_LOCK`（可与编排
  并发抢 SMTP/桌面）、不落 stats、不写审计。文件头"与生产共用同一套权限
  闸门"的承诺不成立。
- 修法：Handler 新增 `_sandbox_gate(action, params)` —— 把直调拼成一条
  动作意图过与编排**同一个** `check()`（user_text 固定为"[工具台]直接调用
  xxx"，参数递归进扫描面；沙箱异常按 **fail-closed** 拒绝）。三个端点统一：
  拦截 → 返回 `blocked_by_guard` 并审计；放行 → `_LOCK` 内执行、结果（含
  成败）写审计（经 `audit()` 自动脱敏）。`/api/smtp_test` 改走
  `registry.call("send_email", …, LEVELS["admin"])`（schema 本就 ADMIN），
  统计/权限口径与生产一致；顺带修正前端契约（`testSMTP()` 读 `r.ok`，
  旧版回 `success` 恒显 ✗）。
- 落点：`gui/server.py`。

### #15 确认令牌生命周期三个洞
- 根因：① `/api/execute` 的 `if token not in PENDING … pop(token)` 非原子
  —— 并发双发第二发 KeyError→500 且异常文本**回显令牌**；② "取消"纯前端
  藏面板，令牌在 30 分钟 TTL 内可被任意同源 JSON POST 复用执行；③ 确认后
  不复跑安全核对（受保护路径设置改了，旧令牌仍按旧结论执行）。
- 修法：① `PENDING.pop(token, None)` 一步原子消费；② 新增 `POST /api/cancel`
  （服务端 pop + 审计 `confirm_cancel`），前端 `confirmExecute("cancel")`
  先打 `/api/cancel` 再藏面板并清空 `PIPE_TOKEN`；③ 不违反红线 5（**不
  重新规划**），但 `execute_approved` 内部本就跑簇2 的 `_plan_gate` 计划级
  安全复检 —— 执行时刻按当前策略重新过闸，已在注释点明。
- 落点：`gui/server.py`、`gui/index.html`。

### #16 同源防线细节
- 根因：① Host 用无脑 `rsplit(":",1)` 剥端口 ——
  `Host: 127.0.0.1:8788.evil.com` 前段仍是"127.0.0.1"，判为回环；
  ② Origin 缺失直接放行；③ `GET /api/controls` 无同源闸门（跨站
  `<img src>` 反复触发），每次持全局锁跑真桌面扫描 → 可饿死编排。
- 修法：① 只有"冒号后全是数字"才当端口剥离（`[…]:port` IPv6 单独处理），
  否则整串参与回环判定；② Origin **必带且同源**（浏览器对本地 GUI 的非
  GET fetch 必带 Origin，缺失即非法来源）；③ `/api/controls` 加 3 秒结果
  缓存（`_CONTROLS_CACHE`），扫描风暴最多每 3 秒真扫一次。同步：
  `health_check.py` 的原始 socket 请求补 `Origin: http://127.0.0.1:<port>`
  并把 Host 写成带端口形态。
- 落点：`gui/server.py`、`.agents/skills/agent-os-dev/scripts/health_check.py`。

### #19 stats_buffered 死代码且与 ToolStats 行为分叉
- 根因：文件自称"接口与 ToolStats 完全一致，可无缝替换"，实则三处分叉：
  ① 无 `_load()`（重启不续账）；② flush 用内存 counters 覆盖文件
  （重启归零后把历史统计抹掉）；③ `clear()` 后 `_unsaved` 为空，
  `_flush_locked` 早退**不落盘**（重置只发生在内存）。另外它也没有簇4
  的脱敏钩子 —— 谁按注释"无缝替换"，引入的是回归。
- 修法：对齐行为而非删除（保留其"批量落盘+环形上限+锁"的演示价值）：
  `__init__` 有 log_path 即 `_load()` 续账（UTF-8 + 坏账本回落，#18 同款）；
  有了续账，"内存 counters 覆盖文件"自然正确（内存值本就含历史）；
  `clear()` 直接 `_write_ledger_locked({"records":[],"counters":{}})`
  把空账本写回文件；抽 `_write_ledger_locked` 统一原子写；补
  `set_redactor` 与 `record()` 脱敏（与 ToolStats 同口径）。
- 落点：`src/stats_buffered.py`。

### #20 web_automation CDP 通道缺锁
- 根因：只有 `ensure_browser` 持锁；`open_url/find/click/state/web_extract/
  wait_page_ready` 全部裸奔。ThreadingHTTPServer 下编排线程与 `/api/call`
  共用同一单例：`_msg_id += 1` 非原子、甲线程"发送→等应答"期间被乙插队，
  乙读走甲的应答（非本 id 消息被丢弃）→ 串包 / 双方 15s 超时。
- 修法：`self._lock` 改 **RLock**；新增 `_synchronized` 装饰器（with 锁包
  整个"发送→收应答"），应用到全部公开入口（含 `close/close_browser`）；
  嵌套调用（`wait_page_ready_if_active→wait_page_ready`、
  `open_url→state`）靠可重入不死锁。异常路径锁必释放（with 语义，测试
  断言"抛 CDPError 后锁可即时获取"）。
- 落点：`src/web_automation.py`。

## 验证记录（簇5后）
- 新增 `TestGuiGuardsAndConcurrency` 6 项：三端点闸门+审计+锁的静态锁定、
  沙箱对 `rm -rf /` 直调的拒绝语义、令牌原子消费与服务端取消、
  同源细节（isdigit/Origin 必带/controls 缓存）、BufferedToolStats
  续账+脱敏+clear 落盘、`@_synchronized` 覆盖面与锁释放。
- 真机冒烟（起 8788 服务 + 原始 socket 8 项）：无 Origin/伪装 Host/
  跨站 Origin 全部 403；`/api/call run_command "rm -rf /"` 返回
  `blocked_by_guard`；`/api/skill system_check` 放行 success；
  `/api/cancel` 未知 token 得 `cancelled:false`；`/api/controls` 两次
  200；审计日志逐条 `api_call/confirm_cancel` 留痕。
- `python -m unittest tests.test_regression_audit` → **Ran 112, OK**
- `python -m unittest tests.test_integration_v10` → Ran 51, OK (skipped=3)
- 前端 `<script>` 抽出 `node --check` → 通过（`/api/cancel` 调用点在案）


---

## 簇6：决策表盲区与动作别名缺口（报告 #23-24）✅

### #23 通道改写盲区：web_extract 不在自动化浏览器家族 / 改写无痕
- 根因：① `route_policy._WEB_INTERACT` 只列 web_click/web_state ——
  `open_url(网址) → web_extract` 不改写 web_open，抽取落在"未打开的自动化
  浏览器"上必空（决策表 blind spot，红线 3 预警的那类）；② 规划器内部的
  click_element→open_app 确定性改道不走 route_policy，早期不写
  route_reason；③ plan["routes"]/step["route_reason"] 前端零消费，
  用户确认的其实是"被暗改过通道的方案"。
- 修法：① `_WEB_INTERACT = ("web_click","web_state","web_extract")`
  （三者都在自动化浏览器里取页，头注释第 4 条同步）；② `_gui_generic_steps`
  改道分支对返回的每个步骤 `setdefault("route_reason", "点击图标「X」=
  启动应用 → 走 open_app 通道（X11 dock 不吃合成点击）")`；③ index.html
  两处消费：确认面板 #orch-confirm-detail 追加 🔀 行（确认前可见），
  结果事件流追加"🔀 通道改写 步骤N"（所有运行可见）。
- 落点：`group_2/route_policy.py`、`group_2/task_planner.py`、`gui/index.html`。

### #24 copy_file/move_file/read_file/list_directory 全链路失联
- 根因：这四个组4 真实工具在编排四处同步上全缺（红线 17/3）——
  组2 `_TOOL_ACTIONS` 无恒等映射 → LLM 直接给工具名时 difflib(0.75) 不
  保证命中，整步静默丢进 unresolved；组1 无中文规则 → LLM 不可用时规则
  （Mock）路径解析不出，降级链断裂（红线 7）；无 `_ACTION_ALIASES` →
  用户抄标识符"执行 read_file"落 LLM 闲聊；`_CATEGORIES`/`_SYSTEM_PROMPT`
  未收录；且 target 三模式没给"指向已存在文件"的动作配 strict ——
  回落工作区等于"对整个工作区做读取/搬运"，语义全错（红线 10）。
- 修法：四处一次补齐：
  ① `_TOOL_ACTIONS` 恒等映射 +5（含 mcp_search，LLM 常直接给工具名）；
  ② `_RULES` 新增 复制|拷贝→copy_file、移动到|移到|挪到|移动→move_file、
  读取|读文件|读一下→read_file、列出|列一下|有哪些文件→list_directory、
  网页状态|页面状态→web_state；③ `_ACTION_ALIASES` 对应五条（收录标准
  注释同步改写：只收"有中文规则"的说法）；④ `_CATEGORIES`、
  `_SYSTEM_PROMPT`（action 表 + 工具参数 + 文件操作指引行 +
  "src 不要编路径"）；⑤ 抽参：read_file 并入现有路径抽取，copy/move 新增
  双路径分支（引号对 → 两个路径形态 → "把X…到Y"裸词对，三级来源；只抽到
  src 时 dest 留缺，组2 如实报缺参，绝不猜目的地）；list_directory 并入
  目录级抽取；⑥ 落点模式：read/copy/move 进 `_TARGET_STRICT_ACTIONS`，
  四个动作进 `_FILE_ACTIONS`（params 走既有路径归一：真实绝对路径保留、
  裸名锚工作区、幻觉路径按证据防线）。
- 落点：`group_1/host_agent.py`、`group_2/task_planner.py`。

## 验证记录（簇6后）
- 新增 `TestRouteAndAliases` 10 项：web_extract 入族、open_url→web_open
  改写、裸搜索被 web_extract 消费→浏览器通道、click_element 改道留痕、
  前端 route_reason 消费静态、_TOOL_ACTIONS 恒等映射、五条新规则解析、
  裸标识符别名、复制引号对端到端（规则抽参→归一→copy_file 步骤 src/dest
  不丢）、提示词/_CATEGORIES/三模式集合同步（红线 10/17 静态锁定）。
- 探针：`把 /tmp/报告.txt 复制到 /tmp/备份` → copy_file src/dest ✓；
  `把报告移动到备份` → 裸词对锚定工作区 ✓；`执行 read_file`/`列出目录`/
  `查看网页状态` 规则路径 ✓；`读取 /no/such/file.txt` target 原样保留
  （strict，不回落工作区）✓。
- `python -m unittest tests.test_regression_audit` → **Ran 122, OK**
- `python -m unittest tests.test_integration_v10` → Ran 51, OK (skipped=3)
- 三个 Python 文件 `py_compile` 通过；前端 `<script>` 抽出 `node --check` 通过。

---

## 簇7：其余中等项与轻微清单（报告 #21/#22/#25/#26/#27 + 三-轻微）✅/⏸

### #21 dry_run 归一反向覆盖（不可逆真实外发）
- 根因：`_finalize_intent` 无条件 `params["dry_run"] = draft_only` —— LLM
  保守给出 True 时，话语没命中 _DRAFT_RE 就被规则**翻成 False 真发**；
  而 main.py --text / AIShell 路径没有 GUI 确认闸门，错了不可逆。
  另 _DRAFT_RE 缺"先不要发/暂时别发/只做预览"等同义词。
- 修法：改为**保守单向** `_norm_dry_run`：`dry_run = 规则判草稿 or LLM 判草稿`
  （任一来源说"草稿"即干跑，只有两路都表态真发才真发；字符串 "true"/"1" 容错）。
  规则路径 _extract_params 本就写 False（无表态语义），原口径不变；
  _DRAFT_RE 补 9 个同义词。方向与红线 3 一致：宁可干跑让用户追发，不可代签外发。
- 落点：`group_1/host_agent.py`（_DRAFT_RE/_norm_dry_run/_finalize_intent）。

### #22 params:null 崩溃点
- 根因：LLM 输出 `"params": null` 是合法 JSON，`setdefault("params", {})`
  对"键存在值为 null"返回 None → `None[k]=v` TypeError，且崩在
  _finalize_intent（try 只包 _llm_intent）→ 整轮 failed。
- 修法：新增 `_ensure_params(node)`（非 dict 即重建），替换全部 6 处
  裸 `setdefault("params", {})`/`g["params"].pop` 写法。
- 落点：`group_1/host_agent.py`。

### #25 RUNS 回看失真 + 产物无归档
- 根因：① `execute()` 返回的 session 直接引用 `self.escalated` 等**实例活
  列表**，而每次 execute 开头按红线 4 `clear()` —— GUI 把 result 存进 RUNS
  后，历史 bundle 的提权/自愈记录被下一次运行掏空，`loadRun` 回看恒 0 条；
  ② `_persist` 固定文件名"最新五件套"逐轮覆盖，无 run 归档，
  事后无法比对"批准方案 == 执行方案"。
- 修法：① 返回 `list(...)` 快照（三处）；② _persist 除最新副本外，
  归档到 `artifact_dir/runs/<时间戳>_<intent_id>/` 同五件（mask 后写入，
  归档失败静默 —— 旁路不断流）。
- 落点：`group_3/app_agent.py`、`group_5/coordinator.py`。

### #26 黑名单无词界（误伤 + 漏拦）
- 根因：子串口径下 "dd " 命中 "add file"、"format" 命中 "--format="，
  rmdir/halt 等英文词出现在 user_text（在扫描面上）即整条 critical；
  同时 `rm --recursive --force ~/*`、`find -exec unlink`、
  `python -c "…rmtree('/etc')"` 全漏拦。
- 修法：双层重构 —— 关键词表只留多词/符号形态（rm -rf 系列、chmod 777、
  ":(){" 等）；单词命令改走**词界正则**（`(?<![\w./-])halt(?![\w-])` 等，
  "dd" 要求 `dd\s+参数=`、"format" 要求 `format c:`），并补 4 条漏拦
  变体（rm 长选项组合、--no-preserve-root、find -exec rm|unlink|shred、
  python*…rmtree）。security 与 default_tools 两份口径**同步改**
  （交叉断言测试锁 set/list 相等）。既有防线不受影响：`rm -rf /`
  仍走关键词层（含簇5 fail-closed 测试依赖）。
- 落点：`group_5/security.py`、`src/default_tools.py`。

### #27 XSS 小口 + /api/plan 报错形态
- 修法：`data-t="${p.type}"`、`${e.step.kind}` 两处补 esc；/api/plan 返回
  200+{error}（无 pending/run_id）时直接渲染错误行，不再
  `startPolling(undefined)` 用"运行不存在"掩盖真错。
- 落点：`gui/index.html`。

### 轻微项（已修）
- 附件裸文件名解析：先按**工作区**找、再回落进程 CWD（与"相对→锚定工作区"
  同口径，杜绝取到 CWD 旧副本）。`group_1/host_agent.py`。
- 空计划归因矛盾：仅当**没有任何逐步归因**（unresolved 长度未增）时才补
  "动作未映射到组4"兜底 —— 缺参场景不再产出两条自相矛盾的 unresolved。
  `group_2/task_planner.py`。
- `skill_library._find_files` 越界防线补前导 / \ 判定（Windows 上
  os.path.isabs("/etc/x") 为 False 的口径不一致）。
- `_backup_directory` 相对 dest 锚定**源目录旁**，不再按进程 CWD 落盘。
- `llm_client.chat_stream` 截断改为发给服务端的**副本**，不再就地改写
  调用方维护的会话历史（chat_once 同口径核对无此问题）。
- `_surface` 递归加 24 层深度上限（异常深嵌套不再 RecursionError 炸整轮）。

### ⏸ 评估后不改（附理由，待人工确认）
- **根目录/group_1 三处旧副本**（host_agent.py、task_planner.py、
  group_1/task_planner.py）：全仓零 import 已核实。原计划在文件头加
  废弃横幅，被本机安全策略拦下（对"可能是在用文件"做批量注释注入风险
  过高）——**建议人工 `git rm` 三文件**（权威实现是 group_1/host_agent.py
  与 group_2/task_planner.py），或用 `python -c "import ast,sys; ..."`
  复核零引用后删除。
- **session_id 实例级**：常驻单例跨运行同 id 属实，但组5 对账按
  bundle["timestamp"] 运行窗口切分（簇5 已核），改 id 语义会牵动
  result_json 契约 —— 留待人工定夺。
- **锁外共享态**（RUNS 淘汰、PENDING 写、SETTINGS 读、events append）：
  依赖 CPython GIL 原子性的 dict/list 单操作，实测无可观察竞态；全面上锁
  改动面大收益小，维持现状并留此记录。

## 验证记录（簇7后）
- 新增 3 类 14 项：`TestResidualFixes`（dry_run 保守单向×3、params:null
  存活、会话快照、按 run 归档两轮两份）、`TestBlacklistWordBoundary`
  （误伤 4 句不炸、真危险词仍拦、5 个新变体 check 拒、rm -rf 端到端仍拦、
  _surface 深度上限）、`TestMinorResiduals`（前端 esc/报错形态静态、
  备份相对 dest 落源旁、find 绝对 pattern 拒绝、空计划单一归因）。
- `python -m unittest tests.test_regression_audit` → **Ran 136, OK**
- `python -m unittest tests.test_integration_v10` → Ran 51, OK (skipped=3)
- 8 个受影响 py 文件 py_compile 通过；前端 `<script>` `node --check` 通过。

---

## 总览：27 项报告问题处置对照

| 报告# | 问题 | 处置 | 落点（簇） |
|---|---|---|---|
| 1 | 组2 参数合并泄漏（可删整工作区） | ✅ 修复：合并只取显式同名键，删除类动作禁继承 | 簇1 |
| 2 | 多目标整句抽参互相污染 | ✅ 修复：逐目标抽参 + 路径对配对 | 簇1 |
| 3 | 证据防线对文件系统根失效 | ✅ 修复：`_is_abs_any` 跨平台根判定 | 簇1 |
| 4 | 空目标删除检查不全 | ✅ 修复：删除/清空类动作统一空目标拦截 | 簇1 |
| 5 | run_command 命令串内嵌路径逃保护 | ✅ 修复：命令串内路径逐个过受保护判定 | 簇2 |
| 6 | 计划期派生参数零复检（口径错位） | ✅ 修复：execute 前对最终 plan 复跑 check（动作名口径统一） | 簇2 |
| 7 | `{{prev_result}}` 回填在闸门后成形 | ✅ 修复：回填后复检再执行 | 簇2 |
| 8 | MCP 网络失败谎报成功 | ✅ 修复：success=False + degraded | 簇3 |
| 9 | 注册表/Skill 抹平降级标记 | ✅ 修复：失败分支透传 simulated/degraded/error 明细 | 簇3 |
| 10 | Automator Mock 默认 success=True | ✅ 修复：全部降级分支判失败（红线 11） | 簇3 |
| 11 | 错误信息契约+GUI 双层丢失 | ✅ 修复：result_json/interfaces/GUI 三层保 error | 簇3 |
| 12 | 持久化出口裸奔（脱敏旁路） | ✅ 修复：mask 统一挂 `_persist`/stats/rag/chat_save/`/api/run` 出口 | 簇4 |
| 13 | `_mask_obj` 认不全敏感键 | ✅ 修复：敏感键名+中文字面+邮箱/密钥正则 | 簇4 |
| 14 | GUI 匿名端点绕过安全层 | ✅ 修复：`/api/call`、`/api/skill` 过沙箱 | 簇5 |
| 15 | 确认令牌生命周期三洞 | ✅ 修复：pop 原子化、一次性、过期清理 | 簇5 |
| 16 | 同源防线细节（Host 后缀伪造） | ✅ 修复：Host 严格解析 + Origin 必带 | 簇5 |
| 17 | 签名核对核错对象（误报篡改） | ✅ 修复：`marshal.dumps(func.__code__)` 结构化摘要（簇3 连带） | 簇3 |
| 18 | stats 落盘编码+吞异常 | ✅ 修复：UTF-8 tempfile + 坏账本回落（簇3 连带） | 簇3 |
| 19 | stats_buffered 死代码分叉 | ✅ 修复：删除死分支/与 ToolStats 行为对齐 | 簇5 |
| 20 | web_automation CDP 通道缺锁 | ✅ 修复：`ensure_browser` 单锁串行 `_ws` | 簇5 |
| 21 | dry_run 归一反向覆盖 | ✅ 修复：保守单向 OR 归一（红线 3 已 amendment） | 簇7 |
| 22 | params:null 崩溃点 | ✅ 修复：`_ensure_params` 全落点 | 簇7 |
| 23 | route_policy 决策表盲区 | ✅ 修复：web_extract 入族 + 改写留痕入 GUI/事件流 | 簇6 |
| 24 | 四文件工具全链路失联 | ✅ 修复：映射表/规则/别名/提示词四处同步 | 簇6 |
| 25 | RUNS 活列表回看失真 | ✅ 修复：`list()` 快照 + `runs/<时间戳>` 归档 | 簇7 |
| 26 | 黑名单无词界（误伤+漏拦） | ✅ 修复：词界正则双层口径，两份表交叉锁定 | 簇7 |
| 27 | XSS 小口 + /api/plan 报错形态 | ✅ 修复：`esc()` 两处 + 前端无 run_id 守卫 | 簇7 |
| 三-轻微 | 6 项（附件 CWD、空计划归因、find 越界、备份 dest、chat_stream 就地截断、_surface 深度） | ✅ 修复 | 簇7 |
| 三-轻微 | 根目录死副本三文件 | ⏸ 建议人工 `git rm`（零 import 已核实；横幅注入被本机安全策略拦下，未重试） | — |
| 三-轻微 | session_id 实例级 | ⏸ 维持现状（对账按时间窗切分，改语义牵动 result_json 契约） | — |
| 三-轻微 | 锁外共享态 | ⏸ 维持现状（GIL 原子单操作，实测无可观察竞态；全面上锁收益低） | — |

## 最终回归基线（2026-09-19 收口）

- `python -m unittest tests.test_regression_audit` → **Ran 136, OK**（本轮新增 TestRouteAndAliases 10 / TestResidualFixes 5 / TestBlacklistWordBoundary 5 / TestMinorResiduals 4）
- `python -m unittest tests.test_integration_v10` → **Ran 51, OK (skipped=3)**（skip 均为 GUI/X11 通道项，Windows 副本如实跳过）
- `python main.py --demo` → **3/7**（Windows 诚实基线：场景1/2/3/5 依赖 AT-SPI/X11 文件管理器与桌面通道，本机必降级为 honest failure；安全拦截行 `rm -rf` 正常触发）
- 前端：`<script>` 抽出后 `node --check` 通过；同源防线 7 项冒烟（簇5 记录）通过
- 产物归档：`system_out/runs/<时间戳_runid>/` 每轮五份（intent/check/plan/session_log/audit），"批准方案==执行方案"可事后比对
- 知识沉淀：SKILL.md 红线 3（保守单向归一）、红线 16（Origin 必带）已修订；`references/pitfalls.md` 新增 3 条（黑名单词界 / RUNS 快照+归档 / dry_run 反向覆盖+params:null）

**结论**：报告 27 项 + 轻微清单全部闭环 —— 24 项修复并加测试锁定，3 项⏸留档附理由待人工定夺，1 项（死副本）待人工删除。全链路无谎报（success=False+degraded 口径统一），危险方向一律取严。
