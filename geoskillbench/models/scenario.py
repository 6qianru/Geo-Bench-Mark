from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class RuntimeConfig(BaseModel):
    executor: str = "skill"  # skill=本地技能评测（历史别名 langgraph）；orchestrator=指挥外部 agent；http_agent=透传
    agent_model: str = "rule-based-agent"
    actor_model: str = "rule-based-actor"
    judge_model: str = ""  # 空 = 跟随 agent_model（迭代 2 LLM judge）；配 rule-based-* 开头或别名缺失则显式降级规则判定
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
    include_conversation: bool = False  # 默认只喂 最终回答+工具调用+断言结果；true 时追加对话（截断）
    penalize_no_ask_back: bool = False  # external_driven 场景：外部 agent 缺必要信息不反问自行猜测 → 连续扣分（LLM rubric + 规则镜像）；默认关=存量零回归


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
    description: str = ""  # 外部 agent 能力说明，喂给 orchestrator 系统提示词（决定发什么指令、何时算达成）
    # orchestrator 任务流：react=现有 ReAct 模板（默认）；scripted=内置固定节点流程；
    # 其它值=orchestrator_flows.FLOW_REGISTRY 里注册的自定义 flow 名
    flow: str = "react"
    # orchestrator 本地 agent 是否允许缺信息时向用户(actor)追问（默认关，存量场景零回归）
    ask_user: bool = False


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
