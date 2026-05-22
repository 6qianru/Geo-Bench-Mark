# GeoSkillBench Skill Package 按需加载支持补充设计文档

版本：v0.1  
性质：主系统设计文档的附加说明  
适用范围：补充说明 GeoSkillBench 如何支持 `SKILL.md + references/ + metadata` 形式的 Agent Skill Package，并支持按需加载参考文档。

---

## 1. 背景说明

GeoSkillBench 主系统设计中已经支持 Agent Skill 作为被测对象。此前主要描述的是单文件 Skill，例如：

```text
gis_buffer_analysis.skill.yml
```

但实际项目中，复杂 GIS Agent Skill 往往不适合写成一个很长的提示词文件。更合理的方式是将技能拆成一个 Skill Package：

```text
gis-vector-analysis/
├── SKILL.md
├── README.md
├── SKILL_FLOWCHART.md
├── skill.metadata.json
└── references/
    ├── S0_执行计划.md
    ├── 00_数据确认.md
    ├── 00_要素定位.md
    ├── 01_缓冲区分析.md
    ├── 02_空间查询.md
    ├── 03_相交叠置.md
    ├── 04_裁剪分析.md
    ├── 05_擦除分析.md
    ├── 06_联合分析.md
    ├── 07_融合分析.md
    ├── 08_属性统计.md
    ├── 09_多图层叠置.md
    └── 10_结果展示.md
```

这种结构适合表达复杂技能：

```text
SKILL.md：
  技能入口文件，描述总体能力、适用范围、执行总原则。

references/：
  子能力参考文档，按需加载，例如缓冲区分析、裁剪分析、空间查询、结果展示等。

skill.metadata.json：
  技能元信息，描述 skill id、category、version、entry、references 等。

README.md：
  给人看的说明文档。

SKILL_FLOWCHART.md：
  可选，描述技能执行流程图或流程说明。
```

本补充文档的目标是让 GeoSkillBench 支持这种 Skill Package，并支持类似 Claude Skills / LangChain Skills 的“按需加载”模式。

---

## 2. 设计目标

GeoSkillBench 需要支持以下能力：

```text
1. 上传 Skill Package zip。
2. 解压并安全校验目录结构。
3. 识别 SKILL.md 作为入口文件。
4. 读取 skill.metadata.json 或 skill.json 作为技能元数据。
5. 扫描 references/ 目录并建立引用索引。
6. Executor 初始只加载 SKILL.md，不一次性加载全部 references。
7. Executor 可通过 load_skill_reference 工具按需读取 references 文件。
8. 系统记录每次 reference 加载行为。
9. Assertion Engine 可以校验某个 reference 是否被加载。
10. 前端可以展示 Skill Package 结构和 references 列表。
```

核心原则：

```text
入口短、细节拆分、按需加载、可记录、可断言、可复现。
```

---

## 3. Skill 类型扩展

系统应支持两类 Agent Skill。

### 3.1 Single-file Skill

单文件技能：

```text
gis_buffer_analysis.skill.yml
gis_buffer_analysis.md
```

适合简单技能。

---

### 3.2 Skill Package

包式技能：

```text
gis-vector-analysis.zip
```

解压后：

```text
gis-vector-analysis/
├── SKILL.md
├── skill.metadata.json
└── references/
```

适合复杂技能。

推荐内部类型：

```text
prompt_skill_package
```

---

## 4. Skill Package 目录规范

### 4.1 必需文件

```text
SKILL.md
```

说明：

```text
技能入口文件。
Executor 创建 session 时，默认注入该文件内容。
```

---

### 4.2 推荐文件

```text
skill.metadata.json
```

说明：

```text
技能元数据文件。
用于前端展示、工具检查、reference 索引构建和 Scenario 匹配。
```

---

### 4.3 推荐目录

```text
references/
```

说明：

```text
存放子能力参考文档。
这些文件不应默认全部注入上下文，而应按需加载。
```

---

### 4.4 可选文件

```text
README.md
SKILL_FLOWCHART.md
examples/
schemas/
fixtures/
```

说明：

