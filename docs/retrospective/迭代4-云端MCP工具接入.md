# 迭代 4 复盘：云端 MCP 工具接入（真 MCP 客户端改造）

> 日期：2026-08-24 · 状态：**代码完成、mock/sse/云端真机验证通过**
> 对应计划：`../plan/迭代4-云端MCP工具接入.md`（立项时曾称"迭代 3"，因编号冲突重命名为迭代 4）

## 1. 迭代目标

把「假 MCP + 硬编码 4 个 GIS 工具」改造为**真 MCP 客户端**：adapter 按 MCP 协议连接 server（本地 mock 与云端 SuperMap DataManager 同一条 code path），工具由 server `tools/list` 自动发现、`tools/call` 调用；skill 所需工具在 server 缺失时 fail-fast 拦截（agent 不启动）。让被测 skill agent 能面向真实云端 MCP 服务评测。

## 2. 完成内容

| 项 | 说明 |
|---|---|
| `mcp/mcp_tool_adapter.py` | **整体重写**。`_MCPConnection`（stdio/sse/http 三种 transport，`mock` 等价映射 stdio）；后台 event loop 线程 + `run_coroutine_threadsafe` 桥接同步调用链；`schema_to_pydantic_model()` 把 inputSchema 转 pydantic 参数模型；`missing_tools()` 供 fail-fast；生成数据集落盘归 client 端 |
| `mcp/mock_gis_server.py` | **新增**。FastMCP 把 4 工具包成本地 MCP server 进程；数据经 `GEO_MCP_DATASETS` env 注入（server 无状态）；create_buffer 按中心点选 UTM 带做米级缓冲；支持 `--transport stdio|sse|http` |
| `executors/skill_executor.py` | `_build_skill_tools` 从手写 4 闭包改为按发现工具的 inputSchema 自动生成 `StructuredTool`；工具可见性 = 场景声明 ∩ skill 推荐集（`publish_map` 这类未推荐工具不暴露给 LLM） |
| `runner.py` | LOAD_SKILL 后、RUN_AGENT 前 fail-fast：skill `recommended_mcp_tools` ⊄ server 已发现工具 → `raise ValueError`，报告含缺失清单与 server 实际工具列表 |
| `models/scenario.py` | `MCPServerConfig.url` 改可空（mock/stdio 不需要远程地址）；`mcp.tools` 保留字段但不再是授权来源 |
| 前端 `ScenarioForm.jsx` + `api/scenario_schema.py` | mcp 分组：server 动态行编辑器（id/name/transport/url），无工具手选（自动发现）；后端剥离空 tools 块 |
| `pyproject.toml` | 新增 `mcp>=1.29,<2.0`、`fastmcp>=3.4.7`（<2.0 防未验证大版本） |

## 3. 关键设计决策及理由（重点）

### 3.1 mock 即本地 MCP server 进程，`transport: mock` 等价映射 stdio（最重要决策）

mock 的 4 个 GIS 工具（geopandas 几何运算）原样搬进 `mock_gis_server.py`，作为独立 MCP server 进程由 adapter 经 stdio 协议调用。存量场景里的 `transport: mock` 不需要改 yml——adapter 连接时把它映射为 stdio。

**为什么**：如果 mock 保留"直接调 Python 函数"的旁路，平台就有两条工具执行路径，mock 测通了不代表云端能通（反之亦然），评测结论的可信度打折。统一 code path 后，`buffer_school_500m_001` 这种 mock 场景跑通即证明整条 MCP 协议链路（连接/发现/schema 解析/调用/结果落盘）是好的，云端只是换了个 transport 和 URL。

**代价**：每次 run 多一个子进程启动开销（毫秒级，实测无感）；调试栈变深（server 端异常要经协议透传回来）。

### 3.2 工具授权层取消，改为「server 自动发现 × skill 推荐集」双重过滤

原设计里 scenario `mcp.tools.required/optional` 是手工授权清单。本迭代取消授权语义（字段保留兼容存量），工具来自 server `tools/list` 自动发现；真正的可见性过滤收敛到 `SkillExecutor`：**场景声明的工具 ∩ skill `recommended_mcp_tools`** 才暴露给 LLM。

**为什么**：skill 本来就声明了"完成这类任务推荐哪些工具"（`gis_buffer_analysis.skill.yml` 的 `recommended_mcp_tools`），这就是天然的授权源——比在场景里重复罗列更符合"skill 控制任务策略"的设计原则（design/00 §4.2）。实测效果：deepseek-v4-flash 不再尝试调用与本任务无关的 `publish_map`。

### 3.3 fail-fast：无法评测就没分，agent 不启动

skill 声明的必需工具在 server 上缺失时，LOAD_SKILL 阶段直接 `raise ValueError`，报告明确写出「缺少 X（skill 声明 [...]，server 仅提供 [...]）」，agent 完全不启动、零工具调用、零云端副作用。

**为什么不做"agent 运行时自己发现报错"的慢路径**：慢路径要烧真实 LLM token 让 agent 自己撞墙，撞完墙的回答质量还依赖模型自觉；fail-fast 把"环境配错了"和"agent 能力不行"两类失败干净地分开——前者不该产生任何评分。这也是「无法评测就没分」评分模型（计划 §0.1）的执行基础：云端 result_* 断言无法评测时不 auto-pass、不给同情分。

