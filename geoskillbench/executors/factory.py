from __future__ import annotations

from geoskillbench.executors.base import Executor
from geoskillbench.executors.external_driven_executor import ExternalDrivenExecutor
from geoskillbench.executors.http_agent_executor import HttpAgentExecutor
from geoskillbench.executors.nanobot_executor import NanobotExecutor
from geoskillbench.executors.orchestrator_executor import OrchestratorExecutor
from geoskillbench.executors.skill_executor import SkillExecutor
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter


class ExecutorFactory:
    @staticmethod
    def create(executor_name: str, adapter: MCPToolAdapter) -> Executor:
        normalized = (executor_name or "skill").lower()
        if normalized in ("skill", "langgraph"):  # langgraph 为历史别名，存量 scenario 兼容
            return SkillExecutor(adapter)
        if normalized == "nanobot":
            return NanobotExecutor(adapter)
        if normalized == "http_agent":
            return HttpAgentExecutor(adapter)
        if normalized == "orchestrator":
            return OrchestratorExecutor(adapter)
        if normalized == "external_driven":
            return ExternalDrivenExecutor(adapter)
        raise ValueError(f"Unsupported executor: {executor_name}")
