from __future__ import annotations

from typing import Any

from geoskillbench.models.result import AssertionItemResult, AssertionResult
from geoskillbench.models.scenario import AssertionConfig
from geoskillbench.models.test_context import TestContext
from geoskillbench.recorder.execution_recorder import ExecutionRecorder


class AssertionEngine:
    def run(
        self,
        assertions: list[AssertionConfig],
        recorder: ExecutionRecorder,
        test_context: TestContext,
    ) -> AssertionResult:
        items = [self._run_single(assertion, recorder, test_context) for assertion in assertions]
        passed_count = sum(1 for item in items if item.passed)
        score = round((passed_count / len(items)) if items else 1.0, 2)
        return AssertionResult(passed=passed_count == len(items), score=score, items=items)

    def _run_single(
        self,
        assertion: AssertionConfig,
        recorder: ExecutionRecorder,
        test_context: TestContext,
    ) -> AssertionItemResult:
        handlers = {
            "skill_loaded": self._skill_loaded,
            "tool_available": self._tool_available,
            "tool_called": self._tool_called,
            "tool_sequence": self._tool_sequence,
            "tool_argument_equals": self._tool_argument_equals,
            "result_dataset_exists": self._result_dataset_exists,
            "result_geometry_type_in": self._result_geometry_type_in,
            "final_response_contains": self._final_response_contains,
            "skill_reference_loaded": self._skill_reference_loaded,
            "skill_reference_not_loaded": self._skill_reference_not_loaded,
            "skill_reference_loaded_before_tool": self._skill_reference_loaded_before_tool,
            "skill_reference_load_count_less_than": self._skill_reference_load_count_less_than,
        }
        if assertion.type not in handlers:
            return AssertionItemResult(
                type=assertion.type,
                passed=False,
                message=f"Unsupported assertion type: {assertion.type}",
                target=assertion.tool or assertion.alias or assertion.target,
            )
        return handlers[assertion.type](assertion, recorder, test_context)

    def _skill_loaded(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        passed = assertion.skill_id in recorder.skill_loaded
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Skill {'was' if passed else 'was not'} loaded: {assertion.skill_id}",
            target=assertion.skill_id,
        )

    def _tool_available(self, assertion: AssertionConfig, _: ExecutionRecorder, test_context: TestContext) -> AssertionItemResult:
        passed = assertion.tool in test_context.mcp_tools and test_context.mcp_tools[assertion.tool].available
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Tool {'is' if passed else 'is not'} available: {assertion.tool}",
            target=assertion.tool,
        )

    def _tool_called(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        passed = any(call.tool_name == assertion.tool for call in recorder.tool_calls)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Tool {'was' if passed else 'was not'} called: {assertion.tool}",
            target=assertion.tool,
        )

    def _tool_sequence(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        called = [call.tool_name for call in recorder.tool_calls]
        sequence = assertion.sequence
        position = 0
        for tool_name in called:
            if position < len(sequence) and tool_name == sequence[position]:
                position += 1
        passed = position == len(sequence)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Tool sequence {'matched' if passed else 'did not match'}: {sequence}",
            target=" -> ".join(sequence),
        )

    def _tool_argument_equals(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        relevant_calls = [call for call in recorder.tool_calls if call.tool_name == assertion.tool]
        passed = any(self._normalize(call.arguments.get(assertion.argument)) == self._normalize(assertion.value) for call in relevant_calls)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Tool argument {'matched' if passed else 'did not match'} for {assertion.tool}.{assertion.argument}",
            target=assertion.tool,
        )

    def _result_dataset_exists(self, assertion: AssertionConfig, recorder: ExecutionRecorder, test_context: TestContext) -> AssertionItemResult:
        dataset_exists = assertion.alias in recorder.final_output.get("datasets", {}) or assertion.alias in test_context.datasets
        return AssertionItemResult(
            type=assertion.type,
            passed=dataset_exists,
            message=f"Result dataset {'exists' if dataset_exists else 'does not exist'}: {assertion.alias}",
            target=assertion.alias,
        )

    def _result_geometry_type_in(self, assertion: AssertionConfig, recorder: ExecutionRecorder, test_context: TestContext) -> AssertionItemResult:
        dataset = recorder.final_output.get("datasets", {}).get(assertion.target) or test_context.datasets.get(assertion.target)
        geometry_type = dataset.geometry_type if dataset else None
        passed = geometry_type in assertion.values
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Geometry type {'matched' if passed else 'did not match'} for {assertion.target}: {geometry_type}",
            target=assertion.target,
        )

    def _final_response_contains(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        response = recorder.final_output.get("final_response", "")
        passed = all(str(value) in response for value in assertion.values)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Final response {'contains' if passed else 'does not contain'} expected values: {assertion.values}",
        )

    def _skill_reference_loaded(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        passed = any(reference.path == assertion.path for reference in recorder.loaded_skill_references)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Skill reference {'was' if passed else 'was not'} loaded: {assertion.path}",
            target=assertion.path,
        )

    def _skill_reference_not_loaded(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        passed = all(reference.path != assertion.path for reference in recorder.loaded_skill_references)
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Skill reference {'was not' if passed else 'was'} loaded: {assertion.path}",
            target=assertion.path,
        )

    def _skill_reference_loaded_before_tool(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        reference_order = next((reference.order for reference in recorder.loaded_skill_references if reference.path == assertion.reference), None)
        tool_order = next((call.order for call in recorder.tool_calls if call.tool_name == assertion.tool), None)
        passed = reference_order is not None and tool_order is not None and reference_order < tool_order
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Skill reference {'was' if passed else 'was not'} loaded before tool: {assertion.reference} -> {assertion.tool}",
            target=assertion.reference,
        )

    def _skill_reference_load_count_less_than(self, assertion: AssertionConfig, recorder: ExecutionRecorder, _: TestContext) -> AssertionItemResult:
        limit = int(assertion.value)
        load_count = len(recorder.loaded_skill_references)
        passed = load_count < limit
        return AssertionItemResult(
            type=assertion.type,
            passed=passed,
            message=f"Skill reference load count {load_count} {'is' if passed else 'is not'} less than {limit}",
        )

    def _normalize(self, value: Any) -> Any:
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
