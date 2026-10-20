# verify.md — CLI / GUI 验证命令清单

> 面向：**在这台机器上亲手确认系统真的能跑**。
> 两条原则：**输出必须打在屏幕上看得见**；每条命令都写清"该看到什么"。
> 环境说明：CLI 用 `python3`（Windows 上用 `python`）；标 🐧 的条目需要 Linux 桌面/浏览器，其余各平台都能跑。
> 位置：本文在项目根目录，命令默认在项目根目录下执行。

---

## 0. 一键把所有验证跑完（输出直接打屏）

```bash
cd AIOS_V0.1

echo "===== 1/5 语法与导入 =====";   python3 -c "import compileall;print('compileall', compileall.compile_dir('.', quiet=1, maxlevels=6));import main;print('import main OK')"

echo "===== 2/5 指令阶梯（先校验路由与计划，再真跑 7 条） ====="; python3 .agents/skills/agent-os-dev/scripts/verify_commands.py

echo "===== 3/5 演示矩阵（七场景） =====";       python3 main.py --demo

echo "===== 4/5 审计修复回归（83 项） =====";     python3 tests/test_regression_audit.py

echo "===== 5/5 集成联调（51 项） =====";         python3 tests/test_integration_v10.py

echo "全部结束：人工核对上面的 7/7、OK、PASS 标记"
```

**想同时留档又要看得见**：在命令末尾加 `2>&1 | tee /tmp/aios_verify.log`（`tee` 一边打屏一边写文件）。
⚠️ **不要用 `> aios.log`** —— 那样屏幕上什么都看不到；已经这么跑过就用 `cat aios.log` 或 `tail -f aios.log` 补看。

---

## 1. CLI · 批量指令阶梯（推荐先跑这条）

```bash
# 先校验路由与计划，再对 7 条可离线执行的走完整编排；输出 95 行左右，全在屏幕上
python3 .agents/skills/agent-os-dev/scripts/verify_commands.py
```

**该看到什么**（两段式）：

```
【一】路由与计划校验（组1 解析 + 组2 规划，不执行）
L1 只读技能｜查一下磁盘使用情况
  组1 动作链 : ['disk_usage']
  组2 步骤   : ['disk_usage', 'run_command']   （提权步骤: [2]）
...
【二】真跑（走完整编排：组1→组5安全→组2→组3→组4←→组5审计/RAG）
L1 只读技能｜查一下磁盘使用情况
  审计     : PASS（2/2 步成功，对账一致=True）
    · 步骤1 disk_usage（user）成功 → {"total_gb": 449.6, "used_gb": 362.1, ...}
L2 安全拦截｜rm -rf /
  ⛔ 被安全层拦截：安全检查未通过: 危险操作: rm -rf
...
L4 截屏｜截个屏保存到 /tmp/aios_shot.png
  跳过执行：依赖桌面会话/自动化浏览器，本机无法实测
```

判读：`PASS` = 对账一致且全部成功；`⛔` = 安全层拦截（这正是期望）；`跳过执行` = 该条需要桌面/浏览器（不是失败）。

---

## 2. CLI · 逐条单跑（看清楚每一步）

```bash
# 通用形式：单条自然语言指令 → 打印 intent / check / plan / session / audit 五段 JSON
python3 main.py --text "查一下磁盘使用情况"
```

按复杂度由低到高，依次跑这 9 条（命令 + 该看到什么）：

