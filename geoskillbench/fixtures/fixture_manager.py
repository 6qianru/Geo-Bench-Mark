from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse
from urllib.parse import urlparse

import geopandas as gpd
from sqlalchemy import create_engine, text

from geoskillbench.models.scenario import FixtureConfig, Scenario
from geoskillbench.models.test_context import DatasetContext


class FixtureManager:
    def __init__(self) -> None:
        # 数据库拉取临时文件的落盘目录（runner 每次 run 前设置，cleanup 时删除）
        self._work_dir: Path | None = None

    def set_work_dir(self, path: str | Path | None) -> None:
        self._work_dir = Path(path) if path else None

    def prepare(self, scenario: Scenario) -> tuple[dict[str, DatasetContext], dict[str, DatasetContext]]:
        """准备数据集，返回 (datasets, reference_datasets)。

        - datasets：data.fixtures（输入数据），供 agent 使用（提示词/工具可解析）。
        - reference_datasets：data.reference（参考数据/ground truth），仅断言引擎比对时读取，
          不注册进 adapter、不进 agent 可见集合，避免标准答案泄露。
        """
        datasets: dict[str, DatasetContext] = {}
        reference_datasets: dict[str, DatasetContext] = {}
        base_path = Path(getattr(scenario, "_base_path", "."))
        for fixture in scenario.data.fixtures:
            datasets[fixture.id] = self._prepare_fixture(fixture, scenario, base_path)
        for fixture in scenario.data.reference:
            reference_datasets[fixture.id] = self._prepare_fixture(fixture, scenario, base_path)
        return datasets, reference_datasets

    def _prepare_fixture(self, fixture: FixtureConfig, scenario: Scenario, base_path: Path) -> DatasetContext:
        if fixture.format == "db_table":
            return self._prepare_db_table(fixture, scenario)
        fixture_path = (base_path / fixture.path).resolve()
        metadata = self._read_fixture_metadata(fixture_path)
        return DatasetContext(
            handle=f"dataset://test/{scenario.id}/{fixture.id}",
            name=fixture.name or fixture.id,
            geometry_type=fixture.geometry_type or metadata["geometry_type"],
            crs=fixture.crs or metadata["crs"],
            feature_count=metadata["feature_count"],
            fields=metadata["fields"],
            path=str(fixture_path),
            semantic_desc=f"{fixture.name or fixture.id}，用于测试场景 {scenario.name}",
            source_alias=fixture.id,
            metadata=metadata,
        )

    def cleanup(self, test_context) -> None:
        """删除从数据库拉下来的临时文件（DB 数据不进报告产物）。"""
        if self._work_dir is not None and self._work_dir.exists():
            shutil.rmtree(self._work_dir, ignore_errors=True)
            self._work_dir = None

    def _read_fixture_metadata(self, path: Path) -> dict:
        suffix = path.suffix.lower()
        if suffix in (".geojson", ".json"):
            # .json 也可能是纯 JSON 数据（非 FeatureCollection），按 GeoJSON 解析失败时回退空元数据
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and data.get("features") is not None:
                features = data["features"]
                first_geometry_type = None
                fields: list[str] = []
                if features:
                    first_geometry_type = features[0].get("geometry", {}).get("type")
                    fields = sorted(features[0].get("properties", {}).keys())
                crs = "EPSG:4326"
                crs_info = data.get("crs", {}).get("properties", {}).get("name")
                if crs_info:
                    crs = crs_info
                return {
                    "feature_count": len(features),
                    "geometry_type": first_geometry_type,
                    "fields": fields,
                    "crs": crs,
                }
            return {"feature_count": 0, "geometry_type": None, "fields": [], "crs": "EPSG:4326"}
        if suffix in (".shp", ".gpkg"):
            # 其它矢量格式用 geopandas 自动识别（shapefile/gpkg）
            gdf = gpd.read_file(path)
            first_geometry = next((g for g in gdf.geometry if g is not None and not g.is_empty), None)
            return {
                "feature_count": len(gdf),
                "geometry_type": first_geometry.geom_type if first_geometry is not None else None,
                "fields": sorted([c for c in gdf.columns if c != gdf.geometry.name]),
                "crs": gdf.crs.to_string() if gdf.crs is not None else "EPSG:4326",
            }
        raise ValueError(f"Unsupported fixture format for metadata extraction: {path}")

    # ---------- 数据库数据源（format: db_table，档 1：拉取到本地临时文件比对） ----------

    def _prepare_db_table(self, fixture: FixtureConfig, scenario: Scenario) -> DatasetContext:
        db_url = fixture.db_url or (os.environ.get("DATABASE_URL") or "").strip()
        if not db_url:
            raise ValueError(f"fixture {fixture.id}: format=db_table 需要 db_url 或 DATABASE_URL 环境变量")
        table = fixture.table
        schema = fixture.db_schema or "public"
        engine = create_engine(db_url)
        try:
            with engine.connect() as conn:
                geom_col, srid = self._find_geometry_column(conn, schema, table)
                # read_postgis 复用同一个连接，with 结束自动归还/关闭，避免连接泄漏
                gdf = gpd.read_postgis(
                    f'SELECT * FROM "{schema}"."{table}"',
                    conn,
                    geom_col=geom_col,
                )
            if gdf.crs is None:
                gdf = gdf.set_crs(f"EPSG:{srid}")
            if fixture.crs:
                gdf = gdf.to_crs(fixture.crs)
        finally:
            engine.dispose()

        path = self._write_db_pull(fixture.id, gdf)
        first_geometry = next((g for g in gdf.geometry if g is not None and not g.is_empty), None)
        return DatasetContext(
            handle=f"dataset://db/{schema}.{table}",
            name=fixture.name or fixture.id,
            geometry_type=fixture.geometry_type or (first_geometry.geom_type if first_geometry is not None else None),
            crs=gdf.crs.to_string() if gdf.crs is not None else (fixture.crs or "EPSG:4326"),
            feature_count=len(gdf),
            fields=sorted([c for c in gdf.columns if c != gdf.geometry.name]),
            path=str(path),
            semantic_desc=f"{fixture.name or fixture.id}（来自数据库表 {schema}.{table}），用于测试场景 {scenario.name}",
            source_alias=fixture.id,
            metadata={
                "source": "db_table",
                "table": table,
                "schema": schema,
                "db_host": urlparse(db_url).hostname,
                "db_database": urlparse(db_url).path.lstrip("/"),
            },
        )

    @staticmethod
    def _find_geometry_column(conn, schema: str, table: str) -> tuple[str, int]:
        row = conn.execute(
            text("SELECT f_geometry_column, srid FROM geometry_columns WHERE f_table_schema = :s AND f_table_name = :t"),
            {"s": schema, "t": table},
        ).first()
        if row is None:
            raise ValueError(f"表 {schema}.{table} 未在 PostGIS geometry_columns 中注册（不是空间表？）")
        return row[0], int(row[1])

    def _write_db_pull(self, fixture_id: str, gdf: gpd.GeoDataFrame) -> Path:
        if self._work_dir is None:
            self._work_dir = Path("reports") / "outputs" / "_db_pull"
        self._work_dir.mkdir(parents=True, exist_ok=True)
        path = self._work_dir / f"{fixture_id}.geojson"
        gdf.to_file(path, driver="GeoJSON")
        return path
