"""EGIB overlay layers (działki + budynki) for the Leaflet map.

Three data sources, selected automatically in this order:

1. **RCN GML geometries** (per-workspace) — polygons of plots / buildings that
   were part of transactions. Served from `plots.wkt` / `buildings.wkt` in the
   workspace SQLite, reprojected on-the-fly to EPSG:4326.
2. **Local EGIB GPKG** — cached files produced by project 22 (EGIB Downloader),
   placed under `data/layers/{teryt}/dzialki.gpkg` or `.../budynki.gpkg`.
   Native EPSG is usually 2180; we filter by bbox using GPKG's spatial index.
3. **Remote GUGiK WFS** — backend proxy to national EGIB WFS, avoids CORS.
   Slower, requires a reasonable bbox size (cap at ~1 km² to stay well-behaved).
"""
from __future__ import annotations

import logging
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pyproj import CRS, Transformer
from shapely import wkt as shapely_wkt
from shapely.geometry import box, mapping
from shapely.ops import transform as shapely_transform

from app.auth import require_auth
from app.config import settings
from app.workspaces import _require_workspace, _workspace_db, _workspace_layers_dir, assert_workspace_idle
from rcn_core.ingest import open_workspace

router = APIRouter(prefix="/api/layers", tags=["layers"])

log = logging.getLogger(__name__)


def _layers_dir() -> Path:
    return settings.data_dir / "layers"


@lru_cache(maxsize=32)
def _transformer(src_epsg: int, dst_epsg: int) -> Transformer:
    return Transformer.from_crs(CRS.from_epsg(src_epsg), CRS.from_epsg(dst_epsg), always_xy=True)


def _reproject_geom(geom, src_epsg: int, dst_epsg: int = 4326):
    if src_epsg == dst_epsg:
        return geom
    tr = _transformer(src_epsg, dst_epsg)
    return shapely_transform(lambda x, y, z=None: tr.transform(x, y), geom)


def _parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    try:
        parts = [float(x) for x in bbox.split(",")]
        if len(parts) != 4:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "bbox must be 4 comma-separated floats: min_lon,min_lat,max_lon,max_lat") from exc
    return tuple(parts)  # type: ignore[return-value]


@router.get("/info")
def layer_info(_: str = Depends(require_auth)) -> dict:
    """List locally cached EGIB layer files."""
    d = _layers_dir()
    entries: list[dict] = []
    if d.exists():
        for sub in sorted(d.iterdir()):
            if not sub.is_dir():
                continue
            for gpkg in sorted(sub.glob("*.gpkg")):
                entries.append({
                    "teryt": sub.name,
                    "layer": gpkg.stem,
                    "file": str(gpkg.relative_to(d)),
                    "size_mb": round(gpkg.stat().st_size / 1024 / 1024, 1),
                })
    return {"layers_dir": str(d), "entries": entries}


def _axis_swap_cached(gpkg_path: Path, src_epsg: int, ref_bbox) -> bool:
    """`rcn_core.geo.gpkg_axis_swap` z cache per (plik, mtime, epsg, odniesienie).

    Detekcja czyta próbkę z dysku, a warstwy serwujemy przy każdym przesunięciu
    mapy -- bez cache płacilibyśmy za to na każdym kafelku.
    """
    import os

    from rcn_core.geo import gpkg_axis_swap
    try:
        mtime = os.stat(gpkg_path).st_mtime_ns
    except OSError:
        mtime = 0
    key = (str(gpkg_path), mtime, src_epsg, ref_bbox)
    if key not in _AXIS_SWAP_CACHE:
        if len(_AXIS_SWAP_CACHE) > 64:
            _AXIS_SWAP_CACHE.clear()
        _AXIS_SWAP_CACHE[key] = gpkg_axis_swap(gpkg_path, src_epsg, ref_bbox)
    return _AXIS_SWAP_CACHE[key]


_AXIS_SWAP_CACHE: dict[tuple, bool] = {}


