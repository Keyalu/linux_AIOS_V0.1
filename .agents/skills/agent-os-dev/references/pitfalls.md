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

### 现象：LLM 参数幻觉（把上一步整个结果对象塞进字符串参数、编造不存在的文件路径）
- 根因：LLM 输出随机性 + 提示词动作表缺项（LLM 不知道某工具存在时只能变通幻觉）。
- 修复三层：组1 提示词动作表与参数规范保持最新（**加新工具必须同步**）；组1 后处理归一（dry_run/attachment/路径解析）；组2 `_fill_params` 类型防御（字符串参数收到 dict/list 一律丢弃）。

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
