# AIOS_V0.1 代码审计报告 — 逻辑漏洞与缺陷清单

> 审计对象：`C:\Users\keyal\Desktop\AIOS\AIOS_V0.1` 全量工程文件
> 审计方式：静态精读全部源码 + 语法编译校验 + 导入/运行实测验证
> 审计日期：2026-09-14
> **本报告只做记录，未修改、未修复、未删除任何已有文件。** 所有条目均保留原样，供后续决策。

---

## 0. 审计范围与结论速览

### 0.1 覆盖范围

| 类别 | 文件数 | 说明 |
|---|---|---|
| 核心库 `src/` | 14 | 工具注册、技能库、MCP、统计、批处理、网页自动化 |
| 分组模块 `group_1`~`group_5` | 18 | 意图/规划/执行/协调/安全/RAG |
| Web 控制台 `gui/` | 2 | `server.py`（719 行）+ `index.html`（966 行） |
| 历史副本 `代码/` | 3 | 项目根文件被 organize 事故搬移后的残留 |
| 测试 `tests/` | 1 | 722 行集成测试 |
| Skill 脚本 `.agents/` | 2 | `health_check.py` / `pytest_shim.py` |
| 文档 `docs/` `文档/` | 3 | 报告与 README |
| 运行产物 `system_out/` `out/` `压缩包/` | 若干 | 含明文凭据，见 B-075 |

已执行验证：
- `python -m compileall` 全量编译 → **0 语法错误**（说明下述"重复定义/不可达代码"均为合法 Python，不会在导入期暴露）。
- `python -c "import main"` → **ModuleNotFoundError**（见 B-001）。
- `python 代码/main.py --text "查询长沙天气"` → **ModuleNotFoundError: No module named 'group_1'**（见 B-002）。

### 0.2 缺陷统计

| 等级 | 数量 | 编号区间 | 含义 |
|---|---|---|---|
| **P0 严重** | 10 | B-001 ~ B-010 | 阻断运行 / 数据不可逆丢失 / 安全防线失效 / 审计失真 |
| **P1 高** | 33 | B-011 ~ B-043 | 明确功能错误、崩溃路径、跨平台失效、契约不一致 |
| **P2 中** | 74 | B-044 ~ B-117 | 边界条件、资源泄漏、健壮性、设计缺陷 |
| **P3 低** | 19 | B-118 ~ B-136 | 死代码、误导性注释、命名/文档不一致 |
| **合计** | **136** | B-001 ~ B-136（连续无缺号） | — |

> 计数口径：第 1、2 章（P0/P1）为独立小节卡片，第 3、4 章（P2/P3）为表格行；两者编号连续，共 136 条，覆盖 38 个文件。

### 0.3 按文件分布（Top 18，合计覆盖 38 个文件）

| 文件 | 缺陷数 | 等级分布 |
|---|---|---|
| `gui/server.py` | 13 | P0×1 P1×2 P2×9 P3×1 |
| `gui/index.html` | 13 | P0×2 P1×4 P2×5 P3×2 |
| `src/web_automation.py` | 9 | P0×1 P1×5 P3×3 |
| `src/default_tools.py` | 8 | P0×1 P1×4 P2×3 |
| `group_1/host_agent.py` | 8 | P1×1 P2×6 P3×1 |
| `src/skill_library.py` | 7 | P0×1 P2×5 P3×1 |
| `src/real_tools.py` | 6 | P2×6 |
| `src/batch_executor.py` | 6 | P2×6 |
| `group_2/task_planner.py` | 5 | P1×2 P2×3 |
| `group_3/automator.py` | 5 | P1×3 P2×2 |
| `src/mcp_connector.py` | 5 | P2×5 |
| `group_3/atspi_io.py` | 5 | P2×5 |
| `group_5/coordinator.py` | 4 | P2×4 |
| `group_5/security.py` | 3 | P0×2 P3×1 |
| `group_3/app_agent.py` | 3 | P1×3 |
| `src/stats.py` | 3 | P2×3 |
| `group_2/route_policy.py` | 3 | P1×1 P2×2 |
| `tests/test_integration_v10.py` | 3 | P0×1 P3×2 |

其余 20 个文件各 1~2 条：`代码/main.py`(2)、`src/tool_registry.py`(2)、`src/stats_buffered.py`(2)、`group_2/control_detector.py`(2)、`src/interfaces.py`(2)、`src/mock_registry.py`(2)、`group_3/coord_check.py`(2)、`代码/`(1)、`src/automator.py`(1)、`group_5/rag.py`(1)、`system_out/llm_config.json`(1)、`system_out/smtp_config.json`(1)、`tests/`(1)、`group_1/ai_shell.py`(1)、`group_3/__init__.py`(1)、`pytest_shim.py`(1)、`health_check.py`(1)、`docs/开发与最终报告.md`(1)、`docs/验证报告.md`(1)、`hnu_intro.md`(1)。

---

## 1. P0 — 严重缺陷

### B-001｜P0｜`tests/test_integration_v10.py:15`｜项目根 `main.py` 缺失，整套测试无法运行

- **现象**：`tests/test_integration_v10.py` 第 12-15 行
  ```python
  ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
  sys.path.insert(0, ROOT)
  import main as system_main
  ```
  实测 `import main` → `ModuleNotFoundError: No module named 'main'`。项目根目录**不存在** `main.py`。
- **根因**：`git status` 显示 `D main.py`、`D host_agent.py`、`D README.md` —— 这三个根目录文件已被删除/搬走。结合 `.agents/skills/agent-os-dev/references/pitfalls.md:45` 的自述「organize 把项目根/进程 CWD 当工作区整理了（源码被移进 代码//文档/）」，可确认是**organize 技能的作用域缺陷真实损坏了项目根**，其产物残留在 `代码/`、`文档/`、`压缩包/`、`out/图片/`、`out/文档/`。
- **影响**：全部 40+ 个集成测试在导入阶段即失败，**无法获得任何测试信号**；`docs/验证报告.md` 与 `文档/README.md` 中所有 `python3 main.py ...` 命令全部不可执行。
- **说明**：缺陷 B-020/B-021 只修了"路径归一"这一层，**没有回滚已造成的目录破坏**。

### B-002｜P0｜`代码/main.py:27-31`｜副本入口自身不可运行

- **现象**：实测 `python 代码/main.py --text "查询长沙天气"` → `ModuleNotFoundError: No module named 'group_1'`。
- **根因**：第 27 行 `ROOT = os.path.dirname(os.path.abspath(__file__))` 得到的是 `代码/` 目录，第 28 行 `sys.path.insert(0, ROOT)` 把 `代码/` 塞进路径；但 `group_1`~`group_5` 位于上一级，Python 3.11+ 不再把 cwd 加入 `sys.path`，故导入必然失败。
- **影响**：该副本文件既无法作为入口运行，也无法作为"备份参考"被直接验证。

### B-003｜P0｜`src/skill_library.py:59-82`｜`call_skill` 被定义两次，首个定义体是死代码

- **现象**：
  ```python
  def call_skill(self, name, params=None) -> ToolResult:      # 59
      """调用一个 Skill：执行并按统一契约规整返回值…"""        # 60
      params = params or {}                                    # 61
  def call_skill(self, name, params=None) -> ToolResult:      # 62 ← 覆盖上面
      params = params or {}
      if name not in self._skills: ...
  ```
