# GeoSkillBench 迭代 2：LLM Judge 实现计划

> 状态：**已实施（2026-08-13）**。LLM judge 主体 + 降级路径 + 前端/报告展示全部落地，mock 验证通过；真实 LLM 调用待配 `DEEPSEEK_API_KEY` 后真机验证。依赖**迭代 1（智能体多轮接入）**真机验证后一并联调。
> 对应"让项目 agent 评估并返回评价"的迭代项。现状评估见 `docs/GeoSkillBench-Evaluation平台改造计划.md` 与 `docs/GeoSkillBench-Executor与Nanobot接入补充设计.md`。

## 0. 设计决策（已确认）

| # | 问题 | 决策 |
|---|---|---|
| 1 | judge 模型 | 与 agent 同模型：`judge_model` 为空时跟随 `agent_model` |
| 2 | 结构化输出方式 | 实现方定：prompt 要求严格 JSON + 宽松解析 + 重试一次；不依赖 response_format / function calling |
| 3 | judge 输入范围 | scenario 文件加变量 `judge.include_conversation`，默认只喂 最终回答 + 工具调用 + 断言结果 |
| 4 | rubric 为空时的默认 | 实现方定，不严：`agent_skill_test` 与 `agent_test` 各一套轻量默认 |
| 5 | LLM 失败处理 | **修订（2026-08-13）**：LLM 为主、**显式降级**。LLM 可用 → `judge_mode="llm"`；LLM 不可用（未配真实模型 / 构建失败 / 调用异常）→ 降级到规则判定（`judge_mode=rule-skill/rule-agent`），issues 注明降级原因，非静默成功。原"直接 fail 不回退"因存量 skill 场景（buffer/vector）`agent_model` 仍是 `rule-based-agent` 占位会断崖，改为显式降级 |
| 6 | 前端 judge 面板 + markdown 报告增强 | 纳入本迭代范围 |

## 1. 行为变更（重要，先读）

- `RuntimeConfig.judge_model` 默认值 `"rule-based-judge"` → `""`（空 = 跟随 `agent_model`）。
- 移除 `judge_runtime.py` 的关键词扣分逻辑；`judge.enabled=true` 且未配真实模型时 → judge 直接 fail + 报错（不再静默用启发式打分）。
- 影响面：
  - `scenarios/agent_external_sqlQBQP.yml`（judge.enabled=false）不受影响。
  - `scenarios/buffer_school_500m_001.yml` 等 judge.enabled=true 的场景，跑前把 `agent_model` 指向 `models.yaml` 里的真实别名（如 `deepseek-v4-flash`）即可得 **LLM 判定**；未配真实模型时**显式降级**为规则判定（judge_mode=rule-skill/rule-agent，报告可见），不失败。

## 2. Schema 改动（`models/scenario.py`）

```python
class RuntimeConfig(BaseModel):
    # ...
    judge_model: str = ""   # 原默认 "rule-based-judge" 改为 ""（空 = 跟随 agent_model）

class JudgeConfig(BaseModel):
    enabled: bool = True
    rubric: list[str] = Field(default_factory=list)
    include_conversation: bool = False  # 默认只喂 最终回答+工具调用；true 时追加对话（截断）
```

## 3. 新增 LLM judge 模块（`geoskillbench/runtime/llm_judge.py`）

职责：把"执行记录 + rubric"发给 LLM，返回结构化 `JudgeResult`。主逻辑：

- 模型解析：`judge_model = scenario.runtime.judge_model or scenario.runtime.agent_model`；以 `rule-based-` 开头 / models.yaml 无此别名 / LLM 调用抛异常 → **显式降级**：返回规则判定结果（judge_mode=rule-skill/rule-agent，沿用 §4 已落地分支），issues 注明降级原因，非静默成功。
- 调用：`build_llm(judge_model, temperature=0, config=load_models_config())`，`invoke([SystemMessage, HumanMessage])`。
- 输出方式：prompt 要求"只输出一个 JSON 对象"；宽松解析——先 `json.loads`，失败则正则截取第一个 `{...}` 再 parse；解析失败重试一次；再失败 → judge fail + 报错。不依赖 `response_format` / function calling（模型能力未确认，此路在任意 OpenAI-compatible 端点上可跑；确认支持后可加 `response_format` 作增强，不动主链路）。
- 输入裁剪：默认只喂 `final_response`（全文）+ `tool_calls`（压缩为 `tool_name` + arguments 摘要）+ `assertion_result.items` + `scenario.user_task` + rubric；`include_conversation=true` 时追加 `conversation`，截断策略：最后 10 条消息、每条截断 500 字。
- 默认 rubric（rubric 为空时）：
  - `agent_skill_test`：目标是否达成 / 工具调用是否贴合任务 / 最终回答是否包含结果信息 / 是否遵循 skill 约束
  - `agent_test`：目标是否达成 / 回答是否具体可执行 / 是否明确给出结果或结论 / 多轮中是否主动补齐必要信息
  - scenario 写了 `judge.rubric` 则优先用它。
