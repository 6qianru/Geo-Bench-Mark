"""external_driven：外部 agent 主导式评测 executor（角色反转）。

设计背景（见 docs/design/01-Agent接入契约.md §7.6）：
- 被测对象是**外部 HTTP agent**（黑盒，主导者）：拿到 user_task 自主执行，缺必要信息
  （数据集名/缓冲距离/输出格式）时**主动反问**。
- 内部 LLM 扮演**模拟用户+引导**：外部 agent 反问时，从 `agent.user_goal` 派生 persona 回答
  （带人设与不确定性，可主动引导）。与 orchestrator 的"内部 LLM 指挥外部 agent"方向相反。
- 终止 = 外部 agent 完成信号（askback 规则判定）+ `runtime.max_turns` 硬兜底；质量交给 judge。
- 评测维度（scenario.judge.penalize_no_ask_back）：外部 agent 缺必要信息不反问、自行猜测执行，
  即使结果对也扣分（LLM rubric 主判 + judge_runtime 规则镜像兜底）。

与 http_agent / orchestrator 的差异：
- http_agent：user_task 直接透传外部，一问一答，无模拟用户。
- orchestrator：内部 LLM 是操作者，指挥外部 agent 干活。
- 本 executor：外部 agent 主导，内部 LLM（UserSimulator）只在反问/进展时被动回答+引导。
  会话内多轮在 executor 内部闭环跑完，一次 send_message 返回 finished=True。

反问闭环下沉后：模拟用户由共享的 UserSimulator 实现（从 role_model_config["user"] 构造，
runner 不再有 actor 循环）。每轮交互写入 recorder.external_interactions，完整对话由
_build_conversation 从交互记录构建，随 ExecutorStepResult.conversation 返回。
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
from geoskillbench.runtime.llm import load_models_config
from geoskillbench.runtime.user_simulator import UserSimulator


@dataclass
class ExternalDrivenSessionState:
    request: ExecutorSessionRequest
    http_executor: HttpAgentExecutor
    http_session_id: str
    max_turns: int  # runtime.max_turns：平台→外部 agent 的消息总数上限（含 persona 回答/引导）
    turn_count: int = 0
    external_interactions: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    user_simulator: UserSimulator | None = None  # 模拟用户：反问回答 / 进展引导
    finished: bool = False


class ExternalDrivenExecutor(Executor):
    """外部 agent 主导 + 内部模拟用户（UserSimulator）。会话内多轮在 executor 内部闭环。"""

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

        # 模拟用户由 runner 注入 role_model_config["user"]（executor 侧看不到 scenario）
        user_cfg = request.role_model_config.get("user") or {}
        user_simulator = None
        if user_cfg.get("user_enabled", True):
            # persona 需要自然度/不确定性 → temperature 0.7（judge 才是 0.0）
            user_simulator = UserSimulator(
                goal=str(user_cfg.get("user_goal") or ""),
                profile=str(user_cfg.get("user_profile") or "normal_user"),
                model=str(user_cfg.get("user_model") or "rule-based-user"),
                models_config=self.models_config,
            )

        state = ExternalDrivenSessionState(
            request=request,
            http_executor=http_executor,
            http_session_id=http_session.session_id,
            max_turns=request.max_turns,
            user_simulator=user_simulator,
        )

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
                "user_model": user_cfg.get("user_model") or "rule-based-user",
                "user_mode": user_simulator.mode if user_simulator else "rule",
                "max_turns": request.max_turns,
            },
        )

    def send_message(self, session_id: str, message: str) -> ExecutorStepResult:
        """内部 while 循环一次跑完整个外部 agent ↔ 模拟用户对话，返回 finished=True。

        message = 首轮 user_task；后续轮次消息由本方法内部生成（模拟用户回答/引导），
        反问闭环已下沉到 executor，need_interaction 恒 False。
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
                next_msg = state.user_simulator.reply(last_reply)
            else:  # continue：进展 → 引导继续
                next_msg = state.user_simulator.nudge(last_reply)
            last_reply = self._send_to_external(state, next_msg)
        return ExecutorStepResult(
            response=last_reply,
            finished=True,
            need_interaction=False,
            tool_calls=list(state.tool_calls),
            conversation=self._build_conversation(state),
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

    @staticmethod
    def _build_conversation(state: ExternalDrivenSessionState) -> list[dict[str, Any]]:
        """从外部交互记录构建完整对话：instruction=平台发出（user 角色），response=外部 agent 回答。"""
        conversation: list[dict[str, Any]] = []
        for interaction in state.external_interactions:
            conversation.append({"role": "user", "content": interaction["instruction"]})
            conversation.append({"role": "assistant", "content": interaction["response"]})
        return conversation
