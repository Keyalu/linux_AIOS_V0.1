# 逻辑问题排查报告（只读审计，未实施修复）

日期：2026-09-19　方式：按 20 条开发红线不变量，对五组 + 编排 + GUI 全量静态审计（三路并行：组1/2+编排、组3/4、组5+GUI）。
标注说明：★ = 审计方已在当前副本实测复现；✓ = 本人二次核对源码确认；其余为静态分析结论。

---

## 一、严重（可造成破坏性后果或安全闸门失效）

### A. 路径语义裂缝 —— "删除/搬运错目标"（红线 10 复活新路径）

1. ★✓ **goal 合并参数泄漏给破坏型动作**。`group_2/task_planner.py:152-154`：
   `merged["params"] = {**intent.params, **goal.params}`。当 `delete_file` 这个 goal 自身无
   params 时（`_extract_params` 返回 `{}` 不写键），顶层 `path` 泄漏成删除目标；而组1 对目录级
   动作会无条件把 path 兜底成工作区（`group_1/host_agent.py:509-511`）。
   实测「整理 demo_task 然后删除里面的临时文件」→ step2 `delete_file path=<整个工作区>`，
   `_PATH_FALLBACK` 白名单形同虚设，check 也不拦（工作区非受保护）。
2. ★ **多目标按整句抽参互相污染**。`group_1/host_agent.py:931-939`：非主目标仍按整句抽参，
   `_extract_path` 取全句第一个引号/绝对路径。实测「把「报告.docx」整理一下，然后删除「临时.txt」」
   → 删的是报告.docx；「先截图保存到 /tmp/a.png 再删除 /tmp/b.png」→ 删 a.png。
3. ★ **证据防线对 `/` 失效**。`group_1/host_agent.py:536-540`：`os.path.basename("/")` 为空串
   → `"" in user_input` 恒真 → 不复落工作区，`path:"/"` 原样保留（Linux 宿主 LLM 输出
   `path:"/"` 即触发，叠加 skill 层 `_norm_path("/")→""` 前缀匹配全不中 → 根目录按类型搬运）。
4. **空目标删除检查不全**。`security.py:284-290` `_empty_delete_target` 只覆盖 `delete_file`，
   `trash_delete/cleanup_temp` 空目标不查；goal 级 target 会被顶层 `intent["target"]` 顶掉。

### B. 组5 安全扫描面缺口（红线 9 口径问题）

5. ✓ **`run_command.cmd` 内嵌路径完全绕过受保护路径检查**。`group_5/security.py:247-254` 把整条
   cmd 当一个"路径候选"交给 `_protected()`（419-438，仅 `startswith` 前缀匹配），命令串以
   `cat `/`tee ` 开头永不命中；组4 侧 `default_tools.py:431` 只跑危险命令黑名单、不查路径。
   绕过示例：`cmd="tee -a /root/.ssh/authorized_keys < /tmp/k"`、`cmd="cat /etc/shadow"`、
   `cmd="cp $HOME/.ssh/id_rsa /tmp/x"`（`$HOME` 不经 expanduser，敏感正则也匹配不上）
   → `approved=True, risk=high`，真实读写凭据区。
6. ★ **检测面动作名口径不符 + check 只在意图阶段跑一次**。`security.py:75-86` `_MUTATING_ACTIONS`
   只列组4 名（`organize_downloads/backup_directory`），但 `check()` 只在 intent 阶段调用
   （`coordinator.py:87/232`），此时动作名是组1 的 `organize/backup/find_duplicates` →
   实测 `backup src=/etc` 返回 `approved=True`（而 `_risk_of` 认识 `backup` 判 medium，证明是漏收）。
   组2 又将缺省 dest 派生为 `src+"_backup"` 无工作区锚定（`task_planner.py:241-246`）
   → Linux 下写 `/etc_backup.zip`。organize 有 skill 层兜底，backup 全程无兜底。
