# 测试与服务管理

## 测试运行

项目测试是 **pytest 格式**。有 pytest 的机器（`pip install pytest`）直接：

```bash
cd Agent_OS_v1.0 && python -m pytest tests/ -v      # 集成联调测试
cd /home/keyal/桌面/111 && python -m pytest tests/ -v  # 全部测试
```

### 无 pytest 环境：兼容层

本开发机（精简 Ubuntu）无 pip，用项目自带的兼容层直跑测试：

```bash
python3 .agents/skills/agent-os-dev/scripts/pytest_shim.py tests/test_groups.py
```

### 特例：tests/test_integration_v10.py 可原生直跑

该文件自带 unittest 入口（无需兼容层）：

```bash
python3 tests/test_integration_v10.py
```

注意：场景矩阵类测试依赖真实 LLM 在环，存在输出波动——单场景偶发失败时
重试一次再判（测试内置一次重试；连续两次失败才是真回归）。

兼容层实现的 pytest 特性：monkeypatch、tmp_path、pytest.raises、pytest.approx、
类风格（setup_method）与函数风格测试。**不兼容**：pytest.mark/parametrize（项目未使用）。

兼容层是"临时诊断工具"的正确归宿：**不要把一次性的验证脚本放 /tmp**（会被系统清理），
也不要在项目里硬编码机器路径——脚本用自身位置推 ROOT。

## 测试基线（当前全绿状态）

| 文件 | 数量 | 覆盖 |
|------|------|------|
| tests/test_groups.py | 25 | 五组真实实现（意图/规划/执行/审计/安全/RAG/截图链路） |
| tests/test_all.py | 31 | 组4 全功能 |
| tests/test_real_tools.py | 11 | open_url/send_email（SMTP SSL/STARTTLS 模拟） |
| tests/test_pipeline_demo.py | 6 | 替身版联调 |
| tests/test_highpressure.py | 24 | 高压特性（分页/缓冲/限流/熔断/并行） |
| tests/test_integration.py | 11 | 组3 集成回路 |
| tests/test_integration_group5.py | 6 | 组5 审计接入 |
| Agent_OS_v1.0/tests/test_integration_v10.py | 13 | v1.0 系统集成（含场景矩阵） |

已知容错：场景矩阵测试对真实 LLM 输出波动有一次重试；send_email 类测试不得真发网络邮件（dry_run/模拟）。

## 无浏览器环境下的前端验证（2026-09-15 补，Windows 开发机实测可用）

GUI 改动不能只靠"看代码"，也不一定非要起浏览器。两条成本极低的替代手段：

### 1. JS 语法 + 纯函数行为：`node --check` + 抽函数 eval

```bash
python - <<'PY'   # 抽出 <script> 与目标函数
h=open('gui/index.html',encoding='utf-8').read()
js=h.split('<script>',1)[1].rsplit('</script>',1)[0]
open('.node_chk.js','w',encoding='utf-8').write(js)              # 全脚本语法检查
i=js.index('function stepOutputMd'); j=js.index('async function runStaged')
open('.node_fn.js','w',encoding='utf-8').write(js[i:j])          # 单个纯函数
PY
node --check .node_chk.js                                       # ★ 真的能拦下语法错误
node -e "eval(require('fs').readFileSync('.node_fn.js','utf8')); console.log(stepOutputMd(fakeSess))"
```
`node --check` 在 2026-09-15 真拦下一处"模板字符串 + 普通字符串拼接"的语法错误 —— 只读代码看不出来。
用完删掉 `.node_chk.js` / `.node_fn.js`（本机 `rm` 被 safe-delete shim 拦，用 python 删）。

### 2. 接口链路：原始 socket 打运行中的服务（不依赖 curl/浏览器）

`docs/bugFix.md` §4.1 与 §9.3 的冒烟就是这么做的：`socket.sendall("POST /api/chat HTTP/1.1 …")`
逐条断言状态码/SSE 正文，覆盖 **CSRF 三路拒绝**、`/api/chat` 的路由判定、
`/api/plan → /api/execute → /api/run` 全链路终态。

两个必备技巧：
- **临时挪开 `system_out/settings.json` 与 `llm_config.json`**：前者里的工作区可能是
  Linux 路径（会被 `makedirs` 到本机），后者会让冒烟意外发起真实 LLM 调用；跑完原样还原。
- 需要"确定性通道"时给子进程设 `AGENT_OS_LLM_CONFIG=/nonexistent`（组1 走规则引擎）。

### 3. 前端状态类改动（聊天记录、页签）的断言方式

无法 headless 跑 DOM，就断言**不变量**而不是行为：例如"清空动作只出现在 `clearChat` 里"
（`html.count('对话已清空')==1`）、"首次回填有 `if(CHAT_SYNCED)return;` 护栏"、
"本地已有内容时不覆盖"。见 `tests/test_regression_audit.py::TestChatHistoryAndRendering`。

## 服务管理

| 服务 | 端口 | 启动 | 说明 |
|------|------|------|------|
| Agent_OS 系统控制台 | 8788 | `cd Agent_OS_v1.0 && python3 gui/server.py` | 主力；SMTP/LLM 配置从 system_out/ 自动加载，重启不丢 |
| 旧工具控制台 | 8765 | `cd /home/keyal/桌面/111 && SMTP_HOST=... SMTP_PASS=... python3 gui/server.py` | SMTP 需 env 注入；当前按用户要求保持关闭 |

- 重启服务：先 `ss -tlnp` 按端口找 pid 再 kill（**禁止 pkill -f 匹配含启动命令文本的模式**）。
- 两个 GUI 的响应都带 `Cache-Control: no-store`——浏览器页面永远取最新，改前端后普通刷新即可。
- 编排/工具调用有全局锁串行化；`/api/run` 轮询无锁，服务忙时也不断流。