- **根因**：同名方法在类体内连续定义两次，后者静默覆盖前者。
- **影响**：功能上"侥幸可用"（第二个是完整实现），但第一段是死代码 + 丢失了原始 docstring；任何后续编辑若只改第一处将完全无效。这是典型的"改动落空"隐患。

### B-004｜P0｜`group_5/security.py:102-113`｜第 3 层"签名验证"实际永不生效

- **现象**：
  ```python
  def _verify_signatures(self) -> list[str]:
      for name, digest in self.signatures.items():
          mod = __import__(name, fromlist=[""]) if "." in name else None
          func = getattr(mod, name, None) if mod else None
          if func is None:
              continue
  ```
- **根因**：两条分支都必然 `continue`：
  1. `name` 不含 `.`（如 `"copy_file"`）→ `mod = None` → `func = None` → 跳过；
  2. `name` 含 `.`（如 `"src.default_tools.copy_file"`）→ `getattr(mod, "src.default_tools.copy_file")` 取不到属性 → `None` → 跳过。
  此外全项目**没有任何地方调用 `register_signature`**（已全局检索确认），`self.signatures` 恒为空字典。
- **影响**："权限检查 → 沙箱执行 → **签名验证** → 隐私保护"四层安全架构中的第 3 层完全未落地，工具被篡改无从检出；而 `check()` 的返回文案会声称"签名核对 N 项"。

### B-005｜P0｜`group_5/security.py:147-156`｜第 4 层"隐私保护"`redact()` 从未被调用

- **现象**：`SecuritySandbox.redact()` 定义了邮箱/密钥打码逻辑，但全局检索显示**除定义处外无任何调用点**。`coordinator.audit()` 直接 `json.dumps(rec)` 落盘。
- **影响**：`system_out/audit_log.jsonl` 与 `rag_traces.jsonl` 中的收件人邮箱、API Key、SMTP 授权码等敏感串**明文持久化**。四层安全架构的第 4 层同样未落地。`check()` 里虽然计算了 `leaked` 字段，但也只是写进 reason 字符串，不阻断、不打码。

### B-006｜P0｜`gui/server.py:291-298, 397-405, 611-620`｜无 CSRF 防护 + 不校验 Content-Type，本地任意网页可执行 ADMIN 工具

- **现象**：
  ```python
  def _body(self) -> dict:
      length = int(self.headers.get("Content-Length") or 0)
      ...
      return json.loads(self.rfile.read(length).decode("utf-8"))
  ```
  `_body()` 不检查 `Content-Type`；`/api/call` 直接信任请求体里的 `level` 字段（`LEVELS.get(...)`，含 `admin`）。
- **根因**：跨站 `fetch("http://127.0.0.1:8788/api/call", {method:"POST", body: JSON.stringify({name:"run_command", params:{cmd:"..."}, level:"admin"}), headers:{"Content-Type":"text/plain"}})` 属于 CORS **简单请求**，不触发预检；服务端既不校验 Origin/Referer，也不校验 Content-Type，会正常执行。响应虽被浏览器 CORS 拦截，但**副作用已经发生**。
- **影响**：用户只要在浏览器中访问任意恶意页面，该页面即可在本机执行 `run_command`（ADMIN）、`delete_file`、真实 `send_email` 等操作。绑定 127.0.0.1 **不能**防御此攻击（浏览器即在本机）。
- **同类**：`/api/skill`、`/api/settings`、`/api/llm_config`、`/api/smtp_save`、`/api/reset_stats`、`/api/rag_clear` 全部同样暴露。

### B-007｜P0｜`gui/index.html:348, 955, 527, 607, 689`｜引用未定义函数，页面初始化被中断

- **现象**：全局检索确认 `loadChatHistory` 与 `saveChatHistory` **只有调用点、没有定义点**：
  - 调用：`348`（切页签）、`955`（页面加载末尾）、`527` / `607` / `689`（`saveChatHistory`）
- **根因**：第 955 行 `loadChatHistory();` 在顶层脚本执行时抛 `ReferenceError: loadChatHistory is not defined`，**中断其后所有顶层语句**：
  ```js
  loadRuns();            // 954 已执行
  loadChatHistory();     // 955 ← 抛错，以下全部不执行
  loadLLM();             // 956
  loadSMTP();            // 957
  loadSettings();        // 958
  { const h=location.hash.slice(1); ... }  // 960-963
  ```
- **影响**：页面首屏 LLM 状态、SMTP 状态、系统设置表单**全部空白**；`#tools`/`#stats` 等 hash 直达失效。任何一次对话发送（`sendChat` 成功路径）或清空对话都会再抛一次 `ReferenceError`。
- **注**：`/api/chat_history` 接口与 `CHAT_HISTORY_PATH` 后端实现完整存在，属于**前端实现遗漏**。

### B-008｜P0｜`gui/index.html:469`｜点击"被拦截的运行"必然抛异常

- **现象**：`loadRun()` 只判 `if(!d.bundle)return;`，随后调用 `renderBundle()`；而 `renderBundle()` 第 469 行无条件执行
  ```js
  $("#orch-steps").innerHTML = BUNDLE.session.steps.map(...)
  ```
- **根因**：被拦截的运行其 `bundle` 来自 `plan_only()` 的提前返回体（含 `user_input/intent/check/rejected/results`，**不含 `session`**），`BUNDLE.session` 为 `undefined` → `TypeError: Cannot read properties of undefined (reading 'steps')`。
- **影响**：「运行历史」列表中所有"已拦截"记录点击后无任何反应（控制台报错）。注意 `pollRun()` 第 447 行有 `if(BUNDLE&&BUNDLE.session)` 守卫，`loadRun()` 漏了同样的守卫。

### B-009｜P0｜`src/default_tools.py:32-38, 127-140, 289-325`｜`delete_file` / `cleanup_temp` 可递归删除用户主目录

- **现象**：`PROTECTED_PATHS` 只包含 Linux 系统目录（`/etc /usr /root /boot /bin /sbin /lib /proc /sys /dev`）与 Windows 系统目录，**不包含 `/home`、`/Users`、`~/`**。
  ```python
  def delete_file(path: str) -> dict:
      target = Path(path).expanduser().resolve()
      if _is_protected_path(str(target)): ...
      if target.is_dir():
          shutil.rmtree(target)          # ← 递归、不可逆、无回收站、无备份
  ```
  `SkillLibrary._cleanup_temp(path)` 同理，只是按 `st_mtime` 批量 `unlink()`。
- **影响**：`delete_file(path="~")` / `delete_file(path="/home/keyal")` 会**静默递归删除用户全部个人文件**，无二次确认、无回收站、无备份、无预演列表。AIOS 自身的整理类技能默认作用域恰是 `~/Downloads`、`~`（`skill_library.py:95/100/112/117`），与 `_DIR_SCOPE_ACTIONS` 归一后极易落到用户目录。
- **加重因素**：`cleanup_temp` 默认 `max_age_days=7` 会按时间批量删文件；`security._risk_of()` 对 `delete_file` 仅判 `"medium"`，不阻断。

### B-010｜P0｜`src/web_automation.py:492-564`｜浏览器不可用时把失败伪装为成功，导致审计失真

- **现象**：所有网页工具在 CDP 不可用时统一返回
  ```python
  except CDPError as e:
      return {"success": True, "result": f"[Mock] web_open({url}) — {e}", "simulated": True}
  ```
  涉及 `web_open`(503-505)、`web_click`(532-534)、`web_state`(546-548)、`web_extract`(559-561)、`web_search`(611-614)。
