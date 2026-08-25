from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError
import yaml

from geoskillbench.api.task_manager import TaskManager
from geoskillbench.executors.skill_executor import SkillExecutor
from geoskillbench.executors.nanobot_executor import NanobotExecutor
from geoskillbench.models.scenario import Scenario, SkillConfig
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
from geoskillbench.runner import TestRunner


ROOT_DIR = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = ROOT_DIR / "scenarios"
SKILLS_DIR = ROOT_DIR / "skills"
REPORTS_DIR = ROOT_DIR / "reports"


class PathRequest(BaseModel):
    path: str


class RunRequest(BaseModel):
    path: str
    output_dir: str = "reports"
    executor: str | None = None
    memory_enabled: bool | None = None


class ScenarioCreateRequest(BaseModel):
    scenario: dict
    overwrite: bool = False


class ScenarioContentRequest(BaseModel):
    content: str  # 场景 YAML 原文（编辑保存用，后端校验后原样写回）


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动时清理历史积压：只保留最近 GEO_BENCH_HISTORY_KEEP 条 run_history（默认 100）。
    # DB 不可用时静默跳过——历史清理是增强能力，不阻塞服务启动。
    try:
        from geoskillbench.api import db

        pruned = db.prune_reports()
        if pruned:
            import logging

            logging.getLogger(__name__).info("pruned %d stale run history row(s)", pruned)
    except Exception:
        pass
    yield


