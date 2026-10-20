# 排障手册：失败特征 → 根因 → 修复

按失败特征检索。每条含：现象、根因、修复、涉及文件。

## 前端 / GUI 控制台

### 现象：点击"运行编排"毫无反应，或永远停在"组1 意图理解中…"
- 根因 A：**浏览器缓存了旧页面**，旧 JS 调用已下线的接口（如重构前的 /api/orchestrate）→ 404。
- 根因 B：**JS 引用了不存在的元素 id**（页面改造时 id 改名残留），运行时抛 `$() is null` 中断整段脚本。
- 修复：服务端所有响应已带 `Cache-Control: no-store`（根治缓存）；用户侧 Ctrl+F5 一次。残留 id 用全页扫描核对：JS 里每个 `$("#id")` 必须在 HTML 中存在 `id="id"`。
- 根因 C：LLM 生成复杂多步意图超过客户端超时（glm 免费档 20~90 秒）。超时已设 60s 并自动降级规则引擎；前端文案已明示等待时长。
- 教训：接口重构时旧端点要么保留兼容路由，要么强推用户刷新——服务端不发缓存头时，"改了代码但页面没变"是默认行为。

### 现象：确认面板不弹出（规划完成后无反应）
- 根因：orchestrate() 在显示确认面板前引用了不存在的元素（#pipe-tabs → 实为 #orch-tabs）。
- 修复：已改；同类问题用全页 id 扫描（见 SKILL.md 红线 8）。

### 现象：提权事件重复出现、出现不属于本次计划的步骤号
- 根因：AppAgent 常驻单例跨运行复用，`escalated`/`self_healed` 列表跨运行累积，历史记录泄进新会话。
- 修复：`execute_plan` 开头重置两个列表（group_3/app_agent.py，已加"会话状态按运行隔离"注释）。

## 编排 / 组间链路

### 现象：确认面板批准的方案与实际执行不一致（如邮件丢附件/空正文）
- 根因：确认后执行时**重新规划**——第二次 LLM 输出与第一次不同（LLM 随机性）。
- 修复：`PENDING[token]` 存完整 `{intent, plan, check}`，`/api/execute` 原样执行，执行阶段零 LLM 调用。**任何新功能都不得在确认后重新规划。**

### 现象：浏览器里根本搜不到网页元素（网页点击/读取不成立）
- 根因：本机浏览器全是 snap 包，**AppArmor 把 snap 应用挡在 a11y 总线外**——应用注册为"(无名)"、树 child_count=-1，Cache 与遍历接口全拦（Firefox snap / Chromium snap 双双实测，2026-09）。AT-SPI 网页自动化在此环境是死路，与控件树预算/深度无关。
- 修复：网页操作走 **CDP（Chrome DevTools Protocol）**——`src/web_automation.py`（零依赖 RFC6455 WebSocket 客户端 + Runtime.evaluate 定位 + Input.dispatchMouseEvent 真实点击），AppArmor 不拦 localhost。已实测 open→find→click→URL 跳转全链路。
- 教训：① snap 化 Ubuntu 上"用 a11y 控制浏览器"的前提先验证 AppArmor；② CDP 必须用**独立 user-data-dir**（否则被单实例代理吞掉）+ `/json/new` 专用标签页（复用旧标签会被内存节省丢弃，evaluate 永久无响应）；③ 同进程测试套件里改 `AIOS_CDP_PORT`/`AIOS_WEB_BROWSER` 环境变量必须恢复并复位 `_AUTO` 单例，否则污染后续测试。

### 现象：改了代码、刷新了页面，行为还是旧的
- 根因：8788 是常驻进程，Python 模块在启动时加载——只刷新浏览器拿到的仍是旧代码；hgfs 挂载下 mtime 粒度还会让 .pyc 缓存失效失灵。
- 修复：清 `__pycache__` → 按端口杀进程 → 重启；**验证必须打运行中的服务**（如 /api/plan 实际发一次），不能只在独立进程里跑同一段逻辑——独立进程永远读源码，会掩盖服务未重启。
- 教训：声称"已重启"之前，先从服务端口验证一次行为。