- **影响**：`success=True` → `ToolRegistry.call` 记为成功 → `AppAgent` 计入成功 → `coordinator._consistent()` 对账通过 → **审计 verdict = PASS**。调用方若只判 `success`（如 `tests` 与前端 `tr.success`）会认为"网页已打开/已点击"。这与项目自述的工程原则"**失败必须可见**"（`docs/开发与最终报告.md:163`）直接冲突：`_NotFoundError` 分支诚实失败，而"浏览器根本起不来"分支却伪成功。

---

## 2. P1 — 高优先级缺陷

### B-011｜P1｜`src/tool_registry.py:48-49` vs `src/web_automation.py:688-694` / `src/real_tools.py:254-301`｜"幂等注册"三种口径

- `ToolRegistry.register` 对重名**直接抛 `ValueError`**；
- `MCPConnector._setup_mock_search/_weather/_translate` 有 `if not registry.has(...)` 前置检查（真幂等）；
- `register_web_tools()` / `register_real_tools()` **没有**任何检查，docstring 却写"幂等"。

**影响**：`register_web_tools(reg)` 被调用两次即崩；`make_stack(include_web=True)` 若在已注册的 registry 上重入会失败。

### B-012｜P1｜`src/tool_registry.py:116-127`｜失败分支丢失 `extra` 附加键

```python
result = ToolResult(..., extra={...}) if raw["success"] else ToolResult.fail(raw.get("error","未知错误"))
```
成功时把 `simulated` 等键收进 `extra`，失败时走 `ToolResult.fail` → `extra={}`。`src/skill_library.py:73-79` 是同一写法。**影响**：工具失败时丢失 `simulated`/`dry_run` 等诊断信息，上层无法区分"真失败"与"降级 Mock 失败"。

### B-013｜P1｜`src/stats_buffered.py:76-82`｜`log_path=None` 时 `_unsaved` 无界增长

`record()` 无条件 `self._unsaved.append(entry)`；`should_flush and self._log_path` 中 `_log_path` 为 None 时永不为真 → 缓冲永不落盘、永不清空 → **内存持续增长**。`flush()` 同样因 `if self._log_path` 而跳过清理。

### B-014｜P1｜`group_1/host_agent.py:300, 340-345`｜`"params": null` 触发 TypeError

```python
g.setdefault("params", {})["dry_run"] = draft_only   # 300：键存在值为 None 时 setdefault 不生效
...
g["params"][k] = default_target                       # 344
```
若 LLM 输出 `{"goals":[{"action":"organize","params":null}]}`，`g["params"]` 为 `None` → `TypeError: 'NoneType' object does not support item assignment`。`_llm_intent` 对 `params` 不做 None 归一，`_finalize_intent` 也没有。

### B-015｜P1｜`group_2/route_policy.py:103`｜`"params": null` 触发 AttributeError

```python
params = s.setdefault("params", {})   # 键存在且为 None → 返回 None
...
params.pop("engine", None)            # AttributeError
```

### B-016｜P1｜`group_2/task_planner.py:124-129`｜GUI 动作丢失 goal 级参数

```python
if action == "open_app":
    return self._gui_open_app_steps((intent.get("params") or {}).get("app") or target, unresolved)
if action in _GUI_ACTIONS and action != "open_app":
    return self._gui_generic_steps(action, intent.get("params") or {}, target, unresolved)
```
只读**顶层** `intent.params`，完全忽略已合并好的 `merged["params"]`（含 `goal.params`）。多 goal 场景下，非主 goal 的 `{"name": "文件"}`、`{"path": "/x"}` 全部丢失 → 退化为 `target`。
**同源副本**：`group_1/task_planner.py:108-113`、`代码/task_planner.py:108-113` 完全相同。

### B-017｜P1｜`group_2/task_planner.py:186, 207-209, 213-215`｜路径判定硬编码 POSIX，Windows 必然失配

```python
src_fb = params.get("src") or (target if str(target).startswith(("/", "~")) else "") or last_file_target or ""
...
if pname in ("path","src","directory","dest") and not str(filler).startswith(("/", "~", ".")):
    continue
```
Windows 绝对路径形如 `C:\Users\...`，既不以 `/` 也不以 `~`/`.` 开头 → 被判"不像路径"→ 拒填 → 必需参数缺失 → `unresolved`。而项目同时提供 Windows 版 GUI/工具链（`default_tools` 含 `c:\windows` 保护项），属跨平台失效。

### B-018｜P1｜`group_3/app_agent.py:168-175`｜自愈重试失败后丢弃真实错误

```python
if not result.success and str(result.error).startswith("工具未注册"):
    fixed = self._selfheal_name(name)
    if fixed and fixed != name:
        retry = self._call_contract(fixed, params, level, idx)
        if retry.success:
            self.self_healed.append(...)
            return retry
return result        # ← 自愈失败时返回原始"工具未注册"，掩盖重试的真实错误
```

### B-019｜P1｜`group_3/app_agent.py:194`｜`{{prev_result}}` 语义错误

```python
if token == "prev_result":
    return ctx[max(ctx)] if ctx else "(无上一步结果)"
```
`max(ctx)` 取的是**已成功步骤中编号最大者**；`_run_step` 只在成功时写 `ref_ctx`（第 138-139 行），因此当紧邻上一步失败时，`{{prev_result}}` 会回填到更早的成功步骤，而非"上一步"。

### B-020｜P1｜`group_3/automator.py:212-221`｜`_resolve_desktop` 缺长度护栏，与组2 口径不一致

```python
for kw in sorted(cls._APP_DESKTOP.keys(), key=len, reverse=True):
    if kw in a:
        return cls._APP_DESKTOP[kw]
```
`open_app("我的文件报告")` → 命中子串 `"文件"` → 返回 `"nautilus"` → **误启动文件管理器**。组2 的 `_known_app_name()` 已加 `len(a) <= max(len(kw),4)+1` 护栏并配有专门测试（`test_long_description_not_routed`），**组3 未同步该修复**。

### B-021｜P1｜`group_3/automator.py:243`｜`os.system` 字符串拼接执行

```python
os.system(f"nohup {desktop} >/dev/null 2>&1 &")
```
`desktop` 来源为 `_resolve_desktop(app)` 的"查不到原样返回"分支，即用户/LLM 可控。虽然前置 `shutil.which(desktop)` 大幅限制了可利用性，但仍是命令注入面，且无返回码校验（`os.system` 结果被丢弃）。

### B-022｜P1｜`group_3/automator.py:248-312`｜`navigate` 的成功判据不可靠

```python
verified = self._nautilus_showing(base)   # 判据：任一 nautilus 窗口标题包含目录名
...
return {"success": verified, ...}
```
- 目录名短或与其他窗口同名时误判成功；
- 标题被截断 / 目录名含特殊字符时误判失败，于是走 `nautilus path` 兜底又开一个窗口；
- CLI 兜底用 `subprocess.Popen` 不校验返回码，`fallback=True` 但 `verified=False` 时 `success=False`，**实际已跳转却报失败**。

### B-023｜P1｜`group_3/app_agent.py:29-33`｜`FileManagerAgent.navigate_to()` 完全忽略 `path` 参数