def _read_gpkg_features_pyogrio(
    gpkg_path: Path,
    bbox_4326: tuple[float, float, float, float],
    limit: int,
    src_epsg_default: int = 2180,
    ref_bbox: tuple[float, float, float, float] | None = None,
) -> tuple[list[dict], bool, int]:
    """Czyta GPKG, bbox-filtruje przez pyogrio (native CRS), reprojectuje
    geometry do 4326. Zwraca (features_native_shape, capped, src_epsg).

    Pyogrio (NIE Fiona) -- używa system libgdal+libproj zamiast statycznie
    zlinkowanej PROJ. Eliminuje SIGSEGV libproj-fiona vs pyproj/SpatiaLite
    w uvicorn worker (zaobserwowane w produkcji 2026-05-01 przy
    request /custom/{slug}.geojson dla Sieradza -- 502 z Traefika +
    restart kontenera).

    features_native_shape: list[{feat_id, geometry (geojson dict),
    properties (dict)}].
    """
    import pyogrio
    from pyogrio.raw import read as _pyogrio_read_raw
    from shapely import wkb as shapely_wkb
    from shapely.geometry import mapping as shapely_mapping

    info = pyogrio.read_info(str(gpkg_path))
    src_crs = info.get("crs")
    src_epsg = src_epsg_default
    if src_crs:
        # pyogrio zwraca CRS jako WKT lub authority code (np. "EPSG:4326").
        # `CRS.from_user_input` lyka oba; `CRS.from_wkt` rzuca dla auth code
        # i kod cicho fallbackowal na 2180 -- skutek: pliki z crs="EPSG:4326"
        # czytane jako 2180 daly 0 features (bbox 4326 → "reproject" 4326→2180
        # gave nonsense, GPKG spatial index returned nothing).
        try:
            src_epsg = int(CRS.from_user_input(src_crs).to_epsg() or src_epsg_default)
        except Exception:
            pass

    # Odporność na błędne metadane CRS (EGIB): jeśli zadeklarowany układ nie daje
    # współrzędnych w Polsce, wykryj faktyczny po próbce (PUWG 1992/2000/WGS84).
    from app.gpkg_discovery import detect_gpkg_epsg
    src_epsg = detect_gpkg_epsg(gpkg_path, src_epsg) or src_epsg

    # Kolejność osi: część GPKG-ów EGIB trzyma (northing, easting) zamiast (x, y).
    # Bez tego warstwa rysuje się kilkaset km obok danych workspace'u (a punkt
    # wciąż wypada w Polsce, więc detect_gpkg_epsg tego nie widzi). Patrz
    # rcn_core.geo.gpkg_axis_swap -- decyzja cache'owana per plik (mtime).
    swap = _axis_swap_cached(gpkg_path, src_epsg, ref_bbox)

    # Reproject bbox 4326 -> native (żeby pyogrio mógł użyć GPKG spatial index)
    tr_to_src = _transformer(4326, src_epsg)
    min_lon, min_lat, max_lon, max_lat = bbox_4326
    min_x, min_y = tr_to_src.transform(min_lon, min_lat)
    max_x, max_y = tr_to_src.transform(max_lon, max_lat)
    if min_x > max_x:
        min_x, max_x = max_x, min_x
    if min_y > max_y:
        min_y, max_y = max_y, min_y
    if swap:
        # Filtr bbox musi być w kolejności osi PLIKU, inaczej indeks przestrzenny
        # GPKG nie zwróci nic (dokładnie to widzieliśmy: 0 features dla Poznania).
        min_x, min_y, max_x, max_y = min_y, min_x, max_y, max_x

    # +1 dla cap detection (jeśli pyogrio zwróci limit+1 features, wiemy że było więcej)
    meta, fids, geometries, field_data = _pyogrio_read_raw(
        str(gpkg_path),
        bbox=(min_x, min_y, max_x, max_y),
        max_features=limit + 1,
        return_fids=True,
        read_geometry=True,
    )

    capped = len(geometries) > limit
    if capped:
        geometries = geometries[:limit]
        field_data = [arr[:limit] for arr in field_data]
        if fids is not None:
            fids = fids[:limit]

    fields = list(meta.get("fields", []))
    tr_to_wgs = _transformer(src_epsg, 4326) if src_epsg != 4326 else None

    features: list[dict] = []
    for i, geom_wkb in enumerate(geometries):
        if geom_wkb is None:
            continue
        try:
            geom = shapely_wkb.loads(bytes(geom_wkb))
            if geom.is_empty:
                continue
            if swap:
                geom = shapely_transform(lambda x, y, z=None: (y, x), geom)
            if tr_to_wgs is not None:
                geom = shapely_transform(lambda x, y, z=None: tr_to_wgs.transform(x, y), geom)
            geom_dict = shapely_mapping(geom)
        except Exception as exc:
            log.debug("gpkg feature skip: %s", exc)
            continue
        props = {}
        for j, fname in enumerate(fields):
            v = field_data[j][i] if j < len(field_data) else None
            if v is None:
                continue
            # numpy scalar -> Python native (JSON-serializable)
            if hasattr(v, "item"):
                try:
                    v = v.item()
                except Exception:
                    pass
            if v == "":
                continue
            props[fname] = v
        feat_id = int(fids[i]) if fids is not None and i < len(fids) else i
        features.append({"feat_id": feat_id, "geometry": geom_dict, "properties": props})
    return features, capped, src_epsg


