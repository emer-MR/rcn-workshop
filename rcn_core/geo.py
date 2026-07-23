"""Geometry helpers: WKT parsing, centroid + reprojection to EPSG:4326.

The parser emits WKT in the source EPSG (2176-2180 for Polish RCN).
For the web map we need centroids in EPSG:4326.  A single module caches the
transformer per source EPSG so we don't re-create pyproj objects per row.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pyproj import CRS, Transformer
from shapely import wkt as shapely_wkt
from shapely.errors import GEOSException
from shapely.geometry.base import BaseGeometry

TARGET_EPSG = 4326


@lru_cache(maxsize=32)
def _transformer(source_epsg: int) -> Transformer:
    return Transformer.from_crs(
        CRS.from_epsg(source_epsg),
        CRS.from_epsg(TARGET_EPSG),
        always_xy=True,
    )


def parse_wkt(wkt: str) -> Optional[BaseGeometry]:
    if not wkt:
        return None
    try:
        return shapely_wkt.loads(wkt)
    except (GEOSException, ValueError, TypeError):
        return None


def centroid_4326(wkt: str, source_epsg: int) -> Optional[tuple[float, float]]:
    geom = parse_wkt(wkt)
    if geom is None or geom.is_empty:
        return None
    try:
        c = geom.centroid
        lon, lat = _transformer(source_epsg).transform(c.x, c.y)
    except Exception:
        return None
    if lon != lon or lat != lat:
        return None
    return (round(lon, 7), round(lat, 7))


def epsg_int(epsg_str: str) -> Optional[int]:
    if not epsg_str:
        return None
    parts = epsg_str.upper().replace(":", " ").split()
    for part in reversed(parts):
        if part.isdigit():
            return int(part)
    return None


def teryt_gminy_from_dzialka(identyfikator_dzialki: Optional[str]) -> Optional[str]:
    """Extract 7-char gmina TERYT code from a plot identifier like `106104_9.0019.211/7`."""
    if not identyfikator_dzialki:
        return None
    head = identyfikator_dzialki.split(".", 1)[0]
    if "_" in head:
        parts = head.split("_", 1)
        return f"{parts[0]}_{parts[1]}" if parts[0].isdigit() else head
    return head if head else None
