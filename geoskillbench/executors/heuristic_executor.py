from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from geoskillbench.executors.base import Executor
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
from geoskillbench.models.result import ExecutorSession, ExecutorSessionRequest, ExecutorStepResult
from geoskillbench.models.result import ToolCallRecord
from geoskillbench.models.skill import AgentSkill
from geoskillbench.skills.reference_tool import SkillReferenceTool


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class HeuristicSessionState:
    request: ExecutorSessionRequest
    skill: AgentSkill
    reference_tool: SkillReferenceTool | None = None
    conversation: list[dict[str, str]] = field(default_factory=list)
    resolved_dataset: str | None = None
    resolved_distance: float | None = None
    latest_metadata: dict[str, Any] | None = None
    finished: bool = False
    final_response: str = ""


class HeuristicSessionExecutor(Executor):
    executor_type = "langgraph"

    def __init__(self, adapter: MCPToolAdapter, executor_type: str = "langgraph", compatibility_note: str | None = None) -> None:
        self.adapter = adapter
        self.executor_type = executor_type
        self.compatibility_note = compatibility_note
        self.sessions: dict[str, HeuristicSessionState] = {}

    def create_session(self, request: ExecutorSessionRequest) -> ExecutorSession:
        session_id = uuid4().hex
        skill = AgentSkill.model_validate(request.test_context["skill"])
        reference_tool = SkillReferenceTool(skill, request.test_context["_recorder"]) if skill.type == "prompt_skill_package" else None
        self.sessions[session_id] = HeuristicSessionState(request=request, skill=skill, reference_tool=reference_tool)
        return ExecutorSession(
            session_id=session_id,
            executor_type=self.executor_type,
            scenario_id=request.scenario_id,
            skill_id=request.skill_id,
            created_at=now_iso(),
        )

    def send_message(self, session_id: str, message: str) -> ExecutorStepResult:
        state = self.sessions[session_id]
        state.conversation.append({"role": "user", "content": message})
        internal_calls: list[ToolCallRecord] = []

        datasets = state.request.test_context.get("datasets", {})
        if state.resolved_dataset is None:
            state.resolved_dataset = self._infer_dataset_alias(message, datasets)
            if state.resolved_dataset is None:
                response = "[NEED_INTERACTION]\n请确认要使用哪个数据集。"
                state.conversation.append({"role": "assistant", "content": response})
                return ExecutorStepResult(response=response, need_interaction=True, finished=False)

        if state.resolved_distance is None:
            state.resolved_distance = self._infer_distance(message)
            if state.resolved_distance is None:
                response = "[NEED_INTERACTION]\n请确认缓冲距离是多少米。"
                state.conversation.append({"role": "assistant", "content": response})
                return ExecutorStepResult(response=response, need_interaction=True, finished=False)

        if state.latest_metadata is None:
            internal_calls.extend(self._load_required_references(state, ["plan", "data", "metadata"]))
            metadata_call = self.adapter.invoke("query_dataset_metadata", {"dataset": state.resolved_dataset})
            state.latest_metadata = metadata_call.result or {}
            tool_calls = internal_calls + [metadata_call]
            if metadata_call.status != "success":
                response = f"数据元信息查询失败：{metadata_call.error_message or 'unknown error'}"
                state.conversation.append({"role": "assistant", "content": response})
                return ExecutorStepResult(response=response, finished=True, tool_calls=tool_calls, error_message=metadata_call.error_message)
        else:
            tool_calls = internal_calls

        working_dataset = state.resolved_dataset
        target_crs = (state.latest_metadata or {}).get("crs", "")
        available_tools = state.request.test_context.get("mcp_tools", {})
        if target_crs.upper() == "EPSG:4326" and "reproject_dataset" in available_tools:
            reproject_call = self.adapter.invoke(
                "reproject_dataset",
                {"dataset": state.resolved_dataset, "target_crs": "EPSG:3857", "output_alias": f"{state.resolved_dataset}_metric"},
            )
            tool_calls.append(reproject_call)
            if reproject_call.status == "success":
                working_dataset = reproject_call.result["dataset"]
                target_crs = reproject_call.result["crs"]

        tool_calls.extend(self._load_required_references(state, ["buffer", "result", "output"]))
        buffer_call = self.adapter.invoke(
            "create_buffer",
            {
                "dataset": working_dataset,
                "distance": state.resolved_distance,
                "distance_unit": "meter",
                "output_alias": "buffer_result",
            },
        )
        tool_calls.append(buffer_call)
        if buffer_call.status != "success":
            response = f"缓冲区分析失败：{buffer_call.error_message or 'unknown error'}"
            state.conversation.append({"role": "assistant", "content": response})
            return ExecutorStepResult(response=response, finished=True, tool_calls=tool_calls, error_message=buffer_call.error_message)

        prefix = "[FINAL]\n"
        response = (
            f"{prefix}已完成 {state.resolved_dataset} 数据的 {state.resolved_distance:g} 米缓冲区分析。"
            f" 结果数据句柄为 {buffer_call.result['handle']}，输出 CRS 为 {target_crs}。"
        )
        artifacts = {"result_dataset": buffer_call.result}
        if self.compatibility_note:
            artifacts["executor_note"] = self.compatibility_note
        state.final_response = response
        state.finished = True
        state.conversation.append({"role": "assistant", "content": response})
        return ExecutorStepResult(response=response, need_interaction=False, finished=True, tool_calls=tool_calls, artifacts=artifacts)

    def close_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    def _infer_dataset_alias(self, text: str, datasets: dict[str, Any]) -> str | None:
        lowered = text.lower()
        for alias in datasets:
            if alias.lower() in lowered:
                return alias
        if len(datasets) == 1:
            return next(iter(datasets.keys()))
        return None

    def _infer_distance(self, text: str) -> float | None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*米", text)
        if match:
            return float(match.group(1))
        return None

    def _load_required_references(self, state: HeuristicSessionState, tags_or_keywords: list[str]) -> list[ToolCallRecord]:
        if state.reference_tool is None or not state.skill.lazy_load_references:
            return []
        loaded_paths = {call.arguments.get("path") for call in state.request.test_context["_loaded_reference_calls"]}
        selected: list[ToolCallRecord] = []
        for reference in state.skill.references:
            if reference.path in loaded_paths:
                continue
            haystack = " ".join([reference.title, reference.path, *reference.tags, *reference.trigger_keywords]).lower()
            if reference.required or any(token.lower() in haystack for token in tags_or_keywords):
                content = state.reference_tool.load_skill_reference(reference.path)
                record = ToolCallRecord(
                    tool_name="load_skill_reference",
                    arguments={"path": reference.path},
                    result={"path": reference.path, "title": reference.title, "excerpt": content[:160]},
                    status="success",
                    tool_type="skill_internal",
                )
                state.request.test_context["_loaded_reference_calls"].append(record)
                selected.append(record)
        return selected
