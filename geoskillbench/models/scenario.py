from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class RuntimeConfig(BaseModel):
    executor: str = "langgraph"
    agent_model: str = "rule-based-agent"
    actor_model: str = "rule-based-actor"
    judge_model: str = "rule-based-judge"
    max_turns: int = 6
    timeout_seconds: int = 180
    memory_enabled: bool = False


class FixtureConfig(BaseModel):
    id: str
    name: str
    type: str
    format: str
    path: str
    crs: str | None = None
    geometry_type: str | None = None
    import_as: str = "dataset"
    register_metadata: bool = True
    cleanup: bool = True


class DataConfig(BaseModel):
    fixtures: list[FixtureConfig] = Field(default_factory=list)


class MCPServerConfig(BaseModel):
    id: str
    name: str
    transport: str
    url: str
    required: bool = True


class ToolRef(BaseModel):
    server: str
    name: str


class MCPToolsConfig(BaseModel):
    required: list[ToolRef] = Field(default_factory=list)
    optional: list[ToolRef] = Field(default_factory=list)


class MCPConfig(BaseModel):
    servers: list[MCPServerConfig] = Field(default_factory=list)
    tools: MCPToolsConfig = Field(default_factory=MCPToolsConfig)


class SkillConfig(BaseModel):
    load_mode: str = "file"
    path: str
    entry: str | None = None
    lazy_load_references: bool = False
    required: bool = True


class ActorConfig(BaseModel):
    enabled: bool = True
    profile: str = "normal_user"
    max_turns: int = 5
    goal: str = ""


class ExpectedBehavior(BaseModel):
    should_load_skills: list[str] = Field(default_factory=list)
    should_call_tools: list[str] = Field(default_factory=list)
    optional_tools: list[str] = Field(default_factory=list)
    should_not: list[str] = Field(default_factory=list)


class AssertionConfig(BaseModel):
    type: str
    tool: str | None = None
    skill_id: str | None = None
    path: str | None = None
    reference: str | None = None
    argument: str | None = None
    value: Any = None
    alias: str | None = None
    target: str | None = None
    values: list[Any] = Field(default_factory=list)
    sequence: list[str] = Field(default_factory=list)
    relation: str | None = None
    source: str | None = None
    field: str | None = None
    rule: str | None = None


class JudgeConfig(BaseModel):
    enabled: bool = True
    rubric: list[str] = Field(default_factory=list)


class PassCriteria(BaseModel):
    required_assertions_passed: bool = True
    judge_score_min: float = 0.8


class AgentConfig(BaseModel):
    """agent_test 模式下外部智能体接入配置（见 docs/Agent接入契约.md）"""

    type: str = "http"
    endpoint: str | None = None
    query_params: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    api_key_env: str | None = None
    body: dict[str, Any] = Field(default_factory=dict)
    stream_response: bool = False
    timeout_seconds: int = 120
    session_id: str | None = None


class TargetConfig(BaseModel):
    skill_id: str | None = None
    skill_version: str | None = None


class Scenario(BaseModel):
    id: str
    name: str
    version: str
    type: Literal["agent_skill_test", "agent_test"] = "agent_skill_test"
    description: str = ""
    target: TargetConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    skill: SkillConfig | None = None
    agent: AgentConfig | None = None
    user_task: str
    actor: ActorConfig = Field(default_factory=ActorConfig)
    expected_behavior: ExpectedBehavior = Field(default_factory=ExpectedBehavior)
    assertions: list[AssertionConfig] = Field(default_factory=list)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    pass_criteria: PassCriteria = Field(default_factory=PassCriteria)

    @model_validator(mode="after")
    def _check_type_fields(self) -> "Scenario":
        if self.type == "agent_skill_test" and self.skill is None:
            raise ValueError("type=agent_skill_test 时 skill 必填")
        if self.type == "agent_test" and self.agent is None:
            raise ValueError("type=agent_test 时 agent 配置必填")
        return self