7. **检测面 ≠ 执行面：`{{prev_result}}` 运行时回填在闸门之后成形**。组3 执行时把上一步结果文本
   回填进任意参数（`app_agent.py:147,230-247`，组2 自动注入 `text:"{{prev_result}}"`，
   `task_planner.py:250`）。示例：goals=`[read_file(notes.txt), run_command("bash -c \"$(echo {{prev_result}})\"")]`
   → 文件内容在安全闸门之后才成形为命令行。红线 5 的"确认什么执行什么"只对 PENDING 快照成立。

### C. 失败/降级诚实性（红线 11 系统性违反映射）

8. ✓ **MCP 三工具网络失败仍谎报成功**。`src/mcp_connector.py:158-221`：`mcp_search/weather/translate`
   真实路径异常被 `except: pass` 吞掉后返回 `"[Mock·离线]…"` 字符串，经注册表包装成
   `success=True`，无 simulated/degraded 标记——与已修复的 `web_*`（`web_automation.py:600-604`）
   同病灶未同步。假天气/假"找到3条结果"会被 `llm_answer` 与前端当事实。
9. **注册表失败分支抹平降级标记**。`tool_registry.py:119-125`（同 `skill_library.py:85-90`）：
   失败走 `ToolResult.fail(raw.get("error"))`，丢弃 `result` 与全部 extra（simulated/degraded/
   before_state）；`_organize_downloads`/`_cleanup_temp` 的 partial 明细同样丢失。即使上游按
   红线 11 打了标记，到契约层也被吞。
10. **组3 Automator 所有 Mock 分支默认 success=True**。`automator.py:80,144,174,185,196,268`：
    Mock 分支不带 success 键 → `setdefault("success", True)`——无控件可点/应用未装仍算成功。
    反向：真实 AT-SPI 动作在未装 pyautogui 的机器被 `_REAL=False` 标 simulated，真假两侧都失真。
    `drag`（:187-191）实际只 click，不回"拖拽完成"，应如实降级。
11. **错误信息在契约与 GUI 双层丢失**。`interfaces.py:113-121` 成功只有 `result`、失败只有 `error`；
    `app_agent.py:155-160` GUI 分支恒含 `result` 从不拷 `error` → automator 的"未知操作/目录不存在,
    未执行导航"全丢，前端渲染空错误；`result` 类型 str（default_tools/mcp）与 dict
    （real_tools/web/skills）混用，`index.html:533` `String(r.result)` 显示 `[object Object]`。

### D. 凭据外流（红线 6/15：脱敏只挂一条路径）

12. **`mask_record` 全项目唯一调用点是审计日志**（`coordinator.py:365`）。旁路入库：
    `_persist()`（349-351，session_log.json 原样写 params_sent+工具结果，含 send_email.password、
    write_file.content）、`stats.py:105`（params 全量进 stats.json，`/api/stats` 再回前端）、
    `rag.py:60-70`（input 原文）、`server.py:1012-1021`（chat_history）、内存 RUNS bundle 经
    `/api/run` 原样下发。示例：编排读 `system_out/llm_config.json`（明文 api_key，且该路径不在
    敏感清单）→ 密钥落 session_log+stats，全程未打码。
13. **第4层隐私判据本身有洞**。`security.py:94-97` 要求英文键名+`[:=]`：中文"密钥是 xxx"、裸
    `sk-…` 不命中；`_mask_obj:498` 仅在值为 str 时按键名打码——`{"password":{"v":"x"}}` 与列表内
    元素原样落盘。

---

## 二、中等

14. **GUI 匿名端点绕过安全层**。`server.py:1033-1042` `/api/call`、`/api/skill` 完全不过
    SecuritySandbox；`server.py:1006-1011` `/api/smtp_test` 直接 `send_email(dry_run=False)`：
    不占 `_LOCK`、不落 stats、不写审计、零确认流。文件头"与生产共用同一套权限闸门"不成立。
