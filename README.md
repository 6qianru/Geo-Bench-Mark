# GeoSkillBench

GeoSkillBench is an MVP GIS Agent Skill / agent evaluation system built from the design in `docs/design/00-系统总体设计.md`. See `docs/README.md` for the full documentation index.

## Backend

```bash
cd /mnt/d/Projects/geo-skill-bench/geoskillbench
./scripts/start_backend.sh
```

Backend endpoints:

- `GET /health`
- `GET /api/scenarios`
- `GET /api/scenarios/schema`
- `POST /api/scenarios` — create scenario (validated)
- `GET/PUT/DELETE /api/scenarios/{scenario_id}` — YAML source round-trip (edit keeps comments), delete config file only
- `GET /api/skills`
- `GET /api/skills/{skill_id}`
- `GET /api/skills/{skill_id}/files?path=...`
- `GET /api/executors`
- `POST /api/validate`
- `POST /api/list-tools`
- `POST /api/run`
- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/events`
- `GET /api/reports`
- `GET /api/reports/{scenario_id}`
- `GET /api/runs`, `GET /api/runs/{run_id}`

Run history in the DB is auto-pruned to the most recent N rows (`GEO_BENCH_HISTORY_KEEP`, default 100) on save and at startup. Report files under `reports/` are not pruned.

The frontend now uses the task endpoints and subscribes to `/api/tasks/{task_id}/events` with SSE for live stage updates.

## Skill Package Support

The backend now supports:

- single-file skills via `load_mode: file`
- skill package directories via `load_mode: package`
- skill package zip files via `load_mode: package_zip`

MVP package behavior:

- only `SKILL.md` is loaded into the initial executor prompt
- package `references/` are indexed but not injected up front
- package references are loaded lazily through the internal `load_skill_reference` tool
- reference load events are recorded and can be asserted in scenarios and reports

## Executor Modes

The backend now exposes a pluggable session-based executor layer:

- `skill`: default executor for local skill evaluation (historical alias `langgraph`)
- `orchestrator`: a local LLM agent drives the external agent over multiple instructions
- `external_driven`: the external agent leads autonomously and asks back when info is missing; a shared `UserSimulator` answers
- `http_agent`: user_task passed straight through, single-shot Q&A
- `nanobot`: nanobot-compatible executor contract

Current `skill` behavior:

- If `langchain`, `langgraph`, and `langchain-openai` are installed and `runtime.agent_model` points to a real model, GeoSkillBench uses the real LangGraph ReAct executor.
- If the runtime dependencies are missing, or the scenario uses a compatibility model such as `rule-based-agent`, the system falls back to the shared heuristic executor.
- The actual runtime path is exposed in:
  - `GET /api/executors`
  - task/session SSE events
  - run result `final_output.runtime_mode` and `final_output.runtime_metadata`

Current `nanobot` behavior:

- If a real `nanobot` Python runtime is installed later, this is the integration point.
- In the current environment, `NanobotExecutor` runs in compatibility mode and preserves the same executor/session/task APIs while annotating the result with an executor note.

## Frontend

```bash
cd /mnt/d/Projects/geo-skill-bench/geoskillbench
./scripts/start_frontend.sh
```

To start both from one terminal:

```bash
cd /mnt/d/Projects/geo-skill-bench/geoskillbench
./scripts/start_all.sh
```

Default frontend URL: `http://127.0.0.1:5173`

If you need a custom backend URL:

```bash
cd /mnt/d/Projects/geo-skill-bench/geoskillbench/frontend
VITE_API_BASE=http://127.0.0.1:8000 npm run dev
```

## CLI

```bash
python -m geoskillbench.cli validate scenarios/buffer_school_500m_001.yml
python -m geoskillbench.cli list-tools scenarios/buffer_school_500m_001.yml
python -m geoskillbench.cli run scenarios/buffer_school_500m_001.yml --output reports
```

## Docker 部署

两个容器镜像（backend + frontend/nginx 反代），同源消灭 CORS，compose 编排。

### 起服务

```bash
# 全栈：backend + frontend + postgis
./scripts/start_docker.sh
# 或额外带 mock-agent（离线验证外部 agent）
./scripts/start_docker.sh mock
# 等价 docker compose 命令：
#   docker compose up --build
#   docker compose --profile mock up --build
```

起好后：
- 前端：`http://127.0.0.1:5173`
- 后端 API：`http://127.0.0.1:8000`（`/health`、`/docs`）
- PostgreSQL/PostGIS：`127.0.0.1:5432`（默认 `geo/geo/geoskillbench`）

### 密钥与配置（均不进镜像）

- `.env`：`DEEPSEEK_API_KEY`、`AGENTX_API_KEY` 等，由 compose `env_file` 注入。
- `models.yaml`：compose 只读挂载到 `/app/models.yaml`。
- 首次需在项目根建 `.env` 与 `models.yaml`（都被 `.gitignore` 排除）。

### 数据库

compose 默认编排 PostGIS（backend 的 `DATABASE_URL` 指向 `postgis` 服务，healthcheck 等 DB 就绪后才起）。不想要 PostGIS 时，注释掉 `postgis` 服务并把 backend 的 `DATABASE_URL` 留空即可——代码自动回退 SQLite 文件库（`reports.db`，见 `geoskillbench/api/db.py`），零改动。

### 数据持久化

- `reports_data` 卷：报告产物 `reports/` + SQLite `reports.db`
- `postgis_data` 卷：PostGIS 数据

### 外部智能体网络说明

平台要评测的是外部 HTTP agent（场景 `agent.endpoint`）。分两种情况：

- **agent 已是容器服务**：让它在同一 compose 网络，场景 `endpoint` 用服务名，如
  `http://mock-agent:8901/agentx/workflowstudio/api/v1/run/mock-askback-001`。
- **agent 跑在宿主机 / 局域网**：容器默认能访问宿主所在局域网 IP（如 `192.168.x.x:8490`），
  直接用局域网地址即可。若 agent 只监听宿主机 `127.0.0.1`，容器无法直达——需把 agent 也容器化
  进同一网络，或改用局域网监听地址。

### 构建说明（首次可能因网络慢）

- Python 依赖走清华 pip 镜像（`Dockerfile.backend` 内 `-i`）。
- 前端 npm 走 npmmirror registry（`Dockerfile.frontend` 内 `--registry`）。

## Real Model Configuration

Create `models.yaml` in the repo root if you want to use a model alias with the real `skill` executor:

```yaml
models:
  deepseek-v4-flash:
    provider: openai_compatible
    model: deepseek-v4-flash
    base_url: https://api.deepseek.com/v1
    api_key_env: DEEPSEEK_API_KEY
```

Then point a scenario at that alias, for example:

```yaml
runtime:
  executor: skill
  agent_model: deepseek-v4-flash
```
