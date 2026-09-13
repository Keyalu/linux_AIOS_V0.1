# 扩展配方

四类高频扩展的逐步配方。通用前置：改完 `111/` 根目录的源后，同步到 `Agent_OS_v1.0/`（本次会话曾因漏同步返工），清 `__pycache__`，重启 8788。

## 配方 1：组4 加新工具

1. 实现函数（`src/default_tools.py` 或 `src/real_tools.py`）：
   - 签名：普通函数，参数全关键字；返回统一 `{"success": bool, "result"/"error": ...}`（用 `ToolResult.ok/fail().to_dict()`）
   - 安全：路径参数过 `_is_protected_path()`；命令过 `_is_dangerous_command()`；网络调用设 timeout 并降级
2. 在 `TOOL_SCHEMAS`（default_tools）或注册处（real_tools）声明 schema：
   - 参数名/类型/描述 = GUI 表单的数据来源；`permission` 决定权限闸门
   - **string 类型参数收到 dict/list 会被组2 类型防御丢弃**——工具内部要做 None/缺省兜底
3. 加入注册字典/函数。
4. **若要被 LLM 编排使用**（缺一不可，漏了就会幻觉变通）：
   - 组1 `group_1/host_agent.py` `_SYSTEM_PROMPT` 动作表加 action 名 + 常用参数名说明
   - 组2 `group_2/task_planner.py` 加动作映射（`_SKILL_ACTIONS` 或 `_TOOL_ACTIONS`）
   - `_steps_for`/_fill_params 若有特殊参数语义（如 backup 的 src/dest、send_email 的 attachment），在类型防御兜底处加特例
5. GUI 工具台自动出现（schema 内省），统计自动落账。
6. 测试：`tests/test_groups.py` 或对应文件加用例（正常路径 + 权限 + 坏参数）。

## 配方 2：加意图动作链（自然语言 → 新编排）

1. 组1 规则路径：`group_1/host_agent.py` `_RULES` 加 `(正则, 动作名, 描述)`——**更具体的规则放前面**（span 重叠会跳过泛化项）。
2. 组1 LLM 路径：`_SYSTEM_PROMPT` 动作清单加同名 action + 参数名说明。
3. 实体抽取：动作需要话语中的对象（城市/路径/邮箱）时，在 `_extract_params` 或 goals 的 entity 抽取里加正则。
4. 组2：`_SKILL_ACTIONS`/`_TOOL_ACTIONS` 加映射；特殊参数语义在 `_fill_params` 兜底处说明。
5. 组5 安全：有外发/删除语义的动作确认风险定级。
6. 测试：意图解析断言 + 编排端到端。

## 配方 3：加 GUI 桌面动作（AT-SPI 真实执行）

1. 组1 `_RULES` 加关键词（注意与泛化词的先后与重叠）。
2. 组2 `_GUI_ACTIONS` 加动作名 → 步骤序列（hotkey/type/click 组合，参照 navigate/create_folder）。
3. 组3 `Automator` 确认对应 handler 存在（click/type_text/hotkey 已覆盖常见组合）。
4. 真实输入注意：合成键盘对 ASCII 可靠；中文用 setTextContents；动作会操作**真实桌面**——测试用无害目标。

## 配方 4：加用户可调设置（GUI 系统设置页）

1. `Agent_OS_v1.0/gui/server.py`：
   - `SETTINGS_DEFAULT` 加字段 + 默认值
   - `apply_settings` 加应用逻辑（逐项 try/except，坏值不拖垮系统）——目标是**在跑的模块实例**（host.use_llm、agent.level、planner.add_finale 等）
2. `gui/index.html` 设置页加表单控件 + saveSettings 的 body 字段。
3. `load_settings` 已按 SETTINGS_DEFAULT 键吸收，无需改。
4. 若设置影响编排行为，确认相关测试仍过（设置是运行时注入，测试环境默认值不变）。

## 配方 5：加 MCP 工具（真实数据源优先）

1. `src/mcp_connector.py` 加 `_setup_mock_xxx(registry)`：**必须带完整 ToolSchema**（参数声明是 GUI 表单和组2 规划的数据来源，缺失会被类型防御丢弃）。
2. 真实数据源实现：纯标准库 urllib，设 timeout，失败降级 `[Mock·离线]` 标记文本（参考 _real_weather/_real_search/_real_translate）。
3. schema 注册进 `registry.register(name, func, ToolSchema(...))`。
4. 若要进编排：组1 词典/提示词 + 组2 映射（参照配方 2 的 weather 路径）。

