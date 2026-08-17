# GeoSkillBench Executor 与 Nanobot 接入补充设计文档

版本：v0.1  
性质：主系统设计文档的附加说明  
适用范围：补充说明 GeoSkillBench 中 Executor 的定位，以及 nanobot 是否可以作为 Executor 接入

---

## 1. 背景说明

在 GeoSkillBench 主系统设计中，核心测试流程包括：

```text
加载 Scenario
  ↓
准备数据
  ↓
连接 MCP Tools
  ↓
加载被测 Agent Skill
  ↓
运行被测 Agent
  ↓
Actor 交互
  ↓
Assertion 校验
  ↓
Judge 评价
  ↓
生成报告
```

这里的“运行被测 Agent”可以进一步抽象为一个独立组件：

```text
Executor
```

Executor 是整个测试系统中的“执行者”。它负责加载被测 Agent Skill、加载可用 MCP Tools、接收用户任务 prompt，并实际执行任务。

本补充文档明确：

```text
1. Executor 在系统中的职责边界
2. Nanobot 是否可以作为 Executor
3. Executor 与 Test Runner / Actor / Judge / Assertion 的关系
4. 推荐的 Executor 接口设计
5. NanobotExecutor 的接入方式
6. SkillExecutor 与 NanobotExecutor 的区别
```

---

## 2. Executor 的定义

Executor 是 GeoSkillBench 中负责执行被测 Agent Skill 的运行时组件。

它不是测试框架本身，也不是 Judge，而是被测试对象的运行容器。

Executor 的输入包括：

```text
1. 用户任务 prompt
2. 被测 Agent Skill Prompt
3. 当前测试数据上下文 TestContext
4. 可用 MCP Tools
5. Agent 模型配置
6. 最大轮次、超时等运行限制
```

Executor 的输出包括：

```text
1. Agent 最终回答
2. 对话历史
3. Tool Call 记录
4. Tool 参数
5. Tool 返回结果摘要
6. 结果数据句柄或输出 artifacts
7. 是否需要继续用户交互
8. 是否完成任务
9. 错误信息
```

可以简单理解为：

```text
Executor = 加载 Skill + 调用 MCP Tools + 执行用户任务的 Agent Runtime
```

---

## 3. Executor 在系统架构中的位置

推荐架构：

```text
GeoSkillBench Test Runner
│
├── Scenario Loader
├── Fixture Manager
├── MCP Tool Adapter
├── Skill Loader
│
├── Executor
│   ├── SkillExecutor
│   ├── NanobotExecutor
│   ├── AgentXExecutor
│   └── CustomExecutor
│
├── Actor Runtime
├── Assertion Engine
├── Judge Engine
└── Report Generator
```

Executor 只负责执行被测 Agent 行为。

GeoSkillBench Test Runner 仍然负责整体测试流程：

```text
1. 加载和校验 Scenario
2. 导入测试数据
3. 检查 MCP Tools 是否可用
4. 加载被测 Skill
5. 创建 Executor Session
6. 控制 Executor 与 Actor 的多轮交互
7. 记录执行过程
8. 执行 Assertions
9. 调用 Judge
10. 生成报告
11. 清理测试数据
```

---

## 4. Executor 与其他模块的边界

### 4.1 Executor 不负责的事情

Executor 不应该负责：

```text
1. Scenario 文件管理
2. 测试数据导入和清理
3. MCP required / optional tools 的环境检查
4. Assertion 规则判断
5. Judge 评分
6. 测试报告生成
7. 测试结果统计
8. 前端状态管理
```

这些应由 GeoSkillBench 自身负责。

---

### 4.2 Executor 负责的事情

Executor 应负责：

```text
1. 接收 Agent Skill Prompt
2. 接收可用 MCP Tools
3. 接收用户任务
4. 运行 Agent 推理和工具调用
5. 在需要时向用户追问信息
6. 接收 Actor 的回复并继续执行
7. 产生最终结果
8. 暴露对话和工具调用轨迹
```

---

## 5. Nanobot 是否可以作为 Executor

结论：

```text
nanobot 可以作为 GeoSkillBench 的一个 Executor 实现。
```

也就是：

```text
NanobotExecutor
```

它的定位是：

```text
使用 nanobot 作为被测 Agent Runtime，
负责加载 Agent Skill、加载 MCP Tools、执行用户 prompt，
并与 Actor 进行多轮交互。
```

