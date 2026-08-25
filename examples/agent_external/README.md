# agent_test mock 示例

离线复现"外部智能体黑盒评测"（`type: agent_test`）的参考示例，对应
`docs/design/01-Agent接入契约.md` 对接对象（SuperMap Workflow Studio / Agentx Server）。

> 本目录为**示例**，不在 `scenarios/` 下，不会被平台扫描，不影响正常运行。

## 内容

| 文件 | 作用 |
|---|---|
| `mock_agent_server.py` | 模拟 Workflow Studio `POST /agentx/workflowstudio/api/v1/run/{flow_id}` 的 HTTP 服务 |
| `scenario_agent_mock.yml` | SSE 流式路径（`stream=true`）的 scenario 示例 |
| `scenario_agent_mock_json.yml` | JSON 非流式路径（`stream=false`）的 scenario 示例 |

## 启动 mock server

```bash
.venv/Scripts/python.exe examples/agent_test_mock/mock_agent_server.py [port]
```

端口默认 `8901`，与示例 scenario 里 `endpoint` 写死的 `127.0.0.1:8901` 一致。

## 运行示例场景

```bash
# 前台跑（用 CLI 直跑，绕过后端进程，验证最干净）
# 注意：--output 指定独立目录，避免示例报告污染正式 reports/
.venv/Scripts/python.exe -m geoskillbench.cli run examples/agent_test_mock/scenario_agent_mock.yml --output /tmp/agent_mock_out

# 或在 Web 控制台：把该 yml 复制到 scenarios/ 后选择运行
```

## 与真实格式的对应关系

mock 模拟的是 **2026-08 实测到的真实格式**（不是早期推断的标准 SSE `data:` 前缀）：

| 路径 | mock 输出 | 真实 Workflow Studio |
|---|---|---|
| `stream=true`（SSE） | 每行一个完整 JSON | 相同：`event: token` 承载最终回答，`event: tool_event` 上报工具调用 |
| `stream=false`（JSON） | `outputs[0].outputs[0].results.message.data.text` | 相同（三层嵌套） |

`tool_event` 里 `tool_start` / `tool_end` 通过 `run_id` 配对，`tool_end` 的
`output` 是 JSON 字符串——executor（`http_agent_executor.py`）会按此结构解析为
`ToolCallRecord`。因此 `scenario_agent_mock.yml` 里可以演示 `tool_called` 断言；
而真实接口不承诺一定上报工具调用，此时 `tool_calls` 为空、工具类断言判失败
而非报错（黑盒信任边界）。

## 真实对接

有真实 Agentx Server 时，把 scenario 的 `endpoint` 换成真实地址即可，示例
本身不包含任何真实服务器地址。
