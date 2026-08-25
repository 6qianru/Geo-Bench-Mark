from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from geoskillbench.executors.base import Executor
from geoskillbench.executors.http_agent_executor import HttpAgentExecutor
from geoskillbench.executors.orchestrator_flows import FLOW_REGISTRY, available_flows, send_external_instruction
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
from geoskillbench.models.result import ExecutorSession, ExecutorSessionRequest, ExecutorStepResult, ToolCallRecord
from geoskillbench.runtime.llm import build_llm, load_models_config
from geoskillbench.runtime.user_simulator import UserSimulator

import geoskillbench.executors.example_flows  # noqa: F401  # 注册示例流程(keyword/pipeline)；不需要示例可删这行


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _last_ai_message(result_state: dict[str, Any]) -> str:
    """从 langgraph 结果里取最后一条非空 AI 消息文本。"""
    from langchain_core.messages import AIMessage  # 惰性导入

    for msg in reversed(result_state.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return str(msg.content)
    return ""


@dataclass
class OrchestratorSessionState:
    request: ExecutorSessionRequest
    agent: Any  # 可 invoke 的图（create_react_agent 产物 或 编译后的 StateGraph）
    http_executor: HttpAgentExecutor
    http_session_id: str
    max_turns: int
    flow: str = "react"  # 当前任务流名（agent.flow，见 orchestrator_flows.FLOW_REGISTRY）
    models_config: dict[str, Any] = field(default_factory=dict)  # models.yaml 配置，供流程内 build_llm
    instruction_count: int = 0
    pending_tool_calls: list[ToolCallRecord] = field(default_factory=list)
    external_interactions: list[dict[str, Any]] = field(default_factory=list)
    react_messages: list[Any] = field(default_factory=list)  # react 流程跨轮对话累积（追问用户后不丢上下文）
    # 反问闭环（下沉）：模拟用户回答 agent 的 [NEED_INTERACTION] 追问
    user_simulator: UserSimulator | None = None
    user_enabled: bool = False
    user_max_turns: int = 0
    user_turn: int = 0
    conversation: list[dict[str, Any]] = field(default_factory=list)  # 完整会话（含模拟用户回答），runner 用它生成 report


def _build_operator_prompt(request: ExecutorSessionRequest, agent: dict[str, Any]) -> str:
    """react 流程的操作者系统提示词（迭代 1 模板，行为保持）。"""
    description = (agent.get("description") or request.test_context.get("scenario_name", "")).strip()
    rules = [
        "把目标分解成外部智能体可执行的指令，一次只发一条。",
        "读取它的回答判断进展：缺参数→补下一条指令；它反问→先回答它；它做完了→进入第 3 步。",
        "目标达成时，以 [FINAL] 开头输出总结，必须包含结果信息。",
        f"最多发送 {request.max_turns} 条指令；超限仍未达成也要以 [FINAL] 说明进展与受阻原因。",
        "不得编造外部智能体没有提供的结果。",
    ]
    if agent.get("ask_user"):
        rules.append(
            "如果外部智能体反问、且所需信息不在用户消息或已有上下文中、你也无法合理推断，"
            "就不要发指令，直接以 [NEED_INTERACTION] 开头输出要向用户确认的问题，等用户回答后再继续。"
        )
    numbered = "\n".join(f"{i}. {r}" for i, r in enumerate(rules, start=1))
    return (
        "你是 GeoSkillBench 评测平台的外部智能体操作者。你的目标是用户消息给出的任务。\n"
        f"外部智能体能力：{description or '未提供，请在对话中自行判断。'}\n"
        "你可以通过 ask_external_agent 工具向它发送指令。规则：\n"
        f"{numbered}\n"
    )


def _build_external_agent_tool(state: OrchestratorSessionState):
    """react 流程的工具：向外部 agent 发一条指令。转发/记录逻辑复用 send_external_instruction。"""
    from langchain_core.tools import tool

    @tool("ask_external_agent")
    def ask_external_agent(instruction: str) -> str:
        """向外部智能体发送一条指令，返回它的回答文本。"""
        return send_external_instruction(state, instruction)

    return ask_external_agent


def _build_react_agent(state: OrchestratorSessionState):
    """react 流程：langgraph prebuilt ReAct（原 create_session 里的构建逻辑，行为零变化）。"""
    from langchain_core.messages import SystemMessage
    from langgraph.prebuilt import create_react_agent

    request = state.request
    llm = build_llm(
        request.role_model_config.get("model", ""),
        temperature=0.0,
        config=state.models_config,
    )
    system_prompt = _build_operator_prompt(request, request.agent or {})
    return create_react_agent(
        llm,
        tools=[_build_external_agent_tool(state)],
        prompt=SystemMessage(content=system_prompt),
    )


FLOW_REGISTRY["react"] = _build_react_agent


class OrchestratorExecutor(Executor):
    """本地 agent（LangGraph）作为操作者，多轮指挥外部 agent 完成目标的 Executor。

    架构（见 docs/plan/迭代1-orchestrator多轮指挥外部agent.md）：
    - 流程可选：agent.flow = react（默认，ReAct）/ scripted（内置固定节点）/ 注册表自定义。
    - 共享原语 send_external_instruction 复用 HttpAgentExecutor 的 session：转发指令、解析 SSE/JSON、
      维持外部多轮上下文；外部 agent 上报的 tool_event 转成 ToolCallRecord 流入 recorder。
    - 多轮 = 流程内部循环；本地 agent 判定目标达成后发 [FINAL]，max_turns 硬兜底。

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

        flow = (agent.get("flow") or "react").strip().lower()
        builder = FLOW_REGISTRY.get(flow)
        if builder is None:
            raise ValueError(
                f"未注册的 flow：{flow!r}。可用：{', '.join(available_flows())}。"
                "自定义流程请用 orchestrator_flows.register_flow 注册后在此引用。"
            )

        # 内部复用 HttpAgentExecutor 管理外部 agent session（SSE/JSON 解析、session_id 多轮上下文都在这里）
        http_executor = HttpAgentExecutor(self.adapter)
        http_session = http_executor.create_session(request)

        # 反问闭环：从 runner 注入的 role_model_config["user"] 构造模拟用户
        user_cfg = request.role_model_config.get("user") or {}
        user_enabled = bool(user_cfg.get("user_enabled", True))
        user_max_turns = int(user_cfg.get("user_max_turns") or 5)
        user_simulator = None
        if user_enabled:
            user_simulator = UserSimulator(
                goal=str(user_cfg.get("user_goal") or ""),
                profile=str(user_cfg.get("user_profile") or "normal_user"),
                model=str(user_cfg.get("user_model") or "rule-based-user"),
                models_config=self.models_config,
            )

        session_id = uuid4().hex
        state = OrchestratorSessionState(
            request=request,
            agent=None,
            http_executor=http_executor,
            http_session_id=http_session.session_id,
            max_turns=request.max_turns,
            flow=flow,
            models_config=self.models_config,
            user_simulator=user_simulator,
            user_enabled=user_enabled,
            user_max_turns=user_max_turns,
        )
        state.agent = builder(state)
        self.sessions[session_id] = state
        return ExecutorSession(
            session_id=session_id,
            executor_type=self.executor_type,
            scenario_id=request.scenario_id,
            skill_id=request.skill_id,
            created_at=now_iso(),
            runtime_mode="real",
            runtime_metadata={"model": model_name, "max_turns": request.max_turns, "flow": flow},
        )

    def send_message(self, session_id: str, message: str) -> ExecutorStepResult:
        state = self.sessions[session_id]
        recursion_limit = state.max_turns * 4 + 6  # 指令数上限 + 收尾余量，作流程循环的第二道安全阀

        if state.flow == "react":
            from langchain_core.messages import HumanMessage, SystemMessage

            # 反问闭环：agent 输出 [NEED_INTERACTION] → UserSimulator 回答 → 追加 react_messages →
            # 再 invoke（agent 记得之前的对话与外部交互），直到完成或 user_max_turns 耗尽。
            # ask_user=False / user_enabled=False 时不回答反问，按普通输出结束（存量行为）。
            if not state.conversation:
                state.conversation.append({"role": "user", "content": message})
            ask_user = bool((state.request.agent or {}).get("ask_user"))
            while True:
                result_state = state.agent.invoke(
                    {"messages": state.react_messages + [HumanMessage(content=message)]},
                    config={"recursion_limit": recursion_limit},
                )
                state.react_messages = [
                    m for m in result_state.get("messages", []) if not isinstance(m, SystemMessage)
                ]
                final_ai_content = _last_ai_message(result_state)
                state.conversation.append({"role": "assistant", "content": final_ai_content})
                need_interaction = ask_user and final_ai_content.strip().startswith("[NEED_INTERACTION]")
                if not need_interaction or state.user_turn >= state.user_max_turns or not state.user_enabled:
                    break
                reply = state.user_simulator.reply(final_ai_content)
                state.react_messages.append(HumanMessage(content=reply))
                state.conversation.append({"role": "user", "content": reply})
                message = reply
                state.user_turn += 1
            tool_calls = list(state.pending_tool_calls)
            state.pending_tool_calls.clear()
            return ExecutorStepResult(
                response=final_ai_content,
                finished=True,
                need_interaction=False,
                tool_calls=tool_calls,
                conversation=list(state.conversation),
            )

        # scripted/自定义：以固定输入键 invoke，从结果 state 取 final_response；不识别 [NEED_INTERACTION]
        result_state = state.agent.invoke(
            {
                "goal": message,
                "latest_response": "",
                "pending_instruction": "",
                "summary": "",
                "blocker": "",
                "done": False,
                "final_response": "",
            },
            config={"recursion_limit": recursion_limit},
        )
        final_ai_content = str(result_state.get("final_response") or "")
        tool_calls = list(state.pending_tool_calls)
        state.pending_tool_calls.clear()
        conversation = list(state.conversation)
        if not conversation:
            conversation.append({"role": "user", "content": message})
        conversation.append({"role": "assistant", "content": final_ai_content})
        return ExecutorStepResult(
            response=final_ai_content,
            finished=True,
            need_interaction=False,
            tool_calls=tool_calls,
            conversation=conversation,
        )

    def close_session(self, session_id: str) -> None:
        state = self.sessions.pop(session_id, None)
        if state is not None:
            state.http_executor.close_session(state.http_session_id)
