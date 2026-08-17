from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from geoskillbench.executors.heuristic_executor import HeuristicSessionExecutor
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
from geoskillbench.models.result import ExecutorSession, ExecutorSessionRequest, ExecutorStepResult, ToolCallRecord
from geoskillbench.models.skill import AgentSkill
from geoskillbench.runtime.llm import build_llm, load_models_config
from geoskillbench.skills.reference_tool import SkillReferenceTool


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SkillSessionState:
    request: ExecutorSessionRequest
    skill: AgentSkill
    agent: Any
    messages: list[Any] = field(default_factory=list)
    reference_tool: SkillReferenceTool | None = None
    last_response: str = ""
    finished: bool = False
    pending_tool_calls: list[ToolCallRecord] = field(default_factory=list)
    output_artifacts: dict[str, Any] = field(default_factory=dict)


class SkillExecutor(HeuristicSessionExecutor):
    def __init__(self, adapter: MCPToolAdapter) -> None:
        super().__init__(adapter=adapter, executor_type="skill")
        self.real_sessions: dict[str, SkillSessionState] = {}
        self.models_config = load_models_config()
        self.real_runtime_available, self.runtime_issue = self._check_runtime_available()
        self.last_runtime_metadata: dict[str, Any] = {
            "runtime_mode": "compatibility" if self.runtime_issue else "real",
            "runtime_available": self.real_runtime_available,
            "issue": self.runtime_issue,
        }

    def create_session(self, request: ExecutorSessionRequest) -> ExecutorSession:
        model_name = request.role_model_config.get("model", "")
        fallback_reason = self._fallback_reason(model_name)
        if fallback_reason:
            self.compatibility_note = fallback_reason
            self.last_runtime_metadata = {
                "runtime_mode": "compatibility",
                "runtime_available": self.real_runtime_available,
                "issue": fallback_reason,
                "model": model_name,
            }
            fallback_session = super().create_session(request)
            fallback_session.runtime_mode = "compatibility"
            fallback_session.runtime_metadata = dict(self.last_runtime_metadata)
            return fallback_session

        from langchain_core.messages import SystemMessage
        from langgraph.prebuilt import create_react_agent

        session_id = uuid4().hex
        skill = AgentSkill.model_validate(request.test_context["skill"])
        reference_tool = SkillReferenceTool(skill, request.test_context["_recorder"]) if skill.type == "prompt_skill_package" else None
        try:
            llm = build_llm(model_name, temperature=0.0, config=self.models_config)
            system_prompt = self._build_system_prompt(request, skill)
            state = SkillSessionState(request=request, skill=skill, agent=None, messages=[], reference_tool=reference_tool)
            tools = self._build_skill_tools(state)
            agent = create_react_agent(llm, tools=tools, prompt=SystemMessage(content=system_prompt))
            state.agent = agent
            self.real_sessions[session_id] = state
            self.last_runtime_metadata = {
                "runtime_mode": "real",
                "runtime_available": True,
                "model": model_name,
                "tool_count": len(tools),
            }
        except Exception as exc:
            fallback_reason = f"Skill executor initialization failed: {exc}"
            self.compatibility_note = fallback_reason
            self.last_runtime_metadata = {
                "runtime_mode": "compatibility",
                "runtime_available": self.real_runtime_available,
                "issue": fallback_reason,
                "model": model_name,
            }
            fallback_session = super().create_session(request)
            fallback_session.runtime_mode = "compatibility"
            fallback_session.runtime_metadata = dict(self.last_runtime_metadata)
            return fallback_session

        return ExecutorSession(
            session_id=session_id,
            executor_type="skill",
            scenario_id=request.scenario_id,
            skill_id=request.skill_id,
            created_at=now_iso(),
            runtime_mode="real",
            runtime_metadata=dict(self.last_runtime_metadata),
        )

    def send_message(self, session_id: str, message: str) -> ExecutorStepResult:
        if session_id not in self.real_sessions:
            return super().send_message(session_id, message)

        from langchain_core.messages import AIMessage, HumanMessage

        state = self.real_sessions[session_id]
        state.messages.append(HumanMessage(content=message))
        previous_count = len(state.messages)
        result_state = state.agent.invoke({"messages": state.messages})
        updated_messages = result_state.get("messages", [])
        state.messages = updated_messages
        new_messages = updated_messages[previous_count:]

        final_ai_content = ""
        for msg in reversed(new_messages):
            if isinstance(msg, AIMessage) and msg.content:
                final_ai_content = str(msg.content)
                break
        if not final_ai_content:
            for msg in reversed(updated_messages):
                if isinstance(msg, AIMessage) and msg.content:
                    final_ai_content = str(msg.content)
                    break

        need_interaction = final_ai_content.strip().startswith("[NEED_INTERACTION]")
        finished = final_ai_content.strip().startswith("[FINAL]")
        if finished:
            state.finished = True
        state.last_response = final_ai_content

        tool_calls = list(state.pending_tool_calls)
        state.pending_tool_calls.clear()
        return ExecutorStepResult(
            response=final_ai_content,
            need_interaction=need_interaction,
            finished=finished,
            tool_calls=tool_calls,
            artifacts=dict(state.output_artifacts),
        )

    def close_session(self, session_id: str) -> None:
        self.real_sessions.pop(session_id, None)
        super().close_session(session_id)

    def _build_system_prompt(self, request: ExecutorSessionRequest, skill: AgentSkill) -> str:
        datasets = request.test_context.get("datasets", {})
        dataset_context = "\n".join(
            f"- {alias}: handle={dataset['handle']}, geometry_type={dataset.get('geometry_type')}, crs={dataset.get('crs')}"
            for alias, dataset in datasets.items()
        ) or "(none)"
        tool_context = ", ".join(request.test_context.get("mcp_tools", {}).keys()) or "(none)"
        base = (
            "You are a GeoSkillBench GIS Agent Executor.\n"
            "Follow the loaded skill strictly, use only the provided tools and datasets, and do not invent missing data.\n"
            "If required information is missing, reply with [NEED_INTERACTION] followed by a concise question.\n"
            "When the task is complete, reply with [FINAL] followed by the result dataset handle and a short explanation.\n"
            "If a Skill Package is loaded, use load_skill_reference before relying on package references.\n"
            f"Loaded Skill Prompt:\n{request.skill_prompt}\n"
            f"Available datasets:\n{dataset_context}\n"
            f"Available tools: {tool_context}\n"
        )
        if skill.type == "prompt_skill_package":
            base += (
                "This skill is a Skill Package. You initially know only the entry file and reference index.\n"
                "Use load_skill_reference to read any needed package reference before acting on it.\n"
            )
        return base

    def _build_skill_tools(self, state: SkillSessionState) -> list[Any]:
        from langchain_core.tools import tool

        created_tools: list[Any] = []
        request = state.request
        reference_tool = state.reference_tool

        def append_record(record: ToolCallRecord) -> None:
            state.pending_tool_calls.append(record)

        if reference_tool is not None:
            @tool("load_skill_reference")
            def load_skill_reference(path: str) -> str:
                """Load a reference file from the current Skill Package by relative path."""
                content = reference_tool.load_skill_reference(path)
                record = ToolCallRecord(
                    tool_name="load_skill_reference",
                    arguments={"path": path},
                    result={"path": path, "excerpt": content[:160]},
                    status="success",
                    tool_type="skill_internal",
                )
                append_record(record)
                return content

            created_tools.append(load_skill_reference)

        available_tools = request.test_context.get("mcp_tools", {})

        if "query_dataset_metadata" in available_tools:
            @tool("query_dataset_metadata")
            def query_dataset_metadata(dataset: str) -> str:
                """Query dataset metadata for a dataset alias or handle."""
                record = self.adapter.invoke("query_dataset_metadata", {"dataset": dataset})
                append_record(record)
                return str(record.result)

            created_tools.append(query_dataset_metadata)

        if "reproject_dataset" in available_tools:
            @tool("reproject_dataset")
            def reproject_dataset(dataset: str, target_crs: str, output_alias: str) -> str:
                """Reproject a dataset to a target CRS."""
                record = self.adapter.invoke(
                    "reproject_dataset",
                    {"dataset": dataset, "target_crs": target_crs, "output_alias": output_alias},
                )
                append_record(record)
                return str(record.result)

            created_tools.append(reproject_dataset)

        if "create_buffer" in available_tools:
            @tool("create_buffer")
            def create_buffer(dataset: str, distance: float, distance_unit: str = "meter", output_alias: str = "buffer_result") -> str:
                """Create a buffer around a dataset using a metric distance."""
                record = self.adapter.invoke(
                    "create_buffer",
                    {
                        "dataset": dataset,
                        "distance": distance,
                        "distance_unit": distance_unit,
                        "output_alias": output_alias,
                    },
                )
                if record.result:
                    state.output_artifacts["result_dataset"] = record.result
                append_record(record)
                return str(record.result)

            created_tools.append(create_buffer)

        if "publish_map" in available_tools:
            @tool("publish_map")
            def publish_map(dataset: str) -> str:
                """Publish a dataset as a map and return the map URL."""
                record = self.adapter.invoke("publish_map", {"dataset": dataset})
                append_record(record)
                return str(record.result)

            created_tools.append(publish_map)

        return created_tools

    def _check_runtime_available(self) -> tuple[bool, str | None]:
        missing = []
        for module_name in ("langchain", "langgraph", "langchain_openai"):
            if importlib.util.find_spec(module_name) is None:
                missing.append(module_name)
        if missing:
            return False, f"Missing runtime dependencies: {', '.join(missing)}"
        return True, None

    def _fallback_reason(self, model_name: str) -> str | None:
        if self.runtime_issue:
            return self.runtime_issue
        if not model_name:
            return "No agent model configured for Skill executor."
        if model_name.startswith("rule-based"):
            return f"Model '{model_name}' is a heuristic compatibility model, so the executor is using the rule-based fallback path."
        return None
