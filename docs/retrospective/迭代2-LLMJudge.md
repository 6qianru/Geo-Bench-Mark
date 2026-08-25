# 迭代 2 复盘：LLM Judge

> 日期：2026-08-13 · 状态：**代码完成、mock 验证通过、真实 LLM 待验证**
> 对应计划：`../plan/迭代2-LLMJudge.md`

## 1. 迭代目标

把 judge 从"规则判定（启发式扣分）"升级为"**LLM 为主、显式降级**"：`judge.enabled=true` 时优先用本地 LLM 对智能体表现打分并给出 reason/issues/suggestions；LLM 不可用时显式降级回规则判定，全程在报告/前端标注所用判定模式。

## 2. 完成内容

| 项 | 说明 |
|---|---|
| `models/scenario.py` | `judge_model` 默认改 `""`（空=跟随 `agent_model`）；`JudgeConfig.include_conversation` |
| `runtime/llm_judge.py` | **新增**。输入裁剪、严格 JSON prompt、宽松解析+重试、两套默认 rubric、`LlmJudgeUnavailable` |
| `runtime/judge_runtime.py` | LLM 优先 → 显式降级 → error 三级流程；`_rule_judge` 保留按场景类型分支 |
| `models/result.py` | `JudgeResult.judge_mode`（迭代 1 后期已落地）、`model` 字段 |
| `runner.py` | `emit("judge")` 补 `judge_mode`、`model` |
| `reports/report_generator.py` | 完整 `## Judge` 小节 |
| 前端 `App.jsx` | Judge 面板（迭代 1 后期已落地，本轮零改动） |
| `scenarios/buffer_*`、`vector_*` | 删除旧 `judge_model: rule-based-judge` 残留 |
| `../plan/迭代2-LLMJudge.md` | 决策 5 修订为显式降级，全部落地标注 |

## 3. 关键设计决策及理由（重点）

### 3.1 LLM 为主、显式降级（最重要决策，修订了原计划）

原计划决策 5 是"LLM 失败直接 fail，不回退规则 judge"。实施前发现计划写作时没有的情况：**存量 skill 场景（buffer/vector）`agent_model` 仍是 `rule-based-agent` 占位**，严格 fail 会让这些场景瞬间跑不了 judge（断崖）。

**修订决策**：LLM 为主、**显式降级**——
- LLM 可用（`judge_model or agent_model` 是真实模型且调用成功）→ `judge_mode="llm"`
- LLM 不可用（`rule-based-*` 开头 / models.yaml 无别名 / 构建失败 / 调用异常 / 解析失败重试后仍失败）→ 降级到规则分支（`rule-skill`/`rule-agent`），**issues 必须注明降级原因**，非静默成功

**为什么这是对的选择**：
1. **不静默**：降级不是"静默兜底"，降级原因进 issues、`judge_mode` 进报告/前端，审查者看到 `rule-skill` + "LLM judge 不可用：未配置真实 judge 模型"就明白为什么没走 LLM。
2. **不断崖**：存量 skill 场景不因升级而废，平滑过渡；真机配好模型后自动切回 LLM。
3. **成本可控**：规则分支是已落地的代码，降级路径零新增复杂度。
4. **哲学一致但更务实**：与迭代 1 复盘"评测平台不该静默成功"一致，但"显式降级"比"直接 fail"更适配有存量资产的项目状态。

### 3.2 judge_mode 值域扩展：rule-skill / rule-agent 拆分

迭代 1 后期落地时把 `judge_mode` 拆成 `rule-skill`（完整规则，含句柄/CRS 契约扣分）和 `rule-agent`（宽松规则，跳过契约扣分）。起因：**句柄/CRS 契约扣分是为内部 skill 评测设计**的（检查最终回答是否含平台内部结果数据集句柄、坐标参考系），外部黑盒 agent 的最终回答不可能用 `dataset://...` 措辞，会被莫名扣 0.15 分。agent 场景开 judge 后必须跳过这两条，`rule-agent` 因此诞生。