但不建议把整个 GeoSkillBench 构建成 nanobot 插件或完全依赖 nanobot。

推荐边界是：

```text
GeoSkillBench 负责测试平台能力；
nanobot 作为一个可插拔 Executor，负责执行被测 Agent 行为。
```

---

## 6. 为什么 nanobot 适合作为 Executor

nanobot 适合作为 Executor 的原因：

```text
1. 它是轻量级 Agent Runtime。
2. 它支持 Agent Loop。
3. 它可以加载上下文、skills 或 prompts。
4. 它支持 MCP 工具调用。
5. 它可以作为被测 Agent 的执行容器。
6. 它比完整重型框架更容易理解和修改。
```

因此，在 GeoSkillBench 中可以这样使用：

```text
Scenario
  ↓
Test Runner
  ↓
Skill Loader
  ↓
MCP Tool Adapter
  ↓
NanobotExecutor
  ↓
Actor / Judge / Assertion
```

---

## 7. 使用 nanobot 作为 Executor 的前提条件

如果要实现 NanobotExecutor，需要确认或适配以下能力：

```text
1. 能注入 system prompt / skill prompt。
2. 能加载指定 MCP Tools。
3. 能接收外部 user message。
4. 能支持多轮对话 session。
5. 能返回 Agent 回复。
6. 能暴露 Tool Call 记录。
7. 能暴露 Tool 参数和结果摘要。
8. 能设置 max_turns / timeout。
9. 能控制或禁用 memory，以保证测试可复现。
10. 能配置不同模型。
11. 能在任务结束时返回 final response 和 artifacts。
```

其中最关键的是：

```text
Tool Call 记录必须能被 GeoSkillBench 捕获。
```

因为 Assertion Engine 需要判断：

```text
是否调用了正确工具
工具调用顺序是否正确
工具参数是否正确
是否生成了结果数据
```

如果 nanobot 默认不暴露足够详细的工具调用信息，则需要在 NanobotExecutor 里做 wrapper 或 hook。

---

## 8. 推荐的 Executor 抽象接口

为了兼容 LangGraph、nanobot、AgentX 或其他 Runtime，建议定义统一的 Executor 接口。

### 8.1 Executor 类型

```text
Executor
  ├── SkillExecutor
  ├── NanobotExecutor
  ├── AgentXExecutor
  └── CustomExecutor
```

---

### 8.2 Session 型接口

推荐使用 session 型接口，而不是一次性 run 接口。

原因是 GeoSkillBench 需要控制 Actor 多轮交互。

推荐接口：

```python
class Executor:
    async def create_session(
        self,
        request: ExecutorSessionRequest
    ) -> ExecutorSession:
        raise NotImplementedError

    async def send_message(
        self,
        session_id: str,
        message: str
    ) -> ExecutorStepResult:
        raise NotImplementedError

    async def close_session(
        self,
        session_id: str
    ) -> None:
        raise NotImplementedError
```

---

### 8.3 ExecutorSessionRequest

```python
from pydantic import BaseModel
from typing import Any

class ExecutorSessionRequest(BaseModel):
    scenario_id: str
    skill_id: str
    skill_prompt: str
    test_context: dict[str, Any]
    tools: list[Any]
    model_config: dict[str, Any]
    max_turns: int = 6
    timeout_seconds: int = 180
    memory_enabled: bool = False
```

字段说明：

```text
scenario_id：
  当前测试场景 ID。

skill_id：
  被测 Agent Skill ID。

skill_prompt：
  已渲染的 Agent Skill 提示词。

test_context：
  当前测试上下文，包括数据句柄、数据元信息、可用工具信息等。

tools：
  已加载的 MCP Tools。

model_config：
  Agent 使用的模型配置。

max_turns：
  最大交互轮次。

timeout_seconds：
  单次执行超时时间。

memory_enabled：
  是否启用 memory。测试场景中建议默认 false，保证结果可复现。
```

---

### 8.4 ExecutorSession

```python
class ExecutorSession(BaseModel):
    session_id: str
    executor_type: str
    scenario_id: str
    skill_id: str
    created_at: str
```

---

### 8.5 ExecutorStepResult

```python
from typing import Literal

class ToolCallRecord(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None
    status: Literal["success", "failed"]
    latency_ms: int | None = None
    error_message: str | None = None

class ExecutorStepResult(BaseModel):
    response: str
    need_interaction: bool
    finished: bool
    tool_calls: list[ToolCallRecord] = []
    artifacts: dict[str, Any] = {}
    error_message: str | None = None
```