```text
README.md 用于人类阅读。
SKILL_FLOWCHART.md 用于展示流程。
examples/ 可放示例任务。
schemas/ 可放工具参数 schema 或输出 schema。
fixtures/ 可放技能自带的轻量测试数据，但正式测试建议仍由 Scenario 管理数据。
```

---

## 5. 推荐 Skill Package 结构

```text
skill-root/
├── SKILL.md
├── skill.metadata.json
├── README.md
├── SKILL_FLOWCHART.md
├── references/
│   ├── S0_执行计划.md
│   ├── 00_数据确认.md
│   ├── 01_缓冲区分析.md
│   └── 10_结果展示.md
├── examples/
│   └── buffer_school_500m.example.md
└── schemas/
    └── output.schema.json
```

MVP 阶段只要求：

```text
SKILL.md
references/
```

`skill.metadata.json` 强烈建议支持，但可以允许缺省。

---

## 6. skill.metadata.json 设计

当前已有的 metadata 可以很简单，例如：

```json
{
  "category": "gis"
}
```

但为了工程化使用，建议扩展为：

```json
{
  "id": "gis-vector-analysis",
  "name": "矢量空间分析技能",
  "version": "1.0.0",
  "type": "prompt_skill_package",
  "category": "gis",
  "entry": "SKILL.md",
  "description": "用于处理缓冲区、空间查询、叠置、裁剪、擦除、联合、融合、属性统计等矢量空间分析任务。",
  "references_dir": "references",
  "trigger_keywords": [
    "周边",
    "范围内",
    "缓冲区",
    "叠加",
    "裁剪",
    "擦除",
    "统计",
    "空间查询",
    "相交",
    "融合"
  ],
  "assumptions": [
    "系统数据均使用投影坐标系，单位为米"
  ],
  "recommended_mcp_services": [
    "common",
    "vectoranalyst",
    "dataprocess",
    "datatools",
    "datamanager"
  ],
  "recommended_tools": [
    "common.searchDatasetsSemantic",
    "common.getDatasetByName",
    "vectoranalyst.createBuffer",
    "vectoranalyst.queryBySQL",
    "datatools.exportGeoJson",
    "datamanager.deleteDataset"
  ],
  "references": [
    {
      "id": "plan",
      "path": "references/S0_执行计划.md",
      "title": "执行计划",
      "required": true,
      "tags": ["plan", "workflow"]
    },
    {
      "id": "data-confirmation",
      "path": "references/00_数据确认.md",
      "title": "数据确认",
      "required": true,
      "tags": ["data", "metadata"],
      "tools": [
        "common.searchDatasetsSemantic",
        "common.getDatasetByName"
      ]
    },
    {
      "id": "buffer",
      "path": "references/01_缓冲区分析.md",
      "title": "缓冲区分析",
      "trigger_keywords": ["周边", "缓冲区", "服务范围", "影响范围"],
      "tools": [
        "vectoranalyst.createBuffer"
      ]
    }
  ]
}
```

---

## 7. SKILL.md 入口文件规范

`SKILL.md` 是 Executor 初始加载的核心提示词。

建议包含：

```text
1. 技能名称
2. 技能适用范围
3. 总体执行原则
4. 按需加载 references 的规则
5. 任务分类逻辑
6. 工具调用约束
7. 最终输出要求
```

建议在 `SKILL.md` 中明确写入：

```text
你当前加载的是一个 Skill Package。
你必须先阅读本文件。
如果本文件或已加载 reference 要求读取某个 references 文件，
你必须通过 load_skill_reference 工具读取该文件后再继续。
不得凭记忆猜测 reference 文件内容。
不得读取当前 Skill Package 外部文件。
```

---

## 8. references 文件设计

references 文件用于描述子任务能力，例如：

```text
references/01_缓冲区分析.md
references/04_裁剪分析.md
references/10_结果展示.md
```

每个 reference 建议包含：

```text
1. 适用场景
2. 前置条件
3. 需要确认的信息
4. 推荐 MCP 工具
5. 工具调用顺序
6. 参数填写规则
7. 常见错误
8. 输出要求
```

示例结构：

