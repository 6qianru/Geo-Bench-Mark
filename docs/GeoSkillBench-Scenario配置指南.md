# GeoSkillBench Scenario 配置指南

> 一份 yml 文件 = 一次评测场景。本文说明 scenario 文件的字段、两种模式、executor/flow 选择、actor 配置与断言写法。
> 配套：模型定义见 `geoskillbench/models/scenario.py`；前端可用"新建 Scenario"表单（覆盖常用字段），复杂配置直接改 yml。

## 1. 概述

| 维度 | 说明 |
|---|---|
| 文件位置 | `scenarios/*.yml`（相对路径引用其他资源：`../skills/...`、`../fixtures/...`） |
| 两种模式 | `type: agent_skill_test`（**skill 模式**，测本地技能）/ `type: agent_test`（**agent 模式**，测外部智能体） |
| 两种编辑方式 | 前端表单（常用字段，保存为 `scenarios/<id>.yml`）/ 手写 yml（全量字段） |
| 校验与运行 | `python -m geoskillbench.cli validate scenarios/xx.yml` / 前端 Validate+Create Task |

**模式决定配置块**：skill 模式需要 `skill` + `data.fixtures`（数据集）；agent 模式需要 `agent` + `actor`。公共块两种模式都有。

## 2. 文件结构总览

```yaml
id: 场景唯一标识（也是文件名）
name: 场景名
version: 版本          # 必填
type: agent_skill_test | agent_test   # 默认 agent_skill_test
description: 描述
target: { skill_id, skill_version }   # 可选，agent 模式通常 target: {}
runtime:              # 公共
  executor: skill     # skill | orchestrator | external_driven | http_agent | nanobot
  agent_model: rule-based-agent   # 本地 agent 模型（models.yaml 别名）
  max_turns: 6
  timeout_seconds: 180
data:                 # 仅 skill 模式
  fixtures: [ ... ]
skill:                # 仅 skill 模式
  path, load_mode, ...
agent:                # 仅 agent 模式
  endpoint, flow, ask_user, ...
actor:                # 仅 agent 模式（模拟用户）
  enabled, max_turns, goal
mcp:                  # 公共（agent_test 可空）
  servers: [ ... ]
  tools: { required, optional }
judge:                # 公共
  enabled, rubric, include_conversation
expected_behavior:    # 公共（skill 模式常用）
  should_load_skills, should_call_tools, optional_tools, should_not
assertions: [ ... ]   # 公共
pass_criteria:        # 公共
  required_assertions_passed, judge_score_min
user_task: 用户任务   # 必填
```

## 3. 公共字段

### 3.1 顶层

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `id` | ✅ | — | 唯一标识，同时是文件名（字母/数字/下划线/中划线） |
| `name` | ✅ | — | 场景名 |
| `version` | ✅ | — | 版本号 |
| `type` | — | `agent_skill_test` | 评测模式，决定后面配置块 |
| `description` | — | `""` | 描述 |
| `target` | ✅（块） | — | `{skill_id, skill_version}`；agent 模式写 `target: {}` |
| `user_task` | ✅ | — | 发给智能体的任务描述（agent 模式会被 orchestrator 本地 agent 当第一轮输入） |

### 3.2 `runtime`（执行与模型）

| 字段 | 默认 | 说明 |
|---|---|---|
| `executor` | `skill` | 见 §4 executor 选择 |
| `agent_model` | `rule-based-agent` | 本地 agent 模型（models.yaml 别名）；`rule-based-*` 走启发式兜底 |
| `actor_model` | `rule-based-actor` | 模拟用户模型；当前 ActorRuntime 纯规则，预留 |
| `judge_model` | `""` | 空 = 跟随 agent_model；配 `rule-based-*` 显式降级规则判定 |
| `max_turns` | `6` | runner 总轮次上限；orchestrator 场景 = 最多发外部指令数 |
| `timeout_seconds` | `180` | 单步超时 |
| `memory_enabled` | `false` | 是否启用会话记忆 |

### 3.3 `judge` / `pass_criteria`（评测判定）