这个拆分后来成了迭代 2 降级路径的天然底座——LLM 不可用时规则分支直接复用，`judge_mode` 全程可见，审查透明。

### 3.3 宽松 JSON 解析 + 重试一次

- prompt 要求"只输出一个 JSON 对象"；解析先整体 `json.loads`，失败则正则截取第一个 `{...}` 再 parse；再失败重试一次。
- **不依赖 `response_format` / function calling**：deepseek 端点的结构化输出能力未确认，宽松解析在任意 OpenAI-compatible 端点上可跑。计划里明确"确认支持后可加 `response_format` 作增强，不动主链路"。
- `score` 必须是 0~1 float，否则视为解析失败走重试/降级——防止 LLM 回 `"score": "high"` 这类垃圾进评分。

### 3.4 include_conversation 默认关闭

默认只喂最终回答 + 工具调用摘要 + 断言结果 + user_task + rubric，控制 token 与噪声。`include_conversation=true` 时才追加对话（最后 10 条、每条 500 字）。计划决策 3，未改。

### 3.5 惰性 import 沿用惯例

`langchain_core.messages` 在 `_build_messages` 内导入，与 `SkillExecutor`/`OrchestratorExecutor` 一致。

### 3.6 前端零改动背后的铺垫价值

前端 Judge 面板和 `judge_mode` 字段在迭代 1 后期提前落地（为"网页上看 judge"做的），迭代 2 主体落地时前端**一行没改**。前置铺垫让大迭代拆成了"展示先行、判定随后"，两次交付互不阻塞。

## 4. 验证结果

- ✅ 8 项 mock 验证全过：disabled 回归 / skill 无模型降级 / LLM 正常 / 解析鲁棒 / 重试 / score 非法 / LLM 调用异常 / include_conversation
- ✅ 端到端：buffer 完整 run，报告显示 `Mode: rule-skill` + 降级说明（"未配置真实 judge 模型（judge_model/agent_model = rule-based-agent）"）
- ✅ 4 个场景 validate 全过（含删 judge_model 残留后）
- ✅ 前端 `npm run build` 通过

## 5. 已知风险与未验证项

- **真实 LLM 调用未测**：mock 验证了逻辑，但 deepseek-v4-flash 实际对 JSON 输出的遵循度、中文评分质量未真机评估。需配 `DEEPSEEK_API_KEY` 后跑一个 `agent_model=deepseek-v4-flash` 的场景。
- **降级路径与迭代 1 orchestrator 联调未做**：orchestrator 场景（agent_test）真机 + LLM judge 同时上线，两者要一起验。
- **LLM 判定质量未调优**：默认 rubric 是初版，真机看 score/reason 是否合理，必要时调 prompt/rubric。
- **`include_conversation=true` 的 token 开销未实测**。

## 6. 过程经验与教训

- **计划要随项目状态修订**：原计划"直接 fail"写于存量场景未定型时，实施前核对发现会断崖，改成显式降级。设计文档是活文档，关键决策变更要回写进计划（本次修订决策 5 并标注日期）。
- **降级要"显式"而非"静默"**：任何 fallback 都必须让审查者一眼看到——本次用 `judge_mode` + issues 降级原因双保险。静默兜底比报错更危险。
- **提前铺垫大迭代的地基**：迭代 1 后期顺手做了 judge_mode 字段和前端面板，迭代 2 主体落地时前端零改动。小额前置工作大幅降低后续迭代耦合。
- **残留旧默认值要清理**：buffer/vector YAML 里显式写死 `judge_model: rule-based-judge`（旧默认），默认值改空后成为误导残留，删除后统一走 agent_model。

## 7. 下一步

1. 配 `DEEPSEEK_API_KEY`，真机验证迭代 1 orchestrator + 迭代 2 LLM judge（一并联调）。
2. 评估 LLM 判定质量（score/reason/issues 合理性），必要时调 rubric/prompt。
3. 迭代 1 复盘里"harness 方差量化"与本次"LLM 判定稳定性"一并评估。
4. 迭代 3（网页端图形化场景创建）评估。