```markdown
# 01 缓冲区分析

## 适用场景

当用户要求分析某类空间对象周边一定距离范围时，使用缓冲区分析。

## 前置条件

- 已确认输入数据集
- 已确认缓冲距离
- 已确认距离单位

## 推荐工具

- vectoranalyst.createBuffer

## 执行步骤

1. 确认输入数据。
2. 确认缓冲距离。
3. 调用 vectoranalyst.createBuffer。
4. 检查结果数据是否生成。
5. 返回结果数据句柄。

## 常见错误

- 未确认缓冲距离就执行。
- 使用错误的数据集。
- 没有返回结果数据句柄。
```

---

## 9. SkillPackage 数据模型

### 9.1 SkillPackage

```python
from pydantic import BaseModel
from typing import Any

class SkillPackage(BaseModel):
    skill_id: str
    name: str
    version: str | None = None
    type: str = "prompt_skill_package"
    category: str | None = None
    entry_file: str = "SKILL.md"
    base_dir: str
    base_prompt: str
    metadata: dict[str, Any] = {}
    references: list["SkillReference"] = []
    recommended_tools: list[str] = []
    assumptions: list[str] = []
```

---

### 9.2 SkillReference

```python
class SkillReference(BaseModel):
    id: str
    path: str
    title: str
    summary: str | None = None
    required: bool = False
    tags: list[str] = []
    trigger_keywords: list[str] = []
    tools: list[str] = []
```

---

### 9.3 LoadedSkillReference

运行时记录已加载 reference：

```python
class LoadedSkillReference(BaseModel):
    path: str
    title: str | None = None
    loaded_at: str
    loaded_by: str = "executor"
    content_hash: str | None = None
```

---

## 10. SkillPackageLoader 设计

### 10.1 职责

SkillPackageLoader 负责：

```text
1. 接收 zip 或目录路径。
2. 安全解压 zip。
3. 查找 SKILL.md。
4. 读取 skill.metadata.json。
5. 解析 SKILL.md frontmatter。
6. 扫描 references/ 目录。
7. 构建 SkillPackage 对象。
8. 生成 reference index。
```

---

### 10.2 接口

```python
class SkillPackageLoader:
    def load_from_zip(self, zip_path: str) -> SkillPackage:
        pass

    def load_from_dir(self, dir_path: str) -> SkillPackage:
        pass

    def build_reference_index(self, base_dir: str) -> list[SkillReference]:
        pass
```

---

### 10.3 加载流程

```text
上传 zip
  ↓
保存到 uploads/skills/
  ↓
创建临时解压目录
  ↓
安全解压
  ↓
定位 skill root
  ↓
校验 SKILL.md 是否存在
  ↓
读取 metadata
  ↓
扫描 references/
  ↓
生成 SkillPackage
  ↓
保存到 Skill Repository
```

---

## 11. ZIP 安全解压

必须防止 Zip Slip 攻击。

不允许 zip 中包含：

```text
../
/absolute/path
符号链接
超大文件
过深目录
非法扩展名
```

推荐安全解压伪代码：

```python
from pathlib import Path
import zipfile

ALLOWED_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml"
}

def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    dest_dir = dest_dir.resolve()

    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            member_name = member.filename

            if member_name.endswith("/"):
                continue

            target = (dest_dir / member_name).resolve()

            if not str(target).startswith(str(dest_dir)):
                raise ValueError(f"Unsafe zip path: {member_name}")

            if target.suffix.lower() not in ALLOWED_EXTENSIONS:
                raise ValueError(f"Unsupported file type: {target.suffix}")

            if member.file_size > 2 * 1024 * 1024:
                raise ValueError(f"File too large: {member_name}")

            target.parent.mkdir(parents=True, exist_ok=True)

            with zf.open(member) as src, open(target, "wb") as dst:
                dst.write(src.read())
```

MVP 可设置限制：

```text
单个 Skill Package zip 不超过 20 MB
单个文本文件不超过 2 MB
references 文件数量不超过 100
目录深度不超过 8
```

---

## 12. load_skill_reference 工具

### 12.1 工具定位

