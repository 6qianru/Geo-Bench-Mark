from __future__ import annotations

import re

from geoskillbench.models.scenario import Scenario
from geoskillbench.models.test_context import TestContext


class ActorRuntime:
    def reply(self, scenario: Scenario, conversation: list[dict], test_context: TestContext) -> str:
        question = conversation[-1]["content"] if conversation else ""
        lowered = question.lower()
        if "哪个数据" in question or "dataset" in lowered or "data" in lowered:
            return self._extract_dataset_alias(scenario, test_context)
        if "缓冲距离" in question or "distance" in lowered or "多少米" in question:
            return self._extract_distance_answer(scenario.actor.goal) or "500 米"
        if "输出格式" in question or "format" in lowered:
            return self._extract_output_format(scenario.actor.goal) or "GeoJSON"
        return "请按场景目标继续执行。"

    def _extract_dataset_alias(self, scenario: Scenario, test_context: TestContext) -> str:
        match = re.search(r"使用\s+([A-Za-z0-9_]+)\s+数据", scenario.actor.goal)
        if match:
            return f"使用 {match.group(1)} 数据。"
        if test_context.datasets:
            alias = next(iter(test_context.datasets.keys()))
            return f"使用 {alias} 数据。"
        return "使用默认数据。"

    def _extract_distance_answer(self, goal: str) -> str | None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*米", goal)
        if match:
            return f"{match.group(1)} 米。"
        return None

    def _extract_output_format(self, goal: str) -> str | None:
        match = re.search(r"输出格式.*?([A-Za-z]+)", goal)
        if match:
            return f"{match.group(1)}。"
        return None