---

## 9. Test Runner 如何控制 Executor 与 Actor

推荐由 Test Runner 控制多轮循环，而不是让 Executor 完全内部自循环。

### 9.1 推荐流程

```text
Test Runner
  ↓
create_session(skill_prompt, tools, test_context, model_config)
  ↓
send_message(user_task)
  ↓
Executor 返回 response
  ↓
判断：
  - finished = true：进入结果收集
  - need_interaction = true：调用 Actor
  - error：记录失败
  ↓
ActorRuntime.reply(...)
  ↓
send_message(actor_reply)
  ↓
继续循环，直到：
  - Executor finished
  - 达到 max_turns
  - timeout
  - error
```

---

### 9.2 伪代码

```python
async def run_agent_with_actor(
    scenario,
    test_context,
    skill,
    executor,
    actor_runtime,
    recorder,
):
    session = await executor.create_session(
        ExecutorSessionRequest(
            scenario_id=scenario.id,
            skill_id=skill.id,
            skill_prompt=skill.prompt,
            test_context=test_context.model_dump(),
            tools=test_context.tools,
            model_config=scenario.role_models.agent,
            max_turns=scenario.runtime.max_turns,
            timeout_seconds=scenario.runtime.timeout_seconds,
            memory_enabled=False,
        )
    )

    current_message = scenario.user_task

    for turn_index in range(scenario.runtime.max_turns):
        step_result = await executor.send_message(
            session_id=session.session_id,
            message=current_message,
        )

        recorder.record_executor_step(step_result)

        if step_result.error_message:
            break

        if step_result.finished:
            break

        if step_result.need_interaction:
            actor_reply = await actor_runtime.reply(
                scenario=scenario,
                conversation=recorder.conversation,
                test_context=test_context,
            )
            recorder.record_actor_message(actor_reply)
            current_message = actor_reply
            continue

        break

    await executor.close_session(session.session_id)
```

---

## 10. 为什么推荐 Test Runner 控制多轮

推荐由 Test Runner 控制多轮，而不是由 nanobot 内部完整控制，原因包括：

```text
1. Actor 插入更自然。
2. max_turns 更容易控制。
3. 每一轮的输入输出都可以被记录。
4. 失败和超时更好处理。
5. 不同 Executor 的行为更容易统一。
6. 后续支持 SkillExecutor / AgentXExecutor 更方便。
```

如果让 nanobot 一次性完成完整任务，会出现：

```text
1. Actor 难以插入。
2. 工具调用日志可能不完整。
3. 测试系统难以控制中间状态。
4. 不同 Executor 的行为差异会放大。
```

---

## 11. NanobotExecutor 的实现思路

NanobotExecutor 作为 Executor 的一个实现，内部负责适配 nanobot。

### 11.1 create_session

create_session 应完成：

```text
1. 创建 nanobot 会话。
2. 注入 system prompt。
3. 注入 Agent Skill Prompt。
4. 注入 TestContext。
5. 注册或连接 MCP Tools。
6. 配置模型。
7. 初始化 recorder hook。
8. 返回 session_id。
```

伪代码：

```python
class NanobotExecutor(Executor):
    async def create_session(self, request: ExecutorSessionRequest) -> ExecutorSession:
        nanobot_session = await self.nanobot.create_session(
            model_config=request.model_config,
            memory_enabled=request.memory_enabled,
        )

        await nanobot_session.set_system_prompt(
            self.build_system_prompt(request)
        )

        await nanobot_session.register_tools(request.tools)

        self.sessions[nanobot_session.id] = nanobot_session

        return ExecutorSession(
            session_id=nanobot_session.id,
            executor_type="nanobot",
            scenario_id=request.scenario_id,
            skill_id=request.skill_id,
            created_at=now_iso(),
        )
```

---

### 11.2 send_message

send_message 应完成：

```text
1. 向 nanobot session 发送用户消息。
2. 等待 nanobot 回复。
3. 捕获工具调用。
4. 判断是否需要用户补充信息。
5. 判断是否完成任务。
6. 返回 ExecutorStepResult。
```

伪代码：

