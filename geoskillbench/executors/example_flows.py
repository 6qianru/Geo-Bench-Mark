"""orchestrator 自定义任务流示例（可直接抄改）。

两个示例 flow：
- ``keyword``   —— 最小骨架：LLM 只负责生成指令，终止用『外部回答含关键词』的纯规则判定。
- ``pipeline``  —— 带计划节点的完整任务流：plan(首轮拆解出执行计划) → 循环 compose→ask→analyze，
                   终止 = analyze 用 LLM 判 done + max_turns 规则硬兜底。

怎么用：
1. 本模块被 import 时 @register_flow 会把 flow 名写进 FLOW_REGISTRY（orchestrator_executor
   顶部已 import 本模块；不需要示例可删那行 import）。
2. scenario 里引用：``agent.flow: keyword``（或 ``pipeline``），再 ``python -m geoskillbench.cli run <scenario>``。
3. 要自定义：复制本文件到独立文件（如 my_flows.py），改成自己的名字和节点，
   然后在 orchestrator_executor.py 顶部 ``import my_flows`` 触发注册。

节点契约：
- 每个节点函数入参是整个状态 dict，返回 dict 只写要更新的字段（未返回的保持原值）。
- 跨节点/跨轮状态在 graph 内部保留，可自定义持久字段（如 pipeline 的 ``plan``，首轮写入、后续轮能读到）。
- send_message 的非 react 分支固定注入 7 个输入 key：goal / latest_response / pending_instruction /
  summary / blocker / done / final_response。本示例 schema 全部声明（不用的可以不读）。
- ``final_response`` 必须以 ``[FINAL] `` 开头，否则 final_response_contains 断言、前端、judge 都会失效。
"""
from __future__ import annotations

from typing import Any, TypedDict

from geoskillbench.executors.orchestrator_flows import register_flow, send_external_instruction
from geoskillbench.runtime.llm import build_llm
from geoskillbench.runtime.llm_judge import extract_json


# ---- 两个 flow 共用的 helper ----

def _invoke_structured(llm: Any, system: str, user: str) -> dict[str, Any]:
    """invoke LLM 并宽松解析 JSON 输出；非 dict 返回空 dict（调用节点决定兜底）。

    LLM 调用异常照常向上抛（与 react 流程一致：失败会让整次 run 失败并暴露错误）。
    """
    from langchain_core.messages import HumanMessage, SystemMessage  # 惰性导入，遵循惯例

    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    text = getattr(response, "content", response)
    if isinstance(text, list):
        text = "".join(str(part.get("text", "")) for part in text if isinstance(part, dict)) or str(text)
    obj = extract_json(str(text))
    return obj if isinstance(obj, dict) else {}


def _history_text(state: Any, limit: int = 5, max_len: int = 300) -> str:
    """压缩的外部交互历史（控制 token），供 compose/analyze/plan 节点构建上下文。"""
    lines: list[str] = []
    for it in state.external_interactions[-limit:]:
        resp = it["response"] or it.get("error_message") or "(无回复)"
        if len(resp) > max_len:
            resp = resp[:max_len] + "…"
        lines.append(f"指令{it['turn']}: {it['instruction']}\n回答{it['turn']}: {resp}")
    return "\n".join(lines)


# ---- 示例 1：keyword —— 最小骨架，终止用纯规则判定 ----

class KeywordState(TypedDict):
    goal: str
    latest_response: str
    pending_instruction: str
    done: bool
    final_response: str


DONE_KEYWORDS = ("完成", "结果数据", "成功", "已生成")


