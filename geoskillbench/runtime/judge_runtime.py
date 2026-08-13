from __future__ import annotations

from geoskillbench.models.result import AssertionResult, JudgeResult
from geoskillbench.models.scenario import Scenario
from geoskillbench.models.test_context import TestContext
from geoskillbench.recorder.execution_recorder import ExecutionRecorder
from geoskillbench.runtime.llm import build_llm, load_models_config
from geoskillbench.runtime.llm_judge import LlmJudgeUnavailable, run_llm_judge


class JudgeEngine:
    def evaluate(
        self,
        scenario: Scenario,
        test_context: TestContext,
        recorder: ExecutionRecorder,
        assertion_result: AssertionResult,
    ) -> JudgeResult:
        if not scenario.judge.enabled:
            # 显式关闭 judge 时直接以断言结果通过，跳过针对内部 skill 产物的启发式扣分
            return JudgeResult(
                score=assertion_result.score,
                passed=assertion_result.passed,
                reason="Judge disabled by scenario config.",
                judge_mode="disabled",
            )

        # 1) LLM 优先：judge_model 空则跟随 agent_model；以 rule-based- 开头视为未配真实模型
        judge_model = scenario.runtime.judge_model or scenario.runtime.agent_model
        degrade_reason = None
        if not judge_model or judge_model.startswith("rule-based-"):
            # 未配真实模型也要显式说明降级原因，不能静默走规则
            degrade_reason = f"未配置真实 judge 模型（judge_model/agent_model = {judge_model or '(空)'}）"
        else:
            try:
                llm = build_llm(judge_model, temperature=0.0, config=load_models_config())
                llm_result = run_llm_judge(scenario, recorder, assertion_result, llm, judge_model=judge_model)
                llm_result.passed = (
                    llm_result.score >= scenario.pass_criteria.judge_score_min and assertion_result.passed
                )
                if not llm_result.reason:
                    llm_result.reason = (
                        "智能体按场景完成了主要流程。"
                        if llm_result.passed
                        else "智能体未满足全部通过标准。"
                    )
                return llm_result
            except LlmJudgeUnavailable as exc:
                degrade_reason = str(exc)
            except Exception as exc:
                degrade_reason = f"LLM 构建/调用异常：{exc}"

        # 2) 显式降级：规则判定（rule-skill/rule-agent）。LLM 不可用原因进 issues，非静默成功。
        result = self._rule_judge(scenario, assertion_result, recorder)
        if degrade_reason:
            result.issues.insert(0, f"LLM judge 不可用：{degrade_reason}，已降级为规则判定。")
        return result

    def _rule_judge(
        self,
        scenario: Scenario,
        assertion_result: AssertionResult,
        recorder: ExecutionRecorder,
    ) -> JudgeResult:
        issues: list[str] = []
        suggestions: list[str] = []
        score = assertion_result.score

        # "结果数据/句柄/CRS" 契约扣分只对内部 skill 评测有意义（外部 agent 不会用平台内部句柄措辞），
        # agent_test 场景跳过，避免误扣。两类都保留 should_not 违禁词扣分。
        use_skill_contracts = scenario.type == "agent_skill_test"
        judge_mode = "rule-skill" if use_skill_contracts else "rule-agent"

        final_response = recorder.final_output.get("final_response", "")
        for item in scenario.expected_behavior.should_not:
            if item and item in final_response:
                issues.append(f"Final response violated expected behavior: {item}")
                score = max(0.0, score - 0.2)

        if use_skill_contracts:
            if "结果数据" not in final_response and "句柄" not in final_response:
                issues.append("Final response does not clearly mention the result dataset handle.")
                suggestions.append("Strengthen the skill output contract for result dataset handles.")
                score = max(0.0, score - 0.1)

            if "CRS" not in final_response and "crs" not in final_response:
                suggestions.append("Include CRS explicitly in the final response.")
                score = max(0.0, score - 0.05)

        score = round(max(0.0, min(1.0, score)), 2)
        passed = score >= scenario.pass_criteria.judge_score_min and assertion_result.passed
        reason = "智能体按场景完成了主要流程。" if passed else "智能体未满足全部通过标准。"
        if not issues and not passed:
            issues.append("Assertion coverage or final response quality did not reach the pass threshold.")
        return JudgeResult(
            score=score,
            passed=passed,
            reason=reason,
            issues=issues,
            suggestions=suggestions,
            judge_mode=judge_mode,
        )