```python
def navigate_to(self, path: str) -> dict:
    return self.automator.execute_step(
        {"action": "hotkey", "params": {"keys": ["ctrl", "l"]}})
```
只按 Ctrl+L，**从不键入 `path`**。注释称"Mock 路径由 navigate 动作记录等效信息"，实际未记录任何信息。该方法是任务书要求的"文件管理器特化实现"。

### B-024｜P1｜`group_2/control_detector.py:167-175`｜Mock 元素缺契约字段

契约声明为 `{"app","role","name","bbox","depth"}`，但 `_simulated_elements()` 返回的 4 个元素**只有 `role/name/bbox`**，缺 `app` 与 `depth`。前端 `loadControls()` 用 `e.depth||0` / `e.app||""` 侥幸不崩，但任何按契约取键的调用方（如 `elements[el["name"]]` 之外的新代码）会 `KeyError`。

### B-025｜P1｜`group_5/rag.py:64-73`｜畸形轨迹导致编排整体崩溃

```python
scored = sorted(((_cosine(q, _tokenize(d["input"] + " " + " ".join(
    d["actions"] if isinstance(d["actions"], list) else [str(d["actions"])]))), d)
    for d in self.documents), ...)
```
`_load()`（79-89 行）直接 `json.loads(line)` 后 `append`，**不校验键**。任何缺 `input`/`actions` 的行都会在此处 `KeyError`。而 `coordinator.execute_approved` 第 161 行 `self.rag.query(...)` **没有 try 包裹**（`_recall_memory` 有）→ 编排在审计之后、返回之前整体抛错。

### B-026｜P1｜`gui/server.py:544-560`｜`/api/run` 分支在 `_route_post` 中重复两遍，第二份永不可达

第 527-543 行与 544-560 行代码逐字相同。第二份为死代码（`elif` 链前一分支已命中）。

### B-027｜P1｜`gui/server.py:197-200`｜运行淘汰按 `run_id` 字典序而非时间

```python
if len(RUNS) > 50:
    for old in sorted(RUNS)[:len(RUNS) - 50]:
        if RUNS[old]["status"] != "running":
            RUNS.pop(old, None)
```
`run_id` 是 `uuid4().hex[:8]`（随机），字典序与创建时间无关 → **淘汰的是随机运行**，可能把最近一次运行删掉。

### B-028｜P1｜`gui/index.html:419-424, 444-449`｜`pollRun` 收到 404 时连锁崩溃

`/api/run` 对不存在的 id 返回 `404 {"error":..., "existing":[...]}`（无 `cursor`/`events`）。`pollRun` 里
```js
CURSOR=d.cursor;              // undefined
d.events.forEach(...)         // TypeError: Cannot read properties of undefined
```
下一轮 `fetch(...cursor=undefined)` → 服务端 `int("undefined")` → `ValueError` → 500。**触发条件**：运行被 B-027 淘汰，或服务重启（`RUNS` 是内存字典）。

### B-029｜P1｜`gui/index.html:484-497`｜`showPipeJson` 重复定义两遍

第 484-490 行与 491-497 行完全相同，后者覆盖前者。同文件 `function _flowTo(n){}`（第 361 行）是**空函数死代码**；`$("#page-settings")&&0;`（第 938 行）是无意义表达式。

### B-030｜P1｜`gui/index.html`｜多个 CSS 类被引用但从未定义

在 `<style>` 块中零定义（已逐项检索确认）：

| 类名 | 使用位置 | 后果 |
|---|---|---|
| `.bar-row` `.bar-track` `.bar-fill` | 803-805 行（按接口聚合条形图） | **条形图完全不可见**（无宽度/高度/背景） |
| `.field` | 239-319、753-763 行 | LLM 设置/系统设置/工具表单**无排版**（无下边距、label 未块级化） |
| `.tip` | 246-310、755 行 | 参数说明文字无小字样式 |
| `.req` | 755 行（必填星号） | 必填标记无颜色 |
| `.hl` | 471 行（提权行高亮） | 提权步骤行无高亮 |
| `.item.on` | 736 行（选中工具） | 工具台选中项无选中态 |

### B-031｜P1｜`gui/index.html:627-633`｜`mdRender` 属性上下文转义不完整，LLM 输出可注入 HTML

```js
const inline=t=>esc(t)
  ...
  .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>')
  .replace(/(^|[\s（(])((?:https?:\/\/)[^\s<）)]+)/g,'$1<a href="$2" ...>$2</a>');
```
`esc()` 只处理 `& < >`，**不转义 `"`**；而 `$2`（URL）被直接放进 `href="..."` 属性内。形如 `https://x.com/" onmouseover="alert(1)` 的文本可逃逸属性上下文。函数注释自称"LLM 输出不可信，防注入"，但未覆盖属性上下文。

### B-032｜P1｜`代码/` 与 `group_1/`、`group_2/` 三份语义分叉的副本

| 文件 | 差异（相对 `group_*` 现行版本） |
|---|---|
| `代码/host_agent.py` | 无 `memory` 形参、无 LLM 失败重试、**无 `_finalize_intent`**（缺 dry_run/附件/路径归一/多目标合并/证据防线） |
| `代码/task_planner.py` | 无 `last_file_target` 透传、无 `apply_route_policy` 通道改写、无路径型参数过滤 |
| `代码/main.py` | `build()` 每次调用 `shutil.rmtree(sandbox)` |

**影响**：任何针对 `group_*` 的修复都不会作用于 `代码/`，反之亦然；三份 `_GUI_ACTIONS`/`_TOOL_ACTIONS` 已产生实际行为差异（组1 版缺 `click_element`，组2 版有）。

### B-033｜P1｜`代码/main.py:54-57, 70-89`｜`build()` 每次都清空工作区

```python
def prepare_sandbox(out=OUT) -> str:
    sandbox = os.path.join(out, "demo_task")
    shutil.rmtree(sandbox, ignore_errors=True)     # ← 无确认、无备份
```
`build()` 无条件调用它，因此 `python main.py --text "..."` 单指令模式也会**静默删除 `system_out/demo_task`** 的全部内容。

### B-034｜P1｜`src/web_automation.py:305-317`｜导航期间把瞬态 CDP 错误当致命错误

```python
while time.monotonic() < deadline:
    try:
        ready = self._cmd("Runtime.evaluate", {...})["result"]["value"]
        if ready == "complete": break
    except CDPError:
        raise                     # ← _CmdError 是 CDPError 子类，一并被抛出
    except Exception:
        pass
```
页面导航过程中 `Runtime.evaluate` 常返回 `Cannot find context with specified id`（`_CmdError`）→ 被 `raise` 直接抛出 → `open_url` 失败。另外若 20s 内 `readyState` 始终不为 `complete`，循环**静默退出**，不报超时。

### B-035｜P1｜`src/web_automation.py:358-380`｜`_nth_result` 的过滤条件在 Bing 结果页失效

```js
const h=a.href||''; return h.startsWith('http') && !h.includes(location.hostname) && ...
```
Bing 的自然结果链接形如 `https://www.bing.com/ck/a?...`，**包含** `location.hostname`（`www.bing.com`）→ 被 `!h.includes(...)` 过滤掉 → `links` 为空 → `web_click(nth=N)` 恒抛 `_NotFoundError`。（百度/部分引擎可能返回直链，故标记为"疑似，需实测确认"。）

### B-036｜P1｜`src/web_automation.py:275-276`｜`_cmd` 使用跨实例共享的可变默认参数且未加锁

