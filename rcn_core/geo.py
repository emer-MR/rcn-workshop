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


def gpkg_axis_swap(
    gpkg_path,
    src_epsg: int,
    ref_bbox: Optional[tuple[float, float, float, float]],
    sample: int = 50,
    margin: float = 0.5,
) -> bool:
    """Czy GPKG trzyma współrzędne jako (northing, easting) zamiast (x, y)?

    Część plików EGIB deklaruje EPSG:2180, ale zapisuje osie w kolejności
    urzędowej. GDAL/pyogrio czytają je w kolejności tradycyjnej GIS, więc cała
    warstwa ląduje kilkaset kilometrów obok (powiat poznański: ~200 km na wschód,
    pod Łódź). Pułapka polega na tym, że przesunięty punkt WCIĄŻ wypada w Polsce,
    więc sprawdzanie "czy w granicach kraju" (`detect_gpkg_epsg`) tego nie łapie.

    Rozstrzygamy odniesieniem: `ref_bbox` (min_lon, min_lat, max_lon, max_lat) to
    zasięg danych, do których warstwa ma pasować -- w praktyce bbox transakcji
    workspace'u. Zamieniamy TYLKO gdy wersja z zamianą trafia w to odniesienie
    częściej. Brak odniesienia albo remis = kolejność zadeklarowana w pliku
    (poprawne GPKG działają jak dotąd).

    Zgłoszenie 2026-08-06 (powiat poznański). Wspólne dla konsumenta (app.layers)
    i producenta (rcn_producer.enrich) -- jedno miejsce, jedna semantyka.
    """
    if not ref_bbox:
        return False
    try:
        from pyogrio.raw import read as _read
        from shapely import wkb as shapely_wkb
    except Exception:
        return False

    min_lon, min_lat, max_lon, max_lat = ref_bbox

    def inside(lon: float, lat: float) -> bool:
        return (min_lon - margin <= lon <= max_lon + margin
                and min_lat - margin <= lat <= max_lat + margin)

    try:
        _m, _f, geoms, _d = _read(str(gpkg_path), max_features=sample, read_geometry=True)
    except Exception:
        return False

    transformer = _transformer(src_epsg)
    hits_plain = hits_swapped = 0
    for wkb_bytes in geoms:
        if wkb_bytes is None:
            continue
        try:
            point = shapely_wkb.loads(bytes(wkb_bytes)).representative_point()
            if inside(*transformer.transform(point.x, point.y)):
                hits_plain += 1
            if inside(*transformer.transform(point.y, point.x)):
                hits_swapped += 1
        except Exception:
            continue
    return hits_swapped > hits_plain


def teryt_gminy_from_dzialka(identyfikator_dzialki: Optional[str]) -> Optional[str]:
    """Extract 7-char gmina TERYT code from a plot identifier like `106104_9.0019.211/7`."""
    if not identyfikator_dzialki:
        return None
    head = identyfikator_dzialki.split(".", 1)[0]
    if "_" in head:
        parts = head.split("_", 1)
        return f"{parts[0]}_{parts[1]}" if parts[0].isdigit() else head
    return head if head else None