对于 Skill Package，Executor 不应一次性加载全部 references。系统应提供一个内部工具：

```text
load_skill_reference
```

它不是 MCP GIS 工具，而是 GeoSkillBench 提供的 Skill 内部参考文档读取工具。

Executor 可以像调用工具一样调用它：

```text
load_skill_reference("references/01_缓冲区分析.md")
```

---

### 12.2 工具职责

```text
1. 读取当前 Skill Package 内的指定 reference 文件。
2. 返回 reference 内容。
3. 记录该 reference 已加载。
4. 防止读取 Skill Package 外部文件。
5. 防止读取非允许类型文件。
```

---

### 12.3 接口设计

```python
class SkillReferenceTool:
    def __init__(self, skill_package: SkillPackage, recorder: ExecutionRecorder):
        self.skill_package = skill_package
        self.recorder = recorder

    async def load_skill_reference(self, path: str) -> str:
        pass
```

---

### 12.4 路径安全校验

```python
def safe_resolve_reference(base_dir: Path, relative_path: str) -> Path:
    base_dir = base_dir.resolve()
    target = (base_dir / relative_path).resolve()

    if not str(target).startswith(str(base_dir)):
        raise ValueError("Invalid reference path")

    if target.suffix.lower() not in {".md", ".txt", ".json", ".yml", ".yaml"}:
        raise ValueError("Unsupported reference file type")

    if not target.exists():
        raise FileNotFoundError(f"Reference not found: {relative_path}")

    return target
```

---

### 12.5 记录已加载 reference

每次调用 `load_skill_reference` 后，ExecutionRecorder 应记录：

```json
{
  "type": "skill_reference_loaded",
  "skill_id": "gis-vector-analysis",
  "path": "references/01_缓冲区分析.md",
  "loaded_at": "2026-05-22T10:00:00Z"
}
```

---

## 13. Executor 如何使用 Skill Package

### 13.1 Session 创建阶段

当被测 Skill 是 Skill Package 时，Executor 创建 session 时应接收：

```text
1. SKILL.md 内容
2. Skill metadata
3. reference index
4. load_skill_reference 工具
5. MCP Tools
6. TestContext
```

但初始 prompt 中只注入：

```text
1. SKILL.md 内容
2. reference index 摘要
3. 按需加载规则
```

不应默认注入全部 references。

---

### 13.2 Executor 系统提示词补充

推荐加入：

```text
你当前加载的是一个 Skill Package。

你已经获得入口文件 SKILL.md 和 reference index。
如果 SKILL.md 或 reference index 表明某个子任务需要阅读对应 reference，
你必须先调用 load_skill_reference 读取该 reference，再调用 GIS MCP 工具。

不得跳过必须读取的 reference。
不得凭记忆猜测未读取 reference 的内容。
不得读取当前 Skill Package 之外的文件。
```

---

### 13.3 可用工具分组

Executor 的可用工具应分组：

```text
Skill Internal Tools:
  - load_skill_reference

GIS MCP Tools:
  - common.searchDatasetsSemantic
  - vectoranalyst.createBuffer
  - vectoranalyst.overlay
  - datatools.exportGeoJson
```

这样记录时也应区分：

```text
tool_type = skill_internal
tool_type = mcp
```

---

## 14. TestContext 增强

TestContext 应增加 Skill Package 信息：

```json
{
  "skill": {
    "id": "gis-vector-analysis",
    "type": "prompt_skill_package",
    "version": "1.0.0",
    "entry_file": "SKILL.md",
    "references": [
      {
        "path": "references/S0_执行计划.md",
        "title": "执行计划"
      },
      {
        "path": "references/01_缓冲区分析.md",
        "title": "缓冲区分析"
      }
    ],
    "assumptions": [
      "系统数据均使用投影坐标系，单位为米"
    ]
  }
}
```

---

## 15. Scenario YAML 增强

Scenario 中可指定 Skill Package。

### 15.1 使用已上传 Skill Package

```yaml
target:
  skill_id: gis-vector-analysis
  skill_type: prompt_skill_package
```

---

### 15.2 使用本地 Skill Package 路径