```python
def _cmd(self, method, params=None, _id: list[int] = [0]) -> dict:
    _id[0] += 1
```
- `_id` 是**所有 `WebAutomation` 实例共享**的同一个 list（可变默认参数）；
- `_cmd` 内部无锁，而 `_ws` 是单连接共享。`gui/server.py` 用 `ThreadingHTTPServer`，多个请求线程并发 `_cmd` 时，`recv_text()` 会互相消费对方的应答，`msg.get("id") != _id[0]` 导致应答被丢弃 → 双方各等 15s 超时。

### B-037｜P1｜`src/interfaces.py:74-75`｜`default=None` 的参数不写入 schema

```python
if p.default is not None:
    props[p.name]["default"] = p.default
```
当默认值**本身就是 None**（合法默认）时被静默省略。受影响：`backup_directory.dest`（`skill_library.py:109`）、`send_email.attachment`（`real_tools.py:295-297`）、`web_click.nth`（`web_automation.py:674-675`）、`take_screenshot.path`（`real_tools.py:277-278`）。**影响**：GUI 表单不预填、LLM/规划器看不到"可省略"的提示。

### B-038｜P1｜`src/web_automation.py:168, 172-173`｜`_binary()` 分支存在不可达/重复求值

```python
for cand in _BROWSER_CANDIDATES:
    if "/" in cand and os.path.exists(cand):
        return cand
    if shutil.which(cand):
        return shutil.which(cand)      # 重复调用 which()
```
`shutil.which` 被调用两次；且 `/snap/bin/chromium` 这类绝对路径若 `os.path.exists` 为假，会再走一次 `which("/snap/bin/chromium")`（对含 `/` 的输入，`which` 语义为"可执行文件校验"，行为依赖实现）。

### B-039｜P1｜`src/web_automation.py:189-200`｜自愈重连会持续泄漏标签页

`_ensure_locked()` 在探活失败后 `self.close()` 再 `self._connect()`，而 `_connect()` 每次都 `PUT /json/new?about:blank` 新建标签页。浏览器崩溃/长时间运行后反复自愈 → 标签页只增不减。

### B-040｜P1｜`src/default_tools.py:23-38` 与 `group_5/security.py:21-29`｜两份黑名单口径不一致

| 项 | `default_tools` | `security` |
|---|---|---|
| `chmod -r 777` | ✅ 有 | ❌ 无 |
| Windows 系统目录保护 | ✅ 有 | ❌ 无 |
| `> /dev/` | ✅ | ✅ |
| `dd ` | ✅ | ✅ |

**影响**：同一个操作在"工具层闸门"被拦、在"安全层闸门"放行（或反之），两层防御强度不一致，且 `security` 是任务书声明的第一道闸门。

### B-041｜P1｜`src/default_tools.py:54-60`｜危险命令黑名单易绕过

黑名单为**子串匹配**，`run_command` 使用 `shell=True`。未覆盖：`rm --recursive --force`、`find / -exec rm {} +`、`$(...)` / 反引号命令替换、`mv ~ /tmp`、`truncate -s 0`、`> ~/.bashrc`、`python -c "import shutil;shutil.rmtree(...)"` 等。

### B-042｜P1｜`src/default_tools.py:63-75`｜`_is_protected_path` 在 Windows 上保护 Linux 路径失效

`Path("/etc").resolve()` 在 Windows 上得到 `C:\etc`，与 `pp_norm="/etc"` 比对失败 → 保护失效。反之 `c:\windows` 在 Linux 语义下同样不匹配。

### B-043｜P1｜`src/default_tools.py:150-198`｜`list_directory` 的"高压优化"注释与实现不符

```python
with os.scandir(base) as it:
    for e in sorted(it, key=lambda x: x.name):     # ← 必须先耗尽整个迭代器
```
`sorted()` 会遍历**全部**目录项并全部载入内存排序，仍是 O(全目录) + O(n) 内存；docstring 声称"扫描成本从 O(全目录) 降为 O(页大小)"不成立（页外条目虽不 stat，但排序开销与内存占用未变）。

---

## 3. P2 — 中等缺陷

### 3.1 工具与技能层

| 编号 | 位置 | 缺陷 |
|---|---|---|
| B-044 | `src/default_tools.py:201-223` | `read_file` TOCTOU：先 `stat().st_size` 判大小再 `open()`，两步之间文件可被替换/增长 |
| B-045 | `src/default_tools.py:226-237` | `write_file` 用未 resolve 的 `Path(path).expanduser()` 校验后写盘，保护判定依赖 `_is_protected_path` 内部 resolve（隐式耦合） |
| B-046 | `src/default_tools.py:244-283` | `run_command` 的 `timeout` 来自 schema 的 NUMBER，GUI 传入字符串时 `subprocess.run(timeout="30")` 抛 TypeError（被兜底吞掉，报"执行异常"而非"参数错误"） |
| B-047 | `src/skill_library.py:257-287` | `_find_files` 循环内 `p.stat()` 未捕获 `FileNotFoundError`（悬空软链）→ 单个坏链接中断整个搜索；且 `count=len(matches)` 被 100 上限截断，与 `files=matches[:50]` 口径不一致（报"找到 100 个"实际返回 50） |
| B-048 | `src/skill_library.py:342-375` | `_find_large_files` 的 `min_size_mb * 1024 * 1024` 在 GUI 传入字符串时 TypeError |
| B-049 | `src/skill_library.py:327-340` | `_backup_directory` 未校验 `dest` 是否落在 `src` 内部 → 用户传 `dest` 在源目录内时归档自身（递归膨胀）；也未校验受保护路径 |
| B-050 | `src/skill_library.py:132-212` | `_organize_downloads` 的 `dest.parent.mkdir(exist_ok=True)` 未 `parents=True`（当前目录结构下恰好可用）；`workers` 来自 GUI 字符串时 `workers > 1` 比较会 TypeError |
| B-051 | `src/skill_library.py:59-82` | `call_skill` 无权限校验、不接入 stats（与 `ToolRegistry.call` 行为不对等；`pitfalls.md:52` 承认这是"设计"，但会造成审计口径差异） |
| B-052 | `src/mcp_connector.py:70-83` | `_real_search` 的去重逻辑失效：`title in [l.split(" ", 1)[-1] for l in lines]` 比较的是"标题\n  URL"整串，永不相等 → 去重形同虚设 |
| B-053 | `src/mcp_connector.py:223-250` | `mock_jsonrpc_call` 同时返回 `result` 与 `error` 两个键（JSON-RPC 2.0 要求二选一）；`req_id = abs(hash(...))` 跨进程不稳定（PYTHONHASHSEED） |
| B-054 | `src/mcp_connector.py:86-97` | `_real_translate` 的 `langpair` 只有 `zh-CN|en` / `en|zh-CN` 两档，其他目标语言静默按中英处理 |
| B-055 | `src/mcp_connector.py:25-29` | `_http_get` 无重试、无异常包装，由 3 个调用方各自 try（易漏） |
| B-056 | `src/mcp_connector.py:111-133` | `connect_server` 不校验 `config` 结构，任意 dict 都标记 `"status": "connected"` |
| B-057 | `src/mock_registry.py:48-60` | `call()` **完全不做权限校验**（忽略 `user_level`），`register()` 不拒绝重名 —— 与真实 `ToolRegistry` 行为不一致，组3 在 Mock 下开发会漏测提权路径 |
| B-058 | `src/mock_registry.py:30-39` / `src/mock_skills.py:31-34` | schema 可缺省，导致 `has(name)=True` 但 `list_tools()` 里没有该工具（与真实实现的不变量不同） |

