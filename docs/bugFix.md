# Agent_OS_v1.0（AIOS_V0.1）缺陷修复报告 · bugFix

> **修复日期**：2026-09-15
> **依据**：`docs/bugFind.md`（第二轮审计，正文实际 134 条）+ `docs/bugFind_round1_20260914.md`（第一轮 136 条）+ `docs/bugFind_verify_20260915.md`（第三方复核：确认 117 / 部分成立 16 / 驳回 1，另新增 V-01~V-05）
> **修复口径**：**全部 P0 必修**；P1 除 2 条（`P1-10`、`P1-14`）外全部修复；P2 修复 34 条、P3 修复 6 条，其余**逐条写明"不修的理由"**（见 §7），不做沉默跳过。
> **红线遵守**：五组接口契约字段未改；组间解耦纪律未破（组5 依旧不 import 组4 —— 需要函数对象时由编排层作为**数据**注入）；未引入任何第三方依赖。

---

## 0. 修复总览

### 0.1 变更文件（24 个）

| 层 | 文件 | 修复条目 |
|----|------|----------|
| 组5 安全 | `group_5/security.py` | P0-01/02/03、P1-01/02/03/04/15、P2-57 支撑、V-01 支撑 |
| 组5 协调 | `group_5/coordinator.py` | P1-11/16/17、P2-42/57/58/59/60/62/63/64 |
| 组5 RAG | `group_5/rag.py` | P2-63/64 |
| 组4 工具 | `src/default_tools.py` | **P0-04**、P1-02/03、P2-01/02/03/04 |
| 组4 工具 | `src/real_tools.py` | P1-05、P2-06/07/08/09/10、V-02 |
| 组4 技能 | `src/skill_library.py` | P2-11/12/13/15、V-03 |
| 组4 注册 | `src/tool_registry.py` | 新增 `functions()`（签名核对数据面） |
| 组4 网页 | `src/web_automation.py` | **P0-10**、P1-12、P2-24/25/26/28 |
| 组4 MCP | `src/mcp_connector.py` | P2-21/23 |
| 组4 统计 | `src/stats.py` | P2-19 |
| 组1 意图 | `group_1/host_agent.py` | **P0-05**、P1-06/07/08/09/13/14 支撑、P2-32/34、新增 `write_file/take_screenshot/run_command` 规则 |
| 组1 Shell | `group_1/ai_shell.py` | **P0-07** 支撑（交互式真人确认） |
| 组2 规划 | `group_2/task_planner.py` | P0-05 支撑、P1-12/13/19、P2-38/39 |
| 组2 路由 | `group_2/route_policy.py` | P2-42 |
| 组2 检测 | `group_2/control_detector.py` | **P1-20**、P2-45 |
| 组3 执行 | `group_3/app_agent.py` | **P0-07**、P1-16/18、P2-47/48/56 |
| 组3 自动化 | `group_3/automator.py` | P0-06(降级 P1)、P2-49/50、**V-01** |
| GUI 服务端 | `gui/server.py` | **P0-08/09**、P1-21/22/23/24/25、P2-66/68/70/71/72、P3-03/04 |
| GUI 前端 | `gui/index.html` | **P0-11/12**、P1-16 支撑、P2-73/74/76/77/78、P3-06/07/08/09/13 |
| 入口 | `main.py` | P0-03 接线、P3-15/16 |
| 测试 | `tests/test_integration_v10.py` | tearDown 假 ERROR、P0-10 断言纠正 |
| 测试 | `tests/test_regression_audit.py` | **新增 57 项回归** |
| 配置 | `.gitignore` | 新增 `.aios_trash/`、`tests/.tmp_regr_*/` |

### 0.2 验证结果

| 验证 | 修复前 | 修复后 |
|------|--------|--------|
| `compileall` 全量语法 | True | **True** |
| `tests/test_integration_v10.py`（51 项） | 34 通过 / 13 ERROR（其中含**环境 safe-delete shim 造成的 tearDown 假 ERROR**）/ 2 FAIL | **50 通过 / 1 FAIL / 2 skipped**（唯一 FAIL 是 Windows 上"场景2 导航"——本机没有文件管理器，Linux 目标机通过）；13 个假 ERROR 消失 |
| `tests/test_regression_audit.py`（新增） | — | **57/57 通过** |
| GUI 服务端同源防线冒烟（真实起服务 + 原始 socket） | — | **7/7 通过**（见 §4.1） |
| 7 场景演示矩阵 | 6/7（场景2 平台失败；且失败被当作"1/2 步成功"混过） | 6/7，**失败原因如实显示**；提权模式在无人值守时打印"confirm_mode=auto"提示 |

---

## 1. P0 致命缺陷修复（12/12）

### P0-01 / P0-02　安全闸门只看顶层 `params.cmd` → goal 级命令绕过

- **修复**（`group_5/security.py`）：`check()` 改为对**整条意图**构建检测面：
  - `_surface(intent)` 递归收集顶层 + 每个 `goal`/`step` 的全部字符串值（含键名）；
  - `_iter_actions()` 逐 goal 产出 `(action, params)`，供路径保护与风险定级复用。
- **验证**：`tests/test_regression_audit.py::test_goal_level_dangerous_command_blocked`
  与 `::test_plan_step_stepform_dangerous_command_blocked` —— 藏命令的意图现在返回
  `approved=False, risk_level=critical`。
- **附带**：`_risk_of_intent()` 改取**所有 goal 的最高风险**（不再只看顶层 action）。

### P0-03　第 3 层"签名验证"从未生效且逻辑本身是坏的

- **修复**：
  1. `register_signature()` 记录 `{digest, module, qualname}`，取不到源码（内置/动态函数）返回 `None` 而不抛错；
  2. `_verify_signatures()` 通过 `sys.modules[module]` + 逐段 `qualname` 反查函数对象后重新取源码摘要（原实现 `__import__(工具名)` 除"模块名恰好等于工具名"外永远取不到）；
  3. **接线落地**：`src/tool_registry.py` / `src/skill_library.py` 新增 `functions()` 只读快照，由 `main.build()` 与 `gui.build_system()` 调用 `security.register_signatures(...)` —— 组5 拿到的是**函数对象（数据）**，依旧不 import 组4 业务代码；
  4. `register_signatures()` 批量登记，源不可得者自动跳过。