def _workspace_data_bbox(workspace_id: str):
    """Zasięg transakcji workspace'u (min_lon, min_lat, max_lon, max_lat) albo None.

    Odniesienie dla wykrywania kolejności osi w GPKG -- warstwa EGIB ma pasować
    do danych, które opisuje. Cache'owane per workspace (`_BBOX_CACHE`), bo to
    zapytanie po indeksowanych centroidach przy każdym kafelku mapy byłoby
    marnotrawstwem; klucz niesie mtime bazy, więc po imporcie liczy się od nowa.
    """
    db = _workspace_db(workspace_id)
    try:
        key = (str(db), db.stat().st_mtime_ns)
    except OSError:
        return None
    if key in _BBOX_CACHE:
        return _BBOX_CACHE[key]
    bbox = None
    try:
        conn = open_workspace(db)
        try:
            row = conn.execute(
                "SELECT MIN(centroid_lon), MIN(centroid_lat), MAX(centroid_lon), MAX(centroid_lat) "
                "FROM tx_cache WHERE centroid_lon IS NOT NULL"
            ).fetchone()
        finally:
            conn.close()
        if row and row[0] is not None:
            bbox = (row[0], row[1], row[2], row[3])
    except Exception as exc:  # baza w trakcie importu / brak tx_cache
        log.debug("bbox workspace %s niedostepny: %s", workspace_id, exc)
    if len(_BBOX_CACHE) > 32:
        _BBOX_CACHE.clear()
    _BBOX_CACHE[key] = bbox
    return bbox


_BBOX_CACHE: dict[tuple, object] = {}


