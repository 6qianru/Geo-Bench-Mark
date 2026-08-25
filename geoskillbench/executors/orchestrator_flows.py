"""orchestrator 任务流注册表与内置流程。

设计背景（见 docs/design/01-Agent接入契约.md §7.4 与迭代复盘 3.1）：
- orchestrator 默认用 react（langgraph prebuilt ReAct），scenario 可经 `agent.flow` 切换。
- `scripted`：内置固定节点 StateGraph，每轮 = 生成一条指令 → 发给外部 agent → 判定是否完成。
  终止判定 = 规则硬判（max_turns/超限）+ LLM 判完成，结构固定以收敛自由式 ReAct 的 harness 方差。
- 自定义流程：`@register_flow("名字")` 注册 `builder(state) -> 可 invoke 的图`，scenario 按名引用。

共享原语 `send_external_instruction`：react 的 ask_external_agent 工具与 scripted 的 ask 节点共用，
保证 HTTP 转发、工具调用记录、external_interactions、recorder 只有一份实现。

注意：本模块不得在运行时 import orchestrator_executor（避免循环依赖），
OrchestratorSessionState 仅在类型注解中使用（TYPE_CHECKING + __future__ annotations）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, TypedDict

from geoskillbench.runtime.llm import build_llm
from geoskillbench.runtime.llm_judge import extract_json

if TYPE_CHECKING:
    from geoskillbench.executors.orchestrator_executor import OrchestratorSessionState

FLOW_REGISTRY: dict[str, Callable[[OrchestratorSessionState], Any]] = {}


def register_flow(name: str):
    """注册一个 orchestrator 任务流 builder。scenario 用 `agent.flow: <name>` 引用。

    builder 签名：``builder(state: OrchestratorSessionState) -> 可 invoke 的图``
    （react 为 create_react_agent 产物，scripted/自定义为编译后的 StateGraph）。
    """

    def deco(builder: Callable[[OrchestratorSessionState], Any]) -> Callable[[OrchestratorSessionState], Any]:
        FLOW_REGISTRY[name] = builder
        return builder

    return deco


def available_flows() -> list[str]:
    return sorted(FLOW_REGISTRY)


# ---- 共享原语：发一条指令到外部 agent 并记录 ----

def send_external_instruction(state: OrchestratorSessionState, instruction: str) -> str:
    """向外部 agent 发送一条指令，返回它的回答文本；同时收集工具调用与交互记录。

    react 的 ask_external_agent 工具与 scripted 的 ask 节点共用（单一实现）。
    超出 max_turns 时不再转发，返回"已达上限"提示（由上层决定收尾）。
    """
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
    recorder = state.request.test_context.get("_recorder")
    if recorder is not None:
        recorder.record_external_interaction(interaction)
    return step.response or fallback_text


# ---- scripted：内置固定节点流程 ----

class ScriptedState(TypedDict):
    """StateGraph 图内状态；会话共享数据（http session / 记录 / 指令数）在 OrchestratorSessionState。"""

    goal: str
    latest_response: str
    pending_instruction: str
    summary: str
    blocker: str
    done: bool
    final_response: str


_HISTORY_LIMIT = 8  # 只取最近几条外部交互构建上下文（控制 token）
_RESPONSE_LIMIT = 500


def _history_text(state: OrchestratorSessionState) -> str:
    """压缩的外部交互历史，供 compose/analyze 节点构建上下文。"""
    lines: list[str] = []
    for it in state.external_interactions[-_HISTORY_LIMIT:]:
        lines.append(f"指令{it['turn']}: {it['instruction']}")
        resp = it["response"] or it.get("error_message") or "(无回复)"
        if len(resp) > _RESPONSE_LIMIT:
            resp = resp[:_RESPONSE_LIMIT] + "…"
        lines.append(f"回答{it['turn']}: {resp}")
    return "\n".join(lines)


def _invoke_structured(llm: Any, system_text: str, user_text: str) -> dict[str, Any]:
    """invoke LLM 并宽松解析 JSON 输出；非 dict 返回空 dict（调用节点决定兜底）。

    沿用 llm_judge 已验证的"严格 JSON prompt + 宽松解析"模式，不赌模型的
    with_structured_output（deepseek 端点能力未确认）。LLM 调用异常照常向上抛，
    与 react 流程的行为一致（失败会让整次 run 失败并暴露错误）。
    """
    from langchain_core.messages import HumanMessage, SystemMessage  # 惰性导入，遵循惯例

    response = llm.invoke([SystemMessage(content=system_text), HumanMessage(content=user_text)])
    text = getattr(response, "content", response)
    if isinstance(text, list):
        text = "".join(str(part.get("text", "")) for part in text if isinstance(part, dict)) or str(text)
    obj = extract_json(str(text))
    return obj if isinstance(obj, dict) else {}


@register_flow("scripted")
def build_scripted_flow(state: OrchestratorSessionState):
    """内置固定节点流程：compose(生成指令) → ask(发外部) → analyze(判完成) → 路由。

    每轮只发一条指令；LLM 只负责两个节点（生成指令、判定完成），
    其余为固定动作与规则判定（max_turns 硬路由）。结构固定，终止判定显式可审计。
    """
    from langgraph.graph import END, START, StateGraph

    request = state.request
    llm = build_llm(
        request.role_model_config.get("model", ""),
        temperature=0.0,
        config=state.models_config,
    )

    def compose_instruction(g: ScriptedState) -> dict[str, str]:
        system = (
            "你是 GeoSkillBench 评测平台的外部智能体操作者。你的任务是把用户目标拆解成"
            "外部智能体可执行的一条指令。\n"
            "规则：\n"
            "1. 一次只输出一条指令，必须完整自包含（含数据集名、参数、期望输出），不依赖外部智能体记忆。\n"
            "2. 参考外部智能体的历史回答：它反问就先回答它；缺参数就在下一条指令补齐。\n"
            '只输出一个 JSON 对象 {"instruction": "..."}，不要输出任何其他文字。'
        )
        user = (
            f"用户目标：{g.get('goal', '')}\n"
            f"已发指令数：{state.instruction_count}（上限 {state.max_turns}）\n\n"
            f"外部智能体历史：\n{_history_text(state) or '（暂无）'}"
        )
        obj = _invoke_structured(llm, system, user)
        instruction = str(obj.get("instruction") or "").strip()
        if not instruction:
            instruction = "请根据上述目标继续执行，并说明当前进展。"
        return {"pending_instruction": instruction}

    def ask_external_agent(g: ScriptedState) -> dict[str, str]:
        response = send_external_instruction(state, g.get("pending_instruction", ""))
        return {"latest_response": response}

    def analyze_response(g: ScriptedState) -> dict[str, Any]:
        system = (
            "你是 GeoSkillBench 评测平台的外部智能体操作者。你刚收到外部智能体的回答，"
            "需要判断任务是否完成。\n"
            '只输出一个 JSON 对象 {"done": true或false, "summary": "结果或进展摘要", "blocker": "受阻原因或空串"}，'
            "不要输出任何其他文字。\n"
            "规则：外部智能体已给出明确结果（结果数据集/完成声明）→ done=true 并概括结果；"
            "目标未达成 → done=false 概括进展并说明受阻原因；"
            "已达指令上限仍无结果 → done=false 概括已有进展。"
        )
        user = (
            f"用户目标：{g.get('goal', '')}\n"
            f"已发指令数：{state.instruction_count}（上限 {state.max_turns}）\n\n"
            f"刚发出的指令：{g.get('pending_instruction', '')}\n"
            f"外部智能体回答：{g.get('latest_response', '')}"
        )
        obj = _invoke_structured(llm, system, user)
        return {
            "done": bool(obj.get("done")),
            "summary": str(obj.get("summary") or "").strip(),
            "blocker": str(obj.get("blocker") or "").strip(),
        }

    def finalize(g: ScriptedState) -> dict[str, str]:
        summary = g.get("summary") or "任务执行结束"
        blocker = g.get("blocker") or ""
        if not g.get("done") and blocker:
            final = f"[FINAL] {summary}（受阻：{blocker}）"
        else:
            final = f"[FINAL] {summary}"
        return {"final_response": final}

    def route(g: ScriptedState) -> str:
        # 规则硬判：目标完成 或 指令数达上限 → 收尾；否则继续生成下一条指令
        if g.get("done") or state.instruction_count >= state.max_turns:
            return "finalize"
        return "compose"

    graph = StateGraph(ScriptedState)
    graph.add_node("compose", compose_instruction)
    graph.add_node("ask", ask_external_agent)
    graph.add_node("analyze", analyze_response)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "compose")
    graph.add_edge("compose", "ask")
    graph.add_edge("ask", "analyze")
    graph.add_conditional_edges("analyze", route, {"compose": "compose", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile()