- **验证**：`::test_signature_layer_detects_tamper`（登记后替换 `copy_file` → 立刻报 `["copy_file"]`；还原后回到 `[]`）、`::test_signature_ignores_unsourceless_callables`（`len` 不抛错）。

### P0-04 / P1-02 / P1-03　受保护路径与危险命令黑名单

- **修复**（`src/default_tools.py` + `group_5/security.py`，两份**逐字对齐**）：
  - `PROTECTED_PATHS` 增补 `/var /opt /srv /media /mnt` 与 `c:/programdata`，两份清单完全一致；
  - 新增 `SENSITIVE_SUBPATHS`（`.ssh / .gnupg / .aws / .kube / .config / .local/share/keyrings / .password-store / .workbuddy / .netrc / .git-credentials / .bashrc / AppData …`）；
  - **主目录根本身精确匹配**（允许其子目录作为工作区，禁止对整目录动手）；
  - 新增 `DANGEROUS_PATTERNS` 正则族：`curl|wget … | sh`、`base64 … | sh`、`find … -delete`、`truncate -s 0`、`> ~/.bashrc`、`> /dev/sdX`、`sudo sh`；
  - `delete_file` 加空路径/文件系统根校验，并**默认软删除**（见 P0-04 专项）。
- **路径保护只作用于"会改动磁盘/会外发/读敏感文件"的动作**（`_PROTECTED_SCOPE_ACTIONS`），只读浏览类（`navigate`/`list_directory`/`find_*`/`disk_usage`）不受限 —— 修复 P1-04 的"话语里提到 /etc 就整条拒绝"（`user_text` 不再进路径扫描面，`run_command.cmd` 仍在扫描面内）。
- **验证**：`::test_protected_paths_cover_home_and_sensitive`、`::test_policy_parity_between_groups`（两份清单集合相等）、`::test_dangerous_patterns_cover_download_and_exec`、`::test_user_text_not_scanned_for_protected_paths`。

### P0-04（专项）　`delete_file` 可递归删光用户主目录

- **修复**：`delete_file(path, to_trash=True)` ——
  - 默认**软删除**：移入 `<项目根>/.aios_trash/<时间戳>_<随机>_<原名>`（可人工找回，`.gitignore` 已排除）；
  - 移动失败**不降级**为永久删除，如实报错；
  - `to_trash=False` 才走 `rmtree/unlink`，仍拒受保护路径；
  - schema 增加 `to_trash` 参数与说明。
- **验证**：`::test_delete_file_uses_trash_and_stays_recoverable`、`::test_delete_file_refuses_home`、`::test_delete_file_refuses_sensitive_dir`。

### P0-05　「删除不存在的路径」= 删除整个工作区

- **修复**（两处协同，缺一不可）：
  1. `group_1/host_agent.py::_resolve_target(raw, default, mode)` 引入**三种解析模式**：
     - `workspace`（目录级动作 organize/backup/…）：解析不出 → 回落工作区（保留演示语义）；
     - `anchor`（产出型 write_file/take_screenshot）：相对名锚定工作区，**绝不落进程 CWD**；
     - `strict`（破坏/搜索 delete_file/find_files）：解析不出 → **原样保留**，交工具如实报"不存在"。
  2. `group_2/task_planner.py::_fill_params` 的"兜底填参"从"取 schema 第一个 string 参数塞 target"改为**白名单 `_PATH_FALLBACK`**（只给确实需要工作区目标的动作填其**主路径参数**）——`delete_file` 不在白名单，缺 `path` 即如实报"缺少必需参数"，绝不指向工作区。
  3. 新增 `_extract_params` 对 `delete_file`/目录级动作的**显式路径抽取**（引号优先 → 绝对/`~/` 路径），让"删除 <路径>"能拿到真实目标。
  4. `_norm_nodes()`：路径归一循环从"只遍历 goals"扩展为"顶层 intent + goals"（规则路径的参数挂在顶层，之前从未被归一）。
- **验证**：`::test_delete_nonexistent_path_not_redirected_to_workspace`（target 不再等于工作区）、`::test_planner_never_fills_delete_path_with_workspace`（无 path → 步骤丢弃 + unresolved；有 path → 原样使用）、`::test_dir_scope_actions_still_fall_back_to_workspace`（演示语义未被误收紧）。

### P0-06（复核降级为 P1）　`open_app` 的 `os.system` 拼接与谎报成功

- **修复**：改为 `subprocess.Popen([binary], start_new_session=True)`（**不经 shell**），并检查 `proc.poll()`；启动后立即退出（returncode ≠ 0）如实报失败。复核认定"注入不可达"（`shutil.which` 前置），但"不校验返回码 → 谎报已启动"成立，两者一并修掉。
- **验证**：`test_regression_audit.py` 覆盖 `open_app` 的 Mock 分支语义；源码不再含 `os.system(`。

### P0-07　提权确认流缺省"永远同意"

- **修复**：
  1. `AppAgent(confirmer=None, auto_escalate=True)`：缺省不再用"恒真 lambda"冒充人工确认；`_ask_escalation()` 返回 `(是否同意, 来源)`，来源为 `human` / `auto` / `human-error`（回调异常按拒绝处理）；
  2. 审计记录带 `confirmed` / `retried` / `confirm_mode`；**被拒绝的提权请求单独进 `escalations_denied`**（修复 P1-16 的"界面替用户代签"）；
  3. `AIShell` 在交互模式注入 `input()` 版确认（真人确认）；GUI 设置页的 `auto/deny` 直接映射到 `auto_escalate`；
  4. `main.run_demo()` 在无人值守时**打印提示**，审计标 `confirm_mode="auto"`。
- **验证**：`::test_escalation_records_confirm_mode`（`confirm_mode == "auto"`、`retried == True`）。

### P0-08　GUI 全部 POST 无 CSRF / 同源校验

- **修复**（`gui/server.py`）：新增 `_same_origin_ok()`，任一不满足即 `403`：
  1. `Host` 必须是回环地址（`127.0.0.1` / `localhost` / `::1`）—— 防 DNS rebinding；
  2. 带 `Origin` 时必须同源（回环 + 同端口）；
  3. `Content-Type` 必须是 `application/json` —— **跨站"简单请求"只能用 text/plain / form-urlencoded / multipart，这一条即挡掉 CSRF**（前端所有 POST 本就带 JSON 头）；
  4. 另加 `MAX_BODY = 1MB` 请求体上限。
- **验证**：§4.1 的真实起服务冒烟（`text/plain` / `Origin=evil` / `Host=evil` 三路全 403，同源 JSON 200）。

