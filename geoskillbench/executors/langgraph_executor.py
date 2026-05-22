from __future__ import annotations

from geoskillbench.executors.heuristic_executor import HeuristicSessionExecutor
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter


class LangGraphExecutor(HeuristicSessionExecutor):
    def __init__(self, adapter: MCPToolAdapter) -> None:
        super().__init__(adapter=adapter, executor_type="langgraph")