```yaml
skill:
  load_mode: package
  path: ./skills/gis-vector-analysis
  entry: SKILL.md
  lazy_load_references: true
```

---

### 15.3 使用 zip 文件

```yaml
skill:
  load_mode: package_zip
  path: ./skills/gis-vector-analysis.zip
  entry: SKILL.md
  lazy_load_references: true
```

---

## 16. 新增断言类型

为了测试按需加载是否正确，需要增加 reference 相关断言。

### 16.1 skill_reference_loaded

判断某个 reference 是否被加载。

```yaml
- type: skill_reference_loaded
  path: references/01_缓冲区分析.md
```

---

### 16.2 skill_reference_not_loaded

判断某个无关 reference 没有被加载。

```yaml
- type: skill_reference_not_loaded
  path: references/05_擦除分析.md
```

这个断言用于测试按需加载是否精确，避免 Agent 把所有文档都加载进上下文。

---

### 16.3 skill_reference_loaded_before_tool

判断某个 reference 是否在某个 MCP 工具调用前加载。

```yaml
- type: skill_reference_loaded_before_tool
  reference: references/01_缓冲区分析.md
  tool: vectoranalyst.createBuffer
```

这个断言非常重要，用于验证：

```text
Agent 先读缓冲区分析参考，再调用缓冲区工具。
```

---

### 16.4 skill_reference_load_count_less_than

判断 reference 加载数量是否低于阈值。

```yaml
- type: skill_reference_load_count_less_than
  value: 6
```

用于防止 Agent 一次性加载所有 references，失去按需加载意义。

---

## 17. 断言实现思路

ExecutionRecorder 中新增：

```python
class ExecutionRecorder:
    loaded_skill_references: list[LoadedSkillReference]
    tool_calls: list[ToolCallRecord]
```

`skill_reference_loaded`：

```python
def assert_skill_reference_loaded(recorder, path):
    return any(ref.path == path for ref in recorder.loaded_skill_references)
```

`skill_reference_loaded_before_tool`：

```python
def assert_skill_reference_loaded_before_tool(recorder, reference, tool):
    ref_time = find_reference_loaded_time(reference)
    tool_time = find_first_tool_call_time(tool)
    return ref_time is not None and tool_time is not None and ref_time < tool_time
```

---

## 18. 示例 Scenario

```yaml
id: vector_buffer_school_500m_001
name: 使用矢量分析 Skill Package 完成学校 500 米缓冲区分析
version: 1.0.0
type: agent_skill_package_test

target:
  skill_id: gis-vector-analysis
  skill_type: prompt_skill_package

skill:
  load_mode: package
  path: ./skills/gis-vector-analysis
  entry: SKILL.md
  lazy_load_references: true

runtime:
  executor: langgraph
  agent_model: qwen3.5-32b
  actor_model: qwen3.5-14b
  judge_model: qwen3.5-32b
  max_turns: 6
  timeout_seconds: 180

data:
  fixtures:
    - id: schools
      name: 学校点数据
      type: vector
      format: geojson
      path: ./fixtures/schools.geojson
      import_as: dataset
      register_metadata: true
      cleanup: true

mcp:
  servers:
    - id: common
      transport: sse
      url: http://localhost:8000/gpamcp/common/sse
      required: true

    - id: vectoranalyst
      transport: sse
      url: http://localhost:8000/gpamcp/vector/sse
      required: true

  tools:
    required:
      - server: common
        name: searchDatasetsSemantic

      - server: vectoranalyst
        name: createBuffer

    optional:
      - server: datatools
        name: exportGeoJson

user_task: >
  请帮我生成学校周边 500 米的服务范围。

actor:
  enabled: true
  profile: normal_user
  max_turns: 5
  goal: >
    如果智能体询问使用哪个数据，请回答使用 schools 数据。
    如果智能体询问缓冲距离，请回答 500 米。
    如果智能体询问输出格式，请回答 GeoJSON。

assertions:
  - type: skill_loaded
    skill_id: gis-vector-analysis

  - type: skill_reference_loaded
    path: references/S0_执行计划.md

  - type: skill_reference_loaded
    path: references/00_数据确认.md

  - type: skill_reference_loaded
    path: references/01_缓冲区分析.md

  - type: skill_reference_loaded
    path: references/10_结果展示.md

  - type: skill_reference_loaded_before_tool
    reference: references/01_缓冲区分析.md
    tool: vectoranalyst.createBuffer

  - type: tool_called
    tool: vectoranalyst.createBuffer

  - type: tool_argument_equals
    tool: vectoranalyst.createBuffer
    argument: bufferDistance
    value: 500

  - type: result_dataset_exists
    alias: buffer_result

judge:
  enabled: true
  rubric:
    - 是否正确识别为缓冲区分析任务
    - 是否按需加载了执行计划、数据确认、缓冲区分析和结果展示参考文档
    - 是否在调用 createBuffer 前读取了缓冲区分析参考文档
    - 是否正确使用 schools 数据
    - 是否正确调用 vectoranalyst.createBuffer
    - 最终回答是否包含结果数据句柄和简要说明
```