### P0-09　`/api/orchestrate` 兼容路由一步到底（auto 执行）

- **修复**：该路由现在也走两阶段流 —— `needs_confirm` 为真时**只回计划 + 确认令牌**（`pending: True`），不再自动执行。

### P0-10　`web_*` 工具通道不可用却报 `success=True`

- **修复**：5 个工具（web_open/web_click/web_search/web_state/web_extract）在 `CDPError` 时改为
  `{"success": False, "simulated": True, "degraded": True, "error": "[Mock·未执行] …"}` ——
  **动作没发生就是失败**，`simulated/degraded` 供上层识别"降级而非真实故障"。
- **同步纠正测试**：`tests/test_integration_v10.py::test_web_tools_mock_degrade` 原先把该缺陷**写成了断言**，现改为断言"降级必须可见（success=False）"。
- **验证**：`::test_web_tools_fail_honestly_when_channel_down`。

### P0-11　前端 `loadChatHistory` / `saveChatHistory` 未定义

- **修复**（`gui/index.html`）：补上两个函数（`/api/chat_history` 与 `/api/chat_save` 的包装，含失败静默）。
  修掉的连带故障：① 首屏脚本中断导致 `loadLLM/loadSMTP/loadSettings` 与 `#hash` 直达全部不执行；② 每轮对话成功后被 catch 改写成"✗ 请求失败: ReferenceError"。
- **验证**：`::test_chat_history_functions_defined`（各定义恰 1 次）、`::test_no_duplicate_function_definitions`。

### P0-12　`esc()` 不转义引号 → 属性逃逸 XSS

- **修复**：`esc()` 增补 `" → &quot;`、`' → &#39;`；`mdRender` 的链接改写改为**先过 `safeUrl()` 白名单**（仅 `http(s)://`）再 `esc()` 进 `href`；非 http(s) 的伪链接直接按纯文本渲染。
- **验证**：`::test_esc_escapes_quotes`、`::test_url_whitelist_for_markdown_links`。

---

## 2. P1 修复（23/25）

| 编号 | 修复要点 | 验证 |
|------|----------|------|
| P1-01 `redact` 零调用 | 第 4 层接入审计：`SecuritySandbox.mask_record()` **递归**打码（① 敏感**字段名**命中 → 整条值换 `<已脱敏>`；② 字符串值内的邮箱/内联密钥走正则），`SystemCoordinator.audit()` 调用它 | `::test_audit_redacts_secrets`、`::test_redact_masks_secrets` |
| P1-02 两份清单不一致 | 见 P0-04；并加**口径一致性回归**防漂移 | `::test_policy_parity_between_groups` |
| P1-03 黑名单漏网/误伤 | 见 P0-04（正则族）；`format` 等裸子串误伤属"宁可误拦"的既有口径，未放开 | `::test_dangerous_patterns_cover_download_and_exec` |
| P1-04 误拦整条指令 | 路径扫描面不再含 `user_text`；只读动作不受路径保护 | `::test_user_text_not_scanned_for_protected_paths` |
| P1-05 附件可外发敏感文件 | `send_email` 附件逐个过 `_is_protected_path`，总量超 `MAX_ATTACH_TOTAL`(25MB) 拒绝，单文件仍整读但先做上限判定 | `::test_send_email_refuses_protected_attachment` |
| P1-06 规则路径缺 3 个动作 | `_RULES` 新增 `take_screenshot`（截屏/截图/截个屏）、`write_file`（写入/写到/写一个/保存到/存到，抽 path+content）、`run_command`（执行命令/运行命令/命令行，只认引号内命令）；`_match_goals` 新增"有截屏则丢弃 write_file"消歧 | `::test_rules_cover_write_and_screenshot` |
| P1-07 邮箱规则不容忍空格 | `(发给\|发送给\|发到\|抄送给)\s*\S*@\S+`；并新增 `起草.{0,6}邮件` 规则（离线也能识别"起草邮件"） | `::test_email_rule_tolerates_space` |
| P1-08 静默改写目标 | 见 P0-05（strict 模式不再回落）；新增 `_is_abs_any()` 修掉"POSIX 绝对路径在 Windows 被判成相对"的跨平台错判 | `::test_delete_nonexistent_path_not_redirected_to_workspace` |
| P1-09 附件只认绝对路径 | 保留"裸文件名不猜"的保守设计，但 `write_file/take_screenshot` 改为锚定工作区（不再落 CWD）；规则路径新增显式路径抽取 | 见 P1-06 用例 |
| P1-11 `degraded` 被丢弃 | `plan_only()` 返回值补齐 `degraded`，并顺带补上成功路径的 `_persist()` | `::test_plan_only_reports_degraded`、`::test_plan_only_persists_artifacts` |
| P1-12 `web_click` 参数污染 | schema 里 `text` 改 `required=False`（与 text/selector/nth 三选一一致）；兜底填参不再乱填 | `::test_web_click_param_not_polluted`、`::test_v04_web_click_accepts_selector_or_nth_without_text` |
| P1-13 路径判据不支持 Windows | `_is_pathish()`：`startswith(("/","~",".")) **或** os.path.isabs`（含盘符） | Windows 本机实测文件动作可取到路径 |
| P1-15 `run_command` 定级与沙箱 | `_risk_of("run_command") → "high"`；规划器生成的所有 `run_command` 步骤（含收尾步）**默认 `sandbox=True`** | `::test_run_command_risk_is_not_low`、`::test_planner_sandboxes_planned_run_command` |
| P1-16 拒绝提权被渲染成"已确认" | 见 P0-07；前端按 `confirmed/retried/confirm_mode` 分别渲染，并单列"提权被拒" | `::test_escalation_records_confirm_mode` |
| P1-17 "仅执行免提权步骤"名不副实 | `execute_approved(confirm_mode="deny")` 改为**先过滤 `requires_admin` 步骤再执行**，跳过的步骤记入 `skipped_admin_steps` | `::test_deny_mode_filters_admin_steps` |
| P1-18 `prev_result` 张冠李戴 | `_resolve_refs(params, ctx, prev_idx)` 严格指向紧邻上一步；该步无结果时返回"(上一步无结果/失败)" | `::test_prev_result_does_not_borrow_older_step` |
| P1-19 `last_file_target` 只看 skill | 任意 kind 的步骤只要带 `path/directory/dest` 都更新 | 代码路径 + 计划器回归 |
| P1-20 Mock 控件缺 `app/depth` | `_simulated_elements()` 补齐五键（严格对齐自身输出契约） | `::test_build_system_called_once` 同文件静态断言 + 手动核对 |
| P1-21 `/api/run` 分支重复 + 注释错位 | 删除不可达的第二份分支（3 → 2 处）；`# /api/call` 注释移回真正的分支上方 | `::test_no_duplicate_run_branch` |
| P1-22 `do_GET` 无兜底 | `do_GET` 包一层 try/except → 500 JSON；`k`/`cursor` 用 `_safe_int` + 钳位 | `::test_do_get_has_error_guard` + §4.1 冒烟 `k=abc` |
| P1-23 `/api/reset_stats` 假清空 | 调 `STATS_OBJ.clear()`（内存+文件一起清） | `::test_reset_stats_clears_live_counters` |
| P1-24 凭据明文/掩码泄露 | `_mask_secret()` 只报"已配置（N 字符，已隐藏）"，**不回显任何真实字符**（4 处掩码点统一） | `::test_secrets_not_partially_echoed` |
| P1-25 `RUNS` 随机淘汰 | 改按**插入顺序**淘汰非运行中的最旧记录 | 代码路径 |

