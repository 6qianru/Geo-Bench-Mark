from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillReference(BaseModel):
    id: str
    path: str
    title: str
    summary: str | None = None
    required: bool = False
    tags: list[str] = Field(default_factory=list)
    trigger_keywords: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class LoadedSkillReference(BaseModel):
    path: str
    title: str | None = None
    loaded_at: str
    loaded_by: str = "executor"
    content_hash: str | None = None
    order: int | None = None


class AgentSkill(BaseModel):
    id: str
    name: str
    version: str
    type: str = "prompt_skill"
    description: str = ""
    category: str | None = None
    when_to_use: str = ""
    required_inputs: list[str] = Field(default_factory=list)
    optional_inputs: list[str] = Field(default_factory=list)
    recommended_mcp_tools: list[str] = Field(default_factory=list)
    instructions: str = ""
    tool_sequence: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    common_failures: list[str] = Field(default_factory=list)
    entry_file: str | None = None
    base_dir: str | None = None
    base_prompt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    references: list[SkillReference] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    lazy_load_references: bool = False