---

## 19. 前端支持设计

### 19.1 Skill 上传区增强

上传 zip 后展示：

```text
Skill ID
Name
Version
Type
Entry File
References Count
Recommended Tools
Assumptions
```

---

### 19.2 Skill Package Tree

前端应展示目录树：

```text
gis-vector-analysis/
├── SKILL.md
├── skill.metadata.json
└── references/
    ├── S0_执行计划.md
    ├── 00_数据确认.md
    ├── 01_缓冲区分析.md
    └── ...
```

用户点击文件可预览内容。

---

### 19.3 Reference Index 面板

展示：

```text
Reference Path
Title
Tags
Recommended Tools
Required
Loaded in Run
```

测试运行后，可高亮已加载 reference：

```text
✅ references/S0_执行计划.md
✅ references/01_缓冲区分析.md
⚪ references/05_擦除分析.md
```

---

### 19.4 测试详情页增强

Tool Call Timeline 中同时展示 reference 加载事件：

```text
[00:01] load_skill_reference(references/S0_执行计划.md)
[00:02] load_skill_reference(references/00_数据确认.md)
[00:03] common.searchDatasetsSemantic(...)
[00:04] load_skill_reference(references/01_缓冲区分析.md)
[00:06] vectoranalyst.createBuffer(...)
[00:08] load_skill_reference(references/10_结果展示.md)
[00:10] final_response
```

---

## 20. API 增强

### 20.1 上传 Skill Package

```http
POST /api/skills/upload-package
Content-Type: multipart/form-data
```

返回：

```json
{
  "skill_id": "gis-vector-analysis",
  "name": "矢量空间分析技能",
  "version": "1.0.0",
  "type": "prompt_skill_package",
  "entry": "SKILL.md",
  "references_count": 13,
  "references": [
    {
      "path": "references/S0_执行计划.md",
      "title": "执行计划"
    },
    {
      "path": "references/01_缓冲区分析.md",
      "title": "缓冲区分析"
    }
  ],
  "recommended_tools": [
    "vectoranalyst.createBuffer"
  ],
  "validation_errors": []
}
```

---

### 20.2 获取 Skill Package 详情

```http
GET /api/skills/{skill_id}
```

返回：

```json
{
  "skill_id": "gis-vector-analysis",
  "type": "prompt_skill_package",
  "entry": "SKILL.md",
  "references": [],
  "metadata": {}
}
```

---

### 20.3 获取 Skill 文件内容

```http
GET /api/skills/{skill_id}/files?path=references/01_缓冲区分析.md
```

注意：

```text
后端必须做路径安全校验。
```

---

### 20.4 获取测试中加载的 references

```http
GET /api/test-runs/{run_id}/iterations/{iteration_id}/skill-references
```

返回：

```json
{
  "loaded_references": [
    {
      "path": "references/S0_执行计划.md",
      "loaded_at": "2026-05-22T10:00:00Z"
    },
    {
      "path": "references/01_缓冲区分析.md",
      "loaded_at": "2026-05-22T10:00:05Z"
    }
  ]
}
```

---

## 21. 数据表增强

### 21.1 skills 表增加字段

