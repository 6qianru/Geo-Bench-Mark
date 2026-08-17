# GeoSkillBench 迭代 3 复盘：orchestrator + 模拟用户 actor 自动多轮

> 日期：2026-08-13 · 状态：**代码完成、mock 验证通过、真实 LLM 待验证**
> 说明：迭代 2 之后实际还做了"可选任务流（agent.flow）"（react 默认 / scripted / 注册表自定义），未单独立复盘；本迭代在其基座上展开。

## 1. 迭代目标

orchestrator 场景（本地 agent 多轮指挥外部 agent）支持**与模拟用户 actor 自动多轮**：本地 agent 缺信息时（外部 agent 反问、缺数据集/参数）向用户追问 → 模拟用户 actor 按 `actor.goal` 自动回答 → agent 拿答案继续指挥 → 最终 `[FINAL]`。

用户通过澄清确认形态："与模拟用户 actor 自动多轮"（而非手动改配置/agent 主动说话），且 v1 只覆盖 **react 流程**（默认）。

## 2. 完成内容

| 项 | 说明 |
|---|---|
| `models/scenario.py` | `AgentConfig.ask_user: bool = False`——追问开关，默认关（存量场景零回归） |
| `executors/orchestrator_executor.py` | 三处：`ask_user=True` 时 prompt 加追问规则（规则 6）；`OrchestratorSessionState.react_messages` 跨轮累积对话；`send_message` 识别 `[NEED_INTERACTION]` → `need_interaction=true, finished=false` |
| `scenarios/agent_orchestrated_actor_multi_turn.yml` | 新场景示例：`ask_user: true` + `actor` 块 + 触发反问的 user_task |
| `docs/Agent接入契约.md` §7.5 | 配置示例、`[NEED_INTERACTION]` 协议、覆盖范围（react 内置 / 非 react 按协议扩展） |
| runner / ActorRuntime / ExecutorSessionRequest / 前端 | **零改动**（通道已就绪，见 §3.1） |

## 3. 关键设计决策及理由（重点）

### 3.1 复用 skill 场景的 `[NEED_INTERACTION]` 协议，不发明新通道（最重要决策）

调查发现三件已经就绪的事，本迭代只是**补上缺失的一环**：
- runner 的 actor 循环早已存在（`runner.py:221-233`：`need_interaction and actor.enabled and turn_index < actor.max_turns` → actor 回复 → 作为下一轮消息回传）；
- skill 场景（`SkillExecutor`）已用 `[NEED_INTERACTION]` 前缀协议触发该循环；
- `Scenario.actor` 字段（`enabled/profile/max_turns/goal`）和 `ActorRuntime.reply`（按关键词从 `actor.goal` 提取数据集/距离/格式）迭代 1 就建好了。

缺的环只在 orchestrator：`send_message` 永远 `finished=true, need_interaction=false`，且 react 的 operator prompt 没教 agent 可以追问。

**决策**：orchestrator 复用同一 `[NEED_INTERACTION]` 前缀协议——不发明新协议、不改 runner、不改 ActorRuntime、不改 request 模型。`[NEED_INTERACTION]`/`[FINAL]` 成为平台两个通用前缀，跨 executor 语义一致。

### 3.2 追问开关走 `AgentConfig.ask_user`，而非 request/runner

`ExecutorSessionRequest` 没有 actor 字段、runner 也不传，executor 侧看不到 `scenario.actor`。若为追问硬传 actor，要动 request 模型 + runner 构造 + 各 executor 契约。

**决策**：本地 agent 是否允许追问是 **agent 的行为配置**，放 `AgentConfig`（与 `flow` 同处），默认 `false`。好处：存量场景零变化；`actor` 块照常在 Scenario 顶层配，runner 直接用。语义分离干净——`ask_user` 决定"本地 agent 会不会问"，`actor` 决定"谁回答、答什么"。

### 3.3 `react_messages` 外部累积替代 checkpointer

`create_react_agent` 每次 `invoke` 是全新图运行（无 checkpointer），追问 actor 后若第二次 invoke 只传 actor 回复，agent 会"失忆"。

**决策**：`OrchestratorSessionState` 存消息列表，每次 invoke 传 `react_messages + [HumanMessage(本轮)]`，invoke 后整体替换（过滤 SystemMessage 防重复）。首次 `[] + [HumanMessage(user_task)]` 与现状完全等价 → 单轮场景零回归；追问后第二轮 agent 带着完整对话历史继续。不引入 checkpointer，成本最低。