## 配方 6：加网页自动化能力（CDP 通道）

网页点击不走 AT-SPI（snap 浏览器被 AppArmor 挡在 a11y 总线外，见 pitfalls），走
`src/web_automation.py` 的 CDP 通道。扩展步骤：

1. 高层操作加在 `WebAutomation` 类：`_cmd(method, params)` 发命令，
   JS 定位用 `Runtime.evaluate` + `json.dumps` 嵌入参数（防注入），交互用
   `Input.dispatchMouseEvent`（真实输入事件）。
2. 组4 工具封装：`web_xxx() -> {"success", "result"/"error", "simulated"?}`，
   浏览器不可用 → `[Mock]` 标记 + simulated=True（不谎报真实成功）；
   schema 进 `WEB_SCHEMAS`，注册进 `register_web_tools`。
3. **同步组1**：`_SYSTEM_PROMPT` 动作清单 + 参数说明 + `_RULES`（网页类规则
   放在泛化"点击"规则之前，防 span 重叠抢走）+ `_extract_params` 分支。
4. **同步组2**：`_TOOL_ACTIONS` 加映射；web_click 的 text 是必填参数，注意
   `_fill_params` 的路径类防御不适用于它。
5. 测试：WS 帧回路（本地假服务器，见 TestWebAutomation）、Mock 降级（改
   `AIOS_WEB_BROWSER` 环境变量后必须恢复+复位 `_AUTO` 单例）、真机集成
   （skipUnless 有浏览器）。

## 配方 7：加语义近邻工具（必须同步通道路由决策表）

凡是与现有工具"用户目标相同、执行通道不同"的工具（如 mcp_search 拿数据 vs
web_search 开结果页、open_url vs web_open）：

1. `group_2/route_policy.py`：把动作加进对应歧义组（或新建组），决策表只写
   **机械可判定**的判据——计划图消费分析（`{{prev_result}}/{{stepN}}` 引用）、
   参数形态（像不像 URL）、后续步骤家族（有没有 web_click/web_state）。
   不要把判据写成提示词散文——散文不可测试、组合爆炸。
2. 改写自动带步骤级 `route_reason` + `plan["routes"]` 汇总（审计可见）；
   目标动作缺必需参数时护栏拒绝改写（宁缺毋滥）。
3. 提示词只教"语义区分"（什么词该给什么工具）+ 一个正例，通道兜底交给决策表
   ——两头都要有，但提示词不再是唯一防线。
4. 测试：决策表每个分支一组用例（见 TestRoutePolicy），必含缺参拒绝分支；
   `{{prev_result}}` 占位符的正则兼容残缺形态 `{{prev_result}`（提示词示例
   曾长期带此错，LLM 会照抄）。

## 配方 8：智能化扩展（回答/记忆/反思）

三个智能能力的实现模式与扩展点：

1. **LLM 解读回答**（llm_answer + web_extract）：`src/llm_client.py` 是组内
   通用文本 LLM 客户端（读 system_out/llm_config.json，零 SDK）。凡是
   "工具输出 → 自然语言结论"的需求都做成 llm_answer 的变体；测试用
   `llm.chat = fake` 替身。
2. **RAG 记忆注入**：`coordinator._recall_memory` 召回 top-2 相似轨迹 →
   `understand_intent(memory=...)` → `_llm_intent` 拼 few-shot。规则路径
   不注入（确定性输出无需记忆）。纠正记忆若要落地：记
   (话语, 错误动作, 纠正动作) 三元组进同一 JSONL 即可复用检索。
3. **失败反思重试**：`HostAgent.repair_intent`（LLM 产出修正 goals）+
   `orchestrate` 步骤 4.5（fail>0 且 LLM 可用时触发一次）。**铁律**：反思
   产出的 intent 与原始计划同等不可信，必须重新过安全闸门 + 决策表 +
   schema 校验；修不出有效方案就如实保留首次结果并标注 reflected。
   GUI 确认流的反思需走 needs_confirm 二次确认，勿绕过。