| # | 命令 | 该看到什么 | 依赖 |
|---|------|-----------|------|
| 1 | `python3 main.py --text "查一下磁盘使用情况"` | `audit.verdict: PASS`；步骤1 `disk_usage` 返回 `total_gb/used_gb/free_gb/percent` | 无 |
| 2 | `python3 main.py --text "rm -rf /"` | `check.approved: false`、`risk_level: critical`；`plan.steps: []`（零步骤） | 无 |
| 3 | `python3 main.py --text "写一个文件 /tmp/aios_note.txt 内容是 hello"` | 步骤1 `write_file` 成功「已写入 … (5 字符)」；`cat /tmp/aios_note.txt` 能读到 `hello` | 无 |
| 4 | `python3 main.py --text "截个屏保存到 /tmp/aios_shot.png"` | `take_screenshot` 成功，`result` 里带 `waited_ms / screen_stable / page_ready`；图片文件真的生成 | 🐧 X11 |
| 5 | `python3 main.py --text "把「demo_task」文件夹里的文件整理一下"` | 步骤1 `organize_downloads` 返回 `total_moved=6`；`ls system_out/demo_task` 能看到按类型分的子目录 | 无 |
| 6 | `python3 main.py --text "帮我把「demo_task」文件夹里的文件整理一下，顺便看看有没有重复文件，最后备个份"` | 4/4 步成功；查重报 1 组重复；产出 `system_out/demo_task_backup.zip` | 无 |
| 7 | `python3 main.py --text "起草一封邮件发给 someone@example.com，主题是验收测试，内容是这是一封草稿"` | 步骤1 `send_email` 成功 且 `"dry_run": true` + `hint:"干跑模式未发送"`（不联网） | 无 |
| 8 | `python3 main.py --text "联网搜索湖南大学"` | 有浏览器：`web_search` 成功并返回结果页信息；无浏览器：`success:false` + `[Mock·未执行]`（如实失败，不假装成功） | 🐧 Chromium |
| 9 | `python3 main.py --text "执行命令「echo aios_verify」"` | 步骤1 `run_command` 输出 `aios_verify`；沙箱不可用时结果前缀写明原因（`[沙箱不可用:未安装 bwrap → 直接执行]` 或 `[沙箱不可用:bwrap 无法启动：setting up uid map: … → 直接执行]`） | 无 |

> 两条别改字：**2** 换成正则外的说法可能绕过拦截；**7** 去掉"起草"就变成**真实发送**（需 SMTP 凭据 + 确认流）。
> `--text` 是非交互模式，末尾的提权步骤按 `auto_escalate` 自动同意，审计里标 `confirm_mode=auto`。

---

## 3. CLI · 演示矩阵与测试套件

```bash
# 七场景联调矩阵（每行是 场景 / 动作 / 结果 / 步数 / 审计 verdict / 轨迹号）
python3 main.py --demo

# 审计修复回归：83 项，期望末行 OK
python3 tests/test_regression_audit.py

# 集成联调：51 项，期望 failures=0（🐧 桌面机上；Windows 无文件管理器时 1 红）
python3 tests/test_integration_v10.py

# 一次跑完两套（共 130 项），逐条显示用例名
python3 -m unittest discover tests -v
```

**该看到什么**：

```
联调结果: 7/7 场景通过 | 产物: .../system_out    ← 🐧 桌面机期望 7/7
知识库轨迹: 6 条 | 审计日志: 13 条

Ran 83 tests in 26.5s
OK

Ran 51 tests in 73.6s
FAILED (failures=1, skipped=2)                    ← 本机唯一失败=「场景2 导航」(无文件管理器)
```

---

## 4. CLI · 起服务前的接口探测（curl / python3 二选一）

