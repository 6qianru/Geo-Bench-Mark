# 迭代 1 复盘：orchestrator 本地 agent 多轮指挥外部 agent

> 日期：2026-08-13 · 状态：**代码完成、单元/错误路径验证通过、真机待验证**
> 对应计划：`../plan/迭代1-orchestrator多轮指挥外部agent.md`

## 1. 迭代目标

把 scenario 的 `user_task` 从"直接丢给外部 agent 一问一答"改成：**本项目 agent（LangGraph）作为操作者，自主向外部 agent 逐条发指令、读取响应决定下一步、多轮对话直到目标完成**，模拟人类操作。

## 2. 完成内容

| 项 | 说明 |
|---|---|
| `models/scenario.py` | `AgentConfig.description`（外部 agent 能力说明，喂给 orchestrator 提示词） |
| `recorder/execution_recorder.py` | `external_interactions` + `record_external_interaction()` |
| `executors/orchestrator_executor.py` | **新增** `OrchestratorExecutor`：prebuilt ReAct + `ask_external_agent` 工具，内部复用 `HttpAgentExecutor` |
| `executors/factory.py` | 注册 `orchestrator` |
| `runner.py` | `final_output` 带 `external_interactions` |
| `reports/report_generator.py` | markdown 加 External Agent Interactions 小节 |
| `frontend/src/App.jsx` | ResultDetail 加外部交互展示区 |
| `scenarios/agent_orchestrated_sqlQBQP.yml` | 示例场景（复用 `agent_external_sqlQBQP.yml` 的 endpoint） |
| `docs/design/01-Agent接入契约.md` | 补第 7 节 orchestrator 模式 |
| `docs/plan/迭代1-orchestrator多轮指挥外部agent.md` | 实现计划（已实施） |
| `../plan/迭代2-LLMJudge.md` | 迭代 2 计划（待迭代 1 真机验证后实施） |

## 3. 关键设计决策及理由（重点）

### 3.1 为什么选自由式 ReAct 而非脚本化流程（重点记录）

1. **贴合"模拟人类操作"的评测目标**。真实人类操作者不照脚本走，而是根据外部 agent 的每一轮回答临场决定下一步。自由式 ReAct 的"推理 → 调工具 → 看结果 → 再推理"循环天然就是这个行为；脚本化流程（plan → 逐条照做 → verify → final）更像死流程，偏离评测意图。
2. **复用代码库已验证的模式**。`SkillExecutor` 早已用 `create_react_agent`（prebuilt ReAct）跑通 skill 评测，自由式直接复用该模式，零新架构、零风险试点。
3. **少写代码、快速跑通**。自由式只需"一个工具 + 一段系统提示词"，多轮循环由 ReAct 内部完成；脚本化要手写 `StateGraph` node/edge/条件路由，量大且偏离目标。
4. **外部 SUT 行为未知，脚本化过早固化**。外部 agent 会怎么回答、要不要反问、缺什么参数，真机跑之前全是未知。自由式能适应任意响应形状；脚本化在 SUT 行为摸清前极易写死、频繁返工。
5. **终止判定有自然承载**。`[FINAL]` 协议由本地 agent 发出，自由式下它天然在"判断目标达成"时发，配合 `max_turns` 硬兜底即可收敛。

**付出的代价（已知）**：harness 方差——每次 run 本地 LLM 给外部 agent 的指令措辞/顺序可能不同，影响 SUT 对比的可复现性。缓解方案（seed、首条指令固定、多跑聚合）已讨论并暂缓，见 `../plan/迭代1-orchestrator多轮指挥外部agent.md` 与记忆 `orchestrator-freeform-variance`。

### 3.2 其他关键取舍

- **绕开 langgraph 的"麻烦"**：用 prebuilt `create_react_agent`，不手写状态机/checkpointer。评测场景不需要持久化（每轮全新会话、可复现优先），这是省掉大量配置的正当理由。
- **`http_agent` 直接模式保留**：orchestrator 作为新 executor 注册，旧场景零回归。
- **无启发式兜底**：orchestrator 缺真实模型/缺 endpoint/缺依赖 → 直接 fail + 明确报错。与迭代 2 LLM judge 的"LLM 失败直接 fail 并报错"哲学一致：评测平台不该在没真正执行时静默成功。
- **惰性 import 沿用惯例**：langgraph 在函数内 import，与 `SkillExecutor` 一致；代价是文件顶部看不到，用户提出过，已解释。

## 4. 验证结果

- ✅ 新旧场景 `validate` 通过（schema 兼容，无回归）
- ✅ orchestrator 错误路径：缺 endpoint / `rule-based` 模型 / 空模型 / 无 models.yaml 别名 → 全部明确报错
- ✅ 全链路 error 路径：无 models.yaml 时 `RUN_AGENT=FAILED`、错误入 `errors`、不崩
- ✅ 前端 `npm run build` 编译通过

## 5. 已知风险与未验证项

- **真机未跑**：缺 `models.yaml`（deepseek-v4-flash）+ `DEEPSEEK_API_KEY`（按红线未代填 `.env`），外部 endpoint 可达性未探测。
- **orchestrator 终止质量未验证**：是否过早 `[FINAL]` / 无限追问，是计划里标记的最大风险，需真机跑数轮收敛提示词。
- **harness 方差未量化**：需同场景连跑 2~3 次对比 `external_interactions` 再决定是否上缓解手段。

## 6. 过程经验与教训

- **先复用再新增**：HTTP 解析、外部 session、ReAct 循环全部是现成资产，新代码只集中在提示词与工具桥接，这是本轮"代码量小"的原因。
- **错误处理要显式**：评测平台"配置缺失静默成功"比"报错失败"更危险；本轮与迭代 2 都采用 fail-with-error 哲学。
- **设计文档先行有效**：迭代 1 计划文档里把流程规则（5 条提示词规则）写清楚、用户确认后再写码，避免实现期返工。

## 7. 下一步

1. 配 `models.yaml` + `DEEPSEEK_API_KEY`，真机跑 `scenarios/agent_orchestrated_sqlQBQP.yml`，验证指挥效果与终止质量。
2. 连跑 2~3 次量化 harness 方差，决定是否实施缓解（seed / 首条指令固定 / 多跑聚合）。
3. 真机验证通过后，启动迭代 2（LLM Judge，计划已定稿）。