@router.get("/workspaces/{workspace_id}/custom/{layer_slug}.geojson")
def custom_workspace_layer(
    workspace_id: str,
    layer_slug: str,
    bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat (EPSG:4326)"),
    limit: int = Query(5000, ge=1, le=20000),
    _: str = Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
):
    """Serwuj warstwę GPKG per-workspace.
    Rozwiązanie ścieżki: (1) auto-wykryte GPKG z korzenia folderu
    (`app.gpkg_discovery`, slug = kind: dzialki/budynki); (2) fallback legacy:
    `layers/<slug>.gpkg`."""
    from app.gpkg_discovery import discover_gpkg_layers
    from app.workspaces import _workspace_dir

    _require_workspace(workspace_id)
    # Anti-traversal: layer_slug musi być samym slugiem, bez '/' albo '..'
    if "/" in layer_slug or "\\" in layer_slug or layer_slug.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid layer slug")
    discovered = {d["slug"]: d["path"] for d in discover_gpkg_layers(_workspace_dir(workspace_id))}
    if layer_slug in discovered:
        gpkg_path = Path(discovered[layer_slug])
    else:
        gpkg_path = _workspace_layers_dir(workspace_id) / f"{layer_slug}.gpkg"
    if not gpkg_path.exists():
        raise HTTPException(status_code=404, detail=f"Layer not found: {layer_slug}")

    bbox_t = _parse_bbox(bbox)
    try:
        raw_features, capped, _src_epsg = _read_gpkg_features_pyogrio(
            gpkg_path, bbox_t, limit, ref_bbox=_workspace_data_bbox(workspace_id)
        )
    except Exception as exc:
        log.warning("Failed to read %s: %s", gpkg_path, exc)
        raise HTTPException(status_code=500, detail=f"Failed to read GPKG: {exc}")

    features = [
        {
            "type": "Feature",
            "id": f"{layer_slug}:{f['feat_id']}",
            "geometry": f["geometry"],
            "properties": _trim_props(f["properties"]),
        }
        for f in raw_features
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "source": f"custom:{layer_slug}",
        "capped": capped,
        "limit": limit,
    }


@router.get("/workspaces/{workspace_id}/poi.geojson")
def workspace_poi_layer(
    workspace_id: str,
    limit: int = Query(20000, ge=1, le=50000),
    _: str = Depends(require_auth),
):
    """Warstwa POI z pliku `<nazwa>.poi.sqlite` w folderze workspace'a
    (generowanego przez `rcn poi`, model folder=komplet). Punkty są nieliczne
    (rzędu tysięcy na powiat) -- zwracamy całość bez bbox. Dane OSM, licencja
    ODbL -- pole `attribution` MUSI trafić do UI warstwy."""
    import sqlite3

    from app.workspaces import _poi_sqlite

    _require_workspace(workspace_id)
    poi_path = _poi_sqlite(workspace_id)
    if poi_path is None:
        raise HTTPException(status_code=404, detail="Workspace nie ma pliku POI (<nazwa>.poi.sqlite)")
    try:
        conn = sqlite3.connect(f"file:{poi_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Nie można otworzyć pliku POI: {exc}")
    try:
        try:
            rows = conn.execute(
                "SELECT id, kind, name, lon, lat FROM poi "
                "WHERE lon IS NOT NULL AND lat IS NOT NULL LIMIT ?",
                (limit + 1,),
            ).fetchall()
            meta = dict(conn.execute("SELECT key, value FROM poi_meta").fetchall())
        except sqlite3.Error as exc:
            raise HTTPException(status_code=500, detail=f"Plik POI nieczytelny: {exc}")
    finally:
        conn.close()

    capped = len(rows) > limit
    features = [
        {
            "type": "Feature",
            "id": f"poi:{rid}",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"kind": kind, "name": name},
        }
        for rid, kind, name, lon, lat in rows[:limit]
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "source": "poi",
        "capped": capped,
        "attribution": meta.get("attribution",
                                "© autorzy OpenStreetMap, licencja ODbL"),
        "generated_at": meta.get("generated_at"),
    }




