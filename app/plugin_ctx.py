"""Kontekst wykonania wtyczki -- API udostępniane jako drugi argument `run(rows, ctx)`.

Daje wtyczkom (stdlib-only) dostęp do danych przestrzennych bez zależności:
shapely/pyproj żyją TUTAJ, w kodzie aplikacji (są w buildzie konsumenta).

Dane POI: plik `<nazwa>.poi.sqlite` w korzeniu folderu workspace'a (dostarczany
przez producenta, `rcn poi`). Tabela `poi(kind, name, lon, lat, x, y, ...)`,
gdzie x/y to pre-rzutowane współrzędne EPSG:2180 (metry). Otwierany read-only
(`mode=ro`) -- to obcy plik, bez apply_schema.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import threading
from pathlib import Path

from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import Point

log = logging.getLogger(__name__)

# WGS84 -> PUWG 1992 (EPSG:2180, metry) -- tworzony raz, thread-safe przy odczycie.
_TO_2180 = Transformer.from_crs(4326, 2180, always_xy=True)

# Współdzielony cache drzew POI: (ścieżka, mtime_ns) -> {kind: entry}.
# Odczyty STRtree są thread-safe; zapis pod lockiem.
_TREE_CACHE: dict = {}
_TREE_CACHE_MAX = 8
_cache_lock = threading.Lock()


class PluginCtx:
    def __init__(self, poi_path: Path | None, params: dict | None = None) -> None:
        self._poi_path = poi_path
        # Parametry uruchomienia z body requestu (np. {"alpha": 0.1,
        # "data_wyceny": "2026-06-10"}) -- wtyczka czyta przez ctx.params.get().
        self.params: dict = dict(params or {})
        self._conn: sqlite3.Connection | None = None
        # Per kategoria: (STRtree, [(name, x, y), ...]) -- lazy, budowane przy
        # pierwszym zapytaniu o daną kategorię (koszt tylko za używane kategorie).
        self._trees: dict[str, tuple[STRtree, list[tuple]] | None] = {}

    def _connection(self) -> sqlite3.Connection | None:
        if self._conn is not None:
            return self._conn
        if self._poi_path is None or not self._poi_path.is_file():
            return None
        try:
            self._conn = sqlite3.connect(f"file:{self._poi_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            log.warning("PluginCtx: nie można otworzyć pliku POI %s: %s", self._poi_path, exc)
            return None
        return self._conn

    def has_poi(self) -> bool:
        """Czy workspace ma użyteczny plik POI (istnieje + zawiera tabelę poi)."""
        conn = self._connection()
        if conn is None:
            return False
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='poi'"
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def _tree_for(self, kind: str) -> tuple[STRtree, list[tuple]] | None:
        if kind in self._trees:
            return self._trees[kind]
        # Cache procesu per (plik, mtime): PluginCtx żyje jeden request, a budowa
        # STRtree dla dużego powiatu jest kosztowna -- kolejne uruchomienia wtyczki
        # na tym samym pliku POI dostają gotowe drzewa. mtime unieważnia po podmianie.
        cache_key = self._cache_key()
        if cache_key is not None:
            with _cache_lock:
                shared = _TREE_CACHE.get(cache_key)
                if shared is not None and kind in shared:
                    self._trees[kind] = shared[kind]
                    return shared[kind]
        conn = self._connection()
        entry = None
        if conn is not None:
            try:
                pts = conn.execute(
                    "SELECT name, x, y, lon, lat FROM poi "
                    "WHERE kind=? AND x IS NOT NULL AND y IS NOT NULL",
                    (kind,),
                ).fetchall()
            except sqlite3.Error as exc:
                log.warning("PluginCtx: błąd odczytu POI kind=%s: %s", kind, exc)
                pts = []
            if pts:
                entry = (STRtree([Point(p[1], p[2]) for p in pts]), pts)
        self._trees[kind] = entry
        if cache_key is not None:
            with _cache_lock:
                if len(_TREE_CACHE) >= _TREE_CACHE_MAX and cache_key not in _TREE_CACHE:
                    _TREE_CACHE.clear()  # prosty reset zamiast LRU -- wystarcza
                _TREE_CACHE.setdefault(cache_key, {})[kind] = entry
        return entry

    def _cache_key(self) -> tuple | None:
        if self._poi_path is None:
            return None
        try:
            return (str(self._poi_path), self._poi_path.stat().st_mtime_ns)
        except OSError:
            return None

    def poi(self, kind: str, near: tuple[float, float]) -> dict | None:
        """Najbliższy POI danej kategorii: `{"name", "dist_m", "lon", "lat"}`
        albo None (brak pliku / kategorii / współrzędnych). `near` = (lon, lat)
        WGS84; lon/lat w wyniku = położenie trafionego POI (np. do rysowania
        linii odległości na mapie)."""
        entry = self._tree_for(kind)
        if entry is None or near is None:
            return None
        lon, lat = near
        if lon is None or lat is None:
            return None
        tree, pts = entry
        x, y = _TO_2180.transform(lon, lat)
        idx = int(tree.query_nearest(Point(x, y))[0])
        name, px, py, plon, plat = pts[idx]
        return {"name": name, "dist_m": math.hypot(px - x, py - y),
                "lon": plon, "lat": plat}

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._trees.clear()
