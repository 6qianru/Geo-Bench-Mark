# GeoSkillBench Evaluation 平台改造路线图

> 状态：**部分完成（2026-08-24 标注）**。本文档为历史存档，正文保留写作时原貌。
> 进度：✅ 阶段一（既测 skill 又测独立智能体，见 `../retrospective/阶段1-外部智能体黑盒接入.md`）；✅ 阶段二（数据库持久化，PostgreSQL/SQLite 回退已落地）；✅ 阶段三最小闭环（前端场景创建/管理表单已落地）。后续迭代见 `../retrospective/` 各篇。
> 文中"无数据库 / 报告被覆盖 / 假 MCP"等现状盘点描述已被后续迭代解决或改变（MCP 现为真协议客户端，见 `迭代4-云端MCP工具接入.md`），仅作历史记录，不代表当前状态。

## 0. 背景与结论

目标：搭建一个智能体/Skill 的 evaluation 平台。对比了 `agent-geo-arena` 和 `geo-skill-bench` 两个候选项目。

**结论：以 `geo-skill-bench` 为基础改造**，原因：

- 定位天然吻合：`docs/design/00-系统总体设计.md` 本身就是为"Agent Skill evaluation"设计的，Scenario schema 里的 `target.skill_id`、`expected_behavior`、`assertions` 正是通用 evaluation 平台需要的核心抽象。
- 断言体系是 evaluation 平台的灵魂，`geoskillbench/assertions/assertion_engine.py` 已有 12 种断言类型，覆盖"技能是否被正确使用"（`skill_loaded`、`tool_called`、`tool_sequence`、`tool_argument_equals`、`skill_reference_loaded*`）和"结果是否正确"（`result_dataset_exists`、`result_geometry_type_in`、`final_response_contains`）两个维度，`agent-geo-arena` 在这块投入少得多。
- 已有可运行的前后端闭环（scenario → executor → recorder → assertion engine → judge → report），可以直接跑通验证。

`agent-geo-arena` 的优点（Postgres + SQLAlchemy 的 Run/Check 表设计、SSE 事件流规范、`/api/runs/aggregates` 成功率聚合 API）计划作为补丁移植到 `geo-skill-bench`，而不是反过来。

## 1. geo-skill-bench 现状盘点

### 1.1 技术栈

- 后端：FastAPI（`geoskillbench/api/app.py`），执行引擎 `geoskillbench/runner.py`
- 智能体 runtime：默认走 LangGraph（`geoskillbench/executors/skill_executor.py`），用 `create_react_agent` 现场搭一个 ReAct 循环，LLM 走 OpenAI-compatible 端点（`geoskillbench/runtime/llm.py`），配置在根目录 `models.yaml`
- Runtime 通过 `executors/factory.py` 按名字生产，目前注册了 `langgraph`（真实可用）和 `nanobot`（占位，未装 `nanobot` 包时 fallback 到纯规则的 `HeuristicSessionExecutor`，无 LLM 参与）
- 前端：React + Vite SPA（`frontend/src/App.jsx`），场景选择、executor 选择、Validate/List Tools/Create Task、SSE 实时事件、报告查看

### 1.2 已确认的架构限制

| 限制 | 证据 | 影响 |
|---|---|---|
| 无数据库 | `api/task_manager.py` 的 `TaskManager` 把所有任务状态存在内存字典 `self._tasks` | 进程重启后任务历史全部丢失 |
| 报告会被覆盖 | `reports/report_generator.py`：`json_path = json_dir / f"{result.scenario_id}.json"`，文件名只用 `scenario_id`，不带 run_id/时间戳 | 同一 scenario 跑第二次直接覆盖上一次报告，无法做历史对比/回归 |
| 无跨运行聚合 | 没有类似 `agent-geo-arena` `/api/runs/aggregates` 的接口 | 无法做排行榜、多模型对比、成功率趋势 |
| 前端不能编辑/上传 case | `App.jsx` 只有一个下拉框选择 `scenarios/*.yml` 里已存在的文件，没有上传/编辑/新建 UI | 加一个新 eval case 必须离开 UI 手写 YAML 文件 |
| MCP 工具是假实现 | `geoskillbench/mcp/mcp_tool_adapter.py` 里 `_create_buffer` 等方法只是打标签（如把 `geometry_type` 硬编码成 `"Polygon"`），没有真实几何运算；scenario 里 `transport: mock` | "结果正确性"断言（如 `result_geometry_type_in`）目前测的是"流程走没走对"，不是"算得对不对" |
| 只能测 skill，不能测独立智能体 | `models/scenario.py` 中 `Scenario.skill: SkillConfig` 是必填字段，`type` 锁死为 `Literal["agent_skill_test"]`；`runner.py` 的 `LOAD_SKILL` 是硬编码必经阶段，skill prompt 直接注入内部现场搭建的 LangGraph agent | 无法直接指向一个已经独立运行的外部智能体做黑盒评测 |

### 1.3 已确认的架构优点（值得保留/复用）