### 3.4 同步/异步桥接：后台 event loop 线程

MCP Python SDK 的 ClientSession 是 async，而 runner/executor 全链路同步。选择在 adapter 内部持有一个常驻 event loop 线程，同步方法用 `run_coroutine_threadsafe` 桥接，对外接口签名不变。

**为什么不改全链路 async**：runner/task_manager/SSE 事件流都是同步代码，async 化是大手术且收益为零（评测吞吐不受 IO 并发约束）。桥接层 30 行代码换零侵入，代价是 adapter 生命周期要自己管（`atexit.register(self.close)` 兜底清理）。

### 3.5 server 无状态，结果落盘归 client 端

mock server 算完几何只返回临时文件路径 + 元数据 dict；adapter 收到结果后把 GeoJSON 持久化到 `reports/outputs/<run_id>/`（run_id 隔离，随报告产物留存），并把生成数据集登记回 client 端 `_dataset_store` 供后续工具链式引用（reproject 的产物喂 create_buffer）与 result_* 断言读取。

**为什么**：server 进程随 run 结束销毁，临时目录会被系统回收；结果数据是评测证据（报告引用、断言比对），生命周期必须跟 run 走而不是跟 server 进程走。

## 4. 验证结果（2026-08-21 全部通过）

- ✅ **mock 回归**：`buffer_school_500m_001.yml` 全过（10 断言全对、judge 1.0、工具走 stdio 协议）——证明等价映射零回归
- ✅ **结果内容断言**：`buffer_school_500m_reference_001.yml` 全过（重合度 1.0 / 面积误差 0% / Hausdorff 0m）——证明 server 产物落盘、client 断言读取链路正确
- ✅ **sse transport**：本地起 mock server（`--transport sse`），adapter 经 `sse_client` 发现 4 工具、真实几何调用成功
- ✅ **云端真机**（SuperMap DataManager，`http://192.168.13.130:8490/.../vectoranalyst/sse/v2.1`）：
  - 连接 ✅、`tools/list` 发现 8 个真实工具（buildSpatialIndex/createDatasetVector/deleteDataset/datasetPrjTranslator/convertDatasetPrjCoordSys 等）✅
  - fail-fast 拦截 ✅：skill 声明的 mock 工具在云端全缺失 → RUN_AGENT 直接 FAILED、0 工具调用、报告列出缺失清单与 server 实际工具
  - 错误透传 ✅：`copyDataset` 空参调用 → 云端结构化校验错误（`Missing required parameter(s): srcDataset`）原样回到报告
- ⚠️ 注意：仓库存档的 `reports/json/cloud_remote_smoke_001.json` 是 **fail-fast 引入前**的早期快照（当时 agent 还会启动并在回答里自行承认"Available tools: none"、judge 给 0.2 分）——恰好可作为 fail-fast 价值的对照：改造前失败方式是"烧一轮 LLM 得到一个低分报告"，改造后是"秒败 + 环境错误清单"。

## 5. 已知风险与未验证项

- **云端数据面是断的（有意搁置）**：云端 server 操作的是服务器侧图层，平台的 fixtures 注入/reference 比对对云端不适用。真 server 场景当前只能走过程断言 + judge，「云端 result_* 结果内容断言」进计划搁置，接受必不过状态。
- **http transport 未真机验证**：adapter 代码路径齐备，但真机只验过 stdio 与 sse。
- **跨 server 同名工具**：当前工具身份以 name 为准（server 只是归属标签），多 server 暴露同名工具取首个。云端接入若出现跨 server 同名，需引入 (server, name) 复合命名空间——代码注释已标记此扩展点。
- **`mcp>=2.0` 未验证**：pin `<2.0` 防大版本破坏，升级需重新评估。

## 6. 过程经验与教训

- **"统一 code path"是可信度问题不是洁癖**：mock 与真实服务走两条路径时，mock 通过什么也证明不了。宁可让 mock 也起进程走协议，换来"mock 测通 ≈ 链路测通"。
- **失败要分清"环境错"还是"被测对象错"**：fail-fast 的价值不只是省 token，而是让报告的 failed 语义干净——环境配置错误的报告不该出现任何 agent 行为评价。
- **授权信息往往已存在于上游资产**：skill 的 `recommended_mcp_tools` 就是现成的授权源，取消独立授权层不是砍功能而是消除冗余——两份清单迟早漂移。
- **async 库接入同步代码库，桥接优于重写**：评估"改多少代码"时要把调用链全长度量进去，30 行桥接 vs 全链路 async 化，答案显然。

## 7. 下一步

1. 云端数据面打通评估：SuperMap DataManager 的数据集上传/查询接口若能映射为 fixture 语义，云端 result_* 断言才有落地条件（当前搁置）。
2. http transport 真机验证（找一台 http 型 MCP server）。
3. 与迭代 2 LLM judge 真机联调：云端场景 judge rubric 需覆盖"工具不可用时 agent 是否诚实报告"维度（早期快照里 judge 已表现出合理行为，可作基线）。