### 3.2 真实动作与邮件

| 编号 | 位置 | 缺陷 |
|---|---|---|
| B-059 | `src/real_tools.py:122-204` | `body=None` 时 `len(body)`（第 199 行）抛 TypeError；`set_content(body or "(空内容)")` 已容错，`len` 没有 |
| B-060 | `src/real_tools.py:159-162, 176-182` | 前半段构建的 `msg` 是死代码（第 176 行起完全重建），注释亦承认"再完整重建一次" |
| B-061 | `src/real_tools.py:108-119` | `_norm_recipients` 只要有一个地址非法即返回 `[]`，错误信息无法指出是哪一个 |
| B-062 | `src/real_tools.py:208` | `port = int(port or os.environ.get("SMTP_PORT", 465))` 在非法端口串时 ValueError，且位于 `try` 之外 |
| B-063 | `src/real_tools.py:71-101` | `take_screenshot` 固定 `time.sleep(2)`，无法配置；`os.makedirs(os.path.dirname(target) or "/", exist_ok=True)` 在 Windows 上对空 dirname 会尝试创建 `C:\` |
| B-064 | `src/real_tools.py:61` | `webbrowser.get().name` 在无浏览器环境抛异常（已被 try 覆盖，但 `get()` 本身可能启动默认浏览器，属副作用） |

### 3.3 执行器与输入通道

| 编号 | 位置 | 缺陷 |
|---|---|---|
| B-065 | `src/automator.py`→`group_3/automator.py:324-328` | `capture_state()` 的 `os.listdir(".")` 在 CWD 被删除时抛 FileNotFoundError，无兜底 → 整个 `execute()` 崩溃 |
| B-066 | `group_3/automator.py:185-194` | `drag()` 在 AT-SPI 分支**实际只做 `click_at`**，不拖拽；语义与函数名不符 |
| B-067 | `group_3/automator.py:78` | `out.setdefault("success", True)` —— 未显式声明结果的 handler 一律记为成功（Mock 点击、拖拽均如此） |
| B-068 | `group_3/atspi_io.py:268-277` | `hotkey` 静默丢弃未识别键；全部键未知时 `codes` 为空 → 什么都不做，但上层按成功记账 |
| B-069 | `group_3/atspi_io.py:280-285` | `type_text` 的 keysym 合成对**大写字母**未处理 Shift，`_KSYM_SPECIAL` 覆盖不全（缺 `$ & * = [ ] { } < > \| ~` 等） |
| B-070 | `group_3/atspi_io.py:132-133` | `find_accessible` 用 `queue.pop(0)` 实现 BFS → O(n²)，预算 8000 节点时开销显著 |
| B-071 | `group_3/atspi_io.py:154` | `if depth < 24` 深度上限硬编码，与 `ControlDetector(max_depth=...)` 的可配置口径脱节 |
| B-072 | `group_3/atspi_io.py:206-223` | `window_names()` 截断为 `out[:20]` → before/after 状态对比可能漏掉新增窗口（而 `_verify_click` 依赖该对比） |
| B-073 | `group_3/coord_check.py:62-72` | `match_window` 中 `w["name"] in name` 在窗口标题为空串时恒为真 → **空标题窗口可匹配任意查询**（`parse_xwininfo` 的正则允许空标题） |
| B-074 | `group_3/coord_check.py:103-119` | `x_windows` 的模块级缓存 `_cache_ts/_cache_wins` 非线程安全；失败结果同样被缓存 0.5s |
| B-075 | `group_2/control_detector.py:66-83` | `detect_elements` 用 `stack.pop()`（LIFO）遍历，实际访问顺序与 `max_children` 顺序相反（子节点倒序），与注释"按序下钻"的预期不符 |

### 3.4 意图理解与规划

| 编号 | 位置 | 缺陷 |
|---|---|---|
| B-076 | `group_1/host_agent.py:83-100` | `_match_goals` 每条规则只 `re.search` 首次命中 → 同一动作多次出现时丢目标；`claimed` 只挡"完全包含"的重叠，**部分重叠会重复计目标** |
| B-077 | `group_1/host_agent.py:103-178` | `_extract_params` 用**整句**而非命中片段抽参 → 多 goal 时参数互相污染（如 translate 的正则会把整句当待译文本） |
| B-078 | `group_1/host_agent.py:112-114` | translate 的正则在 `翻译 xxx` 形式下 `\1` 为空，靠 `or text` 兜底 → 实际把"翻译 xxx"整句当译文 |
| B-079 | `group_1/host_agent.py:470-488` | `detect_operation` 的 60 字保守门：长但单一的操作指令（如带长路径）一律不路由，退化为纯对话 |
| B-080 | `group_1/host_agent.py:556-561` | `_resolve_target` 依次在 `dirname(default_target)`、`cwd`、`~` 下查找 → 会把用户口语称呼解析到**工作区之外**（该函数被 `_finalize_intent` 的路径归一层依赖） |
| B-081 | `group_1/host_agent.py:377-388` | dest 的"证据防线"用 `nd_parent in user_input or nd_tail in user_input` 做子串判断 → 用户话语中出现同名片段即放行工作区外路径（弱判据） |
| B-082 | `group_2/task_planner.py:199-211` | 兜底填参 `for pname, meta in props.items(): ... break` 只填**一个**字符串参数；两个字符串必需参数同时缺失时第二个仍缺 → 报 unresolved |
| B-083 | `group_2/task_planner.py:101-107` | `_steps_for` 中 `merged` 计算语句之后紧跟一个字符串字面量（原 docstring 位置错误），该字面量是无副作用的死表达式 |
| B-084 | `group_2/task_planner.py:111` | `plan()` 的 `last_file_target` 只在 `steps[-1]["kind"] == "skill"` 时更新，GUI/tool 步骤产生的路径不被继承 |
| B-085 | `group_2/route_policy.py:31-46` | `consumed_by` 对 `{{prev_result}}` 固定映射到 `steps[idx-1]`，若上一步是 GUI 步骤则映射错位 |
| B-086 | `group_2/route_policy.py:71-76` | `_looks_like_url` 认为 `file.txt`、`a.b` 这类"点分段无空格"串是 URL → `open_url` 收到文件名时不会被改写为搜索 |

### 3.5 协调器、统计与批处理

| 编号 | 位置 | 缺陷 |
|---|---|---|
| B-087 | `group_5/coordinator.py:164, 270` | `bundle["ok"] = True` **无条件**赋值，即使全部步骤失败 |
| B-088 | `group_5/coordinator.py:289-298` | `_persist` 无异常兜底：bundle 含不可 JSON 序列化对象时 `json.dump` TypeError → 编排在最后一步抛错（审计已记但产物未落盘） |
| B-089 | `group_5/coordinator.py:110-113` vs `174` | 两条路径键名不一致：`plan_only` 用 `user_input`，`orchestrate` 用 `user_text`；`plan_only` 的最终返回体还丢掉了 `user_input` |
| B-090 | `group_5/coordinator.py:115-137` | `execute_approved` 临时替换 `agent.confirmer` 再在 finally 恢复 —— 并发调用时是竞态（GUI 已有 `_LOCK` 串行化，但协调器本身不保证） |
| B-091 | `src/stats.py:55-63` | 注释行夹在 `if/else` 之间，缩进看起来像"`c["fail"] += 1` 在 else 块外"；功能正确但**极易被后续维护者误改** |
| B-092 | `src/stats.py:35-63, 102-119` | `record()` 无锁（并发丢失计数）；每笔都全量重写 JSON（O(n²) 磁盘 IO）；`_save` 的 `except Exception: pass` 静默吞掉统计丢失 |
| B-093 | `src/stats.py:121-131` | `_load` 只捕获 `FileNotFoundError/JSONDecodeError`，权限类 OSError 会向上抛出 |
| B-094 | `src/stats_buffered.py:97-108` | `_flush_locked` 先读文件再合并 `_unsaved`，多进程场景下会互相覆盖；`existing["counters"] = self._counters` 全量覆盖，若文件被外部更新则丢失 |
| B-095 | `src/batch_executor.py:93-103` | `CircuitBreaker.is_open()` 是**有副作用的 getter**：冷却结束即 `_opened_at=None; _total=0; _failures=0` 并返回 False —— 不是"半开"而是完全关闭；且 `BatchExecutor.stats()` 会调用它，**查询统计即静默复位熔断器** |
| B-096 | `src/batch_executor.py:211-215` | `self._results` 只增不减：`drain()` 重复调用返回累积结果（含历史运行）；长驻执行器内存持续增长 |
| B-097 | `src/batch_executor.py:264-269` | `shutdown()` 后 `submit()` 仍返回 True，但 worker 已退出 → 任务永不被处理，`drain()` **永久阻塞** |
| B-098 | `src/batch_executor.py:48-57` | `RateLimiter.acquire` 在锁内取 slot、锁外 sleep → 多线程下线程调度可导致实际速率超过设定值 |
| B-099 | `src/batch_executor.py:82-91` | `CircuitBreaker.record` 在熔断期间直接 return，不计样本；但失败率恰等于阈值（0.5）时不熔断（`>` 而非 `>=`），文档未说明 |
| B-100 | `src/batch_executor.py:196-205` | `submit_batch` 用 `c["tool"]` 直接取键，缺键即 KeyError |

### 3.6 GUI 服务端与前端

| 编号 | 位置 | 缺陷 |
|---|---|---|
| B-101 | `gui/server.py:373-389` / `do_GET` | `/api/run` 的 `int((qs.get("cursor") or ["0"])[0])` 未捕获 ValueError → 非法参数 500/断连；`/api/rag` 的 `int(k)` 同 |
| B-102 | `gui/server.py:174, 500-504` | `PENDING` 令牌无过期时间、无数量上限，且未加锁访问 |
| B-103 | `gui/server.py:591-596` | `/api/smtp_test` 直接 `dry_run=False` **真实发送邮件**，无二次确认，与"真实外发必须确认"的流程相悖 |
| B-104 | `gui/server.py:131-136` | `apply_settings` 直接改写 `security.protected = list(security._base_protected)`（访问私有属性），随后再 `add_protected` —— 逻辑绕且与"追加式"语义耦合 |
| B-105 | `gui/server.py:339-348` | `/api/llm` 回显 key 前 4 后 4 位；`/api/smtp_config` 回显授权码前 3 后 3 位 → 部分凭据泄露给前端 |
| B-106 | `gui/server.py:157-169` | `load_smtp_config()` 把授权码写入 `os.environ`（进程环境），对同机其他进程可读（`/proc/<pid>/environ`） |
| B-107 | `gui/server.py:310-314` | `/api/controls` 在 `_LOCK` 内做全桌面 AT-SPI 遍历 → 阻塞编排（`ThreadingHTTPServer` 下表现为整个控制台卡顿） |
| B-108 | `gui/server.py:247` | `SYSTEM = build_system() if False else SYSTEM` 为无意义死代码 |
| B-109 | `gui/server.py:704-711` | 全站无鉴权（仅靠 127.0.0.1 绑定）；同机其他用户/进程可直接调 `/api/call`（含 admin 级） |
| B-110 | `gui/index.html:616` | `addBubble("assistant", "❌ 任务失败："+esc(d.error))` 双转义（`addBubble` 用 `textContent`）→ 界面显示 `&lt;` 字面量 |
| B-111 | `gui/index.html:432` | `const escSteps=new Set(...)` 在事件循环内声明但未使用，且轮询期间 `BUNDLE` 恒为 null → 恒为空集（死代码） |
| B-112 | `gui/index.html:426-427` | 每次事件循环先 `FLOW.forEach(...add("lit"))` 点亮全部 6 个盒子，紧接着 `litFlow(1)` 又移除 2-6 → 冗余 DOM 操作 |
| B-113 | `gui/index.html:769-782` | `callTool()` 的 `post()` 无 try/catch，服务端 500 时前端抛未捕获异常；`categoryOf(cur)` 为 "mcp" 时走 `/api/call`（正确但语义隐晦） |
| B-114 | `gui/index.html:743-767` | `renderForm` 直接访问 `t.inputSchema.properties`，schema 缺 `inputSchema` 时 TypeError |

### 3.7 凭据与产物

| 编号 | 位置 | 缺陷 |
|---|---|---|
| B-115 | `system_out/llm_config.json`（167B） | **明文保存 API Key**（含 `provider/base_url/model/api_key` 字段）。`.gitignore` 已排除 `system_out/`，但本机任何进程可读；`HostAgent.apply_config` 亦无加密/权限收紧 |
| B-116 | `system_out/smtp_config.json`（125B） | **明文保存 SMTP 授权码**（`SMTP_PASS`），同样仅靠 gitignore 保护 |
| B-117 | `tests/`（1.4MB） | 残留 **48 个** `.tmp_out_*` 临时目录未被清理（`IntegrationBase.setUp` 用 `id(self)` 命名，`tearDown` 在用例崩溃/中断时不执行）。`.gitignore` 已排除但磁盘未清 |

---

## 4. P3 — 低优先级 / 代码卫生

| 编号 | 位置 | 问题 |
|---|---|---|
| B-118 | `group_5/security.py:44-52` | `add_protected` 的 `return` 之后存在不可达代码（第 52 行），作者已加注说明但未删除 |
| B-119 | `src/skill_library.py:59-61` | 重复定义的第一份 `call_skill`（同 B-003，此处仅记死代码） |
| B-120 | `gui/index.html:361` | `function _flowTo(n){/* 点亮前 n 个盒子 */}` 空函数，从未被调用 |
| B-121 | `gui/index.html:938` | `$("#page-settings")&&0; /* 占位 */` 无意义表达式 |
| B-122 | `gui/server.py:181` | `def _start_run(text: str = None, ...)` 类型标注为 `str` 但默认 `None` |
| B-123 | `group_1/ai_shell.py:73` | `h.get("intent", {})` 在 `intent` 键存在且为 `None` 时 AttributeError（当前调用方保证非 None） |
| B-124 | `group_3/__init__.py:8-10`、`group_4/__init__.py:22-24`、`group_5/__init__.py:8-10` | 包导入时执行 `sys.path.insert` —— 修改全局状态的导入副作用 |
| B-125 | `tests/test_integration_v10.py:712-716` | `TestDemoMatrix` 对失败结果自动重试一次，掩盖非确定性缺陷 |
| B-126 | `tests/test_integration_v10.py:38-43` | 临时目录名用 `id(self)`（内存地址），不保证唯一且不可读 |
| B-127 | `.agents/skills/agent-os-dev/scripts/pytest_shim.py:100-108` | `_fixtures` 只支持 `monkeypatch`/`tmp_path`，不支持 `capsys`/`tmpdir`；`MonkeyPatch.setattr` 不校验属性存在 |
| B-128 | `.agents/skills/agent-os-dev/scripts/health_check.py:64-69` | 假定 `/api/plan` 必然返回 `pending=True`；若某指令无需确认（`pending=False`）则 `token` 为空串，后续 `/api/execute` 404，误报失败 |
| B-129 | `docs/开发与最终报告.md:158` | 声称"集成测试 13 项中 12 项通过"，与当前测试文件（40+ 用例）**且根本无法运行**（B-001）矛盾 |
| B-130 | `docs/验证报告.md`、`文档/README.md:38-41` | 全部以 `python3 main.py ...` 为入口，而该文件不存在（B-001） |
| B-131 | `hnu_intro.md` | **0 字节空文件**，疑为被 organize 搬移/截断后的残留 |
| B-132 | `src/web_automation.py:120` | `_WebSocket.recv_text` 对 ping 的 pong 回应 `bytes([0x8A, len(chunk)])`，`chunk` 超过 125 字节时长度编码错误（控制帧通常很短，实际难触发） |
| B-133 | `src/web_automation.py:76-91` | WebSocket 握手未校验 `Sec-WebSocket-Accept`，不验证服务端身份 |
| B-134 | `src/web_automation.py:476-485` | `_AUTO` 单例永不关闭，进程退出后可能遗留自动化浏览器进程 |
| B-135 | `src/interfaces.py:39-40` | `PermissionLevel.rank()` 用字典查找，新增枚举成员时静默 KeyError（无兜底） |
| B-136 | `group_1/host_agent.py:186-188`、`代码/host_agent.py:129-131` | `api_key` 从环境变量读入后长期驻留内存；`test_llm` 会把 key 送入真实请求（预期行为，仅记录） |

---

## 5. 附录 A — 跨文件一致性问题汇总

1. **`task_planner.py` 存在三份**（`代码/`、`group_1/`、`group_2/`），`host_agent.py` 存在两份（`代码/`、`group_1/`），已产生实质行为差异（见 B-032）。
2. **危险命令/受保护路径黑名单两份**（`src/default_tools.py` 与 `group_5/security.py`），口径不一致（见 B-040）。
3. **"幂等注册"三种口径**（见 B-011）。
4. **`_APP_DESKTOP` 应用名映射表两份**（`group_2/task_planner.py:282-292` 与 `group_3/automator.py:198-209`），组3 多出 `zcode`，且组2 有长度护栏、组3 没有（见 B-020）。
5. **`{{引用}}` 解析两份**（`group_2/route_policy.py:_refs_in` 与 `group_3/app_agent.py:_resolve_refs`），正则相同但语义不同（前者只收集引用，后者做回填），改一处易漏另一处。
6. **`bundle` 键名两种**（`user_input` vs `user_text`、`ok` 语义不同，见 B-089）。
7. **`plan["policy"]` 形态两种**：`group_1` 返回 dict，`group_2` 返回字符串短码（`"escalate-once | selfheal-name | continue-on-fail"`）。

## 6. 附录 B — 文档与实现不符

| 文档断言 | 实际 |
|---|---|
| `文档/README.md:38-41` `python main.py --demo` | 根目录无 `main.py`（B-001） |
| `docs/验证报告.md` 21 条验证命令 | 同上，全部不可执行 |
| `docs/开发与最终报告.md:158` "13 项中 12 项通过" | 测试文件 40+ 用例，且导入即失败 |
| `docs/开发与最终报告.md:163` "失败必须可见" | 网页工具把失败伪装为 `success=True`（B-010） |
| `group_5/security.py:1-13` "四层安全架构" | 第 3 层（签名）与第 4 层（隐私）均未落地（B-004/B-005） |
| `src/web_automation.py:688` `register_web_tools` "幂等" | 重复调用抛 ValueError（B-011） |
| `src/default_tools.py:150-156` `list_directory` "O(页大小)" | 实为 O(全目录) + 全量排序（B-043） |
| `src/batch_executor.py:65` 熔断"半开" | 实为完全关闭，且 `stats()` 会复位（B-095） |

## 7. 附录 C — 已确认正确、未发现缺陷的关键点

为避免"全盘否定"的误导，以下经重点核对确认逻辑正确：

- `group_3/coord_check.apply_affine` 的逐轴仿射算法与 4 个单测期望值完全吻合（`[10,200,50,30]` + `[0,0,890,550]`→`[317,88,1012,672]` = `[328,332,57,37]`，手工验算一致）。
- `src/web_automation._WebSocket` 的帧编解码（掩码、126/127 扩展长度、分片、ping/pong、close）实现正确，`tests` 中的本地假服务器回环用例可覆盖。
- `group_2/route_policy.apply_route_policy` 的通道改写逻辑与 8 个单测一致，"宁缺毋滥"（`_required_ok`）设计正确。
- `group_5/coordinator._consistent` 的对账逻辑与 `AppAgent.execute` 的 summary 计算口径一致。
- `src/stats.py` 的原子写（`NamedTemporaryFile` + `os.replace`）实现正确，能抵御写一半崩溃。
- `group_1/host_agent._finalize_intent` 的"证据防线"与 `_normalize_dest` 确实修复了 `pitfalls.md:45-48` 记录的三连漏洞（当前代码不再会把 `.`/相对 dest 落到进程 CWD）。

## 8. 附录 D — 关键缺陷的复现方式

```bash
# B-001：测试套件无法运行
cd <项目根>
python -c "import sys; sys.path.insert(0,'.'); import main"
# → ModuleNotFoundError: No module named 'main'