### 3.4 追问识别仅对 `react + ask_user=true` 生效

非 react 流程（scripted / pipeline / keyword / 自定义）v1 不识别 `[NEED_INTERACTION]`，`send_message` 非 react 分支零改动。用户明确拍板仅覆盖 react。原因：非 react 流程的节点输出是"指令"不是"追问"，要支持需要给每个 flow 加追问节点/改输出 schema，侵入大；协议层已留好（`final_response` 以 `[NEED_INTERACTION]` 开头即可进 actor 循环），后续按需扩展。

## 4. 验证结果

- ✅ **actor 多轮 mock 12 项全过**：新场景 schema（`ask_user`/`actor`）、prompt 规则随开关增删、追问往返（第一轮 `need_interaction=true, finished=false` 且**不发外部指令** → actor 答 → 第二轮发指令 → `[FINAL]`）、`react_messages` 累积（追问后 2 条 / 完成后 6 条完整历史）、`ActorRuntime` 从 goal 答数据集、`ask_user=false` 时即使 agent 输出 `[NEED_INTERACTION]` 也按 finished 处理
- ✅ **零回归**：keyword/pipeline 4 项、react/scripted/unknown-flow 9 项全过（含 react `external_interactions` 结构不变）
- ✅ **validate**：新场景 schema 通过
- ✅ 全程 mock，不触发真实 API

## 5. 已知风险与未验证项

- **真实 LLM 是否按规则输出 `[NEED_INTERACTION]` 未测**：mock 假 LLM 严格输出前缀，但 deepseek-v4-flash 实际对"信息不足就追问而非硬编"的遵循度未知。需配 `DEEPSEEK_API_KEY` 真机跑一次。
- **真实外部 agent 是否真反问未测**：外部 agent 行为不可控，若它不反问、直接猜着做，actor 就没有出场机会（本迭代只建了通道，不保证触发）。
- **actor 回答质量依赖 goal 措辞 + 关键词匹配**：`ActorRuntime` 只认"哪个数据/缓冲距离/输出格式"几类问法，本地 agent 换个问法就落到兜底"请按场景目标继续执行"。必要时扩展 ActorRuntime 或约束 prompt 的问法。
- **追问消耗 runner 轮次**：每次追问占一个 `runtime.max_turns` 轮次（但不消耗外部指令数）；max_turns 偏小时多轮追问会挤占指令预算。文档已注明。

## 6. 过程经验与教训

- **"如何配置"背后常是"通道还没打通"**：用户问配置时，真实现状是 orchestrator 从未返回 `need_interaction`——先澄清"要哪种多轮"、再摸清通道缺失，比直接教配置正确得多。本次 AskUserQuestion 澄清（actor 自动多轮 + 仅 react）一次到位。
- **先找已有的路，再决定修不修新路**：协议、runner 循环、actor 字段全已存在，本次只补 orchestrator 一个环，runner/ActorRuntime/前端零改动。迭代成本大幅低于"自建 actor 体系"。
- **默认关闭是零回归最廉价的护栏**：`ask_user=false` 让存量场景行为不变，验证脚本可原样复用，不用改任何历史断言。
- **状态断言要打在正确的时序点**：mock 脚本里 `state` 引用指向 session 对象，在第二轮后检查第一轮断言，读到的是最终状态（`interactions=1` 而非 0），一度误报 FAIL。代码没问题，是断言时机错了——对会话内可变状态做断言，要在状态被后续步骤改写之前做，或先快照。
- **react 的 model mock 收到的是 messages 列表而非 dict**：`create_react_agent` 内部 `model.invoke(model_input)` 传的是消息列表，首次 mock 报 `TypeError: list indices must be integers`。mock 兼容 dict/列表两种形态即可。

## 7. 下一步

1. 真机验证：配 `DEEPSEEK_API_KEY` + 外部 agent 跑 `agent_orchestrated_actor_multi_turn`，确认"agent 追问 → actor 回答 → 继续指挥"真实链路。
2. 视真机结果扩展 `ActorRuntime` 关键词匹配或约束 agent 追问问法。
3. 非 react 流程的 actor 多轮（按 §7.5 协议扩展）视需求评估。
4. 前端 Task History 已含 `actor_reply`（走 conversation），确认展示效果即可。