15. **确认令牌生命周期**。`/api/execute:949-953` `in`+`pop` 非原子（并发第二发 KeyError→500 并
    回显 token；无双执行✓）；但"取消"纯前端（`index.html:453`），服务端令牌 30 分钟内可被任意
    同源 JSON POST 复用执行；execute 不复跑 check（改受保护路径设置后旧令牌按旧结论执行）。
16. **同源防线细节**。`server.py:743-745` `rsplit(":",1)` 解 Host → `Host: 127.0.0.1:8788.evil.com`
    判为回环；Origin 缺失放行；GET `/api/controls`（628-631）在全局锁内跑真桌面扫描，跨站
    `<img>` 反复触发可饿死编排。（全部 POST 过闸✓、OPTIONS 501 挡预检✓。）
17. **签名核对核错对象**。`security.py:380-391` `_verify_signatures` 比对登记时持有的 `rec["_func"]`，
    而组4 每次调用现取 `self._tools[name]`（`tool_registry.py:113`）→ 运行期替换注册表项检测不到，
    与注释语义相反。另：取不到源码静默跳过、`server.py:116-120` 异常 pass 不核对登记数；
    stat 失败使 `file=""` → 之后任何热更全量误判 tampered 阻断。
18. **stats 落盘编码+吞异常**。✓ `src/stats.py:113-120`（实际在用）：`NamedTemporaryFile("w")`
    未指定 encoding → 中文 Windows/GBK 下 `ensure_ascii=False` 抛错被 `except: pass` 吞，账本
    静默不落盘；即便落盘非 UTF-8，`server.py:348` 按 utf-8 读 → 统计页恒空。
19. **stats_buffered.py 死代码且已分叉**：无 `_load()`、flush 用内存 counters 覆盖文件（重启归零，
    违"累积账本"）、`clear()` 早退不落盘。按注释"无缝替换"即引入回归。
20. **web_automation 缺锁**。`_ensure_locked/state/find/click/web_extract` 均不持 `self._lock`
    （只有 ensure_browser 持）；ThreadingHTTPServer 下编排线程与 `/api/call_tool` 共用同一 `_ws`，
    `_msg_id+=1` 非原子、丢非本 id 应答 → 串包/15s 超时。
21. **dry_run 归一反向覆盖**。★ `host_agent.py:464-469`：无条件写 `draft_only`，把 LLM 保守的
    `True` 强制降为 `False`；`_DRAFT_RE` 缺"先不要发/暂时别发/只做预览"同义词 → 不可逆真实外发。
    `main.py`/AIShell 路径无确认闸门。
22. **params:null 崩溃点**。★ `host_agent.py:467/511/621/627` `setdefault("params",{})` 遇
    `params: null` 返回 None → TypeError，异常在 `_finalize_intent`（try 只包 `_llm_intent`）
    → 整轮编排 failed。（B-014 未修。）
23. **route_policy 决策表盲区**（红线 3）。★ `route_policy.py:26-28`：`web_extract` 未入
    `_WEB_INTERACT/_PAGE_CONSUMERS`——`[open_url(http)→web_extract]` 不改 `web_open`（浏览器未开，
    抽取必空）；`[mcp_search→web_extract]` 因无 `{{prev_result}}` 判"无消费"改回 `web_search`。
    另 `task_planner.py:264-266` `click_element→open_app` 改写不写 route_reason/不Routes 留痕；
    `routes` 字段 gui/ 全仓零消费者，确认面板看不见改写。
24. **红线 17 动作别名缺口**。★ 注册动作 `copy_file/move_file/read_file/list_directory/web_state/
    web_open/mcp_search` 无 `_ACTION_ALIASES`/中文规则覆盖；copy_file/move_file 连组1 提示词动作表
    都不在，LLM 自发产出时组2 difflib 不命中 → 步骤静默丢弃。
