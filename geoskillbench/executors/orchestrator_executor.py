from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from geoskillbench.executors.base import Executor
from geoskillbench.executors.http_agent_executor import HttpAgentExecutor
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
from geoskillbench.models.result import ExecutorSession, ExecutorSessionRequest, ExecutorStepResult, ToolCallRecord
from geoskillbench.runtime.llm import build_llm, load_models_config


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class OrchestratorSessionState:
    request: ExecutorSessionRequest
    agent: Any  # LangGraph ReAct agent
    http_executor: HttpAgentExecutor
    http_session_id: str
    max_turns: int
    instruction_count: int = 0
    pending_tool_calls: list[ToolCallRecord] = field(default_factory=list)
    external_interactions: list[dict[str, Any]] = field(default_factory=list)


class OrchestratorExecutor(Executor):
    """本地 agent（LangGraph ReAct）作为操作者，多轮指挥外部 agent 完成目标的 Executor。

    架构（见 docs/GeoSkillBench-迭代1-多轮指挥实现计划.md）：
    - 本地 ReAct agent 注册唯一工具 ask_external_agent。
    - 工具内部复用 HttpAgentExecutor 的 session：转发指令、解析 SSE/JSON、维持外部多轮上下文。
    - 外部 agent 上报的 tool_event 转成 ToolCallRecord 流入 recorder（tool_called 断言可用）。
    - 多轮 = ReAct 工具循环；本地 agent 判定目标达成后发 [FINAL]，max_turns 硬兜底。

    与 http_agent 的区别：http_agent 把 user_task 直接透传外部 agent（一问一答）；
    orchestrator 由本地 LLM agent 自主拆解目标、逐条发指令、读取响应决定下一步。
    本 executor 无启发式兜底：缺真实本地模型 / 缺 endpoint / langgraph 依赖缺失 → 直接报错，
    由 runner 转成失败的 TestResult 并暴露错误信息。
    """

    executor_type = "orchestrator"

    def __init__(self, adapter: MCPToolAdapter) -> None:
        self.adapter = adapter
        self.sessions: dict[str, OrchestratorSessionState] = {}
        self.models_config = load_models_config()

    def create_session(self, request: ExecutorSessionRequest) -> ExecutorSession:
        agent = request.agent or {}
        if not agent.get("endpoint"):
            raise ValueError("orchestrator 场景缺少 agent.endpoint，无法接入外部 agent")
        model_name = request.role_model_config.get("model", "")
        if not model_name or model_name.startswith("rule-based"):
            raise ValueError(
                f"orchestrator 需要真实本地 agent 模型（models.yaml 别名），当前 agent_model={model_name!r}"
            )
        missing = [m for m in ("langchain", "langgraph", "langchain_openai") if importlib.util.find_spec(m) is None]
        if missing:
            raise ValueError(f"orchestrator 缺少 LangGraph 运行时依赖: {', '.join(missing)}")

        from langchain_core.messages import SystemMessage
        from langgraph.prebuilt import create_react_agent

        llm = build_llm(model_name, temperature=0.0, config=self.models_config)
        # 内部复用 HttpAgentExecutor 管理外部 agent session（SSE/JSON 解析、session_id 多轮上下文都在这里）
        http_executor = HttpAgentExecutor(self.adapter)
        http_session = http_executor.create_session(request)

        session_id = uuid4().hex
        state = OrchestratorSessionState(
            request=request,
            agent=None,
            http_executor=http_executor,
            http_session_id=http_session.session_id,
            max_turns=request.max_turns,
        )
        system_prompt = self._build_operator_prompt(request, agent)
        state.agent = create_react_agent(
            llm,
            tools=[self._build_external_agent_tool(state)],
            prompt=SystemMessage(content=system_prompt),
        )
        self.sessions[session_id] = state
        return ExecutorSession(
            session_id=session_id,
            executor_type=self.executor_type,
            scenario_id=request.scenario_id,
            skill_id=request.skill_id,
            created_at=now_iso(),
            runtime_mode="real",
            runtime_metadata={"model": model_name, "max_turns": request.max_turns},
        )

    def send_message(self, session_id: str, message: str) -> ExecutorStepResult:
        state = self.sessions[session_id]

        from langchain_core.messages import AIMessage, HumanMessage

        # 指令数上限 + 收尾余量，作 ReAct 循环的第二道安全阀（create_react_agent 不接受 recursion_limit）
        recursion_limit = state.max_turns * 4 + 6
        result_state = state.agent.invoke(
            {"messages": [HumanMessage(content=message)]},
            config={"recursion_limit": recursion_limit},
        )
        final_ai_content = ""
        for msg in reversed(result_state.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content:
                final_ai_content = str(msg.content)
                break

        tool_calls = list(state.pending_tool_calls)
        state.pending_tool_calls.clear()
        return ExecutorStepResult(
            response=final_ai_content,
            finished=True,
            need_interaction=False,
            tool_calls=tool_calls,
        )

    def close_session(self, session_id: str) -> None:
        state = self.sessions.pop(session_id, None)
        if state is not None:
            state.http_executor.close_session(state.http_session_id)

    # ---- 工具与提示词 ----

    def _build_external_agent_tool(self, state: OrchestratorSessionState):
        from langchain_core.tools import tool

        recorder = state.request.test_context.get("_recorder")

        @tool("ask_external_agent")
        def ask_external_agent(instruction: str) -> str:
            """向外部智能体发送一条指令，返回它的回答文本。"""
            state.instruction_count += 1
            if state.instruction_count > state.max_turns:
                return (
                    f"你已达到指令数上限（{state.max_turns} 条）。必须停止发送新指令，"
                    "请以 [FINAL] 开头总结当前进展与结果。"
                )
            step = state.http_executor.send_message(state.http_session_id, instruction)
            for call in step.tool_calls:
                state.pending_tool_calls.append(call)
            fallback_text = step.error_message or "(外部智能体无文本回复)"
            interaction = {
                "turn": len(state.external_interactions) + 1,
                "instruction": instruction,
                "response": step.response or fallback_text,
                "tool_calls": [call.model_dump() for call in step.tool_calls],
                "error_message": step.error_message,
            }
            state.external_interactions.append(interaction)
            if recorder is not None:
                recorder.record_external_interaction(interaction)
            return step.response or fallback_text

        return ask_external_agent

    def _build_operator_prompt(self, request: ExecutorSessionRequest, agent: dict[str, Any]) -> str:
        description = (agent.get("description") or request.test_context.get("scenario_name", "")).strip()
        return (
            "你是 GeoSkillBench 评测平台的外部智能体操作者。你的目标是用户消息给出的任务。\n"
            f"外部智能体能力：{description or '未提供，请在对话中自行判断。'}\n"
            "你可以通过 ask_external_agent 工具向它发送指令。规则：\n"
            "1. 把目标分解成外部智能体可执行的指令，一次只发一条。\n"
            "2. 读取它的回答判断进展：缺参数→补下一条指令；它反问→先回答它；它做完了→进入第 3 步。\n"
            "3. 目标达成时，以 [FINAL] 开头输出总结，必须包含结果信息。\n"
            f"4. 最多发送 {request.max_turns} 条指令；超限仍未达成也要以 [FINAL] 说明进展与受阻原因。\n"
            "5. 不得编造外部智能体没有提供的结果。\n"
        )
