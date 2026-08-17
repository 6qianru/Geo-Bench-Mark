from __future__ import annotations

import re

from geoskillbench.models.scenario import Scenario
from geoskillbench.models.test_context import TestContext


class ActorRuntime:
    # 动态候选提示：agent 列出候选（数据集/功能/参数）让用户选，形式如"可选：A、B、C"或"（候选：A, B, C）"
    _CANDIDATE_HINT = re.compile(
        r"(?:可选|候选|可用|可以选择|以下)[^。\n)）:：]*[：:]?\s*[（(]?([^。\n)）]+)"
    )

    def reply(self, scenario: Scenario, conversation: list[dict], test_context: TestContext) -> str:
        question = conversation[-1]["content"] if conversation else ""
        lowered = question.lower()
        candidates = self._extract_candidates(question)
        if candidates:
            # 候选选择优先：动态选项是 agent 探索后生成的，goal 不一定有对应条目，匹配不上取第一个
            chosen = self._choose_from_candidates(candidates, scenario.actor.goal)
            return f"使用 {chosen}。"
        if "哪个数据" in question or "dataset" in lowered or "data" in lowered:
            return self._extract_dataset_alias(scenario, test_context)
        if "缓冲距离" in question or "distance" in lowered or "多少米" in question:
            return self._extract_distance_answer(scenario.actor.goal) or "500 米"
        if "输出格式" in question or "format" in lowered:
            return self._extract_output_format(scenario.actor.goal) or "GeoJSON"
        return "请按场景目标继续执行。"

    def _extract_candidates(self, question: str) -> list[str]:
        """从追问里提取候选列表；无候选返回空列表。"""
        match = self._CANDIDATE_HINT.search(question)
        if not match:
            return []
        return self._split_candidates(match.group(1))

    @staticmethod
    def _split_candidates(body: str) -> list[str]:
        """按顿号/逗号/分号/竖线/或 切分候选，过滤问号尾巴、超长句子与空项。"""
        parts = re.split(r"[、,，;；|/]", body)
        tokens: list[str] = []
        for part in parts:
            if "或" in part:
                tokens.extend(re.split(r"\s*或\s*", part))
            else:
                tokens.append(part)
        result = []
        for token in tokens:
            token = token.strip(" 　")
            if not token or "?" in token or "？" in token or len(token) > 40:
                continue
            result.append(token)
        return result

    def _choose_from_candidates(self, candidates: list[str], goal: str) -> str:
        """用 actor.goal 匹配候选：精确 > 子串包含 > 取第一个。"""
        match = re.search(r"使用\s+([A-Za-z0-9_]+)\s+数据", goal)
        target = match.group(1) if match else ""
        if target:
            for candidate in candidates:
                if candidate == target or target in candidate or candidate in target:
                    return candidate
        return candidates[0]

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