```text
type
entry_file
metadata_json
base_path
```

---

### 21.2 skill_references 表

```text
id
skill_id
path
title
summary
tags_json
tools_json
required
content_hash
created_at
```

---

### 21.3 iteration_skill_reference_events 表

```text
id
iteration_id
skill_id
reference_path
loaded_at
loaded_by
content_hash
```

---

## 22. 记录和报告增强

测试报告中增加：

```text
Loaded Skill References
```

示例：

```markdown
## Loaded Skill References

| Path | Loaded At | Before Tool |
|---|---|---|
| references/S0_执行计划.md | 00:01 | - |
| references/00_数据确认.md | 00:02 | common.searchDatasetsSemantic |
| references/01_缓冲区分析.md | 00:04 | vectoranalyst.createBuffer |
| references/10_结果展示.md | 00:08 | final_response |
```

统计指标增加：

```text
Reference Load Count
Required Reference Load Rate
Unnecessary Reference Load Count
Reference Before Tool Compliance
```

---

## 23. 与 Executor 补充文档的关系

如果系统已经采用 Executor 抽象：

```text
Executor
  ├── LangGraphExecutor
  ├── NanobotExecutor
  └── AgentXExecutor
```

则 Skill Package 支持应放在 Executor 之前的公共层：

```text
SkillPackageLoader
  ↓
SkillReferenceTool
  ↓
Executor
```

也就是说：

```text
Skill Package 的加载、索引、路径安全、reference 记录由 GeoSkillBench 负责；
Executor 只调用 load_skill_reference 工具读取内容。
```

这样无论 Executor 是 LangGraph 还是 nanobot，都可以使用相同的 Skill Package 机制。

---

## 24. 实现优先级

### 24.1 第一阶段：Skill Package 加载

实现：

```text
1. 上传 zip
2. 安全解压
3. 识别 SKILL.md
4. 读取 skill.metadata.json
5. 扫描 references/
6. 构建 SkillPackage 对象
7. 前端展示目录树
```

---

### 24.2 第二阶段：按需加载工具

实现：

```text
1. load_skill_reference 工具
2. 路径安全校验
3. reference 加载事件记录
4. Executor prompt 增加按需加载规则
```

---

### 24.3 第三阶段：断言与报告

实现：

```text
1. skill_reference_loaded
2. skill_reference_not_loaded
3. skill_reference_loaded_before_tool
4. skill_reference_load_count_less_than
5. 报告展示 Loaded References
```

---

### 24.4 第四阶段：高级能力

实现：

```text
1. 根据用户任务自动推荐 references
2. Reference embedding 检索
3. Reference 加载最小化分析
4. Skill Package 版本对比
5. 自动检查 references 与 recommended_tools 是否一致
```

---

## 25. MVP 建议

MVP 中，Skill Package 支持可以先做到：

```text
1. 上传 zip。
2. 解压并识别 SKILL.md。
3. 扫描 references/。
4. Executor 初始只加载 SKILL.md。
5. 提供 load_skill_reference 工具。
6. 记录已加载 references。
7. 支持 skill_reference_loaded 断言。
8. 前端展示 Skill Package 文件树和已加载 references。
```

暂缓：

```text
1. 自动 reference 检索。
2. reference embedding。
3. sophisticated reference routing。
4. reference 版本 diff。
```

---

## 26. 总结

GeoSkillBench 应正式支持两类 Skill：

```text
Single-file Skill：
  简单提示词技能，适合小任务。

Skill Package：
  包含 SKILL.md、metadata 和 references 的复杂技能包，适合 GIS 矢量分析这类多步骤、多工具、多子任务场景。
```

对于 Skill Package，核心机制是：

```text
SKILL.md 作为入口
references/ 存放详细规则
Executor 通过 load_skill_reference 按需读取
Recorder 记录 reference 加载事件
Assertion 校验 reference 是否正确加载
Judge 评价是否遵循 Skill Package
```

这种设计可以让 GIS Agent Skill 从单一大 Prompt 升级为：

```text
模块化、可按需加载、可测试、可复用、可演进的技能包。
```
