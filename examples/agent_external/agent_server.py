"""示例：模拟 SuperMap Workflow Studio Run Flow API（docs/Agent接入契约.md 对接对象）。

这是"可离线复现真实联调"的 mock server：它模拟的是 2026-08 实测到的
真实 Workflow Studio 格式，而不是早期推断的标准 SSE 格式：

- stream=true（SSE）：每行一个完整 JSON，不是标准 SSE 的 `data:` 前缀。
  - `{"event": "token", "data": {"chunk": "..."}}`        → 最终回答 = 各 chunk 按序拼接
  - `{"event": "tool_event", "data": {...}}`              → 工具调用上报（tool_start/tool_end 按 run_id 配对）
  - `{"event": "add_message", "data": {"sender": "AI", "text": "..."}}` → AI 文本兜底
- stream=false（JSON）：最终回答在 outputs[0].outputs[0].results.message.data.text（三层嵌套）。

用途：
- 不依赖真实 Agentx Server（192.168.13.130:8490）离线验证 agent_test 全流程；
- 作为"外部智能体上报工具调用"的参考实现，帮助理解 tool_event 结构。

用法：
    .venv/Scripts/python.exe examples/agent_test_mock/mock_agent_server.py [port]

端口默认 8901，对应 scenarios 示例里 endpoint 写死的 127.0.0.1:8901。
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8901

# 工具调用的入参/出参（模拟"缓冲分析"工具一次完整的调用日志）
TOOL_START = {
    "name": "buffer_analysis",
    "event_type": "tool_start",
    "run_id": "run-buffer-001",
    "input": {"target": "道路要素", "distance": 500},
}
TOOL_END = {
    "name": "buffer_analysis",
    "event_type": "tool_end",
    "run_id": "run-buffer-001",
    "output": json.dumps(  # 真实接口的 output 是 JSON 字符串，executor 会解析为 dict
        {"dataset": "dataset://generated/buffer_result", "count": 128}, ensure_ascii=False
    ),
}


def build_reply(input_value: str) -> str:
    return (
        f"已完成{input_value or '缓冲区分析'}：对目标要素执行 500 米缓冲区分析，"
        "结果数据集句柄为 dataset://generated/buffer_result，共生成 128 个缓冲要素。"
    )


# 反问模式（external_driven 场景 mock）：按 session 维护多轮状态——
# 收到缺信息的任务先反问（数据集/距离），再反问（输出格式），信息齐了才完成。
# 请求文本同时含 "schools" 与 "500"（模拟用户已补齐信息）→ 直接完成。
def build_askback_reply(input_value: str, session_key: str) -> str:
    if "schools" in input_value.lower() and "500" in input_value:
        return (
            "已完成：对 schools 数据执行 500 米缓冲区分析，"
            "结果数据集 dataset://generated/buffer_result，共 128 个要素。"
        )
    if "格式" in input_value or "geojson" in input_value.lower() or "geotiff" in input_value.lower():
        return (
            "已完成：将按您指定的格式输出。已执行 500 米缓冲区分析，"
            "结果数据集 dataset://generated/buffer_result，共 128 个要素。"
        )
    if "500" in input_value or "米" in input_value:
        # 已拿到距离，还缺输出格式 → 反问格式
        return "收到，缓冲距离用 500 米。还需要确认一下输出格式，您希望输出什么格式？"
    if "schools" in input_value.lower():
        # 已拿到数据集，还缺距离 → 反问距离
        return "好的，使用 schools 数据。请问缓冲距离设为多少米？"
    # 首轮：缺数据集与距离 → 反问
    return "好的，请问需要处理哪个数据集？缓冲距离设为多少米？"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        input_value = body.get("input_value", "")
        stream = "stream=true" in self.path
        session_key = str(body.get("session_id") or input_value)
        # 触发反问模式的场景（endpoint 含 mock-askback）：按会话状态返回反问/完成；
        # 其余场景保持原静态行为（直接完成），零回归。
        reply = (
            build_askback_reply(input_value, session_key)
            if "mock-askback" in self.path
            else build_reply(input_value)
        )

        if stream:
            self._stream_reply(reply)
        else:
            self._json_reply(reply)

    def _stream_reply(self, reply: str) -> None:
        """模拟真实 SSE：每行一个完整 JSON 帧。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        # 1) 工具调用日志（tool_start + tool_end 按 run_id 配对）
        for frame in (
            {"event": "tool_event", "data": TOOL_START},
            {"event": "tool_event", "data": TOOL_END},
        ):
            self.wfile.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))

        # 2) 最终回答按 token 分块（真实接口是流式 token，chunk 逐块吐出）
        step = max(1, len(reply) // 5)
        for i in range(0, len(reply), step):
            frame = {"event": "token", "data": {"chunk": reply[i : i + step]}}
            self.wfile.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))

        # 3) add_message 兜底（AI 完整回答；executor 仅在无 token 流时采用，避免重复）
        fallback = {"event": "add_message", "data": {"sender": "AI", "sender_name": "agent", "text": reply}}
        self.wfile.write((json.dumps(fallback, ensure_ascii=False) + "\n").encode("utf-8"))

        self.wfile.flush()
        self.close_connection = True  # 写完即关，让客户端识别流结束

    def _json_reply(self, reply: str) -> None:
        """模拟真实非流式 JSON：回答嵌在 outputs 三层嵌套里。"""
        payload = json.dumps(
            {
                "outputs": [
                    {
                        "outputs": [
                            {
                                "results": {
                                    "message": {
                                        "data": {"text": reply},
                                        "sender": "AI",
                                    }
                                }
                            }
                        ]
                    }
                ],
                "session_id": "mock-session-001",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()

    def log_message(self, *args) -> None:  # 静默日志
        pass


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"mock agent server listening on 127.0.0.1:{PORT}")
    server.serve_forever()