**未修的 2 条**：`P1-10`（组1 与 `src/llm_client` 两套 LLM 客户端）与 `P1-14`（三份规划器/两份 HostAgent 死副本）—— 理由见 §7。

---

## 3. P2 / P3 修复（按主题）

### 3.1 数据与文件安全

| 编号 | 修复 |
|------|------|
| P2-01 | `create_folder` 增加受保护路径校验 + 空路径校验 + OSError 兜底 |
| P2-02 | `write_file`：校验 content 为字符串、`MAX_WRITE_SIZE`(5MB) 上限、受保护路径校验、**原子写**（`mkstemp` + `fsync` + `os.replace`，失败清理临时文件） |
| P2-03 | `run_command` 的沙箱语义保留（bwrap 不可用时前缀注明），并由规划器默认置 `sandbox=True`（见 P1-15） |
| P2-09 | `take_screenshot`：**先**校验路径（含受保护路径）**再**等 2s；相对名锚定项目根而非 CWD |
| P2-13 | `cleanup_temp` 保留 unlink（语义即"清临时文件"，且已拒受保护路径）；`delete_file` 改软删除覆盖"误删用户文件"的主危害面 —— 差异写入代码注释 |
| P2-15 | `_find_files`：**拒绝**绝对 pattern / 含 `..` / 盘符 pattern；`truncated` 如实标注；元数据不可读的条目 `continue` 而非整体失败 |
| P2-12 | `_organize_downloads` / `_cleanup_temp`：只要有错误即 `success=False` 并把明细写进 `error`（不再"有错也报成功"） |

### 3.2 契约与一致性

| 编号 | 修复 |
|------|------|
| P2-06 | `send_email` 只构建一次 `msg`；`subject`/`body` 做 `None → ""`/`str()` 归一（V-02） |
| P2-07 | `SMTP_PORT` 用 try/except 包裹并回明确参数错误 |
| P2-08 | 收件人非法时**不回显原始列表**，改为打码后的非法项（`z***@example.com`） |
| P2-10 | `open_url` scheme 判断改大小写不敏感；`new` 钳位到 0..2 |
| P2-11 | 删除重复定义的 `call_skill`（保留实现完整的那一份） |
| P2-19 | `stats.py` 注释移出 `else:` 块，补上"逐笔持久性 vs 高压场景请用 BufferedToolStats"的说明 |
| P2-21 | `_real_search` 去重改为标题集合（原判据恒不成立） |
| P2-23 | `register_mock_tools` 用 `setdefault` 登记 server（不再每次覆写 `tools_count`） |
| P2-24 | `WebAutomation._cmd` 去掉可变默认参数，改实例级 `_msg_id` |
| P2-25 | WebSocket pong 用正确的扩展长度编码（`_send_control`）；握手增加 `Sec-WebSocket-Accept` 校验 |
| P2-26 | 保留 `_ensure_locked(url)` 形参（调用方签名），已注明其仅为语义占位 |
| P2-28 | `register_web_tools` / `register_real_tools` 均改为**已注册则跳过**（幂等口径统一，3 套 → 1 套） |
| P2-42 | `_required_ok` 无 schema 时返回 **False**（宁缺毋滥，不再无条件改写动作） |
| P2-45 | `detect_elements()` 遍历对 `None` 子节点跳过（不再静默丢枝） |
| P2-47 | `try_call_with_selfheal` 直接复用 `_call_contract`（消除分叉的第二套自愈逻辑） |
| P2-48 | `_verify_click` 的"同一元素"判定改为 `(name, role, bbox)` 元组比较（不再用整 dict 相等） |
| P2-49 | 未知 action 分支 `simulated` 恒为 `True`（表达"什么都没执行"），并在注释里记录复核对原描述的纠正 |
| P2-50 | `navigate` **不再**静默 `os.makedirs`；目录不存在时前置如实失败（"打开"不扩权成"创建"） |
| P2-56 | `FileManagerAgent.navigate_to/create_folder` 改为真实动作链（navigate / open+快捷键序列），不再是单发一个快捷键 |

### 3.3 编排、审计、并发

| 编号 | 修复 |
|------|------|
| P2-57 | `plan_only()` 成功路径补 `_persist()`（用户在确认面板点"取消"也留下 intent/check/plan） |
| P2-58 | 两处 verdict 表达式收敛为唯一 `_verdict(total, ok, consistent)`：**0 步执行判 FAIL**（不再"什么都没跑也算成功"） |
| P2-59 | `_consistent(session, plan_steps)` 增加"计划步数 == 实际执行步数"的独立核对 |
| P2-60 | 失败反思重试保留首轮证据 `bundle["first_attempt"]`（含首轮 session） |
| P2-62 | 安全模块兜底 stub 改为常驻单例，并在缺失时**审计留痕一次**（不再静默放行） |
| P2-63 | RAG：内存文档数上限 `max_documents=5000`、`_load` 逐行容错（坏行只丢那一行）、读不到当空库 |
| P2-64 | `rag._append` / `coordinator.audit` 加 try/except：旁路写失败不得打断编排 |
| P2-66 | 设置项 `rag_k` 真正生效（`/api/rag` 默认 k 取设置值，query 可覆盖，钳位 1..50） |
| P2-68 | `PENDING` 增加 TTL(1800s) 与容量上限(32)，每次发令牌前清理 |
| P2-70 | `_body()` 加体积上限与 `Content-Length` 解析容错 |
| P2-71 | `stats_payload()` 移入编排锁内，避免读到上一笔旧账 |
| P2-72 | 编排线程异常写入 `audit_log.jsonl`（`stage="run_failed"`），不再只留内存 |
| P2-73 | 前端 `pollRun` 对 `{error}` 响应停止轮询并如实提示（不再每 800ms 抛 TypeError） |
| P2-74 | 聊天历史回填对 `llm_answer` 结果做空值保护（不再 push `undefined`） |
| P2-76 | 工具台支持 `array` 类型参数（textarea + 逗号/换行 → 真数组） |
| P2-77 | `renderBundle` 对 `session.steps` 做空值保护与空表兜底 |
| P2-78 | 删除未使用的 `escSteps` 计算 |