- `Executor` 抽象基类干净（`executors/base.py`，只有 `create_session`/`send_message`/`close_session` 三个方法），下游 assertion engine / judge / report / actor 模拟对话完全不关心 SUT 是怎么跑出来的，只依赖标准化的 `ExecutorStepResult`
- `TestResult`（`models/result.py`）已经是干净的 Pydantic 模型，可直接序列化存 DB，不需要拆成范式化多表
- 设计文档里其实已经把"完整版"设计写好了：
  - `docs/design/00-系统总体设计.md` **F-19 数据表建议**：`skills`/`scenarios`/`test_runs`/`test_iterations` 四张表的完整字段设计（数据库选型原文档写的是 PostgreSQL）
  - 同文档 **F-4~F-9**：Skill 上传预览区、Scenario 上传/选择/多选批量、MCP 工具检查区、模型角色配置区等前端功能区设计
  - MVP 阶段主动砍掉了这两块（文档原话："MVP 阶段可先不引入复杂任务队列，使用 FastAPI + asyncio task manager"），不是没想到，是延后了

## 2. 改造范围与优先级

按依赖关系排序（后面的工作依赖前面的核心抽象稳定下来）：

### 阶段一：架构支持"既测 skill 又测独立智能体"（优先级最高）

这个改动涉及 `Scenario` schema 和 `Executor` 接口，是所有其他功能的地基，需要先做，避免后面 DB/前端返工。

- [x] **前置（P0）**：确认外部智能体接入对象与协议（HTTP 轮询 / 流式 / SDK）。没有真实接入对象就开工，`http_agent_executor` 会成为"给不存在的系统写适配器"，写完无法验证、极易返工。本文档末尾"待确认事项"第 2 条（HTTP API vs SDK）必须先有答案。**已完成**：接入对象 = SuperMap Workflow Studio（Agentx Server），`POST /agentx/workflowstudio/api/v1/run/{flow_id}`，`stream=true` 走 SSE 流式，实测真实格式为逐行 JSON（`event: token` / `add_message` / `tool_event`）
- [x] **定义接入契约**（阶段一真正的大头，不是 schema）：在 `docs/` 新增一节或单独文档，明确：`docs/design/01-Agent接入契约.md`（已完成）
  - scenario 新增 agent 配置字段（endpoint / 类型 / 轮数），`type` 新增 `"agent_test"`
  - HTTP 请求 / 响应形状（轮询还是流式）、超时与错误语义
  - 外部智能体**如何上报结构化工具调用**（`tool_called` / `tool_sequence` / `tool_argument_equals` 存活的前提）
  - 外部智能体**如何表达"已完成" / "需要追问"**——平台内部 `[NEED_INTERACTION]` / `[FINAL]` 协议外部 agent 不遵守，需定义适配规则
- [x] `models/scenario.py`：`Scenario.skill` 改可选；`target.skill_id` 一并放开（当前 `scenario.py:109` 也是必填）；`type` 新增 `"agent_test"`；新增 agent 配置字段（已完成：`AgentConfig`，含 endpoint/query_params/headers/stream_response 等）
- [x] `runner.py`：按 type 分支——`agent_skill_test` 保持现状；`agent_test` 跳过 `LOAD_SKILL`，且 `test_context`（`runner.py:112` 的 `skill=SkillContext(...)`）与 `ExecutorSessionRequest`（`skill_prompt=render_prompt(skill)`）都要能接受"无 skill"（已完成：LOAD_SKILL → SKIPPED，skill 全链路置空）
- [x] 新增 `executors/http_agent_executor.py`：实现三段式，按契约转发外部智能体、把上报的工具调用日志转成 `ToolCallRecord`、适配 finished / need_interaction 判定（已完成：`tool_event` 解析、SSE/JSON 提取，实测真实格式）
- [x] 在 `executors/factory.py` 注册新 Executor（已完成：`http_agent`）
- [x] **断言可用性矩阵写入文档**（修订原计划不准确处）：
  - `skill_loaded`、`skill_reference_loaded*`：不可用（外部黑盒不会上报）
  - `result_dataset_exists` / `result_geometry_type_in`：依赖平台侧 mock `_dataset_store`，外部 agent 不会填充 → 同样**不可用**（除非外部 agent 上报结果数据集、平台注册进 store）。原计划"`result_*` 不受影响"不成立
  - `tool_called` / `tool_sequence` / `tool_argument_equals` / `final_response_contains`：可用性**取决于外部 agent 能否上报结构化工具调用**；只返回纯文本则都不可用
  - 黑盒信任边界：`tool_called` 系列依赖对方自报日志，平台无法验证真实性

预估工作量：2~3 天（契约定义 + 真实对接联调是大头；`http_agent_executor` 代码本身半天，但"定契约 + 对方配合上报 + 联调"要一天半以上）。原计划 1~1.5 天低估。

### 阶段二：数据库持久化（Postgres/PostGIS）

范围已确认：**先只做评测结果的持久化**（跑分记录、历史查询），暂不用 PostGIS 替换 MCP 工具里的假地理计算（后续如要做几何正确性校验再启动）。

