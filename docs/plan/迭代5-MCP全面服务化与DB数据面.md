# 迭代 5 计划：MCP 全面服务化 + DB 数据面

> 状态：**已立项（2026-08-25），未启动**。
> 目标：取消本地 stdio/mock MCP 通道，工具全部走网络接入（云上 MCP 服务 + server 已配好，由用户自理）；数据面从"文件随行/env 注入"改为"共享数据库 + 元数据表名引用"。
> 范围边界：**平台侧（geoskillbench/）改动 + 协议约定文档**；云上 MCP 服务与 server 的改造归用户自理，本迭代不碰。

## 1. 背景与动机

- 现状双 code path：`transport: mock/stdio`（本地子进程）+ `sse/http`（远程）。mock 分支是云端未就绪期的过渡产物。
- 本地 stdio 模式隐含三个"同机假设"契约，远程化后同时失效：
  1. **数据集注入**——`connect_servers` 把 dataset_store 序列化塞进子进程 env `GEO_MCP_DATASETS`（mcp_tool_adapter.py:185-190）；
  2. **结果回传**——server 写本机临时文件返回路径，client `shutil.copyfile` 拷到 `reports/outputs/<run_id>/`（mcp_tool_adapter.py:301-337）；
  3. **fixture db_table**——client 拉 PostGIS 落本地临时文件再注册（fixture_manager.py:_prepare_db_table/_write_db_pull）。
- 决策：数据都在云端/共享 PostGIS 时，"数据过网络"变成"引用过网络"——上述契约只需一个"元数据表名"在 MCP 协议里传。

## 2. 架构

数据都在共享 PostGIS。**数据的逻辑名 = 云上元数据表名（方案 A，无映射层）**。`data.fixtures/reference` 的 `id` 直接用元数据表名。

```
data.fixtures id = 元数据表名（如 schools）
        │
        ▼
[平台 adapter] ── tools/call(表名) ──▶ [云上 MCP server] 直连 PostGIS 读写
        │                                    │
        │      ◀── 结果 = 元数据表名引用 ────  │  写结果表（run_id 隔离）
        ▼
[断言引擎] 从 DB 拉结果表 + 参考表比对（result_*）
        │
        ▼
[run 结束] 导出 GeoJSON 存档到 reports/outputs/<run_id>/ + drop 隔离表
```

- **输入注入**：不再需要 env——云上 server 自己从 DB 按元数据表名读表。
- **结果回传**：server 把结果写成 run_id 隔离表，tool result 返回元数据表名；断言引擎从 DB 拉表比对。client 在 run 结束时导出 GeoJSON 存档 + drop 隔离表（清理责任归 client，延续迭代 4 "结果持久化归 client"）。
- **本地文件 `path:` 通道删除**：fixture 只认 `format: db_table`。

## 3. 改动面

