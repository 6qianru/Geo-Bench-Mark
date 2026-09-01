# 迭代 4 计划：云端 MCP 工具接入

> 状态：**已实施（2026-08-21）**。后端主体 + 前端 mcp 表单全部落地并验证通过。真实云端 server 验证完成：连接、工具发现、fail-fast 拦截、云端调用错误透传全部确认。
> 编号说明：立项时曾称"迭代 3"，因此前已有"迭代 3（模拟用户 actor 自动多轮）"复盘在档，整理文档时重编号为迭代 4。
> 本迭代对应「让被测 skill agent 能调用**云端** MCP 工具」的能力。现状评估见 `../design/00-系统总体设计.md`；mock 工具实现见 `geoskillbench/mcp/mcp_tool_adapter.py`。

## 0. 能力边界（已与用户对齐）

- **本地 skill（被测能力，照旧评测）** + **云端 MCP 工具（本次新增，真连接）**。
- **新增**：真 MCP 客户端，连云端 server；server 配置放场景；工具来自 server `tools/list` 自动发现；skill 所需工具在 server 缺失 → connect 后 fail-fast 校验报错（agent 不启动）。
- **保持**：本地 mock 工具逻辑不变（geopandas 几何运算原样保留），但接入方式统一——4 工具做成本地 mock MCP server 进程，adapter 统一走协议，mock/云端同一条 code path。
- **搁置进计划**：云端数据结果断言（`result_*` 对比 reference）。云端场景仍写 `data.fixtures`（与非云端一致），但云端传参/取参当前视为**断的**（不实现），云端场景运行必不过或报错——**接受此状态**。

## 0.1 评分模型（已对齐，重要）

- **无法评测就没分**（推翻早期"真 server 直接满分"的说法）。云端 result_* 无法评测 → 没分 → 不过，无 auto-pass。
- 真 server 场景跳过 `result_*` 结果内容断言，只走**过程断言 + judge**；结果内容断言在真 server 下视为不适用/跳过，过程断言照常判。

## 1. 设计决策（已确认）

| # | 问题 | 决策 |
|---|---|---|
| 1 | mock 是否也作为 MCP server | **是**。mock 是一个本地运行的 MCP server 进程，暴露 4 个 GIS 工具，实现即现在 adapter 的 geopandas 逻辑原样搬入。adapter 对 mock 和真实 server 走完全一样的协议路径 |
| 2 | server 连接信息放哪 | **场景里**。`mcp.servers` 自带 endpoint/auth/transport，场景即测试包自包含，不做平台级注册 |
| 3 | 工具授权层 | **取消**。不做 scenario `mcp.tools` 手选授权。工具来自 server 自动发现，agent 拿到 server 暴露的全部 |
| 4 | skill 所需工具缺失怎么处理 | **fail-fast**。connect 后、跑 agent 前，平台校验「skill 需要的工具 ⊆ server 暴露的」，缺失直接 fail + 报告明确写缺失清单，agent 不启动（不做"agent 运行时自己发现报错"的慢路径） |
| 5 | 云端数据声明 | **照写 `data.fixtures`，与本地一致**。真 server 的数据是服务器已有图层，agent 直接操作服务器上的数据名；本地文件注入 / reference 比对是 mock 专属。云端传参/取参当前断的（见 §0） |
| 6 | 评分 | **无法评测就没分**（见 §0.1）。真 server 跳过 result_*，过程断言 + judge 照常，无 auto-pass |

## 2. 行为变更（重要，先读）

- `MCPToolAdapter` 从"硬编码工具 + 直接调 Python 函数"改为**真 MCP 客户端**：`connect_servers` 真正连接（按 server 类型走 stdio/sse/http），`tools/list` 自动发现工具，`invoke` 走 `tools/call` 协议。
- mock 4 工具搬入本地 mock MCP server 进程，adapter 统一走协议调用。
- `_build_skill_tools` 从手写闭包改为**按工具 schema 自动生成** agent 工具。
- 场景 `mcp.tools.required/optional` 不再作为授权来源（保留字段兼容存量，但新增 server 场景不依赖它）。
- 影响面：存量 `buffer_school_500m_001.yml` 等 mock 场景跑法不变（mock server 进程自动起），但 adapter 内部路径改变，需回归。

## 3. Schema 改动（`models/scenario.py`）

- `MCPServerConfig` 扩展连接字段：`auth`（可选）、`timeout` 等（按真实 MCP 客户端需要）。
- `MCPToolsConfig` 保留但不作为授权来源（兼容存量）。
- `data.fixtures` 在真 server 场景字段语义调整：本地文件注入字段（path）可空，`id` 即服务器图层名。

## 4. 新增/改造模块（✅ = 已落地 2026-08-21）

### 4.1 本地 mock MCP server（✅ `geoskillbench/mcp/mock_gis_server.py`）
- 用 FastMCP 装饰器把 4 工具包成 server：`query_dataset_metadata` / `reproject_dataset` / `create_buffer` / `publish_map`。
- 数据对接：`register_datasets` → 通过 `GEO_MCP_DATASETS` env 注入 `alias → {path, 元数据}` 到 mock server 进程；server 按名读文件算几何。
- 派生数据集登记回 server 端 `DATASETS`（复刻旧 adapter 行为，否则 create_buffer(schools_metric) 找不到）。
- 支持 `--transport stdio|sse|http` 命令行（供 sse 验证与云端部署）。
- 结果落盘归 client 端（adapter 收到结果写入 `reports/outputs/<run_id>/`），server 无状态。

