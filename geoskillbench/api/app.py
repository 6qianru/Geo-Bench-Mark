from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import yaml

from geoskillbench.api.task_manager import TaskManager
from geoskillbench.executors.langgraph_executor import LangGraphExecutor
from geoskillbench.executors.nanobot_executor import NanobotExecutor
from geoskillbench.models.scenario import SkillConfig
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


app = FastAPI(title="GeoSkillBench API", version="0.2.0")
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
    langgraph = LangGraphExecutor(adapter)
    nanobot = NanobotExecutor(adapter)
    return [
        {
            "id": "langgraph",
            "name": "LangGraphExecutor",
            "available": langgraph.real_runtime_available,
            "default": True,
            "runtime_mode": "real" if langgraph.real_runtime_available else "compatibility",
            "issue": langgraph.runtime_issue,
        },
        {
            "id": "nanobot",
            "name": "NanobotExecutor",
            "available": nanobot.compatibility_note is None,
            "default": False,
            "runtime_mode": "real" if nanobot.compatibility_note is None else "compatibility",
            "issue": nanobot.compatibility_note,
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
