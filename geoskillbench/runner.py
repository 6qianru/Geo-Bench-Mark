from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from geoskillbench.executors.factory import ExecutorFactory
from geoskillbench.assertions.assertion_engine import AssertionEngine
from geoskillbench.fixtures.fixture_manager import FixtureManager
from geoskillbench.loader.scenario_loader import ScenarioLoader
from geoskillbench.loader.skill_loader import SkillLoader
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
from geoskillbench.models.result import ExecutorSessionRequest, TestResult
from geoskillbench.models.test_context import MCPToolContext, SkillContext, TestContext
from geoskillbench.recorder.execution_recorder import ExecutionRecorder
from geoskillbench.reports.report_generator import ReportGenerator
from geoskillbench.runtime.actor_runtime import ActorRuntime
from geoskillbench.runtime.judge_runtime import JudgeEngine


STAGES = [
    "LOAD_SCENARIO",
    "PREPARE_DATA",
    "CONNECT_MCP",
    "LOAD_SKILL",
    "RUN_AGENT",
    "RUN_ASSERTIONS",
    "RUN_JUDGE",
    "GENERATE_REPORT",
    "CLEANUP",
]


class TestRunner:
    def __init__(self) -> None:
        self.scenario_loader = ScenarioLoader()
        self.fixture_manager = FixtureManager()
        self.skill_loader = SkillLoader()
        self.adapter = MCPToolAdapter()
        self.actor_runtime = ActorRuntime()
        self.assertion_engine = AssertionEngine()
        self.judge_engine = JudgeEngine()
        self.report_generator = ReportGenerator()

    def validate(self, scenario_path: str) -> dict:
        scenario = self.scenario_loader.load(scenario_path)
        skill = None
        if scenario.type != "agent_test":
            skill = self.skill_loader.load(scenario.skill, getattr(scenario, "_base_path", "."))
        return {
            "scenario_id": scenario.id,
            "scenario_name": scenario.name,
            "skill_id": skill.id if skill else None,
            "scenario_type": scenario.type,
            "executor": scenario.runtime.executor,
            "required_tools": [tool.name for tool in scenario.mcp.tools.required],
        }

    def list_tools(self, scenario_path: str) -> list[dict]:
        scenario = self.scenario_loader.load(scenario_path)
        self.adapter.connect_servers(scenario.mcp.servers)
        self.adapter.get_agent_tools(scenario.mcp.tools.required, scenario.mcp.tools.optional)
        return [
            {"server": tool.server, "name": tool.name, "optional": tool.optional}
            for tool in self.adapter.list_tools()
        ]

    def run(
        self,
        scenario_path: str,
        output_dir: str | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        run_config: dict[str, Any] | None = None,
    ) -> TestResult:
        stage_results = {stage: "PENDING" for stage in STAGES}
        start_time = time.perf_counter()
        recorder = ExecutionRecorder(scenario_id=Path(scenario_path).stem)
        test_context = None
        scenario = None

        def emit(event_type: str, **payload: Any) -> None:
            if event_callback is not None:
                event_callback({"type": event_type, **payload})

        try:
            stage_results["LOAD_SCENARIO"] = "RUNNING"
            emit("stage", stage="LOAD_SCENARIO", status="RUNNING", stage_results=dict(stage_results))
            scenario = self.scenario_loader.load(scenario_path)
            recorder.scenario_id = scenario.id
            stage_results["LOAD_SCENARIO"] = "PASSED"
            emit("stage", stage="LOAD_SCENARIO", status="PASSED", stage_results=dict(stage_results), scenario_id=scenario.id)

            stage_results["PREPARE_DATA"] = "RUNNING"
            emit("stage", stage="PREPARE_DATA", status="RUNNING", stage_results=dict(stage_results))
            datasets = self.fixture_manager.prepare(scenario)
            stage_results["PREPARE_DATA"] = "PASSED"
            emit("stage", stage="PREPARE_DATA", status="PASSED", stage_results=dict(stage_results))

            stage_results["CONNECT_MCP"] = "RUNNING"
            emit("stage", stage="CONNECT_MCP", status="RUNNING", stage_results=dict(stage_results))
            self.adapter.connect_servers(scenario.mcp.servers)
            self.adapter.register_datasets(datasets)
            tools = self.adapter.get_agent_tools(scenario.mcp.tools.required, scenario.mcp.tools.optional)
            self.adapter.validate_required_tools(scenario.mcp.tools.required)
            stage_results["CONNECT_MCP"] = "PASSED"
            emit("stage", stage="CONNECT_MCP", status="PASSED", stage_results=dict(stage_results))

            stage_results["LOAD_SKILL"] = "RUNNING"
            emit("stage", stage="LOAD_SKILL", status="RUNNING", stage_results=dict(stage_results))
            if scenario.type == "agent_test":
                skill = None
                stage_results["LOAD_SKILL"] = "SKIPPED"
                emit("stage", stage="LOAD_SKILL", status="SKIPPED", stage_results=dict(stage_results), reason="agent_test 不加载 skill")
            else:
                skill = self.skill_loader.load(scenario.skill, getattr(scenario, "_base_path", "."))
                recorder.record_skill_load(skill.id)
                stage_results["LOAD_SKILL"] = "PASSED"
                emit("stage", stage="LOAD_SKILL", status="PASSED", stage_results=dict(stage_results), skill_id=skill.id)

            test_context = TestContext(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                skill=(
                    SkillContext(
                        id=skill.id,
                        name=skill.name,
                        type=skill.type,
                        version=skill.version,
                        loaded=True,
                        description=skill.description,
                        category=skill.category,
                        entry_file=skill.entry_file,
                        base_dir=skill.base_dir,
                        base_prompt=skill.base_prompt,
                        metadata=skill.metadata,
                        references=[reference.model_dump() for reference in skill.references],
                        assumptions=skill.assumptions,
                        lazy_load_references=skill.lazy_load_references,
                    )
                    if skill is not None
                    else None
                ),
                datasets=datasets,
                mcp_tools={
                    tool.name: MCPToolContext(server=tool.server, available=True, optional=tool.optional)
                    for tool in self.adapter.list_tools()
                },
            )

            runtime_executor = (run_config or {}).get("executor", scenario.runtime.executor)
            memory_enabled = bool((run_config or {}).get("memory_enabled", scenario.runtime.memory_enabled))
            executor = ExecutorFactory.create(runtime_executor, self.adapter)
            test_context_dump = test_context.model_dump()
            test_context_dump["_recorder"] = recorder
            test_context_dump["_loaded_reference_calls"] = []
            session_request = ExecutorSessionRequest(
                scenario_id=scenario.id,
                skill_id=skill.id if skill else None,
                skill_prompt=self.skill_loader.render_prompt(skill) if skill else None,
                test_context=test_context_dump,
                tools=[tool.model_dump() if hasattr(tool, "model_dump") else tool for tool in scenario.mcp.tools.required + scenario.mcp.tools.optional],
                agent=scenario.agent.model_dump() if scenario.agent else None,
                role_model_config={"model": scenario.runtime.agent_model, "executor": runtime_executor},
                max_turns=scenario.runtime.max_turns,
                timeout_seconds=scenario.runtime.timeout_seconds,
                memory_enabled=memory_enabled,
            )
            session = executor.create_session(session_request)
            emit(
                "executor_session",
                stage="RUN_AGENT",
                status="RUNNING",
                stage_results=dict(stage_results),
                executor=runtime_executor,
                session_id=session.session_id,
                runtime_mode=session.runtime_mode,
                runtime_metadata=session.runtime_metadata,
                memory_enabled=memory_enabled,
            )

            stage_results["RUN_AGENT"] = "RUNNING"
            emit("stage", stage="RUN_AGENT", status="RUNNING", stage_results=dict(stage_results))
            conversation: list[dict[str, Any]] = []
            tool_calls: list[Any] = []
            output_artifacts: dict[str, Any] = {}
            final_response = ""
            run_failed = False
            current_message = scenario.user_task
            conversation.append({"role": "user", "content": current_message})
            turn_limit = scenario.runtime.max_turns

            for turn_index in range(turn_limit):
                step_result = executor.send_message(session.session_id, current_message)
                tool_calls.extend(step_result.tool_calls)
                output_artifacts.update(step_result.artifacts)
                final_response = step_result.response or final_response
                conversation.append({"role": "assistant", "content": step_result.response})
                emit(
                    "executor_step",
                    stage="RUN_AGENT",
                    status="RUNNING",
                    stage_results=dict(stage_results),
                    turn_index=turn_index,
                    need_interaction=step_result.need_interaction,
                    finished=step_result.finished,
                    response=step_result.response,
                    tool_call_count=len(step_result.tool_calls),
                )

                if step_result.error_message:
                    run_failed = True
                    recorder.record_error(step_result.error_message)
                    break

                if step_result.finished:
                    break

                if step_result.need_interaction and scenario.actor.enabled and turn_index < scenario.actor.max_turns:
                    actor_reply = self.actor_runtime.reply(scenario, conversation, test_context)
                    conversation.append({"role": "user", "content": actor_reply})
                    emit(
                        "actor_reply",
                        stage="RUN_AGENT",
                        status="RUNNING",
                        stage_results=dict(stage_results),
                        turn_index=turn_index,
                        message=actor_reply,
                    )
                    current_message = actor_reply
                    continue

                break

            executor.close_session(session.session_id)
            recorder.record_conversation(conversation)
            recorder.record_tool_calls(tool_calls)
            final_datasets = self.adapter.get_dataset_store()
            recorder.record_final_output(
                {
                    "final_response": final_response,
                    "datasets": final_datasets,
                    "output_artifacts": output_artifacts,
                }
            )
            stage_results["RUN_AGENT"] = "FAILED" if run_failed or not final_response else "PASSED"
            emit(
                "agent_result",
                stage="RUN_AGENT",
                status=stage_results["RUN_AGENT"],
                stage_results=dict(stage_results),
                executor=runtime_executor,
                final_response=final_response,
                tool_call_count=len(tool_calls),
            )

            stage_results["RUN_ASSERTIONS"] = "RUNNING"
            emit("stage", stage="RUN_ASSERTIONS", status="RUNNING", stage_results=dict(stage_results))
            assertion_result = self.assertion_engine.run(scenario.assertions, recorder, test_context)
            stage_results["RUN_ASSERTIONS"] = "PASSED" if assertion_result.passed else "FAILED"
            emit(
                "assertions",
                stage="RUN_ASSERTIONS",
                status=stage_results["RUN_ASSERTIONS"],
                stage_results=dict(stage_results),
                passed=assertion_result.passed,
                score=assertion_result.score,
            )

            stage_results["RUN_JUDGE"] = "RUNNING"
            emit("stage", stage="RUN_JUDGE", status="RUNNING", stage_results=dict(stage_results))
            judge_result = self.judge_engine.evaluate(scenario, test_context, recorder, assertion_result)
            stage_results["RUN_JUDGE"] = "PASSED" if judge_result.passed else "FAILED"
            emit(
                "judge",
                stage="RUN_JUDGE",
                status=stage_results["RUN_JUDGE"],
                stage_results=dict(stage_results),
                passed=judge_result.passed,
                score=judge_result.score,
            )

            stage_results["GENERATE_REPORT"] = "RUNNING"
            emit("stage", stage="GENERATE_REPORT", status="RUNNING", stage_results=dict(stage_results))
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            status = "passed" if assertion_result.passed and judge_result.passed else "failed"
            result = TestResult(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                status=status,
                duration_ms=duration_ms,
                stage_results=dict(stage_results),
                skill=test_context.skill.model_dump() if test_context.skill else None,
                tool_calls=[call.model_dump() for call in recorder.tool_calls],
                assertions=[item.model_dump() for item in assertion_result.items],
                judge=judge_result.model_dump(),
                conversation=recorder.conversation,
                final_output={
                    "final_response": recorder.final_output["final_response"],
                    "executor": runtime_executor,
                    "runtime_mode": session.runtime_mode,
                    "runtime_metadata": session.runtime_metadata,
                    "output_artifacts": output_artifacts,
                },
                loaded_skill_references=recorder.loaded_skill_references,
                errors=recorder.errors,
            )
            stage_results["GENERATE_REPORT"] = "PASSED"
            emit("stage", stage="GENERATE_REPORT", status="PASSED", stage_results=dict(stage_results))
            stage_results["CLEANUP"] = "RUNNING"
            emit("stage", stage="CLEANUP", status="RUNNING", stage_results=dict(stage_results))
            self.fixture_manager.cleanup(test_context)
            stage_results["CLEANUP"] = "PASSED"
            result.stage_results = dict(stage_results)
            if output_dir:
                self.report_generator.write_reports(output_dir, result)
            emit("result", stage="CLEANUP", status=result.status, stage_results=dict(stage_results), result=result.model_dump())
            return result
        except Exception as exc:
            recorder.record_error(str(exc))
            failed_stage = next((stage for stage, status in stage_results.items() if status == "RUNNING"), "RUN_AGENT")
            stage_results[failed_stage] = "FAILED"
            emit("error", stage=failed_stage, status="FAILED", stage_results=dict(stage_results), message=str(exc))
            stage_results["CLEANUP"] = "RUNNING"
            emit("stage", stage="CLEANUP", status="RUNNING", stage_results=dict(stage_results))
            if test_context is not None:
                self.fixture_manager.cleanup(test_context)
            stage_results["CLEANUP"] = "PASSED"
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            if scenario is None:
                scenario_id = Path(scenario_path).stem
                scenario_name = scenario_id
                skill_info = {"id": "unknown", "version": "unknown", "loaded": False}
            else:
                scenario_id = scenario.id
                scenario_name = scenario.name
                skill_info = {"id": scenario.target.skill_id or "unknown", "version": scenario.target.skill_version, "loaded": False}
            result = TestResult(
                scenario_id=scenario_id,
                scenario_name=scenario_name,
                status="failed",
                duration_ms=duration_ms,
                stage_results=stage_results,
                skill=skill_info,
                tool_calls=[call.model_dump() for call in recorder.tool_calls],
                assertions=[],
                judge={"score": 0.0, "passed": False, "reason": "Runner failed before judge execution."},
                conversation=recorder.conversation,
                final_output=recorder.final_output,
                loaded_skill_references=recorder.loaded_skill_references,
                errors=recorder.errors,
            )
            emit("result", stage="CLEANUP", status="failed", stage_results=dict(stage_results), result=result.model_dump())
            return result
