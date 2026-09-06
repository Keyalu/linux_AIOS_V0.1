# Agent_OS_v1.0 — Linux 桌面 Agentic OS 原型（五组集成版）

基于微软 UFO² 架构、按《操作系统设计任务书》完成五组联调的系统总集。
自然语言驱动桌面操作自动化，实践中体现操作系统三大原理：
虚拟化（调度/沙箱隔离）、并发（编排/状态同步）、持久性（统计落盘/审计日志/RAG 知识库）。

## 系统架构与数据流

```
用户（自然语言）
    ↓
组1 AI Shell + HostAgent            group_1/  意图理解（LLM qwen-plus + 规则 Mock 双轨）
    ↓
组5 SecuritySandbox 安全检查        group_5/  四层安全（危险命令→沙箱→签名→隐私）
    ↓
组2 TaskPlanner + ControlDetector   group_2/  任务分解(steps) + 控件定位(elements)
    ↓
组3 AppAgent + Automator            group_3/  执行(GUI/工具步骤，提权流+自愈)
    ↓
组4 ToolRegistry + OS Skills        group_4/ → src/  工具公开契约
    ↓
组5 SystemCoordinator 审计 + RAG    group_5/  对账 + 执行轨迹知识库
```

## 接口契约（任务书接口契约表）

| 组 | 输出 | 形态 |
|----|------|------|
| 1 | intent_json | `{"intent": "文件操作", "target": "...", "action": "...", "params": {...}}` |
| 2 | plan_json | `{"steps": [{"step_id":1, "action":..., "target":...}], "elements": {...}}` |
| 3 | result_json | `{"success": true, "before_state": ..., "after_state": ...}` |
| 4 | tool_result_json | `{"success": true, "result": "..."}` |
| 5 | check_json | `{"approved": true, "risk_level": "low|medium|high|critical"}` |

## 运行

```bash
python main.py --demo          # 任务书演示场景表联调（7 场景结果矩阵）
python main.py                 # AI Shell 交互模式（cosh）
python main.py --text "查询长沙天气"
python -m unittest discover tests -v    # 集成联调测试
```

## 系统控制台（Web GUI）

```bash
python gui/server.py            # http://127.0.0.1:8788（--port 可换）
```

四个页签：**编排控制台**（自然语言→五组链路可视化，六份中间产物 JSON 逐个查看）、
**工具台**（组4 能力清单 schema 内省，选身份发起真实调用）、
**系统统计**（system_out/stats.json 累积账本逐笔/聚合）、
**RAG 知识库**（执行轨迹余弦检索）。纯标准库实现，与生产代码共用同一套
权限闸门/黑名单/受保护路径拦截；服务仅绑定 127.0.0.1。

零第三方依赖：pyautogui / pyatspi / openai(标准库直连) 三类真实依赖存在即走真实
路径，不存在自动降级 Mock（任务书"每组必须提供 Mock 实现"）。
LLM 路径配置：`export DASHSCOPE_API_KEY=...`（缺省自动走规则引擎）。

## 运行产物（system_out/）

| 文件 | 内容 |
|------|------|
| intent.json / plan.json | 组1 / 组2 输出契约实例 |
| check.json / session_log.json | 组5 安全检查 / 组3 执行会话日志 |
| stats.json | 组4 调用统计（逐笔原子落盘，累积账本） |
| audit_log.jsonl | 组5 审计日志 |
| rag_traces.jsonl | 组5 RAG 执行轨迹知识库 |
