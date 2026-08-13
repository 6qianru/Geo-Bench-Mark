# GeoSkillBench 迭代 1：本地 agent 多轮指挥外部 agent 实现计划

> 状态：**已定稿，待实施**。对应"将 user_task 接入智能体、由本项目 agent 向外部 agent 发出指令模拟人类操作、一个任务多次对话完成目标"的迭代项。
> 关联文档：接入契约 `docs/Agent接入契约.md`、Executor 定位 `docs/GeoSkillBench-Executor与Nanobot接入补充设计.md`、改造计划 `docs/GeoSkillBench-Evaluation平台改造计划.md`。

## 0. 设计决策（已确认，采纳推荐方案）

| # | 问题 | 决策 |
|---|---|---|
| 1 | orchestrator 运行时 | **LangGraph**（真实可用）；nanobot 目前是占位，后续可作为同一 Executor 接口的另一种 runtime |
| 2 | 直接模式是否保留 | **保留** `http_agent` 直接一问一答（回归安全），`orchestrator` 作为新 executor 注册 |
| 3 | 外部 agent 能力说明 | `AgentConfig` 新增 `description` 字段（可空，默认用 `scenario.description`），喂给 orchestrator 系统提示词 |
| 4 | 终止判定 | 本地 agent 发 `[FINAL]` 时结束；`max_turns` 兜底（语义=最多外部指令数）；超限未 FINAL → 强制要求 [FINAL] 总结进展 |
| 5 | orchestrator 工具集 | v1 只有 `ask_external_agent` 一个工具，不给本地 MCP 工具 |
| 6 | `final_response` 归属 | orchestrator 的 `[FINAL]` 总结（含结果）；外部 agent 每轮响应记录到 `external_interactions` |
| 7 | 多轮对话记录 | 新增 `external_interactions`（instruction/response 对），入报告与前端 |
| 8 | actor 是否参与 | 不参与；orchestrator 自己就是操作者，`need_interaction` 对 orchestrator 恒 False |

## 1. 核心架构

```
scenario.user_task（目标）
      ↓
OrchestratorExecutor（本地 LangGraph ReAct agent，作为"人类操作者"）
      │  系统提示词：目标 + 外部agent能力 + 最多max_turns条指令 + [FINAL]协议
      ↓  自主循环
   ask_external_agent(instruction)  ←→  HttpAgentExecutor（内部复用：SSE/JSON解析、session_id 多轮上下文）
      ↓  每轮：外部响应文本回给本地 agent 推理；外部 tool_event 转 ToolCallRecord 流入 recorder
   [FINAL] 总结（= final_response）
```

要点：
- **外部多轮上下文**由内部复用的 `HttpAgentExecutor` 的 `session_id` 机制维持（`http_agent_executor.py:69-90`），orchestrator 不需要重写任何 HTTP 解析逻辑。
- **多轮 = ReAct 工具循环**：本地 agent 调 `ask_external_agent` → 读响应 → 决定下一条指令 → 再调，直到自己判定达成并发 `[FINAL]`。
- runner 外层循环对 orchestrator 只跑 1 轮（`finished=True`），actor 分支天然跳过。

## 2. Schema 改动（`models/scenario.py`）

```python
class AgentConfig(BaseModel):
    # ...现有字段不变
    description: str = ""   # 新增：外部 agent 能力说明，用于 orchestrator 系统提示词

# RuntimeConfig.max_turns 语义补充注释：
#   orchestrator 模式下 = 最多向外部 agent 发送的指令数（超限强制 [FINAL]）
```

## 3. 新增 OrchestratorExecutor（`geoskillbench/executors/orchestrator_executor.py`）

```python
class OrchestratorExecutor(Executor):
    executor_type = "orchestrator"

    def create_session(self, request: ExecutorSessionRequest) -> ExecutorSession:
        # 前置校验：缺 request.agent.endpoint 或缺真实 agent_model → raise ValueError（明确报错，无启发式兜底）
        # 1. 内部创建 HttpAgentExecutor + 其 session（持外部 endpoint/session_id）
        # 2. build_llm(request.role_model_config["model"], temperature=0, config=load_models_config())
        # 3. create_react_agent(llm, tools=[ask_external_agent],
        #       prompt=SystemMessage(orchestrator 系统提示词))
        # 4. 状态：instruction_count=0, pending_tool_calls=[], external_interactions=[]
        # 5. 返回 ExecutorSession(executor_type="orchestrator", runtime_mode="real")

    def send_message(self, session_id: str, message: str) -> ExecutorStepResult:
        # message = scenario.user_task（目标）
        # state.agent.invoke({"messages": [HumanMessage(message)]})
        # 提取最终 AI 内容；pending 外部 tool_calls 移入返回
        # 返回 ExecutorStepResult(response=终答, finished=True, tool_calls=外部calls)

    def close_session(self, session_id: str) -> None:
        # 关 LangGraph session + 关内部 HttpAgentExecutor session
```

`ask_external_agent` 工具：

