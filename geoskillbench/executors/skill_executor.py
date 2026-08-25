from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from geoskillbench.executors.heuristic_executor import HeuristicSessionExecutor
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter, schema_to_pydantic_model
from geoskillbench.models.result import ExecutorSession, ExecutorSessionRequest, ExecutorStepResult, ToolCallRecord
from geoskillbench.models.skill import AgentSkill
from geoskillbench.runtime.llm import build_llm, load_models_config
from geoskillbench.runtime.user_simulator import UserSimulator
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
    # 反问闭环（下沉）：模拟用户回答 agent 的 [NEED_INTERACTION] 追问
    user_simulator: UserSimulator | None = None
    user_enabled: bool = False
    user_max_turns: int = 0
    user_turn: int = 0
    conversation: list[dict[str, Any]] = field(default_factory=list)  # 完整会话（含模拟用户回答），runner 用它生成 report


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
            # 反问闭环：从 runner 注入的 role_model_config["user"] 构造模拟用户（skill 场景无 agent，靠这里注入）
            user_cfg = request.role_model_config.get("user") or {}
            user_enabled = bool(user_cfg.get("user_enabled", True))
            user_max_turns = int(user_cfg.get("user_max_turns") or 5)
            user_simulator = None
            if user_enabled:
                user_simulator = UserSimulator(
                    goal=str(user_cfg.get("user_goal") or ""),
                    profile=str(user_cfg.get("user_profile") or "normal_user"),
                    model=str(user_cfg.get("user_model") or "rule-based-user"),
                    datasets=request.test_context.get("datasets", {}),
                    models_config=self.models_config,
                )
            state = SkillSessionState(
                request=request,
                skill=skill,
                agent=None,
                messages=[],
                reference_tool=reference_tool,
                user_simulator=user_simulator,
                user_enabled=user_enabled,
                user_max_turns=user_max_turns,
            )
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
        if not state.conversation:
            state.conversation.append({"role": "user", "content": message})
        # 反问闭环：agent 输出 [NEED_INTERACTION] → UserSimulator 回答 → 追加 state.messages → 再 invoke，
        # 直到 [FINAL] / 无协议前缀 / user_max_turns 耗尽。
        while True:
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
            state.conversation.append({"role": "assistant", "content": final_ai_content})

            need_interaction = final_ai_content.strip().startswith("[NEED_INTERACTION]")
            finished = final_ai_content.strip().startswith("[FINAL]")
            if finished:
                state.finished = True
                break
            if not need_interaction:
                break  # 无协议前缀的中间输出 → 结束（行为同存量）
            if not state.user_enabled or state.user_turn >= state.user_max_turns:
                break  # 无法回答反问 → 结束
            reply = state.user_simulator.reply(final_ai_content)
            state.messages.append(HumanMessage(content=reply))
            state.conversation.append({"role": "user", "content": reply})
            message = reply
            state.user_turn += 1
        state.last_response = final_ai_content

        tool_calls = list(state.pending_tool_calls)
        state.pending_tool_calls.clear()
        return ExecutorStepResult(
            response=final_ai_content,
            need_interaction=need_interaction,
            finished=finished,
            tool_calls=tool_calls,
            artifacts=dict(state.output_artifacts),
            conversation=list(state.conversation),
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

        # MCP 工具：从 adapter 已发现（tools/list）的工具按 inputSchema 自动生成，不再手写闭包。
        from langchain_core.tools import StructuredTool

        available_tools = request.test_context.get("mcp_tools", {})
        # 工具可见性 = skill 推荐的工具 ∩ server 已发现工具。
        # skill.recommended_mcp_tools 是 skill 声明的"完成任务需要的工具"（授权给 LLM 的集合），
        # 未推荐的工具（如本 demo 不需要的 publish_map）不暴露，避免 LLM 尝试调用不相关的工具。
        skill_recommended = set(getattr(state.skill, "recommended_mcp_tools", None) or [])
        tool_defs = self.adapter.list_tools()
        for defn in tool_defs:
            if defn.name not in available_tools:
                continue  # 只暴露 test_context 里声明的工具（兼容：executor 仍按声明可见性过滤）
            if skill_recommended and defn.name not in skill_recommended:
                continue  # skill 未推荐的工具不暴露给 agent（如 publish_map）
            tool_name = defn.name
            args_schema = schema_to_pydantic_model(f"{tool_name.title().replace('_', '')}Input", defn.input_schema or {}) if defn.input_schema else None

            def _dynamic_tool(**kwargs: Any) -> str:
                """MCP server tool. 命名参数由 StructuredTool.args_schema 从 JSON Schema 生成，
                收到 kwargs 后组回 dict 转发给 adapter.invoke。

                工具失败不抛异常，而是返回错误文本——langgraph 把它作为 ToolMessage 返回给 LLM，
                LLM 看到错误后修正参数重试（而非直接终止）。工具调用有方差，系统应让 agent 自行纠正。
                """
                arguments: dict[str, Any] = dict(kwargs)
                record = self.adapter.invoke(tool_name, arguments)
                append_record(record)
                if record.status != "success":
                    return f"[工具 {tool_name} 调用失败] {record.error_message or 'unknown error'}"
                return str(record.result)

            created_tools.append(
                StructuredTool.from_function(
                    func=_dynamic_tool,
                    name=tool_name,
                    description=defn.input_schema.get("description", "") if defn.input_schema else "",
                    args_schema=args_schema,
                )
            )

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