### 4.2 真 MCP 客户端改造（✅ `geoskillbench/mcp/mcp_tool_adapter.py`）
- `connect_servers`：按 server 的 transport 类型（stdio / sse / http）真正连接；`transport: mock` 等价映射为 stdio。
- 同步/异步桥接：adapter 持后台 event loop 线程，同步方法用 `run_coroutine_threadsafe` 桥接（MCP SDK 是 async，runner/executor 是同步调用链）。
- `tools/list` 发现 → 填入 `_catalog`（`ToolDefinition` 含 `input_schema`，替代硬编码分发表）。mock 下工具按 name 去重（多 server 暴露同名工具集），server 是归属标签。
- `invoke`：走 `tools/call` 协议，等结果返回；生成数据集落盘到 output_dir 并登记进 client 端 `_dataset_store`。
- `missing_tools()`：供 fail-fast 校验查缺失清单。

### 4.3 executor（✅ `geoskillbench/executors/skill_executor.py`）
- `_build_skill_tools`：从 server 发现的工具 `inputSchema` 自动生成 `StructuredTool`（schema→pydantic model→tool，命名参数正确），删除手写 4 闭包。
- 启发式路径（`heuristic_executor.py`）无需改：仍按 `mcp_tools` 存在性调 `adapter.invoke`，adapter 已统一走协议。

### 4.4 fail-fast 校验（✅ `geoskillbench/runner.py`）
- LOAD_SKILL 后、RUN_AGENT 前：skill `recommended_mcp_tools` ⊆ server 已发现工具，缺失 → `raise ValueError` → runner 走 failed 分支，报告含缺失清单，agent 不启动。

## 5. 前端（✅ 已落地 2026-08-21：`frontend/src/ScenarioForm.jsx` + `api/scenario_schema.py`）

- `scenario_schema.py` 加 `mcp` group（modes: `agent_skill_test`）：server 动态行编辑器（id/name/transport/url），transport 默认 mock。
- `ScenarioForm.jsx`：`mcpServers` state + `FixtureEditor` 复用渲染；`buildPayload` 组装 `mcp.servers`；`cleanMcpServersRows` 空字段剔除、transport 默认 mock。
- `create_scenario`（app.py）：mcp 只有 servers、tools 全空时剥离空 tools 块（工具授权层已取消，不生成 mcp.tools）。
- 工具无手动选择：连接 server 后自动发现，前端只配 server 连接信息。
- 验证：vite build 通过；表单产物场景（无 tools）端到端跑通（mock server、create_buffer、断言全过）。

- `mcp` 分组：server 多选/编辑（endpoint/auth/transport），工具多选（从连上 server 后自动发现的清单拉取）。
- 保存时组装 `mcp.servers` 块写入 yml（工具授权层已取消，不生成 `mcp.tools` 手选）。
- 真 server 场景数据声明 UI：fixtures 支持"服务器图层"模式（id 即图层名，不填 path）。

## 6. 测试（✅ 全部通过 2026-08-21）

- ✅ mock 回归：`buffer_school_500m_001.yml` 全过（10 断言全对，judge 1.0，工具走 stdio 协议）。
- ✅ 结果内容断言：`buffer_school_500m_reference_001.yml` 全过（重合度 1.0 / 面积误差 0% / Hausdorff 0m / 字段 / 要素数），证明 mock 产物正确落盘供断言读取。
- ✅ fail-fast：skill 声明 server 不存在的工具 → RUN_AGENT 直接 FAILED，0 工具调用，报告含缺失清单、skill 声明、server 提供。
- ✅ sse transport：本地起 mock server（--transport sse），adapter 连 `sse_client`、发现 4 工具、query_dataset_metadata + create_buffer 真实几何。
- ⛔ 云端场景必不过验证：✅ **已用真实云端 server 验证**（2026-08-21，`http://192.168.13.130:8490/.../sse/v2.1`）：
  - 连接 ✅、发现 8 个 SuperMap DataManager 工具（buildSpatialIndex/createDatasetVector/deleteDataset/datasetPrjTranslator/convertDatasetPrjCoordSys/datasetGridBuildPyramidByResamplingMethod/datasetImageBuildPyramid/copyDataset）✅；
  - fail-fast 拦截 ✅：skill 声明 mock 工具（query_dataset_metadata 等）在云端全缺失 → RUN_AGENT 直接 FAILED、0 工具调用、报告列缺失清单与 server 实际工具；
  - 云端真实调用 ✅：`copyDataset` 空参 → 云端结构化入参校验错误透传（`Missing required parameter(s): srcDataset`）。
  - 结论：云端场景按边界"必不过/报错"状态成立，报错清晰，agent 不启动。

## 7. 明确不做（本迭代边界）

- **不做**云端数据结果断言（result_* 对比 reference）实现——进计划搁置，云端场景接受必不过状态。
- 不做平台级 server 注册（server 配置放场景）。
- 不做 skill 与工具的强绑定（skill 保持 `recommended_mcp_tools` 文档推荐，不做工具授权层）。
- 不做 agent 运行时"发现工具缺失报错"慢路径（用 fail-fast 预校验替代）。

## 8. 验证依赖

- 需安装 MCP SDK（Python 官方 `mcp` / `mcp-python-sdk`），本机 PyPI 走清华镜像。
- 用户已确认允许临时装依赖验证。