### 现象：orchestrate 卡在 LLM 无响应 / 页面操作静默无效果
- 排查顺序：先探测 `http://127.0.0.1:9222/json/version`（无 curl 用 python urllib）确认 CDP 端口，再看窗口标题是否变化、`session_log` 里 `effect` 字段。

### 现象：LLM 参数幻觉（把上一步整个结果对象塞进字符串参数、编造不存在的文件路径）
- 根因：LLM 输出随机性 + 提示词动作表缺项（LLM 不知道某工具存在时只能变通幻觉）。
- 修复三层：组1 提示词动作表与参数规范保持最新（**加新工具必须同步**）；组1 后处理归一（dry_run/attachment/路径解析）；组2 `_fill_params` 类型防御（字符串参数收到 dict/list 一律丢弃）。

### 现象：organize 把项目根/进程 CWD 当工作区整理了（源码被移进 代码//文档/）
- 根因：路径归一漏洞三连。① 规则引擎/LLM 给目录级动作（organize/find_duplicates/backup 等）填 `path='.'`，`_resolve_target` 对"存在即返回"的相对路径短路，原样放行 → organize 的作用域变成进程 CWD；② 相对 `dest`（如 demo_task_backup）原样放行 → `make_archive` 按进程 CWD 落盘，zip 写进项目根；③ 绝对但**尚不存在**的 `dest` 被 `_resolve_target` 回退成 default_target（=源目录本身），zip 名错位。
- 修复（group_1/host_agent.py）：目录级动作的 `'.'/'..'` 一律映射 default_target（`_DIR_SCOPE_ACTIONS`）；dest 归一进 `_normalize_dest`（绝对 dest 父目录真实才接受，否则丢弃交组2 重派生；相对 dest 锚定 src 同级）。
- 教训：**目录级动作的作用域永远不能是"进程恰好站在哪里"**；输出路径允许不存在，不能拿"存在性"当校验标准。回归测试：`TestIntentPathNormalization`（use_llm=False 确定性复现）。

### 现象：审计对账 FAIL、步骤计数对不上
- 根因 1：会话状态累积（见前端段第 3 条同源问题）。
- 根因 2：技能调用按契约不留 stats 账（organize 等走 SkillLibrary），只有工具层落账——对账公式只核工具步骤，属设计而非 bug。

### 现象：编排进入真实 SMTP 会话期间，连 127.0.0.1 都解析失败（EAI_NONAME）
- 根因：systemd-resolved 在并发查询下短暂抖动（环境现象，非代码）。
- 修复：测试客户端用 AF_INET 直连数字 IP（绕过解析器）；前端轮询 fetch 加 try/catch 下一轮重试。

## AT-SPI / GI 绑定（组2 检测、组3 输入）

### 现象：控件树遍历返回 0 元素
- 根因 A：GI 的 `get_extents()` 需要 coord_type 参数（1=屏幕坐标），缺参会抛异常被逐节点吞掉。
- 根因 B：方法名差异——GI 用 `get_child_at_index`/`get_child_count`，pyatspi 用 `getChildAtIndex`/`childCount`。适配层见 group_2/control_detector.py。
- 修复后验证：`ControlDetector().detect_elements()` 应返回真实桌面元素（含 gnome-shell 桌面图标）。

### 现象：set_text_contents 写入成功但 get_text 返回空
- 根因：GI 新版 `get_text()` 无参取全文；带 (start, end) 的旧签名已弃用且行为不同。
- 修复：get_text 用 character_count + 多方案适配（group_3/atspi_io.py）。

### 现象：queryText / queryAction 报 AttributeError
- 根因：那是 pyatspi 的包装 API；Atspi GI 的接口方法**扁平化在 Accessible 上**（acc.set_text_contents / acc.do_action / acc.get_n_actions）。
- 教训：写 AT-SPI 代码前先用 `dir(acc)` 探测真实 API 形状，不要凭 pyatspi 文档硬写。

