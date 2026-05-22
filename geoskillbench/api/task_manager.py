from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from geoskillbench.runner import STAGES, TestRunner


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TaskState:
    task_id: str
    scenario_path: str
    output_dir: str
    run_config: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    current_stage: str | None = None
    stage_results: dict[str, str] = field(default_factory=lambda: {stage: "PENDING" for stage in STAGES})
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "scenario_path": self.scenario_path,
            "output_dir": self.output_dir,
            "run_config": dict(self.run_config),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_stage": self.current_stage,
            "stage_results": dict(self.stage_results),
            "result": self.result,
            "error": self.error,
        }


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}

    def list_tasks(self) -> list[dict[str, Any]]:
        return [task.snapshot() for task in sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)]

    def get_task(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    async def create_task(self, scenario_path: str, output_dir: str, run_config: dict[str, Any] | None = None) -> TaskState:
        task = TaskState(task_id=uuid4().hex, scenario_path=scenario_path, output_dir=output_dir, run_config=run_config or {})
        self._tasks[task.task_id] = task
        await self._push_event(task, {"type": "task_created", "task": task.snapshot()})
        asyncio.create_task(self._run_task(task))
        return task

    async def event_stream(self, task_id: str):
        task = self._tasks[task_id]
        next_index = 0
        while True:
            async with task.condition:
                if next_index >= len(task.events) and task.status not in {"completed", "failed"}:
                    try:
                        await asyncio.wait_for(task.condition.wait(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                while next_index < len(task.events):
                    event = task.events[next_index]
                    next_index += 1
                    yield self._format_sse(event)
                if task.status in {"completed", "failed"} and next_index >= len(task.events):
                    break

    async def _run_task(self, task: TaskState) -> None:
        loop = asyncio.get_running_loop()
        task.status = "running"
        task.updated_at = utc_now()
        await self._push_event(task, {"type": "task_started", "task": task.snapshot()})

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(asyncio.create_task, self._handle_runner_event(task.task_id, event))

        try:
            result = await asyncio.to_thread(TestRunner().run, task.scenario_path, task.output_dir, emit, task.run_config)
            task.status = "completed" if result.status == "passed" else "failed"
            task.result = result.model_dump()
            task.updated_at = utc_now()
            if result.status != "passed":
                task.error = "; ".join(result.errors) if result.errors else "Task failed."
            await self._push_event(task, {"type": "task_finished", "task": task.snapshot()})
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            task.updated_at = utc_now()
            await self._push_event(task, {"type": "task_finished", "task": task.snapshot()})

    async def _handle_runner_event(self, task_id: str, event: dict[str, Any]) -> None:
        task = self._tasks[task_id]
        task.updated_at = utc_now()
        if "stage" in event:
            task.current_stage = event["stage"]
        if "stage_results" in event:
            task.stage_results = dict(event["stage_results"])
        if event.get("type") == "error":
            task.error = event.get("message")
        if event.get("type") == "result":
            task.result = event.get("result")
        await self._push_event(task, {"type": event.get("type", "event"), "task": task.snapshot(), "payload": event})

    async def _push_event(self, task: TaskState, event: dict[str, Any]) -> None:
        async with task.condition:
            task.events.append(event)
            task.condition.notify_all()

    def _format_sse(self, event: dict[str, Any]) -> str:
        return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
