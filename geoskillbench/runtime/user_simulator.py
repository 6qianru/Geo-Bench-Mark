"""UserSimulator：共享的"模拟用户"实现，回答被测 agent 的反问/引导。

反问闭环下沉重构：原来 runner 层用 ActorRuntime 在每次 need_interaction 后生成用户回答，
现在改为各 executor 内部闭环（orchestrator / skill / heuristic / external_driven 复用本类）。

规则 or LLM 双实现：
- user_model 为空或 rule-based-* 前缀 → 规则回答（正则从 user_goal 提取数据集/距离/格式）。
- 否则用 langchain LLM + persona 提示词（temperature 默认 0.7，保自然度/不确定性）。
- LLM 优先、失败自动降级规则，保证反问永远有回答、评测不卡死。
"""

from __future__ import annotations

import re
from typing import Any

from geoskillbench.runtime.llm import build_llm


class UserSimulator:
    # 动态候选提示：agent 列出候选（数据集/功能/参数）让用户选，形式如"可选：A、B、C"或"（候选：A, B, C）"
    _CANDIDATE_HINT = re.compile(
        r"(?:可选|候选|可用|可以选择|以下)[^。\n)）:：]*[：:]?\s*[（(]?([^。\n)）]+)"
    )

    def __init__(
        self,
        goal: str = "",
        profile: str = "normal_user",
        model: str = "rule-based-user",
        datasets: dict[str, Any] | None = None,
        models_config: dict[str, Any] | None = None,
        temperature: float = 0.7,
    ) -> None:
        self.goal = goal
        self.profile = profile
        self.datasets = datasets or {}
        self.mode = "rule" if (not model or model.startswith("rule-based-")) else "llm"
        self.llm = None
        if self.mode == "llm":
            self.llm = build_llm(model, temperature=temperature, config=models_config)

    @property
    def is_llm(self) -> bool:
        return self.mode == "llm" and self.llm is not None

    def reply(self, question: str) -> str:
        """回答 agent 反问。LLM 优先，失败/无 LLM 走规则回答。"""
        if self.is_llm:
            text = self._llm_chat(self._persona(), f"智能体问你：{question}")
            if text:
                return text
        return self.rule_reply(question)

    def nudge(self, progress: str) -> str:
        """agent 进展中给一句引导。LLM 可顺势补信息，规则固定文案。"""
        if self.is_llm:
            persona = self._persona() + "\n5. 智能体还在执行中，你可以给一句简短引导，或补充目标里可能遗漏的信息。"
            text = self._llm_chat(persona, f"智能体当前进展：{progress}")
            if text:
                return text
        return "请继续执行，完成后把结果告诉我。"

    def rule_reply(self, question: str) -> str:
        """规则回答：候选选择 > 格式 > 数据集 > 距离 > 兜底。

        顺序敏感：反问格式的语句常带"缓冲距离 500 米"等已确认信息，须先判"格式"再判"距离"；
        候选选择最优先——动态选项是 agent 探索后生成的，goal 不一定有对应条目，匹配不上取第一个。
        """
        lowered = question.lower()
        candidates = self._extract_candidates(question)
        if candidates:
            return f"使用 {self._choose_from_candidates(candidates)}。"
        if "格式" in question or "format" in lowered:
            match = re.search(r"输出格式.*?([A-Za-z]+)", self.goal)
            return f"{match.group(1)}。" if match else "GeoJSON。"
        if "数据集" in question or "dataset" in lowered or "data" in lowered:
            match = re.search(r"使用\s+([A-Za-z0-9_]+)\s+数据", self.goal)
            if match:
                return f"使用 {match.group(1)} 数据。"
            if len(self.datasets) == 1:
                return f"使用 {next(iter(self.datasets.keys()))} 数据。"
            return "使用默认数据。"
        if "距离" in question or "distance" in lowered or "多少米" in question:
            match = re.search(r"(\d+(?:\.\d+)?)\s*米", self.goal)
            return f"{match.group(1)} 米。" if match else "500 米。"
        return "这个我不太确定，你按你的判断做吧。"

    # ---- persona 与 LLM 原语 ----

    def _persona(self) -> str:
        return (
            "你正在参与一个 GIS 智能体评测任务，扮演一位真实普通用户。\n"
            f"你的身份：{self.profile}\n"
            f"你的目标/已知信息：{self.goal}\n\n"
            "行为规则：\n"
            "1. 只回答智能体主动提出的问题，不要代替它做决定，不要一次性把目标细节全讲出来，"
            "按它的问题逐步给出所需信息。\n"
            "2. 回答口语化、自然，符合普通用户水平；可以略带不确定（'大概是''记不清名字''按默认的就行吧'）。\n"
            "3. 若问题涉及目标里没有的信息，明确说'这个我不确定，你看着办吧'，不编造。\n"
            "4. 不要输出解释或前缀，直接给回答。"
        )

    def _llm_chat(self, system: str, human: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage  # 惰性导入，遵循惯例

        response = self.llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
        return self._to_text(response)

    @staticmethod
    def _to_text(response: Any) -> str:
        text = getattr(response, "content", response)
        if isinstance(text, list):
            text = "".join(str(part.get("text", "")) for part in text if isinstance(part, dict)) or str(text)
        return str(text).strip()

    # ---- 候选选择（自 ActorRuntime 移植）----

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

    def _choose_from_candidates(self, candidates: list[str]) -> str:
        """用 user_goal 匹配候选：精确 > 子串包含 > 取第一个。"""
        match = re.search(r"使用\s+([A-Za-z0-9_]+)\s+数据", self.goal)
        target = match.group(1) if match else ""
        if target:
            for candidate in candidates:
                if candidate == target or target in candidate or candidate in target:
                    return candidate
        return candidates[0]