@register_flow("keyword")
def build_keyword_flow(state: Any):
    """keyword 流程：compose(LLM 生成指令) → ask(发外部) → check(规则判完成) → 路由。

    终止不依赖 LLM（可复现）：外部回答含完成关键词，或指令数达上限 → 收尾。
    """
    from langgraph.graph import END, START, StateGraph

    llm = build_llm(
        state.request.role_model_config.get("model", ""),
        temperature=0.0,
        config=state.models_config,
    )

    def compose(g: KeywordState) -> dict[str, str]:
        obj = _invoke_structured(
            llm,
            "你是 GeoSkillBench 评测平台的外部智能体操作者。一次只输出一条完整自包含的指令"
            "（含数据集名、参数、期望输出），不依赖外部智能体记忆。\n"
            '只输出一个 JSON 对象 {"instruction": "..."}，不要输出任何其他文字。',
            f"用户目标：{g['goal']}\n"
            f"已发指令数：{state.instruction_count}（上限 {state.max_turns}）\n\n"
            f"外部智能体历史：\n{_history_text(state) or '（暂无）'}",
        )
        instruction = str(obj.get("instruction") or "").strip()
        return {"pending_instruction": instruction or "请继续执行并说明当前进展"}

    def ask(g: KeywordState) -> dict[str, str]:
        # 共享原语：转发 + 记录 external_interactions/tool_calls/recorder，超 max_turns 自动拦截
        return {"latest_response": send_external_instruction(state, g["pending_instruction"])}

    def check_done(g: KeywordState) -> dict[str, bool]:
        resp = g.get("latest_response") or ""
        # 规则硬判：外部回答含完成关键词，或指令数达上限 → 收尾
        return {"done": any(k in resp for k in DONE_KEYWORDS) or state.instruction_count >= state.max_turns}

    def finalize(g: KeywordState) -> dict[str, str]:
        return {"final_response": "[FINAL] " + (g.get("latest_response") or "任务执行结束")}

    def route(g: KeywordState) -> str:
        return "finalize" if g.get("done") else "compose"

    graph = StateGraph(KeywordState)
    graph.add_node("compose", compose)
    graph.add_node("ask", ask)
    graph.add_node("check", check_done)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "compose")
    graph.add_edge("compose", "ask")
    graph.add_edge("ask", "check")
    graph.add_conditional_edges("check", route, {"compose": "compose", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile()


# ---- 示例 2：pipeline —— 带首轮计划节点的完整任务流 ----

class PipelineState(TypedDict):
    goal: str
    plan: str  # 首轮 plan 节点生成的执行计划，跨轮保留供 compose 参考（自定义持久字段示例）
    latest_response: str
    pending_instruction: str
    summary: str
    blocker: str
    done: bool
    final_response: str


@register_flow("pipeline")
def build_pipeline_flow(state: Any):
    """pipeline 流程：plan(首轮拆解出计划+首条指令) → 循环 compose→ask→analyze → 收尾。

    图结构保证 plan 只跑一次（ask 之后路由到 compose，不会回到 plan）。
    终止 = analyze 用 LLM 判 done + 规则硬兜底（max_turns / 空指令）。
    """
    from langgraph.graph import END, START, StateGraph

    description = (state.request.agent or {}).get("description") or ""
    llm = build_llm(
        state.request.role_model_config.get("model", ""),
        temperature=0.0,
        config=state.models_config,
    )

    def plan(g: PipelineState) -> dict[str, str]:
        obj = _invoke_structured(
            llm,
            "你是 GeoSkillBench 评测平台的外部智能体操作者。先拆解目标为执行计划，再给出第一条指令。\n"
            '只输出一个 JSON 对象 {"plan": "执行计划摘要", "instruction": "第一条完整自包含的指令"}，不要输出任何其他文字。',
            f"用户目标：{g['goal']}\n外部智能体能力：{description or '（未提供，自行判断）'}",
        )
        return {
            "plan": str(obj.get("plan") or "").strip(),
            "pending_instruction": str(obj.get("instruction") or "请说明你需要哪些信息来完成目标"),
        }

    def compose(g: PipelineState) -> dict[str, str]:
        obj = _invoke_structured(
            llm,
            "你是 GeoSkillBench 评测平台的外部智能体操作者。根据执行计划与当前进展，给出下一条指令。\n"
            "规则：一次一条、完整自包含；外部智能体反问就先回答它；缺参数就补齐。\n"
            '只输出一个 JSON 对象 {"instruction": "..."}，不要输出任何其他文字。',
            f"执行计划：{g.get('plan') or '（无）'}\n"
            f"已发指令数：{state.instruction_count}（上限 {state.max_turns}）\n\n"
            f"外部智能体历史：\n{_history_text(state) or '（暂无）'}",
        )
        instruction = str(obj.get("instruction") or "").strip()
        return {"pending_instruction": instruction or "请继续执行并说明当前进展"}

    def ask(g: PipelineState) -> dict[str, str]:
        return {"latest_response": send_external_instruction(state, g.get("pending_instruction", ""))}

    def analyze(g: PipelineState) -> dict[str, Any]:
        obj = _invoke_structured(
            llm,
            "你是 GeoSkillBench 评测平台的外部智能体操作者。刚收到外部智能体回答，判断任务是否完成。\n"
            '只输出一个 JSON 对象 {"done": true或false, "summary": "结果或进展摘要", "blocker": "受阻原因或空串"}，不要输出任何其他文字。\n'
            "规则：外部智能体已给出明确结果（结果数据集/完成声明）→ done=true 并概括结果；"
            "目标未达成 → done=false 概括进展并说明受阻原因；已达指令上限仍无结果 → done=false 概括已有进展。",
            f"用户目标：{g['goal']}\n"
            f"已发指令数：{state.instruction_count}（上限 {state.max_turns}）\n\n"
            f"刚发出的指令：{g.get('pending_instruction', '')}\n"
            f"外部智能体回答：{g.get('latest_response', '')}",
        )
        return {
            "done": bool(obj.get("done")),
            "summary": str(obj.get("summary") or "").strip(),
            "blocker": str(obj.get("blocker") or "").strip(),
        }

    def finalize(g: PipelineState) -> dict[str, str]:
        summary = g.get("summary") or "任务执行结束"
        blocker = g.get("blocker") or ""
        if not g.get("done") and blocker:
            final = f"[FINAL] {summary}（受阻：{blocker}）"
        else:
            final = f"[FINAL] {summary}"
        return {"final_response": final}

    def route(g: PipelineState) -> str:
        # 规则硬判：完成 / 达上限 / 空指令 → 收尾；否则继续生成下一条指令（不再回 plan）
        if g.get("done") or state.instruction_count >= state.max_turns:
            return "finalize"
        if not g.get("pending_instruction"):
            return "finalize"
        return "compose"

    graph = StateGraph(PipelineState)
    graph.add_node("plan", plan)
    graph.add_node("compose", compose)
    graph.add_node("ask", ask)
    graph.add_node("analyze", analyze)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "ask")
    graph.add_edge("compose", "ask")
    graph.add_edge("ask", "analyze")
    graph.add_conditional_edges("analyze", route, {"compose": "compose", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile()