# B-002：副本入口无法运行
python 代码/main.py --text "查询长沙天气"
# → ModuleNotFoundError: No module named 'group_1'

# B-003：重复定义（合法 Python，编译期不报错）
python -m compileall -q src group_1 group_2 group_3 group_4 group_5 gui tests 代码
# → 全部通过，证明死代码/不可达代码不会被静态检查发现
grep -n "def call_skill" src/skill_library.py
# → 59: 与 62: 两处

# B-005：redact 从未被调用
grep -rn "redact" --include=*.py .
# → 仅 group_5/security.py:148 一处（定义）

# B-007：前端未定义函数
grep -n "loadChatHistory\|saveChatHistory" gui/index.html
# → 只有调用点（348/527/607/689/955），无 function 定义

# B-030：缺失 CSS 类
grep -c "\.bar-fill" gui/index.html
# → 0（仅 JS 字符串里出现）

# B-117：测试残留
ls -d tests/.tmp_out_* | wc -l
# → 48
```

---

## 9. 说明

- 本报告**仅记录问题**，未对任何已有文件做修改、移动、重命名或删除操作。
- 每条缺陷均标注了文件与行号，可直接定位。
- 分级依据：**P0** = 阻断运行 / 不可逆数据丢失 / 安全防线失效 / 审计结果失真；**P1** = 明确功能错误、必然崩溃路径、跨平台失效、契约不一致；**P2** = 边界条件、资源泄漏、健壮性不足；**P3** = 死代码、注释误导、命名与文档不一致。
- 其中 **B-001 / B-002 / B-009 / B-010 / B-031 / B-006** 建议优先处理：前两者决定工程是否可验证，B-009 关系用户数据安全，B-010 决定审计结论是否可信，B-006 是本地任意网页可触发的实际攻击面。