```python
async def send_message(self, session_id: str, message: str) -> ExecutorStepResult:
    session = self.sessions[session_id]

    raw_result = await session.send(message)

    tool_calls = self.extract_tool_calls(raw_result)

    return ExecutorStepResult(
        response=raw_result.text,
        need_interaction=self.detect_need_interaction(raw_result.text),
        finished=self.detect_finished(raw_result),
        tool_calls=tool_calls,
        artifacts=self.extract_artifacts(raw_result),
        error_message=raw_result.error,
    )
```

---

### 11.3 close_session

close_session 应完成：

```text
1. 清理 nanobot session。
2. 释放临时上下文。
3. 关闭工具连接或释放引用。
4. 删除 session 缓存。
```

---

## 12. 判断 need_interaction 与 finished

Executor 需要向 Test Runner 返回：

```text
need_interaction
finished
```

这两个状态很重要。

### 12.1 need_interaction

当 Agent 明确需要用户补充信息时，应返回：

```json
{
  "need_interaction": true,
  "finished": false
}
```

判断方式可以包括：

```text
1. Agent 回复中出现明确问题。
2. Agent 调用了某种 ask_user 类型工具。
3. Agent 返回结构化状态 need_user_input。
4. Nanobot 内部状态表明等待用户输入。
```

MVP 阶段可以采用文本启发式 + prompt 约束：

```text
要求 Executor 如果需要用户补充信息，必须在输出中标记：
[NEED_INTERACTION]
```

例如：

```text
[NEED_INTERACTION]
请问需要使用哪个学校数据集？
```

后续可以改为结构化输出。

---

### 12.2 finished

当 Agent 已完成任务时，应返回：

```json
{
  "need_interaction": false,
  "finished": true
}
```

判断方式可以包括：

```text
1. Agent 返回最终回答。
2. Agent 返回结果数据句柄。
3. Agent 调用了完成任务所需工具。
4. Agent 输出中包含结构化标记 [FINAL]。
```

MVP 阶段也可以要求：

```text
任务完成时输出：
[FINAL]
...
```

---

## 13. Executor Prompt 约定

为了让不同 Executor 行为稳定，建议给 Executor 统一的系统提示词约定。

示例：

```text
你是 GeoSkillBench 测试系统中的 GIS Agent Executor。
你的任务是根据用户请求、已加载 Agent Skill 和可用 MCP Tools 完成 GIS 分析。

执行规则：
1. 必须遵循已加载 Agent Skill 的 instructions。
2. 只能使用当前提供的 MCP Tools。
3. 只能使用 TestContext 中声明的数据集。
4. 不得编造不存在的数据、字段、工具或结果。
5. 如果缺少必要信息，应向用户追问，并以 [NEED_INTERACTION] 开头。
6. 如果任务完成，应以 [FINAL] 开头，并返回结果数据句柄和简要说明。
7. 每次工具调用都应使用明确参数。
8. 如果工具调用失败，应说明失败原因，不要伪造成功结果。
```

---

## 14. Scenario 中如何指定 Executor

Scenario 或 RunConfig 中可以增加：

```yaml
runtime:
  executor: skill
```

或：

```yaml
runtime:
  executor: nanobot
```

完整示例：

```yaml
runtime:
  executor: nanobot
  agent_model: qwen3.5-32b
  actor_model: qwen3.5-14b
  judge_model: qwen3.5-32b
  max_turns: 6
  timeout_seconds: 180
```

也可以由前端 Run Configuration 指定：

```json
{
  "executor": "nanobot",
  "role_models": {
    "agent": {
      "model": "qwen3.5-32b"
    },
    "actor": {
      "model": "qwen3.5-14b"
    },
    "judge": {
      "model": "qwen3.5-32b"
    }
  }
}
```

---

## 15. 前端需要增加的配置

前端模型配置区可以增加一个 Executor 选择项：

```text
Executor Runtime:
  [LangGraph ▼]
  [Nanobot]
  [AgentX]
  [Custom]
```

UI 示例：

```text
┌──────────────────────────────────────────────┐
│ Executor Configuration                       │
├──────────────────────────────────────────────┤
│ Executor Runtime: [LangGraph ▼]              │
│                                              │
│ Agent Model:      [qwen3.5-32b ▼]            │
│ Actor Model:      [qwen3.5-14b ▼]            │
│ Judge Model:      [qwen3.5-32b ▼]            │
│                                              │
│ Memory Enabled:   [ ]                        │
│ Max Turns:        [6]                        │
│ Timeout:          [180] seconds              │
└──────────────────────────────────────────────┘
```

