"""生成缓冲区分析的结果参考 fixture，供 result_* 断言比对。

独立于 mock 工具产出"正确结果"（ground truth）：
- 按中心点选 UTM 带做米制缓冲，再写回源 CRS（与 mock create_buffer 投影策略一致，
  保证正确调用时重合度高；参考仍建议用真实 GIS/PostGIS 独立生成更佳）。
用法：python scripts/generate_reference_buffer.py
产出：fixtures/expected_buffer_school_500m.geojson（默认）
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parent.parent


def utm_epsg(lon: float, lat: float) -> int:
    zone = max(1, min(60, int((lon + 180) // 6) + 1))
    return 32600 + zone if lat >= 0 else 32700 + zone


def main() -> None:
    parser = argparse.ArgumentParser(description="生成缓冲区参考 fixture")
    parser.add_argument("--input", default=str(ROOT / "fixtures/schools.geojson"))
    parser.add_argument("--output", default=str(ROOT / "fixtures/expected_buffer_school_500m.geojson"))
    parser.add_argument("--distance", type=float, default=500)
    parser.add_argument("--crs", default="EPSG:4326")
    args = parser.parse_args()

    source = gpd.read_file(args.input)
    if source.crs is None:
        source = source.set_crs("EPSG:4326")
    geographic = source.to_crs("EPSG:4326")
    minx, miny, maxx, maxy = geographic.total_bounds
    lon, lat = (minx + maxx) / 2, (miny + maxy) / 2

    projected = source.to_crs(utm_epsg(lon, lat))
    buffered = projected.copy()
    buffered["geometry"] = projected.geometry.buffer(args.distance)
    result = buffered.to_crs(args.crs)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_file(out, driver="GeoJSON")
    area = result.to_crs(utm_epsg(lon, lat)).area.sum()
    print(f"written {out}")
    print(f"features={len(result)} | crs={result.crs} | fields={result.columns.drop('geometry').tolist()} | area_m2={area:.1f}")


if __name__ == "__main__":
    main()