### 现象：控件树坐标点击落点系统性偏移（GTK 应用内尤甚，shell 元素反而准）
- 根因：**GTK4 应用在 X11 下 a11y extents 停留在窗口创建时的默认几何**，窗口被移动/缩放后不刷新（实测 Nautilus 报 (0,0,890,550)，X 真值 1012x672+317+88，间隔 2 秒采样不变，误差非等比——不是缩放变换，是纯陈旧数据）；对照 gnome-shell（gjs 应用）树坐标与 X root 完全一致。X11 + 缩放 1.0 下与屏幕缩放无关。
- 修复：`group_3/coord_check.py` 以元素顶层 frame 为锚，用 xwininfo 真值做逐轴仿射校正（true = a*reported + b）；shell 树信任跳过；Wayland/无 DISPLAY/xwininfo 缺失自动停用；点击主路径改为 API DoAction 语义选动作（press/click/open…，不再盲选 index 0），坐标只兜底。
- 教训：AT-SPI 坐标必须与 X 端真值交叉验证后才可信；`bbox[0] >= 0` 式有效性判定会误杀多显示器合法负坐标（哨兵只认 INT32_MIN）。

### 现象：GTK4 应用（gnome-text-editor 等）里找不到目标控件
- 根因：libadwaita 应用 panel 链嵌套极深（实测 14+ 层），浅深度/小预算的搜索走不到。
- 修复：app 内搜索深度 24、预算 8000，并先用 app_name 定位到应用子树再搜（group_3/atspi_io.py find_accessible）。

### 现象：合成键盘输入对中文无效
- 根因：keysym 逐键合成只对 ASCII/命名键可靠。
- 修复：中文走 setTextContents（Text 接口）；纯合成输入仅用于 ASCII。

## 环境 / 工程习惯

### 现象：`pkill -f` 杀死了自己的终端
- 根因：-f 匹配完整命令行，而启动/排查命令的文本里就包含目标模式。
- 修复：按端口找 pid（`ss -tlnp` 解析）再 kill；或模式用 `[e]` 字符类转义，且**杀进程与重启服务拆成两条命令**。

### 现象：/tmp 下的测试兼容层脚本消失
- 根因：/tmp 会被系统清理。
- 修复：兼容层已固化到 `.agents/skills/agent-os-dev/scripts/pytest_shim.py`（从项目自身定位 ROOT）。

### 现象：python -c "..." 里的正则/转义报错
- 根因：heredoc + shell + python 三层转义叠加（\b、\u4e00、引号）。
- 修复：复杂补丁写成 python 脚本用 assert 锚点替换，或正则里用 `re.escape`/字符类；写完立即 `ast.parse` 验证。

### 现象：bash 管道/循环报 "too many values to unpack" / 计数为 0
- 根因：脚本 cwd 漂移（上一条命令的 cd 残留）或函数多返回值解包笔误。
- 修复：长命令序列开头显式 cd；测试循环前确认测试文件存在。

## LLM / 供应商

### 现象：llm_test 返回 401
- 根因：Key 过期/吊销/填错平台（两平台 Key 装反时两边都 401）。
- 修复：用存储 Key 分别打两个官方端点验证归属（诊断脚本见 git 历史）；用户侧去平台控制台核对状态或重新生成。

### 现象：编排偶发走规则引擎（意图质量下降）
- 根因：LLM 调用失败自动降级（超时/网络/Key 失效），设计如此。
- 修复：GUI 编排结果区会显示当前引擎（llm:模型名 / rules）；Key 问题用 llm_test 定位。

## 安全与契约（2026-09-15 审计修复后新增的对照表）

### 现象：危险命令明明在指令里，`check` 却返回 approved/low
- 根因：`SecuritySandbox.check()` 的检测面只拼了**顶层** `params.cmd`；而执行语义在 `goals[].params.cmd` 里 → 整条绕过黑名单（实测 `rm -rf /home/x` 拿到 `approved=True, risk_level=low`）。
- 修复：`_surface()` 递归展开整条意图（顶层 + goals/steps 的全部字符串值）；`_risk_of_intent()` 取所有 goal 的最高风险。回归：`test_goal_level_dangerous_command_blocked`。
- 教训：**安全检测面必须跟着"数据的真实存放位置"走**；新增任何"参数藏在子结构里"的契约时，同步检查安全层与审计层。