对于测试系统，`Memory Enabled` 建议默认关闭。

---

## 16. Executor 与 Judge 的关系

Judge 不应该由 Executor 内部完成。

原因：

```text
1. Executor 是被测对象。
2. Judge 是评估者。
3. 两者应该隔离，避免自评。
4. Judge 需要看到完整执行记录，而不仅是 Executor 最终回答。
```

正确关系：

```text
Executor 负责执行
Recorder 记录全过程
Judge 根据 Recorder + Scenario + Assertions 评价
```

---

## 17. Executor 与 Assertion 的关系

Assertion Engine 也不应该由 Executor 内部完成。

Assertion Engine 应该基于记录结果做独立判断：

```text
1. 是否加载了指定 Skill
2. 是否调用了指定 Tool
3. Tool 参数是否正确
4. Tool 调用顺序是否正确
5. 是否生成了结果数据
6. 最终回答是否包含关键信息
```

Executor 只需要提供足够的执行证据：

```text
conversation
tool_calls
artifacts
final_response
errors
```

---

## 18. SkillExecutor 与 NanobotExecutor 对比

| 项目 | SkillExecutor | NanobotExecutor |
|---|---|---|
| 控制力 | 高 | 取决于 nanobot 暴露能力 |
| 状态机编排 | 强 | 需要适配 |
| MCP 工具调用 | 可通过 LangChain MCP Adapter | nanobot 自身支持或需要适配 |
| Tool Call 记录 | 容易统一 wrapper | 需要 hook / wrapper |
| Actor 插入 | 容易 | 建议通过 session step 接口实现 |
| Judge 集成 | 外部独立 | 外部独立 |
| 复现性控制 | 强 | 需要控制 memory / context |
| MVP 实现风险 | 低到中 | 中，取决于 nanobot 嵌入能力 |
| 后续扩展 | 强 | 适合作为对比 Runtime |

---

## 19. 推荐落地策略

### 19.1 如果要快速跑通 MVP

优先实现：

```text
SkillExecutor
```

原因：

```text
1. 控制力强。
2. 与 Test Runner 状态机更自然。
3. Tool Call 记录更容易统一。
4. Actor / Judge 分离更清晰。
```

---

### 19.2 如果团队希望验证 nanobot

同时保留接口：

```text
Executor Interface
```

然后实现一个最小版：

```text
NanobotExecutor
```

初版 NanobotExecutor 只需要支持：

```text
1. create_session
2. send_message
3. close_session
4. 注入 skill_prompt
5. 注册 MCP tools
6. 返回 response
7. 返回 tool_calls
```

如果 tool_calls 暂时不好拿，则第一阶段可以先返回对话和最终结果，但要标记：

```text
Tool-level assertions may be limited in NanobotExecutor mode.
```

---

### 19.3 最终推荐

```text
GeoSkillBench Core 独立实现
Executor 可插拔
SkillExecutor 作为默认
NanobotExecutor 作为可选
```

---

## 20. 对主文档的建议修改点

如果后续更新主设计文档，建议将原来的：

```text
AgentBackend
```

统一改为：

```text
Executor
```

并将结构调整为：

```text
Executor Interface
  ├── SkillExecutor
  ├── NanobotExecutor
  ├── AgentXExecutor
  └── CustomExecutor
```

同时在 Scenario / RunConfig 中加入：

```yaml
runtime:
  executor: skill
```

或者：

```yaml
runtime:
  executor: nanobot
```

---

## 21. 总结

本补充设计的核心结论是：

```text
nanobot 可以作为 GeoSkillBench 中的 Executor。
```

但推荐边界是：

```text
GeoSkillBench：
  负责测试平台能力，包括 Scenario、数据准备、MCP 检查、Actor、Judge、Assertion、报告和统计。

NanobotExecutor：
  负责被测 Agent 的执行，包括加载 Skill、加载 MCP Tools、执行 prompt、调用工具和返回执行轨迹。
```

这样设计后，GeoSkillBench 不会被 nanobot 绑定死，同时又可以利用 nanobot 作为一个轻量 Agent Runtime。

最终形成：

```text
Test Runner 控制测试流程
Executor 执行被测 Skill
Actor 模拟交互
Judge 评价表现
Assertion 校验事实
Report 输出结果
```

这是一种清晰、可扩展、可替换的架构。
