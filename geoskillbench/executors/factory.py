from __future__ import annotations

from geoskillbench.executors.base import Executor
from geoskillbench.executors.http_agent_executor import HttpAgentExecutor
from geoskillbench.executors.langgraph_executor import LangGraphExecutor
from geoskillbench.executors.nanobot_executor import NanobotExecutor
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter


class ExecutorFactory:
    @staticmethod
    def create(executor_name: str, adapter: MCPToolAdapter) -> Executor:
        normalized = (executor_name or "langgraph").lower()
        if normalized == "langgraph":
            return LangGraphExecutor(adapter)
        if normalized == "nanobot":
            return NanobotExecutor(adapter)
        if normalized == "http_agent":
            return HttpAgentExecutor(adapter)
        raise ValueError(f"Unsupported executor: {executor_name}")