### 现象：用户说"删除某个不存在的路径"，结果默认工作区被删空
- 根因：两处叠加 —— ① `_resolve_target` 对"解析不出"一律回落 `default_target`；② 组2 `_fill_params` 把 schema 第一个 string 参数填成 target（`delete_file.path` 被填成工作区目录）。`security.check` 只拦 `""`/`/`，工作区路径照过。
- 修复：`_resolve_target(raw, default, mode)` 三模式（`workspace`/`anchor`/`strict`，`delete_file`/`find_files` 走 strict 原样保留）；组2 兜底填参改为 `_PATH_FALLBACK` 白名单（`delete_file` 不在其中 → 缺 path 如实报"缺少必需参数"）。回归：`test_planner_never_fills_delete_path_with_workspace`。
- 教训：**"找不到就用默认值"对产出型动作是便利，对破坏型动作是灾难** —— 按动作性质分别定义。

### 现象：网页/浏览器工具在"没有浏览器"的环境里显示成功
- 根因：`except CDPError` 分支返回 `success=True` 只把 `[Mock]` 塞进 result；审计/统计/对账都只看 `success`。
- 修复：改为 `success=False + simulated=True + degraded=True + "[Mock·未执行]…"`；`test_integration_v10.py` 里那条"把缺陷写成断言"的用例同步纠正。
- 教训：`success` 的语义是"**动作真的发生了**"，降级/模拟只是原因标记；**永远不要把降级写成成功**。

### 现象：点了"仅执行免提权步骤"，提权步骤照样跑了
- 根因：`execute_approved(confirm_mode="deny")` 只把 `agent.confirmer` 换成恒 False，**计划不过滤**；提权步骤照进执行循环、照产生副作用，最后以"权限不足"失败。
- 修复：`deny` 先过滤 `requires_admin` 步骤再执行，跳过的记入 `skipped_admin_steps`。回归：`test_deny_mode_filters_admin_steps`。
- 教训：**模式开关要作用在"计划"上，而不是"确认回调"上**。

### 现象：对话助手"每次都失败"，但 LLM 其实答对了
- 根因：`index.html` 漏定义 `loadChatHistory`/`saveChatHistory`；后者在 `sendChat` 末尾被调用 → 抛 `ReferenceError` → 落进 `catch` 把已渲染好的回答气泡改成"✗ 请求失败"。同时首屏 `loadChatHistory()` 抛错 → 其后 `loadLLM/loadSMTP/loadSettings` 与 `#hash` 直达全不执行。
- 修复：补两个函数（`/api/chat_history`、`/api/chat_save` 的包装）；改前端后 `node --check` 抽出的 `<script>` + 断言调用/定义计数。
- 教训：**"看起来失败"和"真的失败"要能区分** —— 前端 catch 分支不要覆盖已成功的渲染结果。

### 现象：浏览器打开一个陌生页面，本机就被执行了命令
- 根因：8788 的 POST 接口既不校验 `Content-Type` 也不校验 `Origin`/`Host`。只绑 127.0.0.1 挡不住浏览器 —— 跨站**简单请求**（`text/plain`）不触发预检。
- 修复：`_same_origin_ok()`（Host 回环 + Origin 同源 + `Content-Type: application/json`）+ 1MB 请求体上限；前端所有 POST 带 JSON 头。
- 教训：本地服务等于"高危 RPC 端点"，**同源校验是功能需求不是加固选项**。

