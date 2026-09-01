from __future__ import annotations

import os
import re
from typing import Any

from sqlalchemy import create_engine, text

from geoskillbench.assertions.result_comparator import CompareResult
from geoskillbench.models.test_context import DatasetContext

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PostgisResultComparator:
    """在 PostGIS 内比较 result/reference，断言只保留指标，不拉几何。"""

    def __init__(self, db_url: str, schema: str = "public") -> None:
        if not db_url.strip():
            raise ValueError("evaluation database URL is empty")
        self._engine = create_engine(db_url.strip(), pool_pre_ping=True)
        self._schema = _quote_ident(schema)

    @classmethod
    def from_env(cls) -> "PostgisResultComparator | None":
        from pathlib import Path

        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        url = (os.environ.get("GEO_EVAL_DATABASE_URL") or "").strip()
        if not url.startswith("postgresql"):
            return None
        schema = (os.environ.get("GEO_EVAL_DB_SCHEMA") or "public").strip() or "public"
        return cls(url, schema=schema)

    def compare(
        self,
        result_table: str,
        reference_table: str,
        metric: str,
        **params: Any,
    ) -> CompareResult:
        result_table = _quote_ident(result_table)
        reference_table = _quote_ident(reference_table)
        if metric == "feature_count":
            return self._feature_count(result_table, params)
        if metric == "overlap_ratio":
            return self._overlap_ratio(result_table, reference_table, params)
        if metric == "area_error":
            return self._area_error(result_table, reference_table, params)
        if metric == "hausdorff_distance":
            return self._hausdorff(result_table, reference_table, params)
        return CompareResult(False, None, None, f"Unsupported in-db comparison metric: {metric}")

    def _feature_count(self, result_table: str, params: dict[str, Any]) -> CompareResult:
        expected = int(params.get("count", -1))
        actual = int(self._scalar(f"SELECT COUNT(*) FROM {self._schema}.{result_table}") or 0)
        passed = actual == expected
        return CompareResult(passed, actual, expected, f"要素数：实际 {actual} / 预期 {expected}")

    def _overlap_ratio(self, result_table: str, reference_table: str, params: dict[str, Any]) -> CompareResult:
        min_ratio = float(params.get("min", 1.0))
        result_geom = self._geometry_column(result_table)
        reference_geom = self._geometry_column(reference_table)
        row = self._first(
            f"""
            SELECT
              ST_Area(ST_Intersection(r.{result_geom}, e.{reference_geom})) AS inter_area,
              ST_Area(e.{reference_geom}) AS ref_area
            FROM {self._schema}.{result_table} r
            INNER JOIN {self._schema}.{reference_table} e
              ON r.smid = e.smid
            LIMIT 1
            """
        )
        inter_area = float(row["inter_area"] or 0) if row else 0.0
        ref_area = float(row["ref_area"] or 0) if row else 0.0
        if ref_area == 0:
            return CompareResult(False, None, f">= {min_ratio}", "Reference has no geometry to compare against")
        ratio = min(1.0, inter_area / ref_area)
        passed = ratio >= min_ratio
        return CompareResult(passed, round(ratio, 4), f">= {min_ratio}", f"重叠率：实际 {ratio:.4f} / 预期 >= {min_ratio}")

    def _area_error(self, result_table: str, reference_table: str, params: dict[str, Any]) -> CompareResult:
        max_ratio = float(params.get("max_ratio", 0.05))
        result_geom = self._geometry_column(result_table)
        reference_geom = self._geometry_column(reference_table)
        row = self._first(
            f"""
            SELECT
              ABS(ST_Area(r.{result_geom}) - ST_Area(e.{reference_geom}))
              / NULLIF(ST_Area(e.{reference_geom}), 0) AS area_error
            FROM {self._schema}.{result_table} r
            INNER JOIN {self._schema}.{reference_table} e
              ON r.smid = e.smid
            LIMIT 1
            """
        )
        error = float(row["area_error"] or 0) if row else 0.0
        passed = error <= max_ratio
        return CompareResult(
            passed,
            round(error, 6),
            f"<= {max_ratio}",
            f"面积相对误差：实际 {error:.4%} / 预期 <= {max_ratio:.0%}",
        )

    def _hausdorff(self, result_table: str, reference_table: str, params: dict[str, Any]) -> CompareResult:
        max_meters = float(params.get("max_meters", 20))
        result_geom = self._geometry_column(result_table)
        reference_geom = self._geometry_column(reference_table)
        row = self._first(
            f"""
            SELECT ST_HausdorffDistance(r.{result_geom}, e.{reference_geom}) AS hausdorff
            FROM {self._schema}.{result_table} r
            INNER JOIN {self._schema}.{reference_table} e
              ON r.smid = e.smid
            LIMIT 1
            """
        )
        distance = float(row["hausdorff"] or 0) if row else 0.0
        passed = distance <= max_meters
        return CompareResult(
            passed,
            round(distance, 2),
            f"<= {max_meters} m",
            f"Hausdorff 偏移：实际 {distance:.2f} m / 预期 <= {max_meters} m",
        )

    def _geometry_column(self, table: str) -> str:
        row = self._first(
            """
            SELECT f_geometry_column
            FROM geometry_columns
            WHERE f_table_schema = :schema AND f_table_name = :table
            LIMIT 1
            """,
            {"schema": self._schema.strip('"'), "table": table.strip('"')},
        )
        if row and row.get("f_geometry_column"):
            return _quote_ident(str(row["f_geometry_column"]))
        return _quote_ident("smgeometry")

    def _scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        row = self._first(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    def _first(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(text(sql), params or {}).mappings().first()
            return dict(row) if row is not None else None


def dataset_sql_name(dataset: DatasetContext, location: dict[str, str] | None = None) -> str | None:
    if location:
        table = location.get("tableName") or location.get("bufferResult")
        if table:
            return table
    logical_id = (dataset.metadata or {}).get("logical_id")
    return str(logical_id) if logical_id else None


def _quote_ident(value: str) -> str:
    if not _IDENT.match(value):
        raise ValueError(f"unsafe SQL identifier: {value}")
    return f'"{value}"'