| 字段 | 默认 | 说明 |
|---|---|---|
| `judge.enabled` | `true` | 是否跑 judge（LLM 优先，缺模型自动降级规则判定） |
| `judge.rubric` | `[]` | LLM judge 的评分细则（逐条 rubric） |
| `judge.include_conversation` | `false` | true 时 LLM judge 额外喂完整对话（截断） |
| `judge.penalize_no_ask_back` | `false` | external_driven：外部 agent 缺必要信息不反问 → 连续扣分 |
| `pass_criteria.required_assertions_passed` | `true` | 断言全过才判通过 |
| `pass_criteria.judge_score_min` | `0.8` | judge 得分下限 |

### 3.4 `expected_behavior`（预期行为，skill 模式常用）

```yaml
expected_behavior:
  should_load_skills: [gis_buffer_analysis]   # 期望加载的技能
  should_call_tools: [query_dataset_metadata, create_buffer]
  optional_tools: [reproject_dataset]          # 可选调用
  should_not: ["在缺少输入数据时直接执行"]       # 禁止行为（供 LLM judge 参考）
```

### 3.5 `mcp`（MCP 工具服务器）

```yaml
mcp:
  servers:
    - id: gpa_vector
      name: GPA 矢量分析服务
      transport: mock          # mock | http | sse 等
      url: mock://vector
      required: true
  tools:
    required: [{ server: metadata, name: query_dataset_metadata }]
    optional: [{ server: gpa_vector, name: reproject_dataset }]
```

agent 模式通常 `mcp: { servers: [], tools: { required: [], optional: [] } }`（或省略）。

## 4. `runtime.executor` 选择

| executor | 适用 | 角色 |
|---|---|---|
| `skill` | skill 模式（默认） | 本地 agent 用技能评测，可走 MCP 工具；历史别名 `langgraph` |
| `orchestrator` | agent 模式 | 本地 LLM agent **多轮指挥**外部 agent（拆解→发指令→读回答→决定下一步） |
| `external_driven` | agent 模式 | 角色反转：外部 agent 主导自主执行，缺信息反问；平台 LLM 扮演模拟用户回答 |
| `http_agent` | agent 模式 | user_task **直接透传**外部 agent，一问一答（外部 agent 足够聪明时用） |
| `nanobot` | 兼容 | nanobot 运行时契约（当前兼容模式） |

> 判断：外部 agent 全自动执行 → `http_agent`；需要本地拆解/指挥/追问 → `orchestrator` 或 `external_driven`。

## 5. skill 模式专属字段（`type: agent_skill_test`）

### 5.1 `skill`

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `path` | ✅ | — | 相对 `scenarios/`，如 `../skills/gis_buffer_analysis.skill.yml` |
| `load_mode` | — | `file` | `file`（单文件）/ `package`（技能包目录）/ `package_zip` |
| `entry` | — | — | 入口文件（package 模式） |
| `lazy_load_references` | — | `false` | 引用按需加载（配合 `skill_reference_*` 断言） |
| `required` | — | `true` | 加载失败是否判失败 |

### 5.2 `data.fixtures`（数据集注册）

```yaml
data:
  fixtures:
    - id: schools              # 数据集标识（工具/断言里用这个名字）
      name: 学校点数据
      type: vector             # vector | raster
      format: geojson          # geojson | shapefile | geopackage | csv
      path: ../fixtures/schools.geojson
      crs: EPSG:4326
      geometry_type: Point
      # import_as / register_metadata / cleanup 有默认值，可省略
```

## 6. agent 模式专属字段（`type: agent_test`）

### 6.1 `agent`（外部智能体接入）

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `endpoint` | ✅ | — | 外部 agent HTTP 接口 |
| `description` | — | `""` | 能力说明，喂给 orchestrator 提示词（决定发什么指令、何时算达成） |
| `flow` | — | `react` | orchestrator 本地 agent 的流程，见 §7 |
| `ask_user` | — | `false` | 缺信息时是否允许向用户（actor）追问（react 流程内置） |
| `timeout_seconds` | — | `120` | 请求超时 |
| `type` | — | `http` | 接入类型 |
| `api_key_env` | — | — | 请求头鉴权环境变量名 |
| `stream_response` | — | `false` | 是否 SSE 流式 |
| `query_params` / `headers` / `body` / `session_id` | — | — | 透传参数 |

### 6.2 `actor`（模拟用户）

```yaml
actor:
  enabled: true            # 默认 true
  profile: normal_user     # 默认
  max_turns: 3             # 最多 agent↔actor 往返
  goal: 使用 schools 数据对学校周边 500 米做缓冲区分析，输出格式 GeoJSON
```