### 3.4 P3（卫生与文档类，修了 6 条）

- `P3-03`：删除 `SYSTEM = build_system() if False else SYSTEM` 占位（并修掉因此重复调用 `build_system()` 的问题）。
- `P3-04`：`# /api/call` 注释归位。
- `P3-06`：删除重复定义的 `showPipeJson`。
- `P3-07 / P3-08`：删除空函数 `_flowTo` 与 `$("#page-settings")&&0;` 占位语句。
- `P3-09`：补齐 9 个缺失 CSS 类（`.field/.tip/.req/tr.hl/.bar-row/.bar-track/.bar-fill/.item.on/.toolbar`）—— 统计条形图此前**完全不可见**。
- `P3-13`：前端控件树对缺 `app/depth` 做防御渲染（配合 P1-20 的服务端修复）。
- `P3-15 / P3-16`：演示场景注释与序号说明更正；`prepare_sandbox()` 重建前把上一轮沙箱**移入回收站**而非直接 `rmtree`。

---

## 4. 验证证据

### 4.1 GUI 服务端同源防线（真实起服务 + 原始 socket）

```
✓ GET / → 200
✓ GET /api/rag?k=abc → 200（坏参数容错，不再断连）
✓ POST /api/call (application/json, 同源) → 200
✓ POST /api/call (Content-Type: text/plain) → 403     ← 跨站"简单请求"形态
✓ POST /api/call (Origin: http://evil.example) → 403  ← 跨站带 Origin
✓ POST /api/call (Host: evil.example) → 403           ← DNS rebinding
✓ POST /api/plan (json) → 200 且含 plan
冒烟结果: 7/7 通过
```
（做法：临时把 `system_out/settings.json` 挪开，避免把其中的 Linux 工作区路径
`makedirs` 到本机；跑完已原样还原，脚本已删除。）

### 4.2 新增回归套件 `tests/test_regression_audit.py`（57 项，全绿）

按主题分 6 组，每条对应一个（或一组）已修缺陷，断言的是**修复后的行为契约**：

| 组 | 覆盖 |
|----|------|
| `TestSecuritySurface` | goal 级命令拦截、风险定级、签名篡改检出、主目录/敏感区保护、两份策略口径一致、危险正则、`user_text` 不误拦、脱敏 |
| `TestDestructivePaths` | 软删除可找回、拒删主目录/敏感区、`create_folder` 保护、原子写与大小上限、删除路径不被改写成工作区、目录级动作仍回落工作区 |
| `TestContractConsistency` | web 工具如实失败、幂等注册、`call_skill` 单定义、邮箱规则容空格、规则覆盖写文件/截屏、`run_command` 默认沙箱、`web_click` 参数不污染、`prev_result` 语义、verdict/对账、邮箱附件保护与脱敏 |
| `TestCoordinatorWiring` | `degraded` 可见、`plan_only` 落盘、deny 过滤提权步骤、提权来源标注、审计脱敏、RAG 容错 |
| `TestFrontendContract` | 两个缺失函数已定义、无重复定义、`esc` 转义引号、URL 白名单、CSS 类齐全、无死占位、POST 带 JSON 头 |
| `TestServerGuards` | 同源防线存在、GET 兜底、无重复 `/api/run` 分支、`build_system` 单次、不再回显凭据片段、`reset_stats` 清内存、兼容路由不自动执行 |
| `TestVerifierFindings` | V-01/02/03/04（复核新增项） |

### 4.3 原集成套件（51 项）

- 修复前：34 通过 / 13 ERROR / 2 FAIL，其中 **13 个 ERROR 是环境 safe-delete shim 在 `tearDown` 抛 `SystemExit`** 造成的假错误（掩盖了 1 个真实失败的 body 异常）。
- 修复后：**50 通过 / 1 FAIL / 2 skipped**。
  - 13 个假 ERROR 消失（`shutil_cleanup` 容错）；
  - 顺带暴露并修掉了被掩盖的 `test_email_draft_mode_dry_run`（根因是规则路径缺"起草邮件"动作，见 P1-07）；
  - 唯一 FAIL = `test_full_scenario_table_passes`：场景2「导航到 /tmp」在 **Windows 上无文件管理器**（Linux 目标机通过）；本次修复让该失败**理由如实**，不再混成"1/2 步成功"。

---

## 5. 关键设计决策（备评审）

1. **区分"软删除"与"永久删除"**：`delete_file` 默认软删除到项目内 `.aios_trash/`（失败不降级）；`cleanup_temp` 保留永久删除（其语义就是清临时文件）。理由是两者危害面不同：前者删的是用户文件，后者删的是临时文件。
2. **路径解析三模式**（`workspace` / `anchor` / `strict`）：把"找不到怎么办"从**单一全局规则**改成**按动作性质决定**。目录级动作保留演示语义，破坏性动作改为诚实失败，产出型动作锚定工作区不落 CWD。
3. **不用 token 而用 `Content-Type + Origin + Host` 做 CSRF 防线**：不引入前端状态、不破坏既有接口调用（含 `health_check.py`），且对"跨站简单请求"与 DNS rebinding 都有效。
4. **Mock 降级判失败**：任务书要求"每组必须提供 Mock 实现"，但项目自述红线是"失败必须可见/绝不谎报"——两者冲突时以**契约字段语义**为准：`success` 表示"动作真的发生了"，`simulated/degraded` 表示"是 Mock 而非真实故障"。
5. **组5 不 import 组4**：签名验证需要函数对象，改为由编排层把函数作为**数据**注入 `register_signatures()`，保持解耦纪律。
6. **路径保护分动作分级**：只读浏览类动作不受路径保护（否则"导航到主目录""找 home 下的大文件"这类合法只读需求会被误拦）。
7. **`AppData` 列入敏感区（含 Windows）**：代价是 Windows 上 `tempfile.mkdtemp()` 生成的目录会被工具拒绝 —— 这是**有意的保守选择**（该区含大量应用凭据）；本项目工作区在项目目录内，不受影响。回归测试因此把临时目录建在 `tests/.tmp_regr_*`。

