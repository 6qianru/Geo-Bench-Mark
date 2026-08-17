"""external_driven：外部 agent 主导式评测 executor（角色反转）。

设计背景（见 docs/Agent接入契约.md §7.6）：
- 被测对象是**外部 HTTP agent**（黑盒，主导者）：拿到 user_task 自主执行，缺必要信息
  （数据集名/缓冲距离/输出格式）时**主动反问**。
- 内部 LLM 扮演**模拟用户+引导**：外部 agent 反问时，从 `actor.goal` 派生 persona 回答
  （带人设与不确定性，可主动引导）。与 orchestrator 的"内部 LLM 指挥外部 agent"方向相反。
- 终止 = 外部 agent 完成信号（askback 规则判定）+ `runtime.max_turns` 硬兜底；质量交给 judge。
- 评测维度（scenario.judge.penalize_no_ask_back）：外部 agent 缺必要信息不反问、自行猜测执行，
  即使结果对也扣分（LLM rubric 主判 + judge_runtime 规则镜像兜底）。

与 http_agent / orchestrator 的差异：
- http_agent：user_task 直接透传外部，一问一答，无模拟用户。
- orchestrator：内部 LLM 是操作者，指挥外部 agent 干活。
- 本 executor：外部 agent 主导，内部 LLM 只在反问/进展时被动回答+引导；runner 的 actor 循环
  永不触发（need_interaction 恒 False）。

会话内多轮在 executor 内部闭环跑完，一次 send_message 返回 finished=True；每轮交互写入
recorder.external_interactions（runner 已把该字段带进 TestResult.final_output）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from geoskillbench.executors.base import Executor
from geoskillbench.executors.http_agent_executor import HttpAgentExecutor
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
from geoskillbench.models.result import ExecutorSession, ExecutorSessionRequest, ExecutorStepResult, ToolCallRecord
from geoskillbench.runtime.askback import classify_external_reply
from geoskillbench.runtime.llm import build_llm, load_models_config


@dataclass
class ExternalDrivenSessionState:
    request: ExecutorSessionRequest
    http_executor: HttpAgentExecutor
    http_session_id: str
    max_turns: int  # runtime.max_turns：平台→外部 agent 的消息总数上限（含 persona 回答/引导）
    turn_count: int = 0
    external_interactions: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    actor_goal: str = ""
    actor_profile: str = "normal_user"
    actor_model: str = ""
    actor_mode: str = "rule"  # "llm" | "rule"（actor_model 空/rule-based- 前缀 → 规则降级）
    actor_llm: Any | None = None  # actor_mode=="llm" 时构建（temperature=0.7 保自然度/不确定性）
    models_config: dict[str, Any] = field(default_factory=dict)
    finished: bool = False


class ExternalDrivenExecutor(Executor):
    """外部 agent 主导 + 内部 LLM 模拟用户。会话内多轮在 executor 内部闭环。"""

    executor_type = "external_driven"

    def __init__(self, adapter: MCPToolAdapter) -> None:
        self.adapter = adapter
        self.sessions: dict[str, ExternalDrivenSessionState] = {}
        self.models_config = load_models_config()

    def create_session(self, request: ExecutorSessionRequest) -> ExecutorSession:
        agent = request.agent or {}
        if not agent.get("endpoint"):
            raise ValueError("external_driven 场景缺少 agent.endpoint，无法接入外部 agent")

        # 内部复用 HttpAgentExecutor 管理外部 agent session（SSE/JSON 解析、session_id 多轮上下文）
        http_executor = HttpAgentExecutor(self.adapter)
        http_session = http_executor.create_session(request)

        # actor 配置由 runner 注入 role_model_config（executor 侧看不到 scenario.actor）
        actor_cfg = (request.role_model_config.get("actor") or {})
        actor_model = request.role_model_config.get("actor_model") or ""
        actor_mode = "rule" if (not actor_model or actor_model.startswith("rule-based-")) else "llm"

        state = ExternalDrivenSessionState(
            request=request,
            http_executor=http_executor,
            http_session_id=http_session.session_id,
            max_turns=request.max_turns,
            actor_goal=str(actor_cfg.get("goal") or ""),
            actor_profile=str(actor_cfg.get("profile") or "normal_user"),
            actor_model=actor_model,
            actor_mode=actor_mode,
            models_config=self.models_config,
        )
        if actor_mode == "llm":
            # persona 需要自然度/不确定性 → temperature 0.7（judge 才是 0.0）
            state.actor_llm = build_llm(actor_model, temperature=0.7, config=self.models_config)

        session_id = uuid4().hex
        self.sessions[session_id] = state
        return ExecutorSession(
            session_id=session_id,
            executor_type=self.executor_type,
            scenario_id=request.scenario_id,
            skill_id=request.skill_id,
            created_at=http_session.created_at,
            runtime_mode="real",
            runtime_metadata={
                "actor_model": actor_model,
                "actor_mode": actor_mode,
                "max_turns": request.max_turns,
            },
        )

    def send_message(self, session_id: str, message: str) -> ExecutorStepResult:
        """内部 while 循环一次跑完整个外部 agent ↔ 模拟用户对话，返回 finished=True。

        message = 首轮 user_task；后续轮次消息由本方法内部生成（persona 回答/引导），
        runner 的 actor 循环永不触发（need_interaction 恒 False）。
        """
        state = self.sessions[session_id]
        last_reply = self._send_to_external(state, message)
        while not state.finished:
            kind = classify_external_reply(last_reply)
            if kind == "complete":
                state.finished = True
                break
            if state.turn_count >= state.max_turns:  # 硬兜底：轮次耗尽即停，judge 评价
                state.finished = True
                break
            if kind == "ask":
                next_msg = self._simulated_user_reply(state, last_reply)
            else:  # continue：进展 → 引导继续
                next_msg = self._nudge(state, last_reply)
            last_reply = self._send_to_external(state, next_msg)
        return ExecutorStepResult(
            response=last_reply,
            finished=True,
            need_interaction=False,
            tool_calls=list(state.tool_calls),
        )

    def close_session(self, session_id: str) -> None:
        state = self.sessions.pop(session_id, None)
        if state is not None:
            state.http_executor.close_session(state.http_session_id)

    # ---- 内部原语 ----

    def _send_to_external(self, state: ExternalDrivenSessionState, message: str) -> str:
        """发一条消息给外部 agent，返回回答；同时收集工具调用与交互记录。"""
        state.turn_count += 1
        step = state.http_executor.send_message(state.http_session_id, message)
        for call in step.tool_calls:
            state.tool_calls.append(call)
        fallback_text = step.error_message or "(外部智能体无文本回复)"
        interaction = {
            "turn": len(state.external_interactions) + 1,
            "instruction": message,
            "response": step.response or fallback_text,
            "tool_calls": [call.model_dump() for call in step.tool_calls],
            "error_message": step.error_message,
        }
        state.external_interactions.append(interaction)
        recorder = state.request.test_context.get("_recorder")
        if recorder is not None:
            recorder.record_external_interaction(interaction)
        if step.error_message:
            state.finished = True
        return step.response or fallback_text

    def _simulated_user_reply(self, state: ExternalDrivenSessionState, question: str) -> str:
        """内部 LLM 模拟用户回答外部 agent 的反问；LLM 不可用时规则降级。"""
        if state.actor_mode == "llm" and state.actor_llm is not None:
            from langchain_core.messages import HumanMessage, SystemMessage  # 惰性导入，遵循惯例

            response = state.actor_llm.invoke(
                [SystemMessage(content=self._build_persona_prompt(state)), HumanMessage(content=f"外部智能体问你：{question}")]
            )
            text = getattr(response, "content", response)
            if isinstance(text, list):
                text = "".join(str(part.get("text", "")) for part in text if isinstance(part, dict)) or str(text)
            reply = str(text).strip()
            if reply:
                return reply
        return self._rule_actor_reply(state, question)

    def _rule_actor_reply(self, state: ExternalDrivenSessionState, question: str) -> str:
        """规则降级：从 actor.goal 提取数据集/距离/格式（对齐 ActorRuntime 行为）。"""
        import re

        goal = state.actor_goal
        lowered = question.lower()
        # 顺序敏感：反问格式的语句常带"缓冲距离 500 米"等已确认信息，须先判"格式"再判"距离"
        if "格式" in question or "format" in lowered:
            match = re.search(r"输出格式.*?([A-Za-z]+)", goal)
            return f"{match.group(1)}。" if match else "GeoJSON。"
        if "数据集" in question or "dataset" in lowered or "data" in lowered:
            match = re.search(r"使用\s+([A-Za-z0-9_]+)\s+数据", goal)
            return f"使用 {match.group(1)} 数据。" if match else "使用默认数据。"
        if "距离" in question or "distance" in lowered or "多少米" in question:
            match = re.search(r"(\d+(?:\.\d+)?)\s*米", goal)
            return f"{match.group(1)} 米。" if match else "500 米。"
        return "这个我不太确定，你按你的判断做吧。"

    def _build_persona_prompt(self, state: ExternalDrivenSessionState) -> str:
        """模拟用户 persona：从 actor.profile/goal 派生。"""
        return (
            "你正在参与一个 GIS 智能体评测任务，扮演一位真实普通用户。\n"
            f"你的身份：{state.actor_profile}\n"
            f"你的目标/已知信息：{state.actor_goal}\n\n"
            "行为规则：\n"
            "1. 只回答外部智能体主动提出的问题，不要代替它做决定，不要一次性把目标细节全讲出来，"
            "按它的问题逐步给出所需信息。\n"
            "2. 回答口语化、自然，符合普通用户水平；可以略带不确定（'大概是''记不清名字''按默认的就行吧'）。\n"
            "3. 若问题涉及目标里没有的信息，明确说'这个我不确定，你看着办吧'，不编造。\n"
            "4. 不要输出解释或前缀，直接给回答。"
        )

    def _nudge(self, state: ExternalDrivenSessionState, last_reply: str) -> str:
        """外部 agent 在进展中：给一句引导（LLM 模式可顺势补信息，规则模式固定引导）。"""
        if state.actor_mode == "llm" and state.actor_llm is not None:
            from langchain_core.messages import HumanMessage, SystemMessage  # 惰性导入

            response = state.actor_llm.invoke(
                [
                    SystemMessage(content=self._build_persona_prompt(state) + "\n5. 外部智能体还在执行中，你可以给一句简短引导，或补充目标里可能遗漏的信息。"),
                    HumanMessage(content=f"外部智能体当前进展：{last_reply}"),
                ]
            )
            text = getattr(response, "content", response)
            if isinstance(text, list):
                text = "".join(str(part.get("text", "")) for part in text if isinstance(part, dict)) or str(text)
            reply = str(text).strip()
            if reply:
                return reply
        return "请继续执行，完成后把结果告诉我。"