```bash
# 另开一个终端起服务（前台运行，日志可见；Ctrl+C 退出）
python3 gui/server.py --port 8788

# ① 页面是否可访问（状态码 + 真实字节数；%{size_download} 在个别 curl 版本会显示 0，故用 python3 量）
curl -s -o /dev/null -w "GET / → %{http_code}\n" http://127.0.0.1:8788/
curl -s http://127.0.0.1:8788/ | python3 -c "import sys;print('页面字节数:', len(sys.stdin.buffer.read()))"
# 期望：GET / → 200 ；页面字节数: 约 65000（HTML 单文件，含前端）

# ② 能力清单（21 工具 + 8 技能）
curl -s http://127.0.0.1:8788/api/tools | python3 -c "import json,sys;d=json.load(sys.stdin);print('能力数:',len(d['tools']));print([t['name'] for t in d['tools']][:8])"

# ③ 工具直调（必须带 JSON Content-Type，否则 403 —— 这是 CSRF 防线在生效）
curl -s -X POST http://127.0.0.1:8788/api/call -H 'Content-Type: application/json' \
     -d '{"name":"list_directory","params":{"path":"."},"level":"user"}'
curl -s -o /dev/null -w "text/plain POST → %{http_code}（期望 403）\n" -X POST \
     http://127.0.0.1:8788/api/call -H 'Content-Type: text/plain' -d '{}'

# ④ 规划 → 执行 → 轮询（需要确认时 /api/plan 回 token）
curl -s -X POST http://127.0.0.1:8788/api/plan   -H 'Content-Type: application/json' -d '{"user_text":"查一下磁盘使用情况"}'
curl -s -X POST http://127.0.0.1:8788/api/execute -H 'Content-Type: application/json' -d '{"token":"<上一步 token>","mode":"full"}'
curl -s 'http://127.0.0.1:8788/api/run?id=<run_id>&cursor=0'
```

没有 curl 的机器，用 python3 三行替代（等价）：

```bash
python3 - <<'PY'
import json, urllib.request
req = urllib.request.Request("http://127.0.0.1:8788/api/plan",
        data=json.dumps({"user_text": "查一下磁盘使用情况"}).encode(),
        headers={"Content-Type": "application/json"})
print(json.loads(urllib.request.urlopen(req).read())["plan"]["steps"])
PY
```

---

## 5. GUI · 打开界面

```bash
python3 gui/server.py                # 默认 127.0.0.1:8788；换端口加 --port 8899
# 浏览器打开 http://127.0.0.1:8788
```

**改了任何 `.py` 之后必须重启服务**（它是启动时加载的常驻进程），一键脚本免去找 pid：

```bash
python3 scripts/restart_gui.py            # 停掉 8788 上的旧进程 → 前台重启（Ctrl+C 停）
python3 scripts/restart_gui.py --stop     # 只停不起
python3 scripts/restart_gui.py --port 8899 --force   # 换端口 / 不确认
```
它会先查"谁在监听这个端口"，并**校验那是不是 python 进程**——不是就拒绝动手（不会误杀别的服务）。Windows 与 Linux 都能跑（Windows 走 netstat/tasklist/taskkill，Linux 走 /proc 反查 socket inode，不依赖 ss/lsof）。
- 远程机器上跑、本机看界面：`ssh -L 8788:127.0.0.1:8788 用户@目标机`，然后本机浏览器开 `http://127.0.0.1:8788`。
  **不要**用 `--host 0.0.0.0` 直连：同源防线要求 Host 是回环，非回环一律 403。
- 页签共 7 个：编排控制台 / 对话助手 / 工具台 / 系统统计 / RAG 知识库 / LLM 设置 / 系统设置。

---

## 6. GUI · 编排控制台（粘下面这些，一条一条来）

在该页输入框（默认已填场景6那句）里粘贴 → 点 **▶ 运行编排**：

```
查一下磁盘使用情况
rm -rf /
写一个文件 /tmp/aios_note.txt 内容是 hello
把「demo_task」文件夹里的文件整理一下
帮我把「demo_task」文件夹里的文件整理一下，顺便看看有没有重复文件，最后备个份
起草一封邮件发给 someone@example.com，主题是验收测试，内容是这是一封草稿
联网搜索湖南大学
执行命令「echo aios_verify」
截个屏保存到 /tmp/aios_shot.png
```

**操作与判读**：
1. 每条都会弹**确认面板**（计划末尾有提权收尾步骤）→ 点左「全部允许并执行」；点中「仅执行免提权步骤」会**真的过滤掉** ADMIN 步骤；点右「取消」会落盘这一轮的 intent/check/plan 但不执行。
2. 上部五组流程盒会依次点亮；下面是 **KPI + 逐步骤表**（提权行有底色、"提权被拒"会单独显示）。
3. 右侧 **JSON 标签页**就是六份中间产物：`intent / check / plan / session_log / audit / stats` —— 切着看一眼，和 CLI 打出来的五段 JSON 对得上。
4. 页面下方 **运行历史**表：点任意一行可回看那次运行的产物与审计 verdict。
5. 期望：磁盘/整理/组合/写文件/命令 → `审计 PASS`；`rm -rf /` → 顶部显示 **被拦截**、步骤表空。