actor 是**纯规则应答器**：本地 agent 输出 `[NEED_INTERACTION] <问题>` → actor 按关键词从 `goal` 提取答案。详见 §9 goal 写作指南。

## 7. `agent.flow`（orchestrator 本地 agent 流程）

| flow | 定义位置 | 行为 |
|---|---|---|
| `react` | orchestrator_executor（默认） | 自由式 ReAct：LLM 自主决定发指令/追问，`ask_user=true` 时支持 [NEED_INTERACTION] 追问 |
| `scripted` | orchestrator_flows | 内置固定节点：生成指令→发外部→LLM 判完成→路由，结构固定可审计 |
| `keyword` / `pipeline` | example_flows（示例） | keyword：终止用规则关键词；pipeline：带首轮计划节点 |
| 自定义 | `@register_flow("名字")` | 写新模块注册，scenario 按名引用（见 `example_flows.py` 注释） |

> 追问（actor 多轮）**仅 react 流程内置**；scripted/pipeline/keyword 需 flow 作者按 `[NEED_INTERACTION]` 协议自行实现。

## 8. 协议前缀（[NEED_INTERACTION] / [FINAL]）

| 前缀 | 含义 | 触发 |
|---|---|---|
| `[NEED_INTERACTION]` | 本地 agent 要追问用户 | `ask_user=true` 且 react 流程；runner 检测到 → 让 actor 回答 → 继续 |
| `[FINAL]` | 任务完成/收尾 | 最终回答必须以它开头，否则 `final_response_contains` 断言、judge 失效 |

## 9. actor 的 goal 写作指南

goal = **模拟用户"确定知道"的信息**。ActorRuntime 用三类正则从 goal 提取答案，外加候选选择匹配：

| goal 片段（句式） | 服务的问题 | 提取结果 |
|---|---|---|
| `使用 schools 数据` | "用哪个数据集？" + **候选选择** | 目标词 `schools` |
| `500 米` | "缓冲距离？" | `500 米` |
| `输出格式 GeoJSON` | "输出格式？" | `GeoJSON` |

**最佳实践**：
1. 固定句式 `使用 {英文id} 数据`——候选选择的正则只认 `[A-Za-z0-9_]+`，中文/无句式提取不到目标词，会落"取第一个候选"
2. 数据集名写**前缀/系列名**（如 `schools`）而非全名——候选往往是 `schools_a`、`schools_b` 这种动态 id，前缀能子串命中
3. 只写用户"确定知道"的信息；用户对候选无所谓就别写（actor 取第一个正好符合"随便哪个"）
4. 不知道的**别编**——写进 goal 等于替用户编造，会误导本地 agent

```yaml
actor:
  goal: 使用 schools 数据对学校周边 500 米做缓冲区分析，输出格式 GeoJSON
  # 候选 [schools_a, schools_b, rivers] → 子串命中 schools_a
  # "缓冲距离是多少？" → 500 米。  "输出格式？" → GeoJSON。
```

## 10. 断言参考（`assertions`）

`type` + 关键参数（全部断言通过才判 pass）：

| type | 参数 | 说明 |
|---|---|---|
| `skill_loaded` | `skill_id` | 技能是否加载 |
| `tool_available` | `tool` | 工具是否可用 |
| `tool_called` | `tool` | 工具是否被调用 |
| `tool_sequence` | `sequence: [a, b]` | 工具调用顺序（按序出现即可，不要求连续） |
| `tool_argument_equals` | `tool, argument, value` | 某工具某参数等于期望值 |
| `result_dataset_exists` | `alias` | 结果数据集是否存在 |
| `result_geometry_type_in` | `target, values: [Polygon]` | 结果几何类型 |
| `final_response_contains` | `values: [a, b]`（或 `value`） | 最终回答包含所有关键字 |
| `skill_reference_loaded` | `path` | 技能引用是否被按需加载 |
| `skill_reference_not_loaded` | `path` | 引用未被加载 |
| `skill_reference_loaded_before_tool` | `reference, tool` | 引用加载先于某工具调用 |
| `skill_reference_load_count_less_than` | `value` | 引用加载次数上限 |

## 11. 完整示例

### 11.1 skill 模式（本地技能评测）