@router.get("/egib/{layer}.geojson")
def egib_local(
    layer: Literal["dzialki", "budynki"],
    bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat (EPSG:4326)"),
    limit: int = Query(5000, ge=1, le=20000),
    _: str = Depends(require_auth),
):
    """Read cached GPKG files, bbox-filter via GPKG spatial index, reproject to WGS84.

    Pyogrio (NIE Fiona) -- patrz `_read_gpkg_features_pyogrio` dla powodu (segfault
    libproj-fiona vs pyproj/SpatiaLite). Iteruje po wszystkich `data/layers/{teryt}/`
    z globalnym limitem (akumulacja przez subdirs)."""
    bbox_t = _parse_bbox(bbox)

    d = _layers_dir()
    if not d.exists():
        return {"type": "FeatureCollection", "features": [], "source": "local", "capped": False, "note": "no local layers"}

    features: list[dict] = []
    capped = False
    teryts_hit: list[str] = []

    for sub in sorted(d.iterdir()):
        if not sub.is_dir():
            continue
        gpkg = sub / f"{layer}.gpkg"
        if not gpkg.exists():
            continue

        remaining = limit - len(features)
        if remaining <= 0:
            capped = True
            break

        try:
            raw_features, sub_capped, _src_epsg = _read_gpkg_features_pyogrio(
                gpkg, bbox_t, remaining
            )
        except Exception as exc:
            log.warning("Failed to read %s: %s", gpkg, exc)
            continue

        count_before = len(features)
        for f in raw_features:
            features.append({
                "type": "Feature",
                "id": f"{sub.name}:{layer}:{f['feat_id']}",
                "geometry": f["geometry"],
                "properties": _trim_props(f["properties"]),
            })
        if len(features) > count_before:
            teryts_hit.append(sub.name)
        if sub_capped:
            capped = True
            break

    return {
        "type": "FeatureCollection",
        "features": features,
        "source": "local-gpkg",
        "capped": capped,
        "limit": limit,
        "teryts": teryts_hit,
    }


_ALLOWED_PROPS = {
    "teryt", "idgminy", "idpowiatu", "wojew", "teryt_gminy",
    "idjewid", "idewid", "id_dzialki", "identyfikator", "identyfikator_ewidencyjny",
    "numer", "nr_dzialki", "numer_dzialki", "obreb", "nazwa_obrebu", "nr_obrebu", "numer_obrebu",
    "numer_jednostki", "nazwa_gminy",
    "powierzchnia", "pow_ew", "pole", "pole_powierzchni", "pole_ewidencyjne",
    "sposob_uzytkowania", "uzytkowanie", "klasouzytki_egib", "grupa_rejestrowa",
    "id_budynku", "rodzaj", "funkcja_budynku", "rodzaj_budynku",
    "liczba_kondygnacji", "kondygnacje_nadziemne", "kondygnacje_podziemne",
    "etykieta",
    "zrodlo_danych", "data_modyfikacji", "data",
}


def _trim_props(props: dict) -> dict:
    """Filter GPKG properties down to whitelisted keys and normalize keys to
    lowercase so the frontend can match them regardless of GPKG provenance
    (EGIB Downloader emits UPPERCASE like ID_DZIALKI, NUMER_DZIALKI, etc.).
    """
    out = {}
    for k, v in props.items():
        if v is None or v == "":
            continue
        key_norm = k.lower()
        if key_norm in _ALLOWED_PROPS:
            out[key_norm] = v
    if out:
        return out
    return {k.lower(): v for k, v in list(props.items())[:6] if v not in (None, "")}


# --- Workspace-scoped polygon layers (from GML RCN geometries) ---------------

@router.get("/workspaces/{workspace_id}/parcels.geojson")
def workspace_parcels(
    workspace_id: str,
    bbox: str = Query(...),
    limit: int = Query(5000, ge=1, le=20000),
    _: str = Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
):
    return _workspace_polygons(workspace_id, "plots", bbox, limit)


@router.get("/workspaces/{workspace_id}/buildings.geojson")
def workspace_buildings(
    workspace_id: str,
    bbox: str = Query(...),
    limit: int = Query(5000, ge=1, le=20000),
    _: str = Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
):
    return _workspace_polygons(workspace_id, "buildings", bbox, limit)


