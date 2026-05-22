from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from geoskillbench.models.result import ToolCallRecord
from geoskillbench.models.scenario import MCPServerConfig, ToolRef
from geoskillbench.models.test_context import DatasetContext


@dataclass
class ToolDefinition:
    server: str
    name: str
    optional: bool = False


class MCPToolAdapter:
    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}
        self._catalog: dict[str, ToolDefinition] = {}
        self._dataset_store: dict[str, DatasetContext] = {}

    def connect_servers(self, servers: list[MCPServerConfig]) -> None:
        self._servers = {server.id: server for server in servers}

    def register_datasets(self, datasets: dict[str, DatasetContext]) -> None:
        self._dataset_store = dict(datasets)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._catalog.values())

    def validate_required_tools(self, required_tools: list[ToolRef]) -> None:
        missing = [
            f"{tool.server}:{tool.name}"
            for tool in required_tools
            if tool.name not in self._catalog or self._catalog[tool.name].server != tool.server
        ]
        if missing:
            missing_text = ", ".join(missing)
            raise ValueError(f"Required MCP tools are missing: {missing_text}")

    def get_agent_tools(self, required_tools: list[ToolRef], optional_tools: list[ToolRef]) -> dict[str, Callable]:
        self._catalog = {}
        for tool in required_tools:
            self._catalog[tool.name] = ToolDefinition(server=tool.server, name=tool.name, optional=False)
        for tool in optional_tools:
            self._catalog[tool.name] = ToolDefinition(server=tool.server, name=tool.name, optional=True)
        return {tool_name: self._build_tool_callable(tool_name) for tool_name in self._catalog}

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallRecord:
        tool = self._build_tool_callable(tool_name)
        try:
            result = tool(arguments)
            return ToolCallRecord(tool_name=tool_name, arguments=arguments, result=result, status="success")
        except Exception as exc:  # pragma: no cover - defensive path
            return ToolCallRecord(
                tool_name=tool_name,
                arguments=arguments,
                result=None,
                status="failed",
                error_message=str(exc),
            )

    def get_dataset_store(self) -> dict[str, DatasetContext]:
        return self._dataset_store

    def _build_tool_callable(self, tool_name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
        implementations = {
            "query_dataset_metadata": self._query_dataset_metadata,
            "reproject_dataset": self._reproject_dataset,
            "create_buffer": self._create_buffer,
            "publish_map": self._publish_map,
        }
        if tool_name not in implementations:
            raise ValueError(f"No implementation for tool {tool_name}")
        return implementations[tool_name]

    def _resolve_dataset(self, alias_or_handle: str) -> tuple[str, DatasetContext]:
        if alias_or_handle in self._dataset_store:
            return alias_or_handle, self._dataset_store[alias_or_handle]
        for alias, dataset in self._dataset_store.items():
            if dataset.handle == alias_or_handle:
                return alias, dataset
        raise ValueError(f"Dataset not found: {alias_or_handle}")

    def _query_dataset_metadata(self, arguments: dict[str, Any]) -> dict[str, Any]:
        alias, dataset = self._resolve_dataset(arguments["dataset"])
        return {
            "dataset": alias,
            "handle": dataset.handle,
            "geometry_type": dataset.geometry_type,
            "crs": dataset.crs,
            "feature_count": dataset.feature_count,
            "fields": dataset.fields,
        }

    def _reproject_dataset(self, arguments: dict[str, Any]) -> dict[str, Any]:
        alias, dataset = self._resolve_dataset(arguments["dataset"])
        target_crs = arguments.get("target_crs", "EPSG:3857")
        output_alias = arguments.get("output_alias", f"{alias}_reprojected")
        reprojected = dataset.model_copy(
            update={
                "handle": dataset.handle.replace(alias, output_alias),
                "crs": target_crs,
                "source_alias": alias,
            }
        )
        self._dataset_store[output_alias] = reprojected
        return {"dataset": output_alias, "handle": reprojected.handle, "crs": target_crs}

    def _create_buffer(self, arguments: dict[str, Any]) -> dict[str, Any]:
        alias, dataset = self._resolve_dataset(arguments["dataset"])
        output_alias = arguments.get("output_alias", "buffer_result")
        buffered = dataset.model_copy(
            update={
                "handle": f"dataset://generated/{output_alias}",
                "geometry_type": "Polygon",
                "source_alias": alias,
                "metadata": {
                    **dataset.metadata,
                    "buffer_distance": arguments.get("distance"),
                    "buffer_unit": arguments.get("distance_unit", "meter"),
                },
            }
        )
        self._dataset_store[output_alias] = buffered
        return {
            "dataset": output_alias,
            "handle": buffered.handle,
            "geometry_type": buffered.geometry_type,
            "crs": buffered.crs,
        }

    def _publish_map(self, arguments: dict[str, Any]) -> dict[str, Any]:
        alias, dataset = self._resolve_dataset(arguments["dataset"])
        return {
            "dataset": alias,
            "handle": dataset.handle,
            "map_url": f"https://example.local/maps/{alias}",
        }