---

## 7. GUI · 对话助手（验"只回答真实能力/真实数据"）

在该页点顶部 **常用提问** 按钮（或往输入框粘下面这些）：

```
你能做什么
当前系统状态和最近的编排运行结果
调用统计的总调用量、成功率和失败最多的接口
知识库里都有哪些轨迹
当前系统设置和受保护路径有哪些
打开文件管理器
```

**该看到什么**：
- 「你能做什么」→ 气泡上方有来源标签 **「本地能力清单 · 按组4 schema 现场生成」**，正文列出**真实的 21 工具 + 8 技能**和一段"本系统明确做不到"。
- 「当前系统状态…」→ 标签为 **「真实数据…」**，内容来自 `stats.json` / 运行历史，不是模型编的。
- 「打开文件管理器」→ 弹**执行卡片**（🎯 检测到系统操作），点「执行」才走完整管线；点「忽略」丢弃。
- 另外验证：切到别的页签再切回来，**聊天记录还在**；只有点「清空对话」才清空。

**直接粘"能力标识符"也认**（从工具台/产物里抄来的名字）——这几条都能弹卡片：

```
执行 system_check
disk_usage
运行 send_email
看一下 take_screenshot
```

而**缺参数**的能力名会得到一份"参数说明"（不弹卡片、也不乱猜）：

```
read_file          → 必填参数 path；可选：无；怎么跑：工具台搜 read_file / 或说「读一下 /tmp/aios_note.txt」
list_directory     → 可选参数 path、offset、limit；怎么跑：工具台 / 或说「列出 /tmp 里的文件」
```
判读：卡片只由**服务端确定性路由**弹出；如果助手回了一段"参数说明"或让你去编排控制台，说明它**没有**执行，也没有生成卡片。

---

## 8. GUI · 工具台 / 统计 / RAG / 设置

| 页签 | 操作 | 该看到什么 |
|------|------|-----------|
| **工具台** | 左栏搜索 `disk`，点 `disk_usage` → 身份选 `user` → 点「执行 disk_usage」 | 右栏结果 JSON 里 `success: true` + 磁盘数值；左栏分类标签有 工具/技能/MCP 三类 |
| **工具台** | 搜 `send_email`，填 `to=someone@example.com`，`dry_run` 留空（默认 True）→ 执行 | 返回 `"dry_run": true` + `hint:"干跑模式未发送"`（不发信） |
| **工具台** | 搜 `read_file`，`path` 填 `~/.ssh/id_rsa` → 执行 | **拒绝**：受保护/敏感路径（安全层在工具层也拦） |
| **系统统计** | 打开页面，勾「自动刷新」 | 总调用/成功/失败/成功率 KPI + 按接口条形图 + 逐笔流水表；点「清空账本」后计数归零（内存与文件一起清） |
| **RAG 知识库** | 检索框留空点检索；再输入「整理」点检索 | 前者=最近轨迹；后者=相似检索；顶部显示"知识库共 N 条轨迹" |
| **LLM 设置** | 选供应商/填 Key → 保存 → 点「测试连通」 | 连通成功显示耗时与解析示例；Key 只显示「已配置（N 字符，已隐藏）」。下方 SMTP 区填 host/port/user/授权码 → 保存 → 「发送测试邮件」 |
| **系统设置** | 看各字段 → 改「提权确认策略」为 `deny` → 保存 → 再回编排控制台跑「执行命令「echo hi」」 | 提权步骤被**过滤**（跳过清单会显示），不再自动同意；改回 `auto` 恢复 |

---

## 9. 环境差异速查（看到"失败"先对这里）

