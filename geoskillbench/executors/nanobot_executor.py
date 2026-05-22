from __future__ import annotations

import importlib.util

from geoskillbench.executors.heuristic_executor import HeuristicSessionExecutor
from geoskillbench.mcp.mcp_tool_adapter import MCPToolAdapter


class NanobotExecutor(HeuristicSessionExecutor):
    def __init__(self, adapter: MCPToolAdapter) -> None:
        note = None
        if importlib.util.find_spec("nanobot") is None:
            note = (
                "Nanobot runtime package not found. Running in compatibility mode with the shared heuristic executor "
                "while preserving the session-based NanobotExecutor contract."
            )
        super().__init__(adapter=adapter, executor_type="nanobot", compatibility_note=note)