```python
@tool("ask_external_agent")
def ask_external_agent(instruction: str) -> str:
    """向外部智能体发送一条指令并返回它的回答。"""
    # instruction_count 达上限 → 返回强制收尾提示（让本地 agent 必须 [FINAL]）
    step = http_executor.send_message(http_session_id, instruction)
    for call in step.tool_calls:            # 外部 tool_event → 断言可用的 ToolCallRecord
        pending_tool_calls.append(call)
    external_interactions.append({
        "turn": len(external_interactions) + 1,
        "instruction": instruction,
        "response": step.response,
        "tool_calls": [c.model_dump() for c in step.tool_calls],
    })
    return step.response or "(外部智能体无文本回复)"
```

**orchestrator 系统提示词**（初版，需对真实 SUT 联调调优）：

```text
你是 GeoSkillBench 评测平台的外部智能体操作者。你的目标是：{user_task}
外部智能体能力：{agent.description or scenario.description}
你可以通过 ask_external_agent 工具向它发送指令。规则：
1. 把目标分解成外部智能体可执行的指令，一次发一条。
2. 读取它的回答判断进展：缺参数→补下一条指令；它反问→回答它；它做完了→进入步骤3。
3. 目标达成时，以 [FINAL] 开头输出总结，必须包含结果信息。
4. 最多发送 {max_turns} 条指令；超限仍未达成也要以 [FINAL] 说明进展与受阻原因。
5. 不得编造外部智能体没有提供的结果。
```

LangGraph `recursion_limit` 设为 `max_turns * 3 + 4` 作第二道安全阀。

## 4. Recorder 扩展（`recorder/execution_recorder.py`）

```python
class ExecutionRecorder:
    # 新增
    external_interactions: list[dict] = []
    def record_external_interaction(self, interaction: dict) -> None:
        self.external_interactions.append(interaction)
```

runner 在 `record_final_output`（`runner.py:241-247`）时把 `recorder.external_interactions` 一并写入 `final_output["external_interactions"]`——**不进 TestResult schema**，报告/DB/前端通过 final_output 拿到。

## 5. runner / factory

- `runner.py`：orchestrator 模式下现有循环已够（finished=True 单轮结束）。仅补 `final_output["external_interactions"]`。LOAD_SKILL 对 agent_test 的跳过逻辑（`runner.py:113-116`）已存在，无需改。
- `factory.py`：注册 `orchestrator` 分支。

## 6. 报告与前端

- `report_generator.py`：markdown 增加 `## External Agent Interactions` 小节，逐轮展示 instruction/response（及 tool_calls）。
- `App.jsx`：`ResultDetail` 增加"外部交互"区（instruction/response 对，可折叠）。

## 7. 契约文档补充（`docs/Agent接入契约.md`）

新增一节说明 orchestrator 模式：本地 agent 作为操作者的行为边界、`[FINAL]` 由本地 agent 发出、外部 agent 侧的 session_id 复用、断言可用性（`tool_called` 系列取决于外部是否上报 `tool_event`，与直接模式一致）。

## 8. 示例场景

新增 `scenarios/agent_orchestrated_sqlQBQP.yml`：

```yaml
id: agent_orchestrated_sqlQBQP
type: agent_test
runtime:
  executor: orchestrator
  agent_model: deepseek-v4-flash   # 必须配 models.yaml 真实别名，无启发式兜底
  max_turns: 5                  # = 最多外部指令数
agent:
  type: http
  endpoint: http://<host>:8490/agentx/workflowstudio/api/v1/run/<flow_id>
  query_params: { stream: "true" }
  stream_response: true
  body: { input_type: chat, output_type: chat }
  description: 能对 GIS 数据集执行查询、求交、缓冲区等空间分析的智能体
user_task: 对北京市与中心城区求交
assertions:
  - type: final_response_contains
    value: "完成"
  - type: tool_called            # 外部上报 tool_event 时可用
    tool: <外部工具名>
judge:
  enabled: false
pass_criteria:
  required_assertions_passed: true
  judge_score_min: 0.8
```

## 9. 测试

- 真机多轮：executor=orchestrator、max_turns=5，目标需外部反问才能完成 → 验证 `external_interactions` 出现 >1 轮、final_response 是 [FINAL] 总结、`tool_called` 断言按外部上报生效。
- max_turns 兜底：同一目标配 max_turns=1 → orchestrator 被强制第 1 条后 [FINAL] 总结进展。
- 错误路径：缺 endpoint / 缺真实 agent_model / 外部 agent 不可达 → 明确报错，run 失败，错误入 errors。
- 回归：现有 `http_agent` 直接模式场景（`agent_external_sqlQBQP.yml`）不受影响。
- 报告/前端：external_interactions 正确渲染。

## 10. 明确不做（本迭代边界）

- 不做 nanobot orchestrator（先 LangGraph，Executor 接口兼容，后续替换运行时零侵入）。
- 不记录 orchestrator 内部思维链（只记 instruction/response 对）。
- 不给 orchestrator 配本地 MCP 工具。
- 不做多外部 agent 编排（v1 单外部 agent）。

## 11. 工作量与风险

- 预估 **2~3 天**：schema + recorder + runner 半天；orchestrator executor 1 天；orchestrator 系统提示词 + 真机联调 1~1.5 天。
- 最大风险：**终止判定质量**（过早 [FINAL] / 无限追问）。缓解：max_turns 硬兜底 + 工具内强制收尾 + 断言/judge 事后判定。这部分需对真实 SUT 反复调，是工作量大头。
