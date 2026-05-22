from __future__ import annotations

import json
from pathlib import Path

from geoskillbench.models.scenario import Scenario
from geoskillbench.models.test_context import DatasetContext


class FixtureManager:
    def prepare(self, scenario: Scenario) -> dict[str, DatasetContext]:
        datasets: dict[str, DatasetContext] = {}
        base_path = Path(getattr(scenario, "_base_path", "."))
        for fixture in scenario.data.fixtures:
            fixture_path = (base_path / fixture.path).resolve()
            metadata = self._read_fixture_metadata(fixture_path)
            datasets[fixture.id] = DatasetContext(
                handle=f"dataset://test/{scenario.id}/{fixture.id}",
                name=fixture.name,
                geometry_type=fixture.geometry_type or metadata["geometry_type"],
                crs=fixture.crs or metadata["crs"],
                feature_count=metadata["feature_count"],
                fields=metadata["fields"],
                path=str(fixture_path),
                semantic_desc=f"{fixture.name}，用于测试场景 {scenario.name}",
                source_alias=fixture.id,
                metadata=metadata,
            )
        return datasets

    def cleanup(self, test_context) -> None:
        return None

    def _read_fixture_metadata(self, path: Path) -> dict:
        if path.suffix.lower() == ".geojson":
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            features = data.get("features", [])
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
        raise ValueError(f"Unsupported fixture format for metadata extraction: {path}")
