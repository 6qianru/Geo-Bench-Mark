# 待定企划：真机标定批次（harness 方差量化 + LLM Judge 质量评估）

> 状态：**待定企划（2026-08-25 立项，同日降级为待定——迭代 5 编号让位给"MCP 全面服务化 + DB 数据面"，本项近期不排期）**。原文件名 `迭代5-真机标定批次-方差量化与Judge质量.md`，仅改名未改内容。
> 本迭代是评测/标定工作，原则上不改产品代码（除非结论明确要求，另立迭代）。
> 来源：`../README.md` 待办两项——"harness 方差量化（迭代 1 遗留）"与"真机验证收尾：LLM judge 质量评估（迭代 2 遗留）"。两者共用同一批真机运行数据，合并一个批次执行。
> 关联背景：反问闭环下沉重构后（见 `../retrospective/反问闭环下沉重构.md` §5），模拟用户（规则 vs LLM persona）成为新的方差源，两个实验都需要控制这个变量。

## 0. 为什么合并成一个批次

- 方差量化需要"同一场景连跑 N 次"；每次运行的产物（外部指令序列、断言结果、judge 得分、对话）恰好也是 judge 质量分析的输入样本。
- 分开立项会烧两遍 token、配两遍环境。
- orchestrator 真机已于 2026-08-19 跑通（deepseek-v4-flash 多轮指挥 + LLM judge 1.0），具备批量跑的前提。

## 1. 目标与非目标

**目标**：

1. 给出 harness 方差的量化结论：同一场景重复 N 次，指令序列/断言结果/judge 分数的波动有多大？波动来自哪个环节（本地 agent 措辞、外部 agent 表现、模拟用户、judge 本身）？
2. 给出 LLM judge 的质量结论：deepseek-v4-flash 按 rubric 打分与人工判断的一致率、score 区分度、`judge_score_min: 0.8` 这条线是否有依据。
3. 产出可操作的缓解/改进决策：是否需要 seed、首指令固定、多跑聚合；rubric 怎么改、阈值定多少。

**非目标**：

- 不覆盖全部 5 种 executor。聚焦 **orchestrator**（方差问题发源地）与 **skill**（judge 主战场）；external_driven 真机联调是另一件待办，不混入。
- 不在本迭代内实现方差缓解或 judge 改进代码——先把结论拿出来。

## 2. 实验设计 A：harness 方差量化

### 2.1 变量矩阵

| 变量 | 取值 | 说明 |
|---|---|---|
| 场景 | `agent_orchestrated_actor_multi_turn.yml`（基线） | 真机已验证，含模拟用户反问 |
| 重复次数 | 首轮 N=3；若波动明显扩到 N=5~10 | 每次独立 run，独立 session |
| 模拟用户 | 规则回答 一组 / LLM persona 一组（`user_model` 切换） | 两组各自内部比方差，再组间对比 |
| 温度 | temperature=0（现状默认） | 已是缓解措施第一层 |

### 2.2 对比维度（逐 run 提取）

1. `external_interactions` 指令序列：条数、顺序、每条措辞差异（本地 agent 自由式 ReAct 的直接输出）。
2. 外部 agent 回答要点（判断波动是 harness 引入还是 SUT 固有）。
3. 断言通过集合是否一致。
4. judge score 分布（同一对话质量的 run 之间分差多少）。
5. 反问次数与内容（ask_user 开启时）。

分析脚本直接读 `reports/json/*.json` 离线比对即可，不新增 API；后续若要常态化看板再考虑 `/api/runs/aggregates`。

### 2.3 方差可接受判据（跑前预定义，避免事后找补）

- **强稳定**：N 次 run 断言通过集合完全一致，且 judge score 极差 ≤ 0.1 → 可直接单次评分。
- **可接受**：断言通过集合一致，judge score 极差 ≤ 0.2 → 结论可信但需注明波动区间。
- **不可接受**：出现断言通过/失败翻转，或 score 极差 > 0.2 → 启用缓解阶梯。

### 2.4 缓解阶梯（方差超限时按序叠加，每加一层重测一轮）

1. ~~temperature=0~~（已默认生效）。
2. 本地 agent LLM 加 `seed`（OpenAI-compatible `extra_body` 透传，DashScope/DeepSeek 均支持）。
3. 首条外部指令固定为 `user_task` 原文，不让 orchestrator 重新措辞（只影响第一轮，改动小）。
4. 接受残余方差，改为"同场景多跑 N 次取聚合"作为评分口径（多数投票 / 均分），前端与报告标注聚合口径。

## 3. 实验设计 B：LLM Judge 质量评估

### 3.1 人工标注集构建

- 样本来源：批次 A 的全部 run + `reports/` 存量报告（含 mock 全流程与真机 run），挑 10~20 个覆盖面样本：pass/fail 两级、skill 模式与 agent 模式、含反问与不含反问。
- 人工对每个样本给出期望判定（pass/fail + 一句话理由），记录在标注表（CSV 即可）。
- 注意盲评顺序：先标注再看 LLM judge 的原始输出，避免锚定。

### 3.2 评估维度

| 维度 | 指标 | 关注点 |
|---|---|---|
| 准确率 | 与人工标注一致率 | 主指标；低于 80% 说明 judge 不可信 |
| 可用性 | `judge_mode=llm` 成功率（JSON 解析失败/降级占比） | 迭代 2 设计了宽松解析+重试一次，看实战表现 |
| 区分度 | score 分布直方图 | 全挤在 0.9+ 则失去区分能力，`judge_score_min: 0.8` 形同虚设 |
| 阈值合理性 | 不同阈值下 pass/fail 与人工标注的混淆矩阵 | 为 0.8 这条线找依据或替换值 |
| rubric 有效性 | 逐条 rubric 触发情况 | 从未影响的 rubric 删掉；常引发误判的改写法 |

### 3.3 输出

复盘一篇（`retrospective/`）：judge 可信度结论、rubric 修改建议、阈值建议、是否值得引入 `response_format` 结构化输出增强（迭代 2 计划 §3 留的口子）。

## 4. 执行前提

- [ ] `models.yaml` 配好真实别名（当前用 deepseek-v4-flash；qwen-max 需另配 DashScope 别名）。
- [ ] `.env` 密钥就位（`DEEPSEEK_API_KEY` 等；按红线不由助手代填）。
- [ ] 预算预估：批次 A 约 N 组 × 每组（max_turns × 每轮 2~3 次调用）次 LLM 调用 + judge 调用；批次 B 主要是离线分析，仅复跑样本时追加。
- [ ] 跑批期间注意 `GEO_BENCH_HISTORY_KEEP` 默认 100 条足够，不会被自动清理误伤（清理只动 DB 行，报告 JSON 文件不受影响）。

## 5. 交付物

1. `reports/` 下批次原始 run 数据（JSON + markdown）。
2. 标注表 CSV（人工判定 vs LLM judge 对照）。
3. 复盘文档一篇：方差结论（含缓解阶梯走到第几层）+ judge 质量结论 + 后续改进立项建议。
4. `../README.md` 待办勾销对应两项。
