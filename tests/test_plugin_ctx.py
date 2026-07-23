"""Testy app/plugin_ctx.py -- dostęp wtyczek do pliku POI (STRtree + EPSG:2180)."""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest
from pyproj import Transformer

_TO_2180 = Transformer.from_crs(4326, 2180, always_xy=True)

# Centrum Łodzi + POI w okolicy (lon, lat).
NEAR = (19.4550, 51.7592)
POIS = [
    ("szkola", "SP nr 1", 19.4600, 51.7600),
    ("szkola", "SP nr 2", 19.4900, 51.7700),
    ("apteka", "Apteka Centralna", 19.4540, 51.7590),
]


def _make_poi_sqlite(path: Path, pois=POIS) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE poi (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, "
        "lon REAL, lat REAL, x REAL, y REAL, osm_id INTEGER, source TEXT DEFAULT 'osm')")
    for kind, name, lon, lat in pois:
        x, y = _TO_2180.transform(lon, lat)
        conn.execute("INSERT INTO poi(kind, name, lon, lat, x, y) VALUES(?,?,?,?,?,?)",
                     (kind, name, lon, lat, x, y))
    conn.commit()
    conn.close()


def test_nearest_matches_manual(tmp_path):
    from app.plugin_ctx import PluginCtx
    poi_path = tmp_path / "Lodz.poi.sqlite"
    _make_poi_sqlite(poi_path)
    ctx = PluginCtx(poi_path)
    try:
        assert ctx.has_poi() is True
        hit = ctx.poi("szkola", NEAR)
        assert hit is not None and hit["name"] == "SP nr 1"
        # Pozycja trafionego POI (do miarki / rysowania na mapie).
        assert hit["lon"] == pytest.approx(19.4600) and hit["lat"] == pytest.approx(51.7600)
        # Ręczny rachunek w 2180.
        qx, qy = _TO_2180.transform(*NEAR)
        px, py = _TO_2180.transform(19.4600, 51.7600)
        assert hit["dist_m"] == pytest.approx(math.hypot(px - qx, py - qy), rel=1e-9)
        # Sanity: ~350 m w terenie.
        assert 200 < hit["dist_m"] < 600
    finally:
        ctx.close()


def test_has_poi_false_cases(tmp_path):
    from app.plugin_ctx import PluginCtx
    # Brak pliku.
    ctx = PluginCtx(None)
    assert ctx.has_poi() is False and ctx.poi("szkola", NEAR) is None
    ctx.close()
    ctx = PluginCtx(tmp_path / "nie-ma.poi.sqlite")
    assert ctx.has_poi() is False
    ctx.close()
    # Plik bez tabeli poi.
    bad = tmp_path / "pusty.poi.sqlite"
    sqlite3.connect(bad).close()
    ctx = PluginCtx(bad)
    assert ctx.has_poi() is False and ctx.poi("szkola", NEAR) is None
    ctx.close()


def test_empty_kind_returns_none(tmp_path):
    from app.plugin_ctx import PluginCtx
    poi_path = tmp_path / "Lodz.poi.sqlite"
    _make_poi_sqlite(poi_path)
    ctx = PluginCtx(poi_path)
    try:
        assert ctx.poi("przystanek", NEAR) is None
        assert ctx.poi("szkola", (None, None)) is None
    finally:
        ctx.close()


def test_readonly_connection(tmp_path):
    from app.plugin_ctx import PluginCtx
    poi_path = tmp_path / "Lodz.poi.sqlite"
    _make_poi_sqlite(poi_path)
    ctx = PluginCtx(poi_path)
    try:
        assert ctx.has_poi()
        with pytest.raises(sqlite3.OperationalError):
            ctx._connection().execute("INSERT INTO poi(kind, name) VALUES('x', 'y')")
    finally:
        ctx.close()


def test_tree_cache_shared_between_ctx(tmp_path):
    """Drzewa POI są cache'owane per (plik, mtime) -- drugi PluginCtx na tym
    samym pliku reużywa wpisu zamiast budować STRtree od nowa."""
    import app.plugin_ctx as pc
    poi_path = tmp_path / "Cache.poi.sqlite"
    _make_poi_sqlite(poi_path)
    pc._TREE_CACHE.clear()

    ctx1 = pc.PluginCtx(poi_path)
    try:
        assert ctx1.poi("szkola", NEAR) is not None
    finally:
        ctx1.close()
    key = (str(poi_path), poi_path.stat().st_mtime_ns)
    assert key in pc._TREE_CACHE and "szkola" in pc._TREE_CACHE[key]

    cached_entry = pc._TREE_CACHE[key]["szkola"]
    ctx2 = pc.PluginCtx(poi_path)
    try:
        assert ctx2.poi("szkola", NEAR) is not None
        assert ctx2._trees["szkola"] is cached_entry  # reuse, nie rebuild
    finally:
        ctx2.close()


def test_run_plugin_sees_poi(client, auth, tmp_data_dir):
    """End-to-end: plik POI w folderze workspace -> ctx.has_poi() w pluginie."""
    from tests.test_plugins_api import ECHO_SRC, _make_workspace_with_tx, _write_plugin
    _write_plugin(tmp_data_dir, "echo.py", ECHO_SRC)
    wid = _make_workspace_with_tx(tmp_data_dir)
    _make_poi_sqlite(tmp_data_dir / "workspaces" / wid / f"{wid}.poi.sqlite")
    r = client.post(f"/api/workspaces/{wid}/plugins/echo", auth=auth,
                    json={"id_rcn_in": ["RCN-1"]})
    assert r.status_code == 200, r.text
    assert r.json()["table"]["has_poi"] is True
