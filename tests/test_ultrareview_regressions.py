"""Testy czystej (bez SpatiaLite) ścieżki przestrzennej -- schema v8.

Zastępują dawne testy `populate_geom`/`geom` (Bug 1 z /ultrareview 2026-04-29):
po usunięciu SpatiaLite kolumna `geom` i `populate_geom` już nie istnieją, a
zapytania przestrzenne liczone są shapely (bbox/wielokąt) + pyproj.Geod (dystans).
Te testy pilnują, że pure-path daje poprawne wyniki (w tym dokładny wielokąt
i dokładny okrąg), bez natywnego rozszerzenia.

Bug 3 (find_workspace_layers per-resource fallback) -- sprawdzony review kodu +
manual sanity na VPS; pełny test wymaga zapisu GPKG (osobny tor).
"""
from __future__ import annotations

from rcn_core.ingest import open_workspace


def _seed(conn, rows):
    """rows: lista (id_rcn, lon, lat). Tworzy import + transakcje + plots."""
    conn.execute(
        "INSERT INTO imports (id, original_filename, stored_filename, file_size_bytes, "
        "tryb, upload_timestamp, status, stage, progress_pct) "
        "VALUES (1,'x','x',0,'delta',0,'success','done',100)"
    )
    for id_rcn, lon, lat in rows:
        conn.execute(
            "INSERT INTO transakcje (id_rcn, source_import_id, import_timestamp, liczba_obiektow) "
            "VALUES (?,1,0,0)",
            (id_rcn,),
        )
        conn.execute(
            "INSERT INTO plots (id_rcn, source_import_id, centroid_lon, centroid_lat) "
            "VALUES (?,1,?,?)",
            (id_rcn, lon, lat),
        )
    conn.commit()


def test_bbox_filter_uses_centroid(tmp_path):
    """Filtr bbox po indeksowanych centroidach -- punkt poza prostokątem odrzucony."""
    from app.query import BBoxFilter, _spatial_filter_ids

    conn = open_workspace(tmp_path / "bbox.sqlite")
    try:
        _seed(conn, [("IN", 19.45, 51.75), ("OUT", 20.50, 52.50)])
        ids = _spatial_filter_ids(conn, BBoxFilter(bbox=[19.40, 51.70, 19.50, 51.80]))
        assert ids == {"IN"}
    finally:
        conn.close()


def test_polygon_filter_precise_covers(tmp_path):
    """Wielokąt wklęsły (kształt L): punkt leżący w bboxie, ale POZA wielokątem
    musi zostać odrzucony -- dowód, że to dokładny shapely covers, nie sam bbox."""
    from app.query import PolygonFilter, _spatial_filter_ids

    conn = open_workspace(tmp_path / "poly.sqlite")
    try:
        # L: dolny pas y∈[0,0.3] (x∈[0,1]) + lewy pas x∈[0,0.3] (y∈[0,1]).
        _seed(conn, [("INSIDE", 0.1, 0.1), ("BBOX_ONLY", 0.9, 0.9)])
        coords = [[[0, 0], [1, 0], [1, 0.3], [0.3, 0.3], [0.3, 1], [0, 1], [0, 0]]]
        ids = _spatial_filter_ids(conn, PolygonFilter(coordinates=coords))
        assert "INSIDE" in ids
        assert "BBOX_ONLY" not in ids  # w bboxie [0,1]² ale poza L
    finally:
        conn.close()


def test_geod_distance_exact_circle():
    """pyproj.Geod (sąsiedzi) daje dokładny dystans w metrach -- punkt tuż za
    promieniem 100 m jest odrzucony, tuż przed -- zaakceptowany."""
    from app.transactions import _GEOD

    lon, lat = 19.45, 51.75
    near_lat = lat + 90 / 111_000.0   # ~90 m na północ
    far_lat = lat + 130 / 111_000.0   # ~130 m na północ
    _, _, d_near = _GEOD.inv(lon, lat, lon, near_lat)
    _, _, d_far = _GEOD.inv(lon, lat, lon, far_lat)
    assert d_near <= 100, f"~90 m powinno być <=100 m, jest {d_near:.1f}"
    assert d_far > 100, f"~130 m powinno być >100 m, jest {d_far:.1f}"