app = FastAPI(title="GeoSkillBench API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
task_manager = TaskManager()




def _runner() -> TestRunner:
    return TestRunner()


def _scenario_listing() -> list[dict]:
    runner = _runner()
    items: list[dict] = []
    for path in sorted(SCENARIOS_DIR.rglob("*.yml")):
        try:
            scenario = runner.scenario_loader.load(str(path))
            items.append(
                {
                    "id": scenario.id,
                    "name": scenario.name,
                    "version": scenario.version,
                    "type": scenario.type,  # agent_test / agent_skill_test，前端据此决定显示哪些 stage
                    "path": str(path.relative_to(ROOT_DIR)),
                    "description": scenario.description,
                    "skill_id": scenario.target.skill_id,
                    "executor": scenario.runtime.executor,
                }
            )
        except Exception as exc:
            items.append(
                {
                    "id": path.stem,
                    "name": path.name,
                    "version": "unknown",
                    "type": "unknown",
                    "path": str(path.relative_to(ROOT_DIR)),
                    "description": f"Failed to load scenario: {exc}",
                    "skill_id": "unknown",
                }
            )
    return items


def _skill_listing() -> list[dict]:
    runner = _runner()
    items: list[dict] = []
    seen_paths: set[str] = set()
    for path in sorted(SKILLS_DIR.rglob("*.skill.yml")) + sorted(SKILLS_DIR.rglob("*.yml")):
        rel = str(path.relative_to(ROOT_DIR))
        if rel in seen_paths:
            continue
        seen_paths.add(rel)
        try:
            skill = runner.skill_loader.load(SkillConfig(load_mode="file", path=str(path.relative_to(SKILLS_DIR))), str(SKILLS_DIR))
            items.append(
                {
                    "id": skill.id,
                    "name": skill.name,
                    "version": skill.version,
                    "type": skill.type,
                    "path": rel,
                    "description": skill.description,
                    "entry_file": skill.entry_file,
                    "references_count": len(skill.references),
                }
            )
        except Exception as exc:
            items.append(
                {
                    "id": path.stem,
                    "name": path.name,
                    "version": "unknown",
                    "path": rel,
                    "description": f"Failed to load skill: {exc}",
                }
            )
    for skill_md in sorted(SKILLS_DIR.rglob("SKILL.md")):
        package_dir = skill_md.parent
        rel = str(package_dir.relative_to(ROOT_DIR))
        if rel in seen_paths:
            continue
        seen_paths.add(rel)
        try:
            skill = runner.skill_loader.load(SkillConfig(load_mode="package", path=str(package_dir.relative_to(SKILLS_DIR))), str(SKILLS_DIR))
            items.append(
                {
                    "id": skill.id,
                    "name": skill.name,
                    "version": skill.version,
                    "type": skill.type,
                    "path": rel,
                    "description": skill.description,
                    "entry_file": skill.entry_file,
                    "references_count": len(skill.references),
                }
            )
        except Exception as exc:
            items.append(
                {
                    "id": package_dir.name,
                    "name": package_dir.name,
                    "version": "unknown",
                    "type": "prompt_skill_package",
                    "path": rel,
                    "description": f"Failed to load skill package: {exc}",
                    "entry_file": "SKILL.md",
                    "references_count": 0,
                }
            )
    return items


def _load_skill_from_relative_path(relative_path: str):
    runner = _runner()
    target = ROOT_DIR / relative_path
    if target.is_dir():
        return runner.skill_loader.load(SkillConfig(load_mode="package", path=str(target.relative_to(SKILLS_DIR))), str(SKILLS_DIR))
    if target.suffix == ".zip":
        return runner.skill_loader.load(SkillConfig(load_mode="package_zip", path=str(target.relative_to(SKILLS_DIR))), str(SKILLS_DIR))
    return runner.skill_loader.load(SkillConfig(load_mode="file", path=str(target.relative_to(SKILLS_DIR))), str(SKILLS_DIR))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    return _scenario_listing()


# ---- 新建场景（前端"新建 Scenario"表单落盘）----

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_scenario_id(scenario_id: str) -> None:
    """id 即文件名，只允许安全字符，防路径穿越。"""
    if not _SAFE_ID_RE.fullmatch(scenario_id):
        raise HTTPException(400, "场景 ID 仅允许字母、数字、下划线、中划线")


@app.get("/api/scenarios/schema")
def scenario_schema() -> list[dict]:
    """返回前端"新建 Scenario"表单的定义（字段/必填/默认值/分组/模式联动）。"""
    from geoskillbench.api.scenario_schema import get_form_schema

    return get_form_schema()


@app.post("/api/scenarios")
def create_scenario(request: ScenarioCreateRequest) -> dict:
    """校验前端提交的场景 dict，落盘为 scenarios/<id>.yml。

    - id 同时用作文件名，做安全清洗（防路径穿越）；重复 id 且未显式 overwrite → 409。
    - 复用 Scenario.model_validate 做完整校验（含"agent_skill_test 必须带 skill"等规则）。
    - 清掉前端不填的空结构（空断言/预期行为/空 mcp/data），让生成的 yml 贴近手写。
    """
    data = dict(request.scenario)
    scenario_id = str(data.get("id") or "").strip()
    if not _SAFE_ID_RE.fullmatch(scenario_id):
        raise HTTPException(400, "场景 ID 仅允许字母、数字、下划线、中划线")
    target = SCENARIOS_DIR / f"{scenario_id}.yml"
    if target.exists() and not request.overwrite:
        raise HTTPException(409, f"场景 {scenario_id} 已存在，请确认是否覆盖？")

    data.setdefault("target", {})
    # 剥离与评测模式不符的残留块：agent_test 不带 skill、agent_skill_test 不带 agent。
    # 正常提交不受影响（对应模式本来就是要带的那块）；这里是兜底，防止前端残留字段
    # 拼出残缺对象导致校验失败。
    if data.get("type") == "agent_test":
        data.pop("skill", None)
        if not data.get("agent"):
            data.pop("agent", None)
    else:
        data.pop("agent", None)
        if not data.get("skill"):
            data.pop("skill", None)
    try:
        scenario = Scenario.model_validate(data)
    except ValidationError as exc:
        # 参数校验失败返回可读的 400，而不是笼统的 500
        raise HTTPException(400, f"场景参数不合法：{exc.errors()}")
    payload = scenario.model_dump(exclude_none=True)
    # 清空结构：前端常用字段表单不暴露的块（断言/预期行为/MCP/数据源），空时不写入
    for key in ("expected_behavior", "assertions"):
        if not payload.get(key):
            payload.pop(key, None)
    if not payload.get("mcp", {}).get("servers"):
        payload.pop("mcp", None)
    elif not payload.get("mcp", {}).get("tools", {}).get("required") and not payload.get("mcp", {}).get("tools", {}).get("optional"):
        # mcp 只有 servers、tools 全空（表单只写 servers，工具由 server 自动发现）→ 剥离空 tools 块
        payload["mcp"].pop("tools", None)
    # data 块（fixtures + reference）都为空才清掉；只配参考（fixtures 空、reference 有值）时保留
    if not payload.get("data"):
        payload.pop("data", None)

    target.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"id": scenario.id, "path": f"scenarios/{target.name}"}