### 现象：Windows 上文件类动作拿不到路径 / 删除步骤被静默丢弃
- 根因：`os.path.isabs("/x")` 在 Windows 上为 **False**，而 `os.path.join(base, "/x")` 又会丢弃 `base` —— 两个内置函数判定不一致，导致"绝对但不存在 → 丢弃/锚定"的防线与白名单判据在 Windows 上全部失效。
- 修复：`HostAgent._is_abs_any()`（`/`、`\`、盘符都算绝对）用于 `_resolve_target`/`_normalize_dest`/路径归一循环；组2 用 `_is_pathish()`（`startswith(("/","~",".")) or os.path.isabs`）。
- 教训：**跨平台代码里不要单用 `os.path.isabs` 当"绝对路径"判据**。

### 现象：对话助手把"通用助手的能力"说成自己的（管理 Agent/任务/会话、检索日志…）
- 根因：`/api/chat` 原先只给 LLM 一句"你是控制台对话助手，用简洁中文回答"，**没有任何事实约束** → 模型自由发挥，宣称本系统并不存在的业务。
- 修复：① `_capability_brief()` 从 `registry.list_tools()`/`skills.list_skills()` **现场生成**能力清单（含"明确做不到"清单与浏览器通道实况）；② `_is_capability_question()` 让"你能做什么"类提问**零 LLM** 直出清单；③ `_system_facts()` 让"系统状态/统计/知识库/设置"类提问**先取真实数据**（无 LLM 就直接给事实）；④ `_chat_system_prompt()` 把清单钉进 system prompt 并写死"不得编造功能/数据"。
- 教训：**给 LLM 的 system prompt 必须携带"运行时事实"**，否则它会用训练语料里的通用产品能力填空。

### 现象：问"当前系统状态与最近的编排结果"，却弹出"打开应用"的执行卡片
- 根因：`/api/chat` 的路由顺序是"操作判定 → LLM"，而规则引擎只看关键词 —— "运行/系统状态"命中了 `open_app` / `system_check` 规则。
- 修复：路由顺序改为 **能力 → 事实 → 操作卡片 → 复杂任务引导 → 普通对话**；事实通道的关键词刻意收窄（不收"任务/最近/状态"这类泛词）。
- 教训：**"提问"与"指令"要分流** —— 对系统自身数据的追问优先走查询通道，别让关键词规则把它变成操作。

### 现象：截图/邮件发出去的图是"半张白页"（页面还没渲染完就抓了屏）
- 根因：`take_screenshot` 是**固定 `time.sleep(2)` 后抓屏**，既不判断页面是否就绪，也不判断画面是否还在变化。搜索结果页的结果列表是在 `document.readyState=complete` **之后**才异步填上的，两秒往往不够；`web_search` 的就绪等待也只覆盖到 `readyState`，管不到"绘制"。
- 修复：两道闸门 —— ① `WebAutomation.wait_page_ready()`：`Page.bringToFront`（把窗口提到最前，避免拍到别的窗口）→ 等 `readyState=complete` + 所有 `document.images` complete → 连等两帧 `requestAnimationFrame` → `body.innerText` 长度连续两次采样一致（异步渲染的判据）；② `_wait_screen_stable()`：连续两帧像素 SHA-1 一致（最短 1.2s / 最长 6s）。**拿不准要在返回值里如实标注**（`page_note`/`screen_note`/`waited_ms`），不要假装稳定。
- 注意：`wait_page_ready_if_active()` 只在浏览器**已经在跑**时等待 —— 纯桌面截图不该为了截图去拉起一个 Chromium。
- 教训：**"等 N 秒"不是同步手段**；要等的是"目标状态出现了"（就绪 + 绘制 + 内容不再变化）。

### 现象：编排末尾的 `run_command` 步失败「bwrap: setting up uid map: Permission denied」，整轮审计变 FAIL
- 根因：`run_command` 只用 `shutil.which("bwrap")` 判断"有没有沙箱" —— 只查**存在性**。Ubuntu 24.04 默认 `kernel.apparmor_restrict_unprivileged_userns=1`，bwrap 装了但非特权进程建不了 user namespace，于是**沙箱层**先失败（命令根本没被执行）。计划末尾的收尾步默认 `sandbox=True`，所以**每一轮编排**都会以这一步失败收场、审计判 FAIL —— 明明是天气那步成功的。
- 修复：① `_bwrap_usable()` 真探测一次（`bwrap --ro-bind / / --dev /dev --proc /proc true`），结果按进程缓存，区分"没装"与"装了启不动"；② 不可用时**明说降级**：结果前缀写 `[沙箱不可用:<原因> → 直接执行]`；③ 探测通过但实跑时沙箱层报错（`_BWRAP_ERR_RE` 识别 `bwrap:` / `setting up uid map` / `Failed to setup * namespace`）→ 自动**直接重试一次**，写成 `[沙箱层失败 → 已直接执行] 沙箱错误: …；<输出>`，并把缓存标记为不可用（后续不再白试）。
- 判读：看到 `[沙箱不可用:…]` / `[沙箱层失败 → …]` 都是**如实降级**，不是故障。想要真沙箱：放开 `kernel.apparmor_restrict_unprivileged_userns` 或给 bwrap 配 AppArmor profile（都要 sudo，按安全策略取舍）。
- 教训：**"有可执行文件"≠"能用"** —— 沙箱、浏览器、封装层这类外部依赖，判定必须跑一次真握手（health probe），而不是查 PATH。

### 现象：编排被拦「工具签名不符(可能被篡改): ['run_command']」，但其实只是改了代码没重启
- 根因：第 3 层原先拿 `inspect.getsource(func)` 的**文本摘要**当唯一判据，而 `getsource` 是**按 `co_firstlineno` 从磁盘文件重读**的。运行中的进程用的是加载时的旧代码，只要磁盘上的 `.py` 变过（哪怕只是在同一函数上方插了几行，偏移错位），同一函数读出来的文本就变了 → 摘要必然不符 → **判为"可能被篡改"并挡住一切执行**（实测：开发期改一次 `run_command`，整轮编排就被拒，还会让人以为被攻击）。
- 修复：第 3 层改成**双信号**
  · **篡改** = `marshal.dumps(func.__code__)` 摘要与登记时不一致 → 进程内真正在跑的工具被动过 → **拦**（这才是本层要防的）；
  · **漂移** = 只有源文件 `(mtime_ns,size)` 变了 → **不拦**，在 `check.reason` 里提示 `⚠ 磁盘源码已变更: [...]（进程仍运行旧版本，重启后生效）`。
- 判读：看到 `工具运行时代码与登记不一致` = 真异常；看到 `⚠ 磁盘源码已变更` = 重启一下就好，不影响本次执行。
- 教训：**"磁盘上的源码"和"进程里在跑的代码"是两件事** —— 拿文件摘要当运行时代码的完整性判据，等于把"忘了重启"报成"被入侵"。

### 现象：在对话里打「执行 system_check」不弹执行卡片，助手还说"卡片已生成，等待你确认"
- 根因：**规则引擎只认动作语义（中文说法），不认名字**。`system_check` 是能力标识符，`_match_goals` 扫不到 → 没有 goals → `detect_operation` 返回 None → 不弹卡片；接着落到 LLM 闲聊，而模型看到能力清单里有 `system_check`，就**顺口编了"执行卡片已生成"**（它并没有这个能力）。
- 修复：两条一起做
  ① **别名路由**（`group_1/host_agent.py`）：`_ACTION_ALIASES` 把标识符换成规范中文（`system_check`→"检查系统状态"），`_BARE_ACTION_RE` 先判"整条消息是不是一个裸名字（可带 执行/运行/启动/打开 前缀）"，是就按标识符解析 —— 优先级**高于规则表**，否则"运行 send_email"会被"运行"抢成 `open_app`。只收有中文规则的动作，且要求标识符独立成词（`read_file.txt` 不算调用）。
  ② **缺参数就给说明**（`src/capability_hints.py` + `/api/chat` 第③段）：没有中文规则或必填参数未给的名字（`read_file`/`list_directory`…）直接回**确定性**的"必填/可选参数 + 工具台怎么点 + 一句可照抄的自然语言例子"，绝不猜参数。
  ③ **prompt 加固**：明确禁止 LLM 声称"卡片已生成/正在等待确认/已开始执行"。
- 教训：**确定性路由的产物，不能让 LLM 代言**；同时"用户会直接抄标识符"是必须支持的一等输入 —— 入口（CLI/编辑器/工具台/产物 JSON）到处都印着标识符。

### 现象：发封邮件被拦「受保护路径: ['/mnt']」，可用户压根没提路径
- 根因（两条叠加）：
  1. **LLM 会编路径**：组1 的 system prompt 只给了动作表，没告诉模型"本机真实的工作区在哪"，于是它照训练语料编 `/mnt/...`、`/home/user/...`（实测「发送aaa给3398…@qq.com」被编出一个 `/mnt` 下的附件/目标）；
  2. **路径保护面过宽**：`/mnt`、`/media` 曾被我列进受保护目录 —— 但它们是**用户数据区**（WSL 的 Windows 盘、可移动介质），一列进去等于"不许碰我自己的文件"；而且 `send_email` 的 `target` 也被当成"要触碰的路径"扫描，工作区一旦落在受保护目录，正常发信也被拦。
- 修复：① `/mnt`、`/media` 从两份受保护目录里去掉（仍保留 `/etc /usr /bin /sbin /lib /boot /root /var /opt /srv /proc /sys /dev`）；② `_TARGET_IS_PATH = _PROTECTED_SCOPE_ACTIONS - {"send_email"}` —— 发信只查**附件**；③ 拦截原因带上出处（`受保护路径: ['/etc（来自 write_file.path）']`），不再只报一个光秃秃的目录；④ LLM prompt 注入**真实工作区 + 平台 + 路径纪律**（"路径必须是话语里出现过的或工作区内的，不要编造"）。
- 实测（真实 LLM 路径）：修复前 → 编出 `/mnt` 并被拦；修复后 → `send_email(to=…, body="aaa")`、`approved=True`、无编造路径。
- 教训：**给 LLM 的提示词要带"真实环境坐标"**（工作区、平台、分隔符），否则它会用训练语料里的路径填空；**路径保护要按"这个动作真的会碰什么"取字段**。

### 现象：所有指令都被拦「工具运行时代码与登记不一致(疑似被篡改)」，换哪条都一样
- 根因：第 3 层的运行时核对原本是**按 `module+qualname` 反查函数**再比 `marshal.dumps(func.__code__)` 的摘要。可"同一模块被两条路径导入"（`sys.path` 混用、顶层副本与包内副本并存、热更/重导入）时，**反查回来的对象 ≠ 注册时持有的对象** → 摘要必然不符 → 判"被篡改"并拒执行；注册表里有 29 个工具，只要有一个对不上，**每条指令都被拦**（用户看到的现象：换指令也一样，且报的工具名会变）。
- 修复：① 签名记录**直接持有注册时的函数对象**（`rec["_func"]`，只存内存不序列化），核对它 —— 这正是注册表将来要执行的对象，也就是说"名字反查"这一层脆弱性被彻底去掉；② 判定分三档：**代码变+源文件未变 → 拦**（真·就地篡改，实测把 `func.__code__` 换掉仍能被抓住）；**代码变+源文件也变 → 提示不拦**（热更/重导入，可解释）；**只有源文件变 → 提示不拦**（改码没重启）；③ "源文件是否变过"按**登记时记录的路径** stat，不信当前 `co_filename` —— 否则篡改者顺手换掉 `co_filename`，就能把真篡改伪装成"可解释"混过去。
- 教训：**完整性核对要盯"将要执行的那个对象"**，而不是"按名字再找到的对象"；名字反查在有多份副本/多路径导入的项目里天然不可靠。另外，安全层一旦误报就会**全量阻断**，所以它的判据宁可保守（提示）也不能把"开发期常态"判成攻击。

### 现象：测试里一大堆 ERROR，但代码其实没问题
- 根因：加固过的运行环境（safe-delete shim）会在 `shutil.rmtree` 时抛 `SystemExit`；`ignore_errors=True` 不吞 `SystemExit` → `tearDown` 报 ERROR，把 body 的真实结果也盖住了（曾掩盖 1 个真实失败）。
- 修复：测试的 `shutil_cleanup` 捕获 `BaseException`。
- 教训：**先分清"环境噪音"与"真实失败"再去修**；tearDown 异常会污染整个用例结论。

### 现象：正常指令被判"危险操作"拦截，只因为话语里出现了 add/format/halt 这类英文词（2026-09-19 逻辑审计）
- 根因：危险黑名单用**子串**匹配，而 `SecuritySandbox._surface()` 把 user_text
  也铺进扫描面 —— 裸 "dd " 命中 "add file"、"format" 命中 "--format="。
  反过来长选项变体（`rm --recursive --force`、`find -exec unlink`、
  `python -c "…rmtree('/etc')"`）却全漏。
- 修复：双层口径 —— 关键词表只留多词/符号形态；单词命令走**词界正则**
  `(?<![\w./-])halt(?![\w-])`（dd 要求 `dd 参数=`、format 要求 `format c:`），
  并补漏拦变体。security 与 default_tools 两份表**必须同步改**
  （test_regression_audit 交叉断言 set/list 相等）。
- 教训：子串黑名单在"话语全文也是扫描面"的架构下必然误伤；
  单词级命令只能用词界，且误伤修掉的同时要拿漏拦变体回归成对测试。

### 现象：GUI 回看历史运行，提权/自愈记录恒 0 条；产物 JSON 查不到"批准方案==执行方案"
- 根因：① 组3 `execute()` 返回的 session 直接引用 `self.escalated` 等**实例活
  列表**，红线 4 要求每次运行 `clear()` —— 存进 RUNS 的旧 bundle 被后续运行
  当场掏空；② 产物五件套固定文件名逐轮覆盖。
- 修复：返回 `list(...)` 快照；`_persist` 在最新副本之外按
  `runs/<时间戳>_<intent_id>/` 归档（mask 后写入，归档失败静默）。
- 教训：跨运行常驻单例的"会话级可变列表"，**离开实例中时一律交快照**；
  "确认即执行所确认方案"（红线 5）要有事后可对账的载体才成立。

### 现象：LLM 说"先干跑"，规则却把邮件真发出去了（确定性归一的**方向**错了）
- 根因：`dry_run` 归一无条件用规则判定值**覆盖** LLM 输出 —— 话语没命中
  草稿词表时，把模型保守给出的 True 翻成 False；而 --text/AIShell 路径
  没有确认闸门，方向不可逆。
- 修法：确定性归一在"危险自由度"上必须**保守单向**：任一来源判"草稿"即
  干跑，两路都表态真发才真发。新增执行语义字段的归一时先问：
  覆盖方向是"更安全"还是"更危险"？后者一律 OR/取严。
- 连带（同轮审计）：LLM 给 `"params": null` 是合法 JSON，
  `setdefault("params", {})` 返回 None 直接 TypeError 炸整轮 ——
  取 params 一律走 `_ensure_params`。


## 合法"删除不存在的绝对路径"被误拦/被改写（2026-09-19 evals 首轮现场，四处同根）

**现象**：`删除 /no/such/path/x.txt` ① params.path 被静默改写成 `工作区\x.txt`；
② 修掉①后又被安全闸门以"删除类动作的目标为空或根目录"整体拦截；③ `把 /home/u/a.txt
复制到 /home/u/sub/b.txt` 的 dest 被丢弃（规划报"缺少必需参数"）；④ open_url(裸域名)→web_open
改写后不补 `https://`（原生 web_open 步骤却会补）。

