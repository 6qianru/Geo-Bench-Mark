"""临时验证用：模拟 SuperMap Workflow Studio Run Flow API（docs/Agent接入契约.md 对接对象）。

POST /agentx/workflowstudio/api/v1/run/{flow_id}
- query stream=true  -> SSE 流式（data: {"text": "..."} 分段 + data: [DONE]）
- 否则               -> JSON {"outputs": [{"text": "..."}], "session_id": "..."}

用法：.venv/Scripts/python.exe _agent_test/mock_agent_server.py [port]
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8901


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        input_value = body.get("input_value", "")
        stream = "stream=true" in self.path

        reply = (
            f"已完成{input_value or '缓冲区分析'}：对目标要素执行 500 米缓冲区分析，"
            "结果数据集句柄为 dataset://generated/buffer_result，共生成 128 个缓冲要素。"
        )

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            step = max(1, len(reply) // 5)
            for i in range(0, len(reply), step):
                part = reply[i : i + step]
                self.wfile.write(f"data: {json.dumps({'text': part}, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True  # SSE 写完即关，让客户端识别流结束
        else:
            payload = json.dumps(
                {"outputs": [{"text": reply}], "session_id": "mock-session-001"},
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