25. **RUNS 内存回看失真**。`app_agent.py:131-133` escalations/self_healed 是实例活列表，109-111
    每次运行 clear → RUNS 旧 bundle 被后续运行清空，`loadRun` 回看历史提权步骤恒 0 条；
    `_persist` 固定文件名逐轮覆盖，无 run_id 归档 → 无法事后比对"批准方案==执行方案"。
26. **黑名单无词界（误伤+漏拦）**。`security.py:31-36`：`"dd "` 命中 "add file"、`format` 命中
    `--format=`/platform、`halt/rmdir` 子串 → 整条 critical（user_text 在扫描面上）；
    漏拦：`rm --recursive --force ~/*`、`find -exec unlink`、`python -c "…rmtree('/etc')"`。
27. **XSS 小口**。`index.html:490 ${e.step.kind}`、`867 data-t="${p.type}"` 未 esc 的数据驱动插值
    （schema 可控即可执行脚本，当前 MCP 为硬编码 mock 故风险受限）。

---

## 三、轻微 / 待人工确认

- `host_agent.py:611-616` 裸文件名附件按进程 CWD 解析，与 530 行"相对→锚定工作区"口径矛盾。
- `task_planner.py:118-119` 空计划报"动作未映射到组4"，真实原因是缺参，unresolved 自相矛盾误导排障。
- `skill_library.py:295` 仍用裸 `os.path.isabs`（未同步 `_is_abs_any`）；`_backup_directory:383`
  相对 dest 按进程 CWD 落盘。
- `llm_client.chat_stream:51-52` 就地截断调用方 messages。
- `index.html:423` `/api/plan` 回 `{error}` 未处理 → `startPolling(undefined)`，真错被"运行不存在"掩盖。
- `_surface` 递归无深度上限（深嵌套 intent → RecursionError 整轮 failed）。
- `session_id` 实例级 → 常驻单例跨运行同 id（需人工确认是否影响对账）。
- 锁外共享态（RUNS 淘汰、PENDING 写、SETTINGS 读、events append）依赖 CPython 原子性侥幸无害。
- 根目录 `host_agent.py`(403行)/`task_planner.py` ≡ `group_1/task_planner.py` 旧副本：全仓零 import✓，
  非高危；但仍沿用"schema 第一个 string 参数填 target"的旧危险兜底，且 `gui/server.py:66-67`
  热重载清单只列 `group_*`——一旦被接线即复活 P0。建议尽快删除或显式标记。

## 四、核对通过项（无需处理）

- 组2 未 import 组4；plan_json 下游消费字段全部产出；GUI 确认执行复用 PENDING、零重规划（红线5✓）。
- 红线4 会话重置、红线12 deny 先过滤 requires_admin、红线16 全部 POST 过闸、红线20 未重启误报分支、
  红线15 `[:4/-4:]` 掩码——均已落地。
- `_surface()` 对 JSON 全容器（嵌套 list 内 dict、键名、非字符串值）覆盖完整，意图阶段无结构性绕过面。
- 前端 48 个 `$("#id")` 与函数定义一致，markdown/工具结果走 esc/textContent。

## 五、问题聚类（供修复排序参考）

1. **"闸门看的数据 ≠ 执行用的数据"是最大同构病灶**（#1,2,3,5,6,7,14,15）：路径合并泄漏、cmd 内嵌
   路径、意图/计划双阶段动作名口径、`{{prev_result}}` 回填、匿名端点、令牌复用——建议方向：
   动作名单一命名空间 + execute 前对最终 plan（回填后）复跑 check + 删除合并的泄漏语义。
2. **"失败诚实性"链条从底层到契约层逐级被吞**（#8,9,10,11）：MCP 谎报 → 注册表抹平 → Automator
   默认成功 → error 字段丢失。改一处无效，需沿链一次性打通。
3. **脱敏是"逐点补"而非"入库前统一"**（#12,13）：mask 应挂在 `_persist`/stats/rag/chat_save/
   `/api/run` 所有出口。
4. 决策表/别名盲区类（#23,24）属红线 3/17 的例行同步债，按既有范式补表即可。