| 模块 | 改动 | 规模 |
|---|---|---|
| `models/scenario.py` | transport 收缩为 sse/http，url 必填校验 | 小 |
| `mcp/mcp_tool_adapter.py` | 删 stdio/mock 分支、`GEO_MCP_DATASETS` env 注入、`_persist_result` 拷贝；结果回传改为"服务端元数据表名 → 导出 GeoJSON 存档 + drop 表"；`register_datasets` 语义改为校验"逻辑名=元数据表名"并透传 | 中 |
| `fixtures/fixture_manager.py` | 删 `path:` 分支与 `_read_fixture_metadata`/`geopandas` 读文件；只留 db_table（元数据表名直读/引用） | 中 |
| `assertions/result_comparator.py` | 比对输入从本地文件改为按元数据表名从 DB 拉取（复用 fixture_manager 的 db_table 拉取逻辑） | 中 |
| scenarios/*.yml | 批量迁移：transport mock→sse/http + url；fixtures/reference 改 `format: db_table` + `id`=元数据表名 | 机械 |
| docker-compose.yml | 云上 MCP 服务若纳入 compose 编排则补服务；否则仅文档说明（用户自理） | 小 |
| docs 三处 | guide §3.5、design/00、README 迁移说明 | 机械 |

## 4. 协议约定文档（对接云端，本迭代交付物之一）

放 `docs/design/`（如 `04-MCP服务化数据协议.md`），定义平台侧与云上 server 的对接契约：

1. **数据集注册/引用**：`data.fixtures` 的 `id` == 云上元数据表名；元数据表需在平台侧登记来源（元数据名、schema、几何列、SRID）。
2. **run 注册**：平台启动 run 时通知 server 本次 run_id 与隔离命名（`POST /admin/runs` 注册 `run_id` + alias→表映射）。
3. **结果表命名约定**：`<run_id>_<alias>` 或独立 schema，防并发 run 撞表。
4. **清理责任**：run 结束 client 导出 GeoJSON 到 `reports/outputs/<run_id>/` + drop 隔离表。
5. **结果回传格式**：tool result 的 `dataset`/`handle` 字段承载元数据表引用。

## 5. 关键设计点与风险

1. **run 隔离**：结果表带 run_id 前缀或独立 schema；client drop 兜底，残留有清理脚本/约定。
2. **存量兼容过渡**：`transport: mock` 场景全挂。给一个版本窗口：mock 别名自动映射 `http://127.0.0.1:<默认端口>`（一行代码），文档标注废弃；**一个版本后删除**。
3. **硬依赖变化**：PostGIS 成为数据类场景（skill + result_*）的硬前提；纯对话 agent 场景不受影响。"零依赖快速试跑"消失，换 compose postgis 覆盖。
4. **调试链路变长**：远程挂了表现为超时而非 stderr；fail-fast 的 missing_tools 已区分"连不上 vs 工具缺失"，够用。
5. **跨库边界写死**：限定同一 PostGIS 实例多 schema；真跨实例 FDW/dblink 明确不做。
6. **映射层砍掉**：`id` 直接用云上元数据表名，不做 `schools → schools_a` 别名映射层——避免又引入冗余。
7. **硬切，无过渡别名**（2026-08-25 用户拍板）：`transport: mock/stdio` 删除并校验报错，存量场景一次性全改，不设 mock→http 别名兼容窗口。

## 6. 验收标准

- [ ] 全部场景 transport 无 mock/stdio；`transport: mock` 校验直接报错（无别名兼容）。
- [ ] skill 模式全流程真机跑通：db_table 输入（元数据表名）→ 云上 server 计算 → 结果入 DB → result_* 断言过 → 报告含 GeoJSON 存档。
- [ ] 并发两个 run 不撞表、互不留残留。
- [ ] 协议约定文档成文（设计/04-…），云上 server 按此对接可通。
- [ ] 复盘一篇入 retrospective/。

## 7. 实施步骤

按依赖顺序推进：

1. **协议约定文档先行**：`docs/design/04-MCP服务化数据协议.md`——数据集注册、run 注册、结果表命名、清理责任、结果回传格式。两端对接契约以它为准。
2. **models 层**：`transport` 字段收紧为 sse/http，`url` 必填；`mock`/`stdio` 校验报错。
3. **fixture_manager**：删 `path:` 分支与 `_read_fixture_metadata`/`geopandas` 读文件；只留 db_table（元数据表名直读/引用）；`cleanup` 语义调整为"导出存档 + drop 隔离表"。
4. **adapter**：删 stdio/mock 分支、`GEO_MCP_DATASETS` env 注入、`_persist_result` 拷贝；结果回传改为"元数据表引用 → 导出 GeoJSON 存档 + drop 表"；`register_datasets` 改为校验"id=元数据表名"并透传。
5. **result_comparator**：比对输入改按元数据表名从 DB 拉取（复用 fixture_manager 的 db_table 拉取逻辑）。
6. **场景批量迁移**：8 个场景 transport mock→sse/http + url；fixtures/reference 改 `format: db_table`，`id` 用云上元数据表名（数据已在云上库）。
7. **run 注册端点**：adapter 连接时调 `POST /admin/runs` 注册 run_id + 隔离命名。
8. **文档同步**：guide §3.5、design/00、README。

## 8. 执行前提（用户自理，已配好）

- 云上 MCP 服务 + server（只认元数据表名）。
- 共享 PostGIS，数据（原文件/参考文件）已在库中。
- server 端口与 url 约定（协议文档第 1 步落定）。