def _workspace_polygons(workspace_id: str, table: str, bbox: str, limit: int) -> dict:
    _require_workspace(workspace_id)
    min_lon, min_lat, max_lon, max_lat = _parse_bbox(bbox)
    bbox_wgs = box(min_lon, min_lat, max_lon, max_lat)

    conn = open_workspace(_workspace_db(workspace_id))
    try:
        epsg_row = conn.execute(
            "SELECT parser_epsg FROM imports ORDER BY upload_timestamp DESC LIMIT 1"
        ).fetchone()
        from rcn_core.geo import epsg_int
        src_epsg = (epsg_int(epsg_row["parser_epsg"]) if epsg_row and epsg_row["parser_epsg"] else None) or 2180

        ident_col, pow_col = {
            "plots": ("identyfikator_dzialki", "powierzchnia_m2"),
            "buildings": ("identyfikator_budynku", "pow_uzytkowa"),
        }[table]

        # bbox po indeksowanych centroidach (idx_<tbl>_centroid), bez SpatiaLite.
        spatial_where = "AND x.centroid_lon BETWEEN ? AND ? AND x.centroid_lat BETWEEN ? AND ?"
        spatial_params = (min_lon, max_lon, min_lat, max_lat, limit + 1)
        rows = conn.execute(
            f"""SELECT x.id, x.id_rcn, x.{ident_col} AS ident, x.teryt_gminy, x.obreb,
                       x.miejscowosc, x.adres, x.{pow_col} AS pow, x.cena_brutto,
                       x.wkt, x.centroid_lon, x.centroid_lat,
                       t.rodzaj_nieruchomosci AS rodzaj
                FROM {table} AS x
                LEFT JOIN transakcje AS t ON t.id_rcn = x.id_rcn
                WHERE x.wkt IS NOT NULL
                  {spatial_where}
                LIMIT ?""",
            spatial_params,
        ).fetchall()
    finally:
        conn.close()

    features = []
    capped = len(rows) > limit
    for row in rows[:limit]:
        try:
            geom = shapely_wkt.loads(row["wkt"])
            if geom is None or geom.is_empty:
                continue
            geom_wgs = _reproject_geom(geom, src_epsg, 4326)
            if not bbox_wgs.intersects(geom_wgs):
                continue
            ident = row["ident"] or ""
            # Wyciągnij krótki numer działki/budynku z pełnego identyfikatora
            # EGIB (format `{TERYT}.{OBREB}.{NUMER}`, np. `106104_9.0029.85/3`
            # → numer `85/3`). Fallback: cały ident.
            numer = ident.rsplit(".", 1)[-1] if "." in ident else ident
            features.append({
                "type": "Feature",
                "id": f"{table}:{row['id']}",
                "geometry": mapping(geom_wgs),
                "properties": {
                    "id_rcn": row["id_rcn"],
                    "ident": ident,
                    "numer_dzialki": numer,
                    "teryt_gminy": row["teryt_gminy"],
                    "obreb": row["obreb"],
                    "miejscowosc": row["miejscowosc"],
                    "adres": row["adres"],
                    "powierzchnia": row["pow"],
                    "cena_brutto": row["cena_brutto"],
                    "rodzaj": row["rodzaj"],
                },
            })
        except Exception as exc:
            log.debug("parcels reproject skip: %s", exc)
            continue

    return {
        "type": "FeatureCollection",
        "source": f"rcn-{table}",
        "features": features,
        "capped": capped,
        "limit": limit,
    }


# --- WFS GUGiK fallback (online) --------------------------------------------

_GUGIK_WFS = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow"


@router.get("/wfs/dzialki.geojson")
async def wfs_dzialki(
    bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat (EPSG:4326)"),
    _: str = Depends(require_auth),
):
    """Proxy to GUGiK national EGIB WFS. Bbox should be small (< ~1 km²) to stay fast."""
    min_lon, min_lat, max_lon, max_lat = _parse_bbox(bbox)

    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": "ms:dzialki",
        "srsName": "EPSG:4326",
        "outputFormat": "application/json",
        "bbox": f"{min_lat},{min_lon},{max_lat},{max_lon},EPSG:4326",
        "count": "2000",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(_GUGIK_WFS, params=params)
            resp.raise_for_status()
            if "application/json" in resp.headers.get("content-type", ""):
                data = resp.json()
            else:
                raise HTTPException(502, f"GUGiK returned non-JSON response: {resp.text[:200]}")
    except httpx.RequestError as exc:
        raise HTTPException(502, f"GUGiK WFS request failed: {exc}") from exc

    data.setdefault("type", "FeatureCollection")
    data["source"] = "wfs-gugik"
    return data
