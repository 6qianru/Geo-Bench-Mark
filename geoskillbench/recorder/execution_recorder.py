from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from geoskillbench.models.result import ToolCallRecord
from geoskillbench.models.skill import LoadedSkillReference


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ExecutionRecorder:
    def __init__(self, scenario_id: str) -> None:
        self.scenario_id = scenario_id
        self.skill_loaded: list[str] = []
        self.loaded_skill_references: list[LoadedSkillReference] = []
        self.conversation: list[dict[str, Any]] = []
        self.tool_calls: list[ToolCallRecord] = []
        self.final_output: dict[str, Any] = {}
        self.errors: list[str] = []
        self.external_interactions: list[dict[str, Any]] = []  # orchestrator 模式下与外部 agent 的每轮指令/回答
        self._event_order = 0

    def record_skill_load(self, skill_id: str) -> None:
        self.skill_loaded.append(skill_id)

    def record_conversation(self, conversation: list[dict[str, Any]]) -> None:
        self.conversation = conversation

    def record_tool_calls(self, calls: list[ToolCallRecord]) -> None:
        for call in calls:
            self._event_order += 1
            call.order = call.order or self._event_order
            call.recorded_at = call.recorded_at or utc_now()
        self.tool_calls.extend(calls)

    def record_skill_reference_loaded(self, path: str, title: str | None = None, content_hash: str | None = None) -> None:
        self._event_order += 1
        self.loaded_skill_references.append(
            LoadedSkillReference(
                path=path,
                title=title,
                loaded_at=utc_now(),
                loaded_by="executor",
                content_hash=content_hash,
                order=self._event_order,
            )
        )

    def record_external_interaction(self, interaction: dict[str, Any]) -> None:
        self.external_interactions.append(interaction)

    def record_final_output(self, final_output: dict[str, Any]) -> None:
        self.final_output = final_output

    def record_error(self, message: str) -> None:
        self.errors.append(message)