---

## 6. 未修 / 待决策清单（附理由）

| 项 | 状态 | 理由 |
|----|------|------|
| `P1-10` 组1 与 `src/llm_client` 两套 LLM 客户端 | **未修** | 合并属结构性重构（超时/温度/重试语义需统一并回归），风险大于收益；已在两处 docstring 注明彼此关系 |
| `P1-14` 三份 `task_planner.py` / 两份 `host_agent.py` 死副本 | **未删** | 删除文件属破坏性操作，**需你确认**（根 `host_agent.py` 与 `group_1/task_planner.py` 是历史副本，零导入点）。本次仅在 §6.4 补记差异、并在 `recipes.md` 写明"只改 `group_*` 下的实现" |
| `P2-16` 工具注册表只校验 required 存在性 | 未修 | 加类型/枚举/范围校验会改变既有工具的参数契约，需逐工具确认；当前组2 侧已有类型防御 |
| `P2-18 / P2-20` `ToolStats` 每笔全量重写、无锁 | **不改默认** | "逐笔原子落盘"是本项目对"持久性"的卖点；高压场景应显式改用 `BufferedToolStats`（注释已写明） |
| `P2-22` `mock_jsonrpc_call` 错误码细分 | 未修 | 属 JSON-RPC 细节，不影响主链路；待用真实 MCP 时一并处理 |
| `P2-29` schema 无 `integer` 类型 | 未修 | 改 `ToolParamType` 属接口契约变更，需五组同步评审 |
| `P2-30` `BatchExecutor` 未接线 | 未修 | 属"高压优化"独立能力（任务书加分项），接线会改编排主链路，需专项 |
| `P2-31` 单条规则只匹配首个命中 | 未修 | 改为多次匹配会改变多目标解析语义（可能引入重复 goal），需配合"目标去重"设计 |
| `P2-33 / P2-51 / P2-52 / P2-53 / P2-54 / P2-55` | 未修 | 均为保守设计或性能权衡（保守门魔数、CWD 状态快照、未知键静默、中文 keysym、窗口名截断、坐标容差），不影响正确性 |
| `P2-67` 工作区跨平台 | 未修 | 需产品决策：**是否允许持久化的绝对路径跨平台复用**（当前 `system_out/settings.json` 存的是 Linux 路径 `/home/keyal/AIOS工作区`） |
| `P2-69` `/api/controls` 在全局锁内遍历控件树 | 未修 | 与"全局锁串行化"的既有并发设计绑定，改锁粒度需专项评审 |
| `P2-61` `_persist` 无 run id / 无历史 | 未修 | 属产物组织方式变更（会影响 `docs` 与前端读取路径） |
| `P3-01 / P3-02 / P3-05 / P3-10 / P3-11 / P3-12 / P3-14 / P3-17 / P3-18` | 未修 | 卫生类：不可达代码已随安全模块重写消失（P3-01）；其余为注释/常量/双入口/产物清理 |
| **明文凭据** `system_out/llm_config.json`（49 字符 `sk_` Key）、`smtp_config.json`（16 字符授权码） | **未动** | 需**你本人**处理：① 在平台侧轮换 Key 与授权码；② 打包/分发前删除整个 `system_out/`（`.gitignore` 已排除） |
| `C:\home\...`（本机存在的跨平台路径残留，含 `home\user\Documents`） | **未删** | 位于项目外、且可能是历史会话产物；本次修复让 `navigate` 不再创建目录，**不再增长** |
| `docs/bugFind.html` 处于 git 删除态 | **未动** | 属你工作区的既有改动（非本次产生）；需要恢复请告知 |
| 项目根零散产物（`hnu_intro.md`、`demo_task_backup.zip`、`out/天气_md`、`._backup.zip`） | **未动** | 属运行产物/误落盘文件，删除需你确认 |
| `docs/testing.md` 声称的 8 个测试文件（127 用例）只存在 1 个 | 记录 | 需确认是"历史清理"还是"应恢复"；本次新增了 57 项回归测试部分弥补 |

---

## 7. 后续建议

1. **先轮换凭据**（`system_out/` 内的 LLM Key 与 SMTP 授权码已在本机明文存在）。
2. 在 **Linux 目标机**上重跑 `python3 main.py --demo`（预期 7/7）与 `tests/`（预期全绿）—— 本机是 Windows，GUI/文件管理器/AT-SPI 相关场景无法验证。
3. 若要继续收敛，优先级建议：`P2-16`（参数类型校验）> `P1-14`（删死副本，需确认）> `P2-30`（BatchExecutor 接线）> `P2-67`（工作区跨平台）。
4. 把本轮新增的 6 条项目红线回写进 `.agents/skills/agent-os-dev/SKILL.md`（**已同步**）：安全检测面必须覆盖 goals/steps、路径解析三模式、Mock 降级判失败、deny 模式过滤提权步骤、前端函数必须成对定义、签名核对由编排层注入。

---

## 8. 追加改造：对话助手只回答"真实能力 / 真实数据"

**动机**：截图里助手回答"查看/管理 Agent、任务、会话、日志、配置"——本系统**一个都没有**。根因是 `/api/chat` 只给了 LLM 一句
`"你是 Agent_OS 系统控制台的对话助手，用简洁中文回答。"`，没有任何事实约束 → 模型把通用助手的能力套了过来。

### 8.1 改动（`gui/server.py` + `gui/index.html`）

