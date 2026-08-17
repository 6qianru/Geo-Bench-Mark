"""前端"新建 Scenario"表单的 schema 定义（常用字段子集，模型驱动）。

前端通过 GET /api/scenarios/schema 拉取本结构并动态渲染表单：
- 每个 group 用 ``modes`` 标注适用哪种 scenario type（agent_skill_test / agent_test），
  前端按用户选择的模式过滤分组 → 实现"选择模式后配置项自动适配"。
- 每个 field 用点路径 key（如 ``runtime.max_turns``）映射到 Scenario 模型字段；
  ``required`` 标必填；``default`` 是预填的默认值（前端灰字展示，用户输入后覆盖）。
- ``data.fixtures`` 是动态行列表（group.list 定义行字段，前端可增删）。
- ``agent.flow`` 的 options 运行时由 FLOW_REGISTRY 动态填充（get_form_schema）。

为什么单独建文件而不是放 app.py：表单定义是稳定的领域数据，独立模块便于维护；
app.py 只暴露两个薄接口（拉 schema / 保存场景）。
"""

from __future__ import annotations

from copy import deepcopy

# 表单定义（静态部分；agent.flow 的 options 在 get_form_schema 里动态注入）
FORM_SCHEMA: list[dict] = [
    {
        "key": "basic",
        "label": "基本信息",
        "modes": ["agent_skill_test", "agent_test"],
        "fields": [
            {
                "key": "type",
                "label": "评测模式",
                "type": "select",
                "required": True,
                "default": "agent_skill_test",
                "options": [
                    {"value": "agent_skill_test", "label": "skill 模式 · 本地技能评测"},
                    {"value": "agent_test", "label": "agent 模式 · 指挥外部智能体"},
                ],
                "help": "决定后续配置项：skill 模式显示技能/数据源，agent 模式显示外部智能体/actor",
            },
            {"key": "id", "label": "场景 ID", "type": "text", "required": True, "default": "", "help": "唯一标识，也是保存的文件名（仅字母/数字/下划线/中划线）"},
            {"key": "name", "label": "场景名称", "type": "text", "required": True, "default": ""},
            {"key": "version", "label": "版本", "type": "text", "default": "1.0.0"},
            {"key": "description", "label": "描述", "type": "textarea", "default": ""},
            {"key": "user_task", "label": "用户任务", "type": "textarea", "required": True, "default": "", "help": "发给智能体的任务描述"},
        ],
    },
    {
        "key": "runtime",
        "label": "运行时配置",
        "modes": ["agent_skill_test", "agent_test"],
        "fields": [
            {
                "key": "runtime.executor",
                "label": "执行器",
                "type": "select",
                "required": True,
                "default": "skill",
                "options": [
                    {"value": "skill", "label": "skill · 本地技能评测"},
                    {"value": "orchestrator", "label": "orchestrator · 本地agent指挥外部agent"},
                    {"value": "external_driven", "label": "external_driven · 外部agent主导(角色反转)"},
                    {"value": "http_agent", "label": "http_agent · 直接透传外部agent"},
                    {"value": "nanobot", "label": "nanobot · 兼容模式"},
                ],
                "help": "跟随评测模式自动切换（skill模式→skill，agent模式→orchestrator），可手动改",
            },
            {"key": "runtime.agent_model", "label": "本地 Agent 模型", "type": "text", "default": "rule-based-agent", "help": "models.yaml 别名或 rule-based-agent（无真实模型时启发式兜底）"},
            {"key": "runtime.max_turns", "label": "最大轮次", "type": "number", "default": 6, "help": "orchestrator 最多向外部 agent 发送的指令数"},
            {"key": "runtime.timeout_seconds", "label": "超时(秒)", "type": "number", "default": 180},
        ],
    },
    {
        "key": "skill",
        "label": "技能配置 · skill 模式",
        "modes": ["agent_skill_test"],
        "fields": [
            {"key": "skill.path", "label": "技能文件路径", "type": "text", "required": True, "default": "", "help": "相对 scenarios/ 目录，如 ../skills/gis_buffer_analysis.skill.yml"},
            {
                "key": "skill.load_mode",
                "label": "加载模式",
                "type": "select",
                "default": "file",
                "options": [
                    {"value": "file", "label": "file · 单文件技能"},
                    {"value": "package", "label": "package · 技能包目录"},
                    {"value": "package_zip", "label": "package_zip · 技能包压缩包"},
                ],
            },
        ],
    },
    {
        "key": "data",
        "label": "数据源 · skill 模式",
        "modes": ["agent_skill_test"],
        "list": {
            "key": "fixtures",
            "label": "数据集",
            "row_label": "数据集",
            "fields": [
                {"key": "id", "label": "ID", "type": "text", "required": True, "default": ""},
                {"key": "name", "label": "名称", "type": "text", "default": ""},
                {
                    "key": "type",
                    "label": "类型",
                    "type": "select",
                    "default": "vector",
                    "options": [
                        {"value": "vector", "label": "vector"},
                        {"value": "raster", "label": "raster"},
                    ],
                },
                {
                    "key": "format",
                    "label": "格式",
                    "type": "select",
                    "default": "geojson",
                    "options": [
                        {"value": "geojson", "label": "geojson"},
                        {"value": "shapefile", "label": "shapefile"},
                        {"value": "geopackage", "label": "geopackage"},
                        {"value": "csv", "label": "csv"},
                    ],
                },
                {"key": "path", "label": "路径", "type": "text", "required": True, "default": "", "help": "相对 scenarios/ 目录，如 ../fixtures/schools.geojson"},
                {"key": "crs", "label": "CRS", "type": "text", "default": "EPSG:4326"},
            ],
        },
    },
    {
        "key": "agent",
        "label": "外部智能体 · agent 模式",
        "modes": ["agent_test"],
        "fields": [
            {"key": "agent.endpoint", "label": "Endpoint", "type": "text", "required": True, "default": "", "help": "外部智能体 HTTP 接口地址"},
            {"key": "agent.description", "label": "能力说明", "type": "textarea", "default": "", "help": "喂给 orchestrator 提示词，决定发什么指令、何时算达成"},
            {"key": "agent.flow", "label": "任务流程", "type": "select", "required": True, "default": "react", "options": [], "help": "orchestrator 本地 agent 的流程；自定义流程经 FLOW_REGISTRY 注册后也会出现在这里"},
            {"key": "agent.ask_user", "label": "允许追问用户", "type": "switch", "default": False, "help": "本地 agent 缺信息时按 [NEED_INTERACTION] 协议向用户(actor)追问（需配合下方 actor 开启）"},
            {"key": "agent.timeout_seconds", "label": "超时(秒)", "type": "number", "default": 120},
            {"key": "agent.api_key_env", "label": "API Key 环境变量", "type": "text", "default": "", "help": "请求头鉴权用的环境变量名（可选）"},
        ],
    },
    {
        "key": "actor",
        "label": "模拟用户 actor · agent 模式",
        "modes": ["agent_test"],
        "fields": [
            {"key": "actor.enabled", "label": "启用", "type": "switch", "default": True},
            {"key": "actor.max_turns", "label": "最大往返", "type": "number", "default": 3, "help": "最多允许 agent↔actor 往返次数"},
            {"key": "actor.goal", "label": "模拟用户目标", "type": "textarea", "default": "", "help": "模拟用户的目标/已知信息（如\"使用 schools 数据做 500 米缓冲\"），actor 回答追问时从中提取"},
        ],
    },
    {
        "key": "judge",
        "label": "评测判定",
        "modes": ["agent_skill_test", "agent_test"],
        "fields": [
            {"key": "judge.enabled", "label": "启用判定", "type": "switch", "default": True},
            {"key": "pass_criteria.judge_score_min", "label": "判定通过分数", "type": "number", "default": 0.8, "help": "judge 得分低于该值判失败"},
        ],
    },
]


def _available_flows() -> list[str]:
    # 触发完整注册链（react 在 orchestrator_executor 定义，scripted 在 orchestrator_flows，
    # keyword/pipeline 在 example_flows），再取注册表全量
    import geoskillbench.executors.orchestrator_executor  # noqa: F401

    from geoskillbench.executors.orchestrator_flows import available_flows

    return available_flows()


def get_form_schema() -> list[dict]:
    """返回深拷贝的表单定义，并注入 agent.flow 的动态选项。"""
    schema = deepcopy(FORM_SCHEMA)
    flows = _available_flows()
    for group in schema:
        for field in group.get("fields", []):
            if field.get("key") == "agent.flow":
                field["options"] = [{"value": name, "label": name} for name in flows]
    return schema
