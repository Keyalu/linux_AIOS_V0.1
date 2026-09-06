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

## 服务管理

| 服务 | 端口 | 启动 | 说明 |
|------|------|------|------|
| Agent_OS 系统控制台 | 8788 | `cd Agent_OS_v1.0 && python3 gui/server.py` | 主力；SMTP/LLM 配置从 system_out/ 自动加载，重启不丢 |
| 旧工具控制台 | 8765 | `cd /home/keyal/桌面/111 && SMTP_HOST=... SMTP_PASS=... python3 gui/server.py` | SMTP 需 env 注入；当前按用户要求保持关闭 |

- 重启服务：先 `ss -tlnp` 按端口找 pid 再 kill（**禁止 pkill -f 匹配含启动命令文本的模式**）。
- 两个 GUI 的响应都带 `Cache-Control: no-store`——浏览器页面永远取最新，改前端后普通刷新即可。
- 编排/工具调用有全局锁串行化；`/api/run` 轮询无锁，服务忙时也不断流。