- [x] `geoskillbench` 加依赖：`sqlalchemy`、`psycopg[binary]`、`python-dotenv`（已完成 2026-08）
- [x] 新增一张 `reports` 表：`run_id`（主键）、`scenario_id`、`scenario_name`、`executor`、`status`、`created_at`、`json_content`、`md_content`（已完成。**比原计划的 `runs` 表简化**：不存 `result_json`，直接存报告全文 json+md，agent 结果地图不重复存——报告里已有数据服务 URL）
- [x] 新增 `geoskillbench/api/db.py` 连接层（已完成。**未复用 agent-geo-arena**——本机无该项目，且 SQLAlchemy 连接层是标准写法无需抄。`DATABASE_URL` 留空时本地自动用 SQLite 文件库 `reports.db`，填 PostGIS 连接串即切服务器库，代码零改动）
- [x] `.env` 增加 `DATABASE_URL` 配置项（已完成。已加入 `.gitignore`，连接串不进 git）
- [x] `api/task_manager.py`：把 `task_id` 作为 `run_id` 传给 `runner.run()`，报告落库（已完成。内存态 SSE 事件流不动）
- [x] `TestResult` / `runner.run()` 增加 `run_id` 支持（已完成。`run()` 未传 `run_id` 时自动生成 uuid，CLI 直跑也兼容）
- [x] 报告文件命名从 `{scenario_id}.json` 改为 `{run_id}.json`，避免覆盖（**本次未改文件命名**——DB 已按 run_id 区分多次运行、解决覆盖问题；文件系统仍按 scenario_id 命名保留现状，避免动 `/api/reports` 前端逻辑。如需文件层也区分可后续补）
- [x] 新增 API：`GET /api/runs`（列表，支持按 `scenario_id` 过滤）、`GET /api/runs/{run_id}`（详情，含报告全文）（已完成，2026-08 本地 SQLite 验证通过）
- [ ] 数据库层面可以顺手 `CREATE EXTENSION postgis;`，为将来把 mock 几何计算换成真实 PostGIS 运算做准备（本阶段不使用，只是提前开好）——**延后**：连接串尚未配置，等部署服务器填 `DATABASE_URL` 时一并执行

预估工作量：1~2 天。**实际约 1 天**（比原计划省，因无需抄 agent-geo-arena、无需拆 runs 表；多出的成本是 run_id 贯穿改动）。

### 阶段三：前端支持添加/编辑 eval case（砍掉文档里的完整版，只做最小闭环）

不照抄设计文档 F-5~F-9 的完整可视化工作台（结构化 assertion 编辑器、MCP 工具连接状态可视化、Agent/Actor/Judge 分角色模型配置面板、批量多选——这些明确推迟，用不上时不做）。

- [ ] 后端：复用现有 `loader/scenario_loader.py`、`loader/skill_loader.py`、`runner.validate()` 的解析/校验逻辑，包一层"上传/粘贴 YAML → validate → 存文件"的接口（scenario 和 skill 各一个）
- [ ] 前端：加一个文本框/文件上传 + 已有的 Validate 按钮流程，保存成功后刷新场景下拉框
- [x] 前端：把 Task History / Reports 区块从"读文件系统"改为查阶段二新增的 `/api/runs` 接口，这样重启后历史不丢（**Task History 已接 /api/runs**，点击历史行加载该 run 报告全文到 Run Result 区，复用 ResultDetail 渲染；DB 不可用时显示错误提示而非崩溃。**Reports 区块未切**——仍读 `/api/reports` 文件系统，如需要可后补）

预估工作量：前端 1~2 天，后端半天。

### 暂不处理（明确延后，非本轮范围）

- MCP 工具从 mock 换成真实几何计算（真实 PostGIS 运算 / 真实 MCP server 连接）——留到"结果正确性"验证真正成为阻塞项时再做
- 结果正确性断言补充数值容差类型（类似 `agent-geo-arena` 的 `feature_area_about`，判断面积是否落在误差范围内）——依赖上一条先完成
- `nanobot` executor 真正实现（目前是占位，fallback 到规则引擎）
- 文档 F-5~F-9 里的结构化可视化前端（assertion 编辑器、MCP 工具连接状态面板、批量多选跑等）

## 3. 总体工作量估算

| 阶段 | 内容 | 预估 |
|---|---|---|
| 一 | 接入契约 + Scenario schema + Executor 扩展，支持测独立智能体 | 2~3 天 |
| 二 | Postgres/PostGIS 持久化 + 查询 API | 1~2 天 |
| 三 | 前端最小化 case 上传/编辑闭环 | 1.5~2.5 天 |
| **合计** | | **约 1~1.5 周**（单人，不含真实几何计算和完整可视化工作台） |

## 4. 待确认事项

- [ ] PostgreSQL/PostGIS 实例：是否已有可用实例（比如跟 `agent-geo-arena` 共用），还是需要新起一个（如 docker-compose 拉 `postgis/postgis` 镜像）？
- [ ] 阶段一里"独立智能体"的具体接入方式：目标智能体是走 HTTP API，还是某个 SDK（如 Claude Agent SDK）？决定新 Executor 的具体实现方式。
