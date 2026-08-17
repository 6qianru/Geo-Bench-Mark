from __future__ import annotations

import json
import re
from typing import Any

from geoskillbench.models.result import AssertionResult, JudgeResult
from geoskillbench.models.scenario import Scenario
from geoskillbench.recorder.execution_recorder import ExecutionRecorder


class LlmJudgeUnavailable(Exception):
    """LLM judge 不可用，携带降级原因（供 JudgeEngine 显式降级到规则判定）。"""


# rubric 为空时的默认评分标准（scenario.judge.rubric 写了则优先）
DEFAULT_RUBRIC: dict[str, list[str]] = {
    "agent_skill_test": [
        "目标是否达成",
        "工具调用是否贴合任务",
        "最终回答是否包含结果信息",
        "是否遵循 skill 约束",
    ],
    "agent_test": [
        "目标是否达成",
        "回答是否具体可执行",
        "是否明确给出结果或结论",
        "多轮中是否主动补齐必要信息",
    ],
}

RETRY_HINT = "\n注意：上次输出不符合要求，请重新只输出一个合法 JSON 对象。"


def _tool_summary(recorder: ExecutionRecorder) -> list[dict[str, Any]]:
    """工具调用压缩为 tool_name + 关键参数摘要（控制 token）。"""
    summary: list[dict[str, Any]] = []
    for call in recorder.tool_calls:
        args = call.arguments or {}
        shown: dict[str, Any] = {}
        for key, value in list(args.items())[:8]:
            text = str(value)
            shown[key] = text[:120] + "…" if len(text) > 120 else value
        summary.append({"tool": call.tool_name, "status": call.status, "args": shown})
    return summary


# external_driven 场景：judge 要评估"外部 agent 缺信息是否主动反问"时的 rubric 追加行
_ASKBACK_RUBRIC_LINE = (
    "外部智能体在缺少必要信息时是否主动反问澄清而非自行猜测执行；"
    "即使最终结果正确，未反问也应酌情扣分。"
)


def _external_interaction_summary(recorder: ExecutionRecorder) -> list[dict[str, Any]]:
    """外部 agent 每轮交互压缩（平台发了什么 → 外部回了什么），控制 token。"""
    summary: list[dict[str, Any]] = []
    for it in recorder.external_interactions[-10:]:
        summary.append(
            {
                "turn": it.get("turn"),
                "platform_said": str(it.get("instruction", ""))[:200],
                "agent_reply": str(it.get("response", ""))[:200],
            }
        )
    return summary


def _build_input(
    scenario: Scenario,
    recorder: ExecutionRecorder,
    assertion_result: AssertionResult,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "user_task": scenario.user_task,
        "final_response": recorder.final_output.get("final_response", ""),
        "tool_calls": _tool_summary(recorder),
        "assertions": [
            {"type": item.type, "passed": item.passed, "message": item.message}
            for item in assertion_result.items
        ],
        "rubric": scenario.judge.rubric or DEFAULT_RUBRIC.get(scenario.type, DEFAULT_RUBRIC["agent_test"]),
    }
    if scenario.judge.penalize_no_ask_back:
        # 外部 agent 反问维度：喂逐轮交互 + rubric 追加一行（仅开关开启时，存量 judge 输入逐字节不变）
        data["external_interactions"] = _external_interaction_summary(recorder)
        if _ASKBACK_RUBRIC_LINE not in data["rubric"]:
            data["rubric"] = list(data["rubric"]) + [_ASKBACK_RUBRIC_LINE]
    if scenario.judge.include_conversation:
        # 截断策略：最后 10 条消息、每条内容 500 字
        data["conversation"] = [
            {"role": msg.get("role", ""), "content": str(msg.get("content", ""))[:500]}
            for msg in recorder.conversation[-10:]
        ]
    return data


def _build_messages(scenario: Scenario, data: dict[str, Any], retry: bool) -> list[Any]:
    from langchain_core.messages import HumanMessage, SystemMessage  # 惰性导入，遵循 executor 惯例

    system = SystemMessage(
        "你是 GeoSkillBench 评测平台的 LLM Judge。根据场景目标、智能体的执行记录与评分标准，"
        "对智能体本次表现打分。只输出一个 JSON 对象，不要输出任何其他文字。格式：\n"
        '{"score": 0.0~1.0, "reason": "评分理由", "issues": ["问题1", ...], "suggestions": ["建议1", ...]}'
    )
    content = json.dumps(data, ensure_ascii=False, indent=2)
    if retry:
        content += RETRY_HINT
    return [system, HumanMessage(content)]


def extract_json(text: str) -> dict[str, Any] | None:
    """宽松解析 LLM 输出为 dict：先整体 json.loads，失败则正则截取第一个 {...} 再 parse。

    供 LLM judge 与 orchestrator scripted flow 等需要"LLM 输出结构化结果"的调用点共用。
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _to_judge_result(obj: dict[str, Any], judge_model: str) -> JudgeResult:
    """校验 LLM 返回结构并组装 JudgeResult；非法则 raise LlmJudgeUnavailable。"""
    try:
        score = float(obj.get("score"))
    except (TypeError, ValueError):
        raise LlmJudgeUnavailable(f"LLM 返回的 score 非法：{obj.get('score')!r}") from None
    if not 0.0 <= score <= 1.0:
        raise LlmJudgeUnavailable(f"LLM 返回的 score 超出 0~1：{score}")
    issues = [str(item) for item in (obj.get("issues") or [])]
    suggestions = [str(item) for item in (obj.get("suggestions") or [])]
    return JudgeResult(
        score=round(score, 2),
        reason=str(obj.get("reason") or ""),
        issues=issues,
        suggestions=suggestions,
        judge_mode="llm",
        model=judge_model,
    )


def run_llm_judge(
    scenario: Scenario,
    recorder: ExecutionRecorder,
    assertion_result: AssertionResult,
    llm: Any,
    *,
    judge_model: str,
) -> JudgeResult:
    """执行 LLM 判定。任何不可用（调用异常 / 输出解析失败重试后仍失败）→ raise LlmJudgeUnavailable。

    passed 由 JudgeEngine 统一结合断言与 judge_score_min 判定，此处不设。
    """
    data = _build_input(scenario, recorder, assertion_result)
    last_error = ""
    for attempt in (1, 2):
        messages = _build_messages(scenario, data, retry=(attempt == 2))
        try:
            response = llm.invoke(messages)
        except Exception as exc:
            raise LlmJudgeUnavailable(f"LLM 调用异常：{exc}") from exc
        text = getattr(response, "content", response)
        if isinstance(text, list):
            text = "".join(str(part.get("text", "")) for part in text if isinstance(part, dict)) or str(text)
        obj = extract_json(str(text))
        if obj is not None:
            try:
                return _to_judge_result(obj, judge_model)
            except LlmJudgeUnavailable as exc:
                last_error = str(exc)
                continue  # 校验失败也允许重试一次
        last_error = "输出不符合 JSON 格式"
    raise LlmJudgeUnavailable(f"LLM 输出解析失败（重试 1 次后仍失败）：{last_error}")