| 现象 | 是不是问题 | 说明 |
|------|-----------|------|
| 「场景2 导航操作」红 | 否 | 该场景依赖文件管理器（nautilus）；Windows/无桌面机必红 |
| 截屏返回 `ModuleNotFoundError: No module named 'gi'` | 否 | 无 GTK/X11 会话；🐧 桌面机上正常 |
| 网页工具返回 `success:false` + `[Mock·未执行]` | 否 | 没检测到 Chromium；装了浏览器即走真实通道 |
| 结果里出现 `[沙箱不可用:… → 直接执行]` | 否 | 沙箱没生效但命令照跑，**原因写在结果里**（`未安装 bwrap` / `bwrap 无法启动：setting up uid map: Permission denied`） |
| 结果里出现 `[沙箱层失败 → 已直接执行]` | 否 | 探测通过但实跑时沙箱报错，已自动直接重试一次，沙箱错误一并写明 |
| 想真的用上沙箱（Ubuntu 24.04） | 可选 | 先自证：`sysctl kernel.apparmor_restrict_unprivileged_userns`；若为 `1` 且 `bwrap --ro-bind / / --dev /dev --proc /proc true` 报 `setting up uid map: Permission denied`，就是 AppArmor 限制了非特权 userns。放开需要 sudo（`sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`，或给 bwrap 配 AppArmor profile），按你的安全策略取舍 |
| 编排被拦：`工具运行时代码与登记不一致(疑似被篡改)` | 先重启一次（`python3 scripts/restart_gui.py`） | 第 3 层发现**注册表持有的那个函数对象**字节码与登记时不一致 —— 真被就地改过才会拦。重启会重建干净基线；若重启后仍复现，把完整 reason 发出来（里面带工具名） |
| `check.reason` 里出现 `⚠ 磁盘源码已变更:` 或 `⚠ 运行时代码与登记不一致但源文件同时变更:` | 否 | 前者＝改了 `.py` 没重启（进程仍跑旧版本）；后者＝进程经历过热更/重导入。**都不拦执行**，重启后恢复干净基线 |
| 编排被拦：`受保护路径: ['xxx（来自 write_file.path）']` | 是（设计如此） | 该动作真的要触碰系统/敏感目录。括号里写明是**哪个字段**触发的，换到工作区内即可。注意 `/mnt`、`/media` **不在**受保护列表（WSL 的 Windows 盘、可移动介质都属于用户数据） |
| 发邮件被拦"受保护路径" | 已修（重启生效） | `send_email` 现在只检查**附件**，不看 `target`（收件人是地址语义）；工作区无论设在哪都不会再拦住发信 |
| `check.reason` 里出现 `⚠ 磁盘源码已变更: [...]` | 否 | 只是磁盘上的 `.py` 改过、进程仍跑加载时的旧版本（开发期常态）→ **不拦执行**，仅提示；重启后生效。想确认：`curl -s -X POST .../api/plan -d '{"user_text":"查一下磁盘使用情况"}'` 看 `check.reason` |
| 提权步骤没问就过了 | 否 | 非交互/默认策略是 `auto`，审计记 `confirm_mode=auto`；要人工确认用 `python3 main.py` 交互 Shell 或改成 `deny` |
| 脚本调用返回 403 | 是（用法问题） | 少了 JSON `Content-Type`，或 Host 不是回环 |
| 改了代码没变化 | 是（用法问题） | 服务是启动时加载的，清 `__pycache__` 后重启；验证要打运行中的服务 |

---

## 10. 相关文档

- `../简单跑起来_readme.md` —— 移植上手手册（前置条件、干净打包、完整功能勾选清单、排障表）
- `docs/bugFind.md` / `docs/bugFix.md` —— 审计与修复记录
- `.agents/skills/agent-os-dev/scripts/verify_commands.py` —— 本文 §1 用的批量脚本
- `.agents/skills/agent-os-dev/references/pitfalls.md` —— 现象 → 根因 → 修复速查
