"""Transaction details endpoint.

Returns plots/buildings/locals belonging to a single transakcja, with both the
dedicated SQLite columns (ident, obreb, powierzchnia, ...) and the full
`attributes_json` payload parsed from the plugin's GML export. The UI uses
this to expand a row in-place without requesting the whole transakcja list.
"""
from __future__ import annotations

import json
import math
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from pyproj import Geod

from app.auth import require_auth
from app.prywatnosc import bez_notariusza, ukrywac_notariusza
from app.workspaces import _require_workspace, _workspace_db, assert_workspace_idle
from rcn_core.ingest import open_workspace

# Dokładny dystans geodezyjny (elipsoida WGS84) -- parytet z dawnym
# ST_Distance(..., use_spheroid=1) ze SpatiaLite, bez natywnego rozszerzenia.
_GEOD = Geod(ellps="WGS84")

router = APIRouter(prefix="/api/workspaces", tags=["transactions"])


def _rows(conn: sqlite3.Connection, sql: str, params: tuple) -> list[dict]:
    out: list[dict] = []
    for row in conn.execute(sql, params).fetchall():
        d = dict(row)
        attrs_raw = d.pop("attributes_json", None)
        try:
            d["extra"] = json.loads(attrs_raw) if attrs_raw else {}
        except Exception:
            d["extra"] = {}
        out.append(d)
    return out


@router.get("/{workspace_id}/transactions/details/{id_rcn:path}")
def transaction_details(
    workspace_id: str,
    id_rcn: str,
    ctx=Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
) -> dict:
    _require_workspace(workspace_id)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        tx = conn.execute(
            "SELECT id_rcn, data_transakcji, cena_transakcji_brutto, kwota_vat, "
            "rodzaj_transakcji, rodzaj_rynku, rodzaj_nieruchomosci, dokument, "
            "tworca_dokumentu, strona_sprzedajaca, strona_kupujaca, liczba_obiektow, "
            "data_quality_flags, attributes_json FROM transakcje WHERE id_rcn = ?",
            (id_rcn,),
        ).fetchone()
        if tx is None:
            raise HTTPException(404, f"Transakcja {id_rcn} nie istnieje w tym workspace")

        tx_dict = dict(tx)
        tx_raw = tx_dict.pop("attributes_json", None)
        try:
            tx_dict["extra"] = json.loads(tx_raw) if tx_raw else {}
        except Exception:
            tx_dict["extra"] = {}
        # data_quality_flags: parse JSON -> list[str] dla UI badge.
        flags_raw = tx_dict.get("data_quality_flags")
        try:
            tx_dict["data_quality_flags"] = json.loads(flags_raw) if flags_raw else []
        except Exception:
            tx_dict["data_quality_flags"] = []

        plots = _rows(
            conn,
            "SELECT id, identyfikator_dzialki AS ident, teryt_gminy, obreb, "
            "miejscowosc, adres, powierzchnia_m2, cena_brutto, kwota_vat, "
            "centroid_lon, centroid_lat, geom_source, attributes_json "
            "FROM plots WHERE id_rcn = ? ORDER BY id",
            (id_rcn,),
        )
        buildings = _rows(
            conn,
            "SELECT id, identyfikator_budynku AS ident, teryt_gminy, obreb, "
            "miejscowosc, adres, rodzaj_budynku, pow_uzytkowa, cena_brutto, kwota_vat, "
            "centroid_lon, centroid_lat, geom_source, attributes_json "
            "FROM buildings WHERE id_rcn = ? ORDER BY id",
            (id_rcn,),
        )
        locals_ = _rows(
            conn,
            "SELECT id, identyfikator_lokalu AS ident, teryt_gminy, miejscowosc, "
            "adres, funkcja, pow_uzytkowa, liczba_izb, kondygnacja, cena_brutto, kwota_vat, "
            "centroid_lon, centroid_lat, geom_source, attributes_json "
            "FROM locals WHERE id_rcn = ? ORDER BY id",
            (id_rcn,),
        )
    finally:
        conn.close()

    # Nazwisko notariusza tylko dla admina -- czyścimy i kolumnę, i jej kopię
    # w surowych atrybutach GML. Numer repertorium (`dokument`) zostaje.
    if ukrywac_notariusza(ctx):
        tx_dict = bez_notariusza(tx_dict)
        tx_dict["extra"] = bez_notariusza(tx_dict.get("extra") or {})

    return {
        "transakcja": tx_dict,
        "plots": plots,
        "buildings": buildings,
        "locals": locals_,
    }


@router.get("/{workspace_id}/transactions/neighbors/{id_rcn:path}")
def transaction_neighbors(
    workspace_id: str,
    id_rcn: str,
    radius_m: int = 100,
    _: str = Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
) -> dict:
    """Zwraca id_rcn transakcji w promieniu `radius_m` od centroidu focused tx.

    Bbox prefilter po indeksowanych `centroid_lon/lat` (kwadrat ~2×promień),
    a następnie dokładny okrąg liczony geodezyjnie `pyproj.Geod.inv` (metry).
    Daje dokładny okrąg bez SpatiaLite.

    radius_m: 25..2000 (clamp). Default 100 -- typowy zakres porównawczy
    rzeczoznawcy w gęstej zabudowie miejskiej.
    """
    _require_workspace(workspace_id)
    radius_m = max(25, min(2000, int(radius_m)))

    conn = open_workspace(_workspace_db(workspace_id))
    try:
        focused = conn.execute(
            "SELECT centroid_lon, centroid_lat FROM tx_cache WHERE id_rcn = ?",
            (id_rcn,),
        ).fetchone()
        if not focused or focused["centroid_lon"] is None or focused["centroid_lat"] is None:
            return {
                "neighbor_ids": [],
                "focused_lat": None, "focused_lon": None,
                "radius_m": radius_m,
                "spatial": False,
                "error": "Focused transaction has no centroid",
            }
        lon = focused["centroid_lon"]
        lat = focused["centroid_lat"]

        # bbox dla R-tree prefilter (radius_m -> stopnie wokół focused).
        # 1° lat ~= 111 km, 1° lon = 111 km × cos(lat) -- przy lat=51.5 ~69 km.
        delta_lat = radius_m / 111000.0
        delta_lon = radius_m / (111000.0 * max(math.cos(math.radians(lat)), 0.1))

        # Bbox prefilter po indeksowanych centroidach (kwadrat ~2×promień),
        # potem dokładny okrąg geodezyjnie -- odfiltrowanie nadmiarowych rogów.
        candidates = conn.execute(
            """
            SELECT id_rcn, centroid_lon, centroid_lat FROM tx_cache
            WHERE id_rcn != ?
              AND centroid_lon BETWEEN ? AND ?
              AND centroid_lat BETWEEN ? AND ?
            """,
            (id_rcn, lon - delta_lon, lon + delta_lon, lat - delta_lat, lat + delta_lat),
        ).fetchall()
        neighbor_ids = []
        for r in candidates:
            if r["centroid_lon"] is None or r["centroid_lat"] is None:
                continue
            _, _, dist_m = _GEOD.inv(lon, lat, r["centroid_lon"], r["centroid_lat"])
            if dist_m <= radius_m:
                neighbor_ids.append(r["id_rcn"])
        return {
            "neighbor_ids": neighbor_ids,
            "focused_lat": lat,
            "focused_lon": lon,
            "radius_m": radius_m,
            "spatial": True,  # dokładny okrąg (pyproj.Geod), nie aproksymacja bbox
        }
    finally:
        conn.close()