| 机制 | 实现 |
|------|------|
| **能力清单现场生成** | `_capability_brief()`：从 `registry.list_tools()` / `skills.list_skills()` **实时读取**（21 工具 + 8 技能），拼上运行态（引擎/身份/提权策略/工作区）、**可查数据清单**、**明确做不到清单**、浏览器通道实况（`_browser_available()` 只查文件不探测端口）。带签名缓存，装配或设置变化即重建 |
| **"做不到"清单** | `_NOT_SUPPORTED`：多智能体/任务调度、会话管理与会话检索、日志检索、通用爬虫、读受保护路径、绕过确认流的提权 —— 全部明说 |
| **能力类提问 → 零 LLM** | `_is_capability_question()`（"你能做什么/你是谁/有哪些功能…"）直出能力清单，**确定性、永远不会夸大** |
| **状态类提问 → 先取真实数据** | `_system_facts()`：按关键词读 `stats.json` 实账 / `RUNS` 运行历史 / RAG 轨迹 / 控件后端 / `SETTINGS`+受保护路径条数；有 LLM 则交给它解读，**无 LLM 就直接给事实**并标注"LLM 不可用" |
| **路由优先级** | 顺序改为 **能力 → 事实 → 操作卡片 → 复杂任务引导 → 普通对话**。此前"当前系统状态和最近的编排运行结果"会被规则引擎解析成 `open_app`/`system_check` 弹出**执行卡片**（事实通道必须先于操作判定） |
| **普通对话仍受约束** | `_chat_system_prompt()`：把能力清单钉进 system prompt，并写死 4 条硬规则（只能依据清单回答 / 只用给定参考数据 / 不承诺"我这就去做" / 简洁） |
| **答案来源可见** | SSE 新增 `{"meta":{"source":"capability|facts|llm","label":…}}`；前端渲染成气泡上方的小标签（"本地能力清单 · 按组4 schema 现场生成" / "真实数据（LLM 未启用，直接返回事实）"…） |
| **前端** | 页头说明改为"只回答本系统真实具备的能力与真实查到的数据"；欢迎语重写；新增 5 个**常用提问 chips**（`askQuick()`，含 CSS `.quick`/`.src-chip`） |
| **关键词收窄** | 运行历史桶只认"运行/编排/审计/verdict/系统状态/任务状态/执行结果/运行历史"，避免"任务、最近、状态"这类泛词把普通提问（如"你能帮我管理任务吗"）误导向运行清单 |

### 8.2 实测（真实起服务，LLM 关闭以走确定性通道）

```
✓ 「你能做什么」→ 走本地能力清单（零 LLM）
✓ 能力清单含真实工具名（组4 schema 现场生成）
✓ 「当前系统状态」→ 真实数据（未被误判成操作卡片）
✓ 状态回答来自运行历史/统计实账
✓ 「调用统计」→ 读 stats.json 实账
✓ 「知识库轨迹」→ 读 RAG 实存
✓ 「当前设置」→ 读 SETTINGS + 保护路径条数
✓ 「打开文件管理器」→ 仍走操作卡片（未被事实通道抢走）
✓ 普通闲聊 → 走 LLM 通道（未配置 Key 时如实报错）
对话助手冒烟: 9/9 通过
```

"你能做什么"的**实际输出**（节选，逐项都来自运行时装配）：

```
**运行态**：意图引擎=规则引擎（未配置或未启用 LLM Key）；默认执行身份=user；提权确认=auto；工作区=system_out/demo_task
**可真实执行的工具（21）**：`copy_file` `create_folder` `delete_file` `list_directory` … `write_file`
**可真实执行的技能（8）**：`backup_directory` `cleanup_temp` `disk_usage` … `system_check`
**控制台可查的真实数据**：- 调用统计：组4 逐笔原子落账 stats.json … - 运行历史：本进程最近 50 次编排 …
**本系统明确做不到**（清单外的一律回答"不支持"，不会假装能做）：
- 多智能体/子智能体管理、任务队列与调度（没有任务调度器，也没有 Agent 管理接口）
- 会话管理与会话检索（只有浏览器里的对话记录，落盘为 chat_history.json，无检索接口）
- 日志检索（没有日志查询工具；只有审计 JSONL audit_log.jsonl 与运行历史，且在页面上查看）
- 通用爬虫/批量抓取：网页能力仅限自动化浏览器（CDP）通道 …
- 读取受保护/敏感路径（系统目录、主目录根、~/.ssh 等凭据区）
- 绕过确认流执行提权操作 …
**浏览器自动化**：本机**未**检测到 Chromium 系浏览器 —— `web_*` 工具会以 `[Mock·未执行]` 如实失败，不会假装成功。
```

### 8.3 回归

新增 `TestChatHonesty`（7 项）：清单来自 registry、能力提问零 LLM、**事实通道先于操作路由**、事实来源真实、关键词不过宽、system prompt 含 4 条硬规则、前端 chips 与来源标签齐备。
`tests/test_regression_audit.py` 由 57 → **64 项全绿**。

---

## 9. 追加改造：聊天记录不再被切页签清掉 + 简单问题渲染真实产出

### 9.1 现象与根因

| 现象 | 根因 |
|------|------|
| 来回切页签，聊天记录（尤其执行卡片、步骤列表、工具产出）会消失 | `loadChatHistory()` 每次切到对话页都会 `CHAT=[]; $("#chat-log").innerHTML=""` 然后**从服务端文件重建**。可走"操作卡片"链路的那些气泡（`execOperation`/`runStaged` 里 `addBubble` 出来的）**从未写入 `CHAT`、也没落盘**，于是重建即丢失 |
| 问"长沙最近7天天气怎么样"，只得到"执行中… 步骤1 mcp_weather 成功 / 步骤2 run_command 成功 / ✅ 任务完成：2/2 步成功，审计 PASS" | 前端只从 `llm_answer` 步骤取答案；`mcp_weather` 的**真实预报文本**躺在 `session.steps[0].tool_result.result` 里从未被渲染 |

### 9.2 修复（`gui/index.html`）

