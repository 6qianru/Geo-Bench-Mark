from __future__ import annotations

from geoskillbench.models.result import AssertionResult, JudgeResult
from geoskillbench.models.scenario import Scenario
from geoskillbench.models.test_context import TestContext
from geoskillbench.recorder.execution_recorder import ExecutionRecorder


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
            )
        issues: list[str] = []
        suggestions: list[str] = []
        score = assertion_result.score

        final_response = recorder.final_output.get("final_response", "")
        for item in scenario.expected_behavior.should_not:
            if item and item in final_response:
                issues.append(f"Final response violated expected behavior: {item}")
                score = max(0.0, score - 0.2)

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
        )