- JSON 校验：`score` 必须是 0~1 的 float，否则视为解析失败走 fail。

## 4. 改造 `JudgeEngine`（`runtime/judge_runtime.py`）

- 关键词扣分逻辑（"结果数据/句柄/CRS"那套）**保留**，作为规则降级路径（rule-skill/rule-agent 分支，迭代 1 后期已落地）。
- 新流程：
  1. `judge.enabled=false` → 透传断言分（保持现状）。
  2. 否则先尝试 LLM judge：`judge_model or agent_model` 能构建 LLM 且调用成功 → `judge_mode="llm"`，`passed = score >= judge_score_min and assertion_result.passed`。
  3. LLM 不可用（未配真实模型 / 构建失败 / 调用异常 / 解析失败重试仍失败）→ **显式降级**到规则分支（judge_mode=rule-skill/rule-agent），issues 注明 `LLM judge 不可用：<原因>，已降级为规则判定`。
  4. 规则分支本身再抛异常 → `JudgeResult(judge_mode="error", ...)`，调用 `recorder.record_error()` 写入错误列表。

## 5. `JudgeResult` 扩展（`models/result.py`）

```python
class JudgeResult(BaseModel):
    score: float = 0.0
    passed: bool = False
    reason: str = ""
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    # ✅ 已落地（迭代 1 后期）：值域 llm/rule-skill/rule-agent/disabled/error，默认 rule-skill，老数据不崩
    judge_mode: Literal["llm", "rule-skill", "rule-agent", "disabled", "error"] = "rule-skill"
    model: str = ""                                                    # 待加：LLM 判定时记录所用模型
```

## 6. `runner.py`

基本不动（`evaluate` 已拿到整个 scenario）。`emit("judge", ...)` 事件补带 `judge_mode` 与 `model`。

## 7. 前端（`frontend/src/App.jsx`）

`ResultDetail` 增加 **Judge** 区块，渲染 `result.judge`：

- 顶部：`score`（0-1 显示为百分比）+ `passed` pill + `judge_mode` 标签 + 所用 `model`
- 正文：`reason`、`issues`（红色列表）、`suggestions`（提示列表）
- ✅ **已落地**（迭代 1 后期）：面板 + mode 标签（LLM 判定 / 规则判定·skill契约 / 规则判定·宽松 / 已禁用 / 判定错误）。迭代 2 补 `model` 行。

## 8. Markdown 报告增强（`reports/report_generator.py`）

`## Judge` 小节：mode、model、score、reason、issues、suggestions 各一行（✅ mode 行已加；补 model/reason/issues/suggestions）。

## 9. 测试

- 回归：`agent_external_sqlQBQP.yml`（judge.enabled=false）→ judge 透传不变。
- LLM 正常：`buffer_school_500m_001.yml` 配 `agent_model=deepseek-v4-flash`，看 score/reason/issues/suggestions 是否合理。
- 解析鲁棒：mock LLM 返回带前后噪声的 JSON → 宽松解析能抽出。
- 失败路径：模型不配 → 显式降级 rule-skill/rule-agent（issues 注明原因）；返回非法 JSON / 调用抛异常 → 降级；规则分支本身异常 → judge_mode=error + 报错入 errors。
- 前端：手点一次跑通，确认 Judge 面板渲染。

## 10. 明确不做（本迭代边界）

- 不做 `judge.strategy` 选项（暂无"规则 judge"可选项；如需轻量评分通道以后再加）。
- 不做 LLM 对断言本身的重新评判（断言仍由规则引擎判，LLM 只看质量分）。
- 不引入 judge 单独 prompt 模板文件（模板先硬编码在 `llm_judge.py`，稳定后再外置）。