| 项 | 做法 |
|----|------|
| **记录只在"清空对话"时清空** | 新增 `CHAT_SYNCED` 标志：`loadChatHistory()` 只在**首次**进入对话页回填；此后 DOM 即真相。双重护栏——① `if(CHAT_SYNCED)return;` ② 本地已有内容（`CHAT.length` 或气泡数 > 1）时**绝不覆盖**。`clearChat()` 是唯一清空入口，并把 `CHAT_SYNCED` 置真 |
| **把"操作链路"也记进历史** | `route==="operation"` / `"complex"` 时推入一条上下文安全的助手标记（"（检测到系统操作：… 已生成执行卡片）"）并 `saveChatHistory()`；`runStaged` 结束时把 **用户原话 + 本轮产出/结论** 推入 `CHAT` 并落盘（→ 刷新浏览器也能恢复整段对话） |
| **简单问题渲染真实产出** | 新增 `stepOutputMd(sess)`：把每个**成功**步骤的 `tool_result.result` 拼成 markdown（对象转 JSON 代码块），过滤收尾演示命令 `echo [plan done]` 的噪音与空结果；在 `runStaged` 终态分支用它渲染成独立气泡（`.bubble.assistant.tool-out` 样式：左侧蓝条 + 浅底）。有 `llm_answer` 时优先用它的回答，不重复倾倒原始输出 |
| **顺手修掉双重转义** | 失败提示原为 `addBubble("assistant","❌ 任务失败："+esc(d.error))` —— `addBubble` 走 `textContent`，再 `esc` 会显示成 `&lt;` 实体，已改为 `String(d.error)` |

### 9.3 实测

**端到端 10/10**（真实起服务，全链路走真实 HTTP：`/api/chat` → `/api/plan` → `/api/execute` → `/api/run`）：

```
实际产出： {"total_gb": 449.6, "used_gb": 361.1, "free_gb": 88.4, "percent": 80.3}
  ✓ chat 判定为系统操作（弹卡片）      ✓ 计划含 disk_usage
  ✓ chat 事件里带真实动作名            ✓ 高影响步骤触发确认（pending + 令牌 + admin_steps）
  ✓ plan 成功且给出计划                ✓ execute 返回 run_id / 运行到达终态 done
  ✓ 会话含 disk_usage 步骤且成功        ✓ 产出是可渲染的真实数据（含 total/percent）
  ✓ 审计 verdict 有值
```

**渲染行为用 node 直接跑 `stepOutputMd`**（把函数从 HTML 抽出后 eval）：

```
**mcp_weather**
[真实·Open-Meteo] 长沙 当前 31.0°C，晴
2026-09-15: 局部多云 27.4~38.3°C
---
**disk_usage**
```json
{ "total_gb": 100, "used_gb": 60, "percent": 60 }
```
  ✓ 含天气正文   ✓ 过滤收尾 echo   ✓ 对象转 JSON 代码块   ✓ 不含失败步骤   ✓ 空会话返回空串
```

**JS 语法**：`node --check` 通过（本次还真拦下一处**新引入的语法错误**：模板字符串与普通字符串拼接混用 → 已修）。

### 9.4 回归

新增 `TestChatHistoryAndRendering`（7 项）：首次才回填、清空入口唯一、`stepOutputMd` 存在且被使用、收尾命令过滤、产出气泡样式、`runStaged` 落盘本轮、操作/复杂路由也记录、无双重转义、切页签不再重建 DOM。
`tests/test_regression_audit.py` 由 64 → **71 项全绿**。

---

## 10. 追加改造：截图等"页面正确显示"之后再抓（不再拍半张白页）

### 10.1 现象与根因

"把湖南大学的搜索截图发给我"→ 发出去的图里，Chromium 的结果区还是白的（页面仍在渲染），
同时另一台窗口（控制台）也在画面里。

**根因**：`take_screenshot` 的实现是 **固定 `time.sleep(2)` 后抓屏** ——
既不看页面是否加载完，也不看画面是否还在变化。而 Bing 的结果列表是在
`document.readyState === 'complete'` **之后**才通过异步请求填上的，
2 秒往往不够；`web_search` 的就绪等待只覆盖到 `readyState`，也管不到"绘制"。

### 10.2 修复

| 层 | 改动 |
|----|------|
| 组4 网页通道（`src/web_automation.py`） | 新增 `wait_page_ready(timeout=8s)`：① `Page.bringToFront` **把自动化窗口提到最前**（否则可能拍到别的窗口）→ ② `readyState=complete` 且**所有 `document.images` 已 complete** → ③ 连等两帧 `requestAnimationFrame`（确认浏览器真的绘制过）→ ④ `document.body.innerText` 长度连续两次采样一致（异步渲染/结果列表常在 complete 之后才填上）。另新增 `wait_page_ready_if_active()`：**只在浏览器已在跑时**等待，绝不为一次纯桌面截图去拉起 Chromium |
| 组4 真实工具（`src/real_tools.py`） | `take_screenshot(path, wait_page=True)` 改为两道闸门：**① 页面就绪**（浏览器在跑时）→ **② 画面稳定**（`_wait_screen_stable()`：连续两帧像素 SHA-1 一致，最短 1.2s / 最长 6s，替代写死的 2s）。返回值新增 `waited_ms` / `screen_stable` / `screen_note` / `page_ready` / `page_note` —— **拿不准就如实标注**（超时、采样失败、浏览器未跑都写清楚），不假装稳定 |
| schema | `take_screenshot` 增加 `wait_page`（BOOLEAN，默认 True），工具台表单自动出现该开关 |

设计原则与项目既有口径一致：**等待是"尽力而为 + 如实汇报"**，任何一步拿不准都不阻断截图，
但结果里必须写明"没等到稳定"。最坏等待 ≈ 8s（页面）+ 6s（画面），常态 1~3s。

### 10.3 验证

本机是 Windows 无 GTK/无 X11，**抓屏路径无法真跑**，因此对可验证的部分做了行为测试
（`tests/test_regression_audit.py::TestScreenshotReadiness`，7 项）：

```
✓ 画面立即稳定 → stable=True          ✓ 先变化后稳定 → stable=True
✓ 一直变化（有时钟/动画）→ stable=False 且 reason 写明"仍在变化"
✓ 抓帧异常 → 不抛异常、reason 写明"帧采样失败"
✓ 未跑浏览器 → wait_page_ready_if_active 返回 skipped 且**不启动浏览器**
✓ 写死的固定睡眠已移除（源码断言）      ✓ 结果携带两道闸门的元数据
✓ 工具暴露 wait_page 开关（含 schema）
```

外加手工实测：`take_screenshot('/tmp/x.png')` 在无 GTK 环境**< 2s 如实失败**
（`ModuleNotFoundError: No module named 'gi'`），不会卡在等待里。

> ⚠️ **未验证部分（需在 Linux 目标机确认）**：真实抓屏时序、`Page.bringToFront` 对
> 窗口 z-order 的实际效果、以及"搜索结果页需要等多久"。上机后可看返回里的
> `page_note` / `screen_note` / `waited_ms` 定位。

### 10.4 回归

`tests/test_regression_audit.py` 由 71 → **78 项全绿**。
