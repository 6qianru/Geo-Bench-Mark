# GeoSkillBench

GeoSkillBench is an MVP GIS Agent Skill evaluation system built from the design in `docs/GeoSkillBench完整系统设计文档-v0.3.md`.

## Backend

```bash
cd /mnt/d/Projects/geo-skill-bench/geoskillbench
./scripts/start_backend.sh
```

Backend endpoints:

- `GET /health`
- `GET /api/scenarios`
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

- `skill`: default executor implementation for GeoSkillBench (local skill evaluation; historical alias `langgraph`)
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
