"""外部智能体"反问/完成/进展"判定的单一事实源。

external_driven executor 在执行时用它分类外部回复，judge 的规则镜像扣分也用它判定
"外部 agent 是否反问过"。两处共用同一实现，保证"执行时怎么分类"与"判分时怎么判没反问"
严格一致，避免镜像漂移（改判定只改这一处）。

约定（与现有平台前缀协议无关，纯文本启发式）：
- 反问（ask）：外部回复像是向用户要信息 —— 问号结尾或含提问/确认关键词。
- 完成（complete）：外部回复明确宣告结果 —— 含完成/结果关键词，且不以问号结尾
  （"我该输出什么格式？" 这类问句不能被误判为完成）。
- 进展（continue）：其余 —— 中间状态，平台侧可引导继续。
"""

from __future__ import annotations

from typing import Literal

_QUESTION_KEYWORDS = (
    "请问", "哪个", "什么", "多少", "哪些", "怎么", "如何", "是否", "能否", "麻烦",
    "请提供", "请告诉我", "请确认", "需要您", "请补充",
    "please", "which", "what", "how", "could", "would",
    "数据集", "格式", "距离", "单位", "crs", "坐标系", "坐标",
)

_COMPLETION_KEYWORDS = (
    "完成", "已生成", "已保存", "已创建", "成功", "结果数据", "结果数据集",
    "result", "done", "finished", "complete", "dataset://",
)


def looks_like_question(text: str) -> bool:
    """外部回复是否像在反问/要信息。"""
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith(("?", "？")):
        return True
    low = t.lower()
    return any(k in t or k in low for k in _QUESTION_KEYWORDS)


def looks_complete(text: str) -> bool:
    """外部回复是否像已完成任务（以问号结尾的不算完成宣告）。"""
    t = (text or "").strip()
    if not t or t.endswith(("?", "？")):
        return False
    low = t.lower()
    return any(k in t or k in low for k in _COMPLETION_KEYWORDS)


def classify_external_reply(text: str) -> Literal["complete", "ask", "continue"]:
    """分类外部回复：complete 优先（终止语义），其次 ask，其余 continue。"""
    if looks_complete(text):
        return "complete"
    if looks_like_question(text):
        return "ask"
    return "continue"
