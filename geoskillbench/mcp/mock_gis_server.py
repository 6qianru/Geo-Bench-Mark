"""本地 mock GIS MCP server（迭代 3 验证用）。

把 4 个 GIS 工具（query_dataset_metadata / reproject_dataset / create_buffer / publish_map）
包成一个本地 MCP server 进程，供 MCP 客户端（adapter）通过 stdio 协议发现并调用。

设计（与迭代 3 计划 §4.1 对齐）：
- 用 FastMCP 装饰器声明工具，客户端 tools/list 自动发现、schema 自动生成。
- 数据对接：server 无状态、不持有数据集；客户端启动时通过环境变量注入
  ``GEO_MCP_DATASETS``（JSON：alias -> {path, crs, geometry_type, feature_count, fields}）。
  server 按 alias 读本地文件计算真实几何。
- 几何逻辑与 ``MCPToolAdapter`` 内的实现保持等价（UTM 带投影做米级缓冲）。

当前为最小验证实现（不落盘结果到客户端目录，直接返回结果 dict + 结果临时文件路径）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import geopandas as gpd
from fastmcp import FastMCP


def _load_datasets() -> dict[str, dict[str, Any]]:
    """读取客户端注入的数据集映射（alias -> 元数据+文件路径）。"""
    raw = os.environ.get("GEO_MCP_DATASETS", "")
    if not raw:
        return {}
    return json.loads(raw)


DATASETS: dict[str, dict[str, Any]] = _load_datasets()


def _resolve(alias_or_handle: str) -> tuple[str, dict[str, Any]]:
    if alias_or_handle in DATASETS:
        return alias_or_handle, DATASETS[alias_or_handle]
    for alias, dataset in DATASETS.items():
        if dataset.get("handle") == alias_or_handle:
            return alias, dataset
    raise ValueError(f"Dataset not found: {alias_or_handle}")


def _load_gdf(dataset: dict[str, Any]) -> gpd.GeoDataFrame:
    path = dataset.get("path")
    if not path:
        raise ValueError(f"Dataset has no local geometry: {dataset.get('handle') or dataset.get('name')}")
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    return gdf


@staticmethod
def _utm_epsg(lon: float, lat: float) -> int:
    """按经纬度选所在 UTM 带：北半球 326xx / 南半球 327xx。"""
    zone = max(1, min(60, int((lon + 180) // 6) + 1))
    return 32600 + zone if lat >= 0 else 32700 + zone


def _write_result(gdf: gpd.GeoDataFrame, output_alias: str) -> str:
    """把结果 GeoDataFrame 写临时 geojson，返回路径（客户端可落盘归档）。"""
    tmp_dir = Path(tempfile.mkdtemp(prefix="mock_gis_result_"))
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in output_alias)
    path = tmp_dir / f"{safe}.geojson"
    gdf.to_file(path, driver="GeoJSON")
    return str(path)


def _register_generated(
    alias: str,
    *,
    handle: str | None,
    path: str,
    crs: str | None,
    feature_count: int | None,
    geometry_type: str | None,
) -> None:
    """把派生数据集登记进 server 端 DATASETS，后续工具可按 alias 解析（复刻旧 adapter 行为）。

    server 端进程内状态：派生数据集在客户端与 server 端都要可见，否则 create_buffer(schools_metric) 找不到。
    """
    DATASETS[alias] = {
        "handle": handle,
        "name": alias,
        "geometry_type": geometry_type,
        "crs": crs,
        "feature_count": feature_count,
        "fields": [],
        "path": path,
    }


mcp = FastMCP("mock-gis")


@mcp.tool()
def query_dataset_metadata(dataset: str) -> dict[str, Any]:
    """Query metadata (handle, geometry type, CRS, feature count, fields) for a dataset alias or handle."""
    alias, data = _resolve(dataset)
    return {
        "dataset": alias,
        "handle": data.get("handle"),
        "geometry_type": data.get("geometry_type"),
        "crs": data.get("crs"),
        "feature_count": data.get("feature_count"),
        "fields": data.get("fields"),
    }


@mcp.tool()
def reproject_dataset(dataset: str, target_crs: str = "EPSG:3857", output_alias: str = "") -> dict[str, Any]:
    """Reproject a dataset to a target CRS. Returns the reprojected dataset handle/path/crs."""
    alias, data = _resolve(dataset)
    output_alias = output_alias or f"{alias}_reprojected"
    gdf = _load_gdf(data).to_crs(target_crs)
    path = _write_result(gdf, output_alias)
    _register_generated(
        output_alias,
        handle=data.get("handle"),
        path=path,
        crs=target_crs,
        feature_count=len(gdf),
        geometry_type=data.get("geometry_type"),
    )
    return {
        "dataset": output_alias,
        "handle": data.get("handle"),
        "path": path,
        "crs": target_crs,
        "feature_count": len(gdf),
    }


@mcp.tool()
def create_buffer(dataset: str, distance: float, distance_unit: str = "meter", output_alias: str = "buffer_result") -> dict[str, Any]:
    """Create a metric buffer around a dataset. Reprojects to UTM for meter-accurate buffering, then back to source CRS."""
    alias, data = _resolve(dataset)
    distance = float(distance)
    source = _load_gdf(data)
    source_crs = source.crs
    geographic = source.to_crs("EPSG:4326")
    minx, miny, maxx, maxy = geographic.total_bounds
    center_lon, center_lat = (minx + maxx) / 2, (miny + maxy) / 2
    projected = source.to_crs(_utm_epsg(center_lon, center_lat))
    buffered = projected.copy()
    buffered["geometry"] = projected.geometry.buffer(distance)
    result = buffered.to_crs(source_crs)
    path = _write_result(result, output_alias)
    first_geometry = next((g for g in result.geometry if g is not None and not g.is_empty), None)
    geometry_type = first_geometry.geom_type if first_geometry is not None else data.get("geometry_type")
    crs = source_crs.to_string() if source_crs is not None else "EPSG:4326"
    _register_generated(
        output_alias,
        handle=f"dataset://generated/{output_alias}",
        path=path,
        crs=crs,
        feature_count=len(result),
        geometry_type=geometry_type,
    )
    return {
        "dataset": output_alias,
        "handle": f"dataset://generated/{output_alias}",
        "path": path,
        "geometry_type": geometry_type,
        "feature_count": len(result),
        "crs": crs,
    }


@mcp.tool()
def publish_map(dataset: str) -> dict[str, Any]:
    """Publish a dataset as a map and return the map URL."""
    alias, data = _resolve(dataset)
    return {"dataset": alias, "handle": data.get("handle"), "map_url": f"https://example.local/maps/{alias}"}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="mock GIS MCP server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse", "http"], help="MCP transport")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    kwargs: dict[str, Any] = {"transport": args.transport}
    if args.transport != "stdio":
        kwargs.update(host=args.host, port=args.port, allowed_origins=["*"])
    mcp.run(**kwargs)
