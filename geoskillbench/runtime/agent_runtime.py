from __future__ import annotations

import re
from typing import Any

from geoskillbench.models.result import AgentRunResult, ToolCallRecord
from geoskillbench.models.scenario import Scenario
from geoskillbench.models.skill import AgentSkill
from geoskillbench.models.test_context import TestContext
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter
from geoskillbench.runtime.actor_runtime import ActorRuntime


class AgentRuntime:
    def __init__(self, adapter: MCPToolAdapter, actor_runtime: ActorRuntime | None = None) -> None:
        self.adapter = adapter
        self.actor_runtime = actor_runtime or ActorRuntime()

    def run(
        self,
        scenario: Scenario,
        test_context: TestContext,
        skill: AgentSkill,
        tools: dict[str, Any],
    ) -> AgentRunResult:
        conversation: list[dict[str, str]] = [{"role": "user", "content": scenario.user_task}]
        tool_calls: list[ToolCallRecord] = []
        output_artifacts: dict[str, Any] = {}

        dataset_alias = self._infer_dataset_alias(scenario.user_task, test_context)
        if dataset_alias is None and scenario.actor.enabled:
            conversation.append({"role": "assistant", "content": "请确认要使用哪个数据集。"})
            actor_reply = self.actor_runtime.reply(scenario, conversation, test_context)
            conversation.append({"role": "user", "content": actor_reply})
            dataset_alias = self._parse_dataset_alias(actor_reply, test_context)
        if dataset_alias is None and test_context.datasets:
            dataset_alias = next(iter(test_context.datasets.keys()))

        distance = self._infer_distance(scenario.user_task)
        if distance is None and scenario.actor.enabled:
            conversation.append({"role": "assistant", "content": "请确认缓冲距离是多少米。"})
            actor_reply = self.actor_runtime.reply(scenario, conversation, test_context)
            conversation.append({"role": "user", "content": actor_reply})
            distance = self._infer_distance(actor_reply)

        if dataset_alias is None or distance is None:
            return AgentRunResult(
                final_response="任务执行失败，缺少必要输入。",
                tool_calls=tool_calls,
                conversation=conversation,
                output_artifacts=output_artifacts,
                status="failed",
                error_message="Missing required dataset alias or buffer distance.",
            )

        metadata_call = self.adapter.invoke("query_dataset_metadata", {"dataset": dataset_alias})
        tool_calls.append(metadata_call)
        if metadata_call.status != "success":
            return AgentRunResult(
                final_response="任务执行失败，无法读取数据元信息。",
                tool_calls=tool_calls,
                conversation=conversation,
                output_artifacts=output_artifacts,
                status="failed",
                error_message=metadata_call.error_message,
            )

        working_dataset = dataset_alias
        metadata = metadata_call.result or {}
        target_crs = metadata.get("crs", "")
        if target_crs.upper() == "EPSG:4326" and "reproject_dataset" in tools:
            reproject_call = self.adapter.invoke(
                "reproject_dataset",
                {"dataset": dataset_alias, "target_crs": "EPSG:3857", "output_alias": f"{dataset_alias}_metric"},
            )
            tool_calls.append(reproject_call)
            if reproject_call.status == "success":
                working_dataset = reproject_call.result["dataset"]
                target_crs = reproject_call.result["crs"]

        buffer_call = self.adapter.invoke(
            "create_buffer",
            {
                "dataset": working_dataset,
                "distance": distance,
                "distance_unit": "meter",
                "output_alias": "buffer_result",
            },
        )
        tool_calls.append(buffer_call)
        if buffer_call.status != "success":
            return AgentRunResult(
                final_response="任务执行失败，缓冲区分析未完成。",
                tool_calls=tool_calls,
                conversation=conversation,
                output_artifacts=output_artifacts,
                status="failed",
                error_message=buffer_call.error_message,
            )

        output_artifacts["result_dataset"] = buffer_call.result
        summary = (
            f"已完成 {dataset_alias} 数据的 {distance:g} 米缓冲区分析。"
            f" 结果数据句柄为 {buffer_call.result['handle']}，输出 CRS 为 {target_crs}。"
        )
        conversation.append({"role": "assistant", "content": summary})
        return AgentRunResult(
            final_response=summary,
            tool_calls=tool_calls,
            conversation=conversation,
            output_artifacts=output_artifacts,
            status="passed",
        )

    def _infer_dataset_alias(self, text: str, test_context: TestContext) -> str | None:
        explicit = self._parse_dataset_alias(text, test_context)
        if explicit:
            return explicit
        if len(test_context.datasets) == 1:
            return next(iter(test_context.datasets.keys()))
        return None

    def _parse_dataset_alias(self, text: str, test_context: TestContext) -> str | None:
        lowered = text.lower()
        for alias in test_context.datasets:
            if alias.lower() in lowered:
                return alias
        return None

    def _infer_distance(self, text: str) -> float | None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*米", text)
        if match:
            return float(match.group(1))
        return None