@app.get("/api/scenarios/{scenario_id}")
def get_scenario(scenario_id: str) -> dict:
    """返回场景 YAML 原文（前端"管理 → 编辑"回显用，原文透传保留注释）。"""
    _validate_scenario_id(scenario_id)
    target = SCENARIOS_DIR / f"{scenario_id}.yml"
    if not target.exists():
        raise HTTPException(404, f"场景不存在：{scenario_id}")
    return {
        "id": scenario_id,
        "path": f"scenarios/{target.name}",
        "content": target.read_text(encoding="utf-8"),
    }


@app.put("/api/scenarios/{scenario_id}")
def update_scenario(scenario_id: str, request: ScenarioContentRequest) -> dict:
    """更新场景：校验 yaml 后原样写回（保留注释/格式）。id 即文件名，不可修改。"""
    _validate_scenario_id(scenario_id)
    target = SCENARIOS_DIR / f"{scenario_id}.yml"
    if not target.exists():
        raise HTTPException(404, f"场景不存在：{scenario_id}")
    content = request.content.strip()
    if not content:
        raise HTTPException(400, "场景内容为空")
    try:
        data = yaml.safe_load(content)
        scenario = Scenario.model_validate(data)
    except yaml.YAMLError as exc:
        raise HTTPException(400, f"YAML 解析失败：{exc}")
    except ValidationError as exc:
        raise HTTPException(400, f"场景参数不合法：{exc.errors()}")
    if scenario.id != scenario_id:
        raise HTTPException(400, f"场景 id 不能修改（文件名即 id）：{scenario.id} ≠ {scenario_id}")
    target.write_text(content + ("\n" if not content.endswith("\n") else ""), encoding="utf-8")
    return {"id": scenario.id, "path": f"scenarios/{target.name}"}


@app.delete("/api/scenarios/{scenario_id}")
def delete_scenario(scenario_id: str) -> dict:
    """删除场景配置文件。历史报告（reports/ 与 DB run_history）按 scenario_id 独立留存，不受影响。"""
    _validate_scenario_id(scenario_id)
    target = SCENARIOS_DIR / f"{scenario_id}.yml"
    if not target.exists():
        raise HTTPException(404, f"场景不存在：{scenario_id}")
    target.unlink()
    return {"deleted": scenario_id}


@app.get("/api/skills")
def list_skills() -> list[dict]:
    return _skill_listing()


@app.get("/api/skills/{skill_id}")
def get_skill(skill_id: str) -> dict:
    for item in _skill_listing():
        if item["id"] == skill_id:
            skill = _load_skill_from_relative_path(item["path"])
            return {
                "skill_id": skill.id,
                "name": skill.name,
                "version": skill.version,
                "type": skill.type,
                "entry_file": skill.entry_file,
                "path": item["path"],
                "references": [reference.model_dump() for reference in skill.references],
                "metadata": skill.metadata,
                "description": skill.description,
            }
    raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")


@app.get("/api/skills/{skill_id}/files")
def get_skill_file(skill_id: str, path: str) -> dict:
    skill_info = get_skill(skill_id)
    skill = _load_skill_from_relative_path(skill_info["path"])
    base_dir = Path(skill.base_dir or ROOT_DIR / skill_info["path"]).resolve()
    target = (base_dir / path).resolve()
    if not str(target).startswith(str(base_dir)):
        raise HTTPException(status_code=400, detail="Invalid skill file path")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Skill file not found: {path}")
    return {"path": path, "content": target.read_text(encoding="utf-8")}


@app.get("/api/executors")
def list_executors() -> list[dict]:
    adapter = MCPToolAdapter()
    skill = SkillExecutor(adapter)
    nanobot = NanobotExecutor(adapter)
    return [
        {
            "id": "skill",
            "name": "SkillExecutor",
            "available": skill.real_runtime_available,
            "default": True,
            "runtime_mode": "real" if skill.real_runtime_available else "compatibility",
            "issue": skill.runtime_issue,
        },
        {
            "id": "nanobot",
            "name": "NanobotExecutor",
            "available": nanobot.compatibility_note is None,
            "default": False,
            "runtime_mode": "real" if nanobot.compatibility_note is None else "compatibility",
            "issue": nanobot.compatibility_note,
        },
        {
            "id": "http_agent",
            "name": "HttpAgentExecutor",
            "available": True,
            "default": False,
            "runtime_mode": "compatibility",
            "issue": "外部 HTTP 智能体黑盒接入（见 docs/design/01-Agent接入契约.md）",
        },
    ]