```yaml
id: buffer_school_500m_001
name: 学校周边 500 米缓冲区分析
version: 1.0.0
type: agent_skill_test
description: 测试 gis_buffer_analysis 技能是否引导 agent 正确调用 MCP 工具完成缓冲分析。
target:
  skill_id: gis_buffer_analysis
  skill_version: 1.0.0
runtime:
  executor: skill
  agent_model: rule-based-agent
  max_turns: 6
data:
  fixtures:
    - id: schools
      name: 学校点数据
      type: vector
      format: geojson
      path: ../fixtures/schools.geojson
      crs: EPSG:4326
      geometry_type: Point
mcp:
  servers:
    - id: gpa_vector
      name: GPA 矢量分析服务
      transport: mock
      url: mock://vector
      required: true
  tools:
    required:
      - { server: metadata, name: query_dataset_metadata }
      - { server: gpa_vector, name: create_buffer }
skill:
  load_mode: file
  path: ../skills/gis_buffer_analysis.skill.yml
user_task: 请帮我生成 schools 数据周边 500 米的服务范围。
expected_behavior:
  should_load_skills: [gis_buffer_analysis]
  should_call_tools: [query_dataset_metadata, create_buffer]
  should_not: ["在缺少输入数据时直接执行"]
assertions:
  - { type: skill_loaded, skill_id: gis_buffer_analysis }
  - { type: tool_called, tool: create_buffer }
  - { type: tool_argument_equals, tool: create_buffer, argument: distance, value: 500 }
  - { type: result_geometry_type_in, target: buffer_result, values: [Polygon, MultiPolygon] }
  - { type: final_response_contains, values: ["500", "缓冲区"] }
judge:
  enabled: true
pass_criteria:
  required_assertions_passed: true
  judge_score_min: 0.8
```

### 11.2 agent 模式（orchestrator 指挥 + actor 追问）

```yaml
id: agent_orchestrated_actor_multi_turn
name: 外部智能体-多轮指挥-与actor自动多轮
version: "1.0.0"
type: agent_test
target: {}
runtime:
  executor: orchestrator
  agent_model: deepseek-v4-flash   # orchestrator 需要真实本地模型（无启发式兜底）
  max_turns: 5                     # 最多向外部 agent 发的指令数
agent:
  type: http
  endpoint: http://<host>:8490/agentx/workflowstudio/api/v1/run/<flow_id>
  description: 能对 GIS 数据集执行查询、叠加求交、缓冲区等空间分析的智能体
  ask_user: true                   # 缺信息时允许向用户(actor)追问
  flow: react
data: {}
mcp: { servers: [], tools: { required: [], optional: [] } }
user_task: 对学校做缓冲区分析
actor:
  enabled: true
  max_turns: 3
  goal: 使用 schools 数据对学校周边 500 米做缓冲区分析
assertions:
  - { type: final_response_contains, value: "完成" }
judge:
  enabled: true
pass_criteria:
  required_assertions_passed: true
  judge_score_min: 0.8
```

## 12. 校验与运行

```bash
# 校验场景（schema + 依赖检查）
python -m geoskillbench.cli validate scenarios/buffer_school_500m_001.yml

# 列出场景可用工具
python -m geoskillbench.cli list-tools scenarios/buffer_school_500m_001.yml

# 运行并生成报告
python -m geoskillbench.cli run scenarios/buffer_school_500m_001.yml --output reports
```

前端：选中场景 → Validate → Create Task，SSE 实时看阶段进度，结果含工具调用/对话/断言/LLM judge。

## 13. 边界与常见问题

- **orchestrator 无启发式兜底**：`agent_model` 必须是 models.yaml 真实别名，缺 langgraph 依赖 / 缺 endpoint 直接报错（不是失败场景而是配置错误）。
- **`[FINAL]` 必须打头**：最终回答不以 `[FINAL]` 开头，judge 和 `final_response_contains` 可能误判。
- **actor 追问占 runner 轮次**：每次追问消耗 `runtime.max_turns` 的一轮（不消耗外部指令数）；max_turns 偏小时追问会挤占指令预算。
- **前端表单覆盖常用字段**：断言、expected_behavior、mcp、自定义 flow 等复杂块表单不暴露，需要时直接编辑生成的 yml。
- **`executor: langgraph` 是历史别名**，等价 `skill`，存量场景兼容可不动。