**根因**（同一个病灶的四个变体："闸门/归一看的数据 ≠ 执行用的数据"）：
1. `_finalize_intent` 的"LLM 幻觉锚定"（不存在绝对路径→工作区+文件名）没区分动作危险性，
   把**规则路径里用户亲口说的严格动作路径**也劫持了（红线 10 原本只落在 target 面，没落 params 面）；
2. dest 的"父目录必须存在"驳回没有话语证据直通 —— 复制到**尚不存在的新子目录**是合法意图；
3. `security._iter_actions` 扫 goal 空 params 判"空目标"，但 planner 执行时以 intent.params
   为第 1 层基底填参 —— 闸门看到的 goal 与执行用的 step 参数集不同；
4. 裸域名补协议只写在"原生动作是 web_open"的分支，改道路径漏了。

**修复**：严格动作（_TARGET_STRICT_ACTIONS）的不存在绝对路径原样保留；dest 在话语中有迹可循
→直通（LLM 编造 dest 锚回工作区的防线不变）；同动作 goal 缺参按组2 口径继承顶层 params 再过
闸门；改写产物走同一补协议逻辑。回归锁定：`TestStrictPathPreserve` + evals 16/16。

**教训**：改一条"跨组口径"时必须同时问 —— 闸门扫描面、归一层、执行面、改写产物路径这四处
是不是都看见同一份数据；以及：**把复现文本先写成 evals/tasks.py 任务，再动手修**。

## verify_all 偶发 FAIL 定位不到用例：子进程输出只留尾行

**现象**：verify_all 报"回归套 FAILED (failures=1)"但表格里看不到失败用例名。
**根因**：失败时只打印最后一行；TTL 到期类用例在 Windows（定时器粒度 ~15.6ms，
sleep 可短睡）+ 机器负载下偶发。
**修复**：_run 非零退出即把完整输出落 `logs/verify/<项目>.log`；测试时间窗从
50ms 放宽到 300ms。经验：**时间敏感断言在 Windows 副本上留 ≥5 倍余量**。