@app.post("/api/validate")
def validate_scenario(request: PathRequest) -> dict:
    scenario_path = ROOT_DIR / request.path
    if not scenario_path.exists():
        raise HTTPException(status_code=404, detail=f"Scenario not found: {request.path}")
    return _runner().validate(str(scenario_path))


@app.post("/api/list-tools")
def list_tools(request: PathRequest) -> list[dict]:
    scenario_path = ROOT_DIR / request.path
    if not scenario_path.exists():
        raise HTTPException(status_code=404, detail=f"Scenario not found: {request.path}")
    return _runner().list_tools(str(scenario_path))


@app.post("/api/run")
def run_scenario(request: RunRequest) -> dict:
    scenario_path = ROOT_DIR / request.path
    if not scenario_path.exists():
        raise HTTPException(status_code=404, detail=f"Scenario not found: {request.path}")
    output_dir = ROOT_DIR / request.output_dir
    run_config = {
        key: value
        for key, value in {"executor": request.executor, "memory_enabled": request.memory_enabled}.items()
        if value is not None
    }
    result = _runner().run(str(scenario_path), str(output_dir), run_config=run_config)
    return result.model_dump()


@app.post("/api/tasks")
async def create_task(request: RunRequest) -> dict:
    scenario_path = ROOT_DIR / request.path
    if not scenario_path.exists():
        raise HTTPException(status_code=404, detail=f"Scenario not found: {request.path}")
    output_dir = ROOT_DIR / request.output_dir
    run_config = {
        key: value
        for key, value in {"executor": request.executor, "memory_enabled": request.memory_enabled}.items()
        if value is not None
    }
    task = await task_manager.create_task(str(scenario_path), str(output_dir), run_config)
    return task.snapshot()


@app.get("/api/tasks")
def list_tasks() -> list[dict]:
    return task_manager.list_tasks()


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    task = task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task.snapshot()


@app.get("/api/tasks/{task_id}/events")
async def stream_task_events(task_id: str):
    task = task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return StreamingResponse(task_manager.event_stream(task_id), media_type="text/event-stream")


@app.get("/api/reports")
def list_reports() -> list[dict]:
    reports: list[dict] = []
    json_dir = REPORTS_DIR / "json"
    markdown_dir = REPORTS_DIR / "markdown"
    for path in sorted(json_dir.glob("*.json")):
        reports.append(
            {
                "scenario_id": path.stem,
                "json_path": str(path.relative_to(ROOT_DIR)),
                "markdown_path": str((markdown_dir / f"{path.stem}.md").relative_to(ROOT_DIR)),
            }
        )
    return reports


@app.get("/api/reports/{scenario_id}")
def get_report(scenario_id: str) -> dict:
    json_path = REPORTS_DIR / "json" / f"{scenario_id}.json"
    md_path = REPORTS_DIR / "markdown" / f"{scenario_id}.md"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"Report not found for scenario {scenario_id}")
    return {
        "scenario_id": scenario_id,
        "json": json_path.read_text(encoding="utf-8"),
        "markdown": md_path.read_text(encoding="utf-8") if md_path.exists() else "",
    }


def _runs_unavailable() -> dict:
    return {"available": False, "runs": [], "error": "数据库不可用：请检查 DATABASE_URL 配置与数据库服务状态"}


@app.get("/api/runs")
def list_runs(scenario_id: str | None = None) -> dict:
    """阶段二：从 DB 查历史评测记录（列表，可选按 scenario_id 过滤）。

    DB 不可用时返回 {"available": false, "runs": []}，不抛 500——
    读历史是增强能力，数据库故障不应让前端崩溃。
    """
    from geoskillbench.api import db

    try:
        rows = db.list_reports(scenario_id=scenario_id)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("DB unavailable, /api/runs degraded: %s", exc)
        result = _runs_unavailable()
        result["error"] = str(exc)
        return result
    # 列表不返回全文，只返回元数据，避免大数据量
    for row in rows:
        row.pop("json", None)
        row.pop("md", None)
    return {"available": True, "runs": rows}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """阶段二：从 DB 查单条评测记录（含报告全文）。DB 不可用返回 503。"""
    from geoskillbench.api import db

    try:
        row = db.get_report(run_id)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("DB unavailable, /api/runs/{id} degraded: %s", exc)
        raise HTTPException(status_code=503, detail=f"数据库不可用：{exc}")
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return row
