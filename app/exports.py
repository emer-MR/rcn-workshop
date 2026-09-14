"""Export endpoints (XLSX + GPKG) honoring the same filter params as /geojson.

XLSX reuses the plugin's own exporter (self-contained XML builder, no openpyxl).
GPKG używa pyogrio (NIE Fiony) -- pyogrio linkuje system libgdal+libproj zamiast
statycznie zlinkowanej PROJ jak `libproj-fiona-*.so`. Eliminuje SIGSEGV gdy ten
sam Python proces używa pyproj+SpatiaLite (refresh_tx_cache, populate_geom).
Patrz `rcn_core/enrich.py` dla szerszego kontekstu (test Sieradz 2026-05-01).
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.prywatnosc import bez_notariusza_w_wierszach, ukrywac_notariusza
from app.query import QueryFilters, _attribute_clauses
from app.workspaces import _require_workspace, _workspace_db, _meta_get, assert_workspace_idle
from app.xlsx_builder import ExportMeta, build_workshop_xlsx
from rcn_core.geo import epsg_int
from rcn_core.ingest import open_workspace

router = APIRouter(prefix="/api/workspaces", tags=["export"])


class ExportRequest(BaseModel):
    filters: QueryFilters = Field(default_factory=QueryFilters)
    # PR6 fix: jednorazowy komentarz "do tego eksportu" (wpisany w stopce
    # modala koszyka). Trafia do arkusza "Podsumowanie" w XLSX. Nie persystowany.
    export_comment: Optional[str] = None


def _build_filters(
    rodzaj_rynku: Optional[list[str]],
    rodzaj_transakcji: Optional[list[str]],
    cena_min: Optional[float],
    cena_max: Optional[float],
    data_od: Optional[str],
    data_do: Optional[str],
    miejscowosc: Optional[list[str]],
    teryt_gminy: Optional[list[str]],
) -> QueryFilters:
    return QueryFilters(
        rodzaj_rynku=rodzaj_rynku,
        rodzaj_transakcji=rodzaj_transakcji,
        cena_min=cena_min,
        cena_max=cena_max,
        data_od=data_od,
        data_do=data_do,
        miejscowosc=miejscowosc,
        teryt_gminy=teryt_gminy,
    )


def _matching_id_rcn(conn: sqlite3.Connection, filters: QueryFilters) -> list[str]:
    """Return id_rcn list matching attribute filters (spatial filters intentionally
    omitted -- exports use the explicit attribute filters submitted in query params).

    Używa materialized view `tx_cache` (_attribute_clauses używa prefiksu `tc.`).
    Jeden JOIN zamiast wielu CTE -- drastyczna różnica dla DB >100k transakcji.
    """
    clauses, params = _attribute_clauses(filters)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT t.id_rcn
        FROM transakcje t
        LEFT JOIN tx_cache tc USING (id_rcn)
        {where}
    """
    return [r[0] for r in conn.execute(sql, params).fetchall()]


def _reconstruct_rows(conn: sqlite3.Connection, id_rcns: list[str]) -> dict[str, list]:
    """Reconstruct plugin-shaped rows (summary/plot/building/local) from `attributes_json`."""
    if not id_rcns:
        return {"summary": [], "plot": [], "building": [], "local": []}

    def fetch(table: str) -> list[dict]:
        out: list[dict] = []
        MAX = 800
        for i in range(0, len(id_rcns), MAX):
            chunk = id_rcns[i:i + MAX]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT id_rcn, attributes_json, wkt FROM {table} WHERE id_rcn IN ({placeholders})",
                chunk,
            ).fetchall() if table != "transakcje" else conn.execute(
                f"SELECT id_rcn, attributes_json FROM {table} WHERE id_rcn IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                attrs = json.loads(row["attributes_json"]) if row["attributes_json"] else {}
                if table != "transakcje" and row["wkt"]:
                    attrs["_wkt"] = row["wkt"]
                out.append(attrs)
        return out

    # TODO(notatki-transakcji): dolaczanie notatek per transakcja do XLSX --
    # wylaczone do pozniejszej analizy/wdrozenia. Jesli chcemy je wlaczyc:
    # 1) tutaj fetch z tabeli `notes` (LEFT JOIN po id_rcn) i dodanie do
    #    summary jako "__user_note"
    # 2) w xlsx_builder._build_transakcje_sheet odkomentuj _Col("Notatka", ...)
    # 3) w xlsx_builder Raport zbiorczy odkomentuj dolaczanie do uwagi_tx
    # Na razie XLSX dostaje TYLKO "Komentarz do eksportu" (ExportMeta.export_comment)
    # w arkuszu "Podsumowanie".
    return {
        "summary": fetch("transakcje"),
        "plot": fetch("plots"),
        "building": fetch("buildings"),
        "local": fetch("locals"),
    }


@router.get("/{workspace_id}/export.xlsx")
def export_xlsx(
    workspace_id: str,
    rodzaj_rynku: Optional[list[str]] = Query(None),
    rodzaj_transakcji: Optional[list[str]] = Query(None),
    cena_min: Optional[float] = Query(None),
    cena_max: Optional[float] = Query(None),
    data_od: Optional[str] = Query(None),
    data_do: Optional[str] = Query(None),
    miejscowosc: Optional[list[str]] = Query(None),
    teryt_gminy: Optional[list[str]] = Query(None),
    ctx=Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
):
    _require_workspace(workspace_id)
    filters = _build_filters(
        rodzaj_rynku, rodzaj_transakcji, cena_min, cena_max,
        data_od, data_do, miejscowosc, teryt_gminy,
    )
    return _export_xlsx_impl(workspace_id, filters,
                             ukryj_notariusza=ukrywac_notariusza(ctx))


@router.post("/{workspace_id}/export.xlsx")
def export_xlsx_post(
    workspace_id: str,
    body: ExportRequest = Body(...),
    ctx=Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
):
    """POST variant accepting a full QueryFilters JSON (including `id_rcn_in`).

    Used by the UI when exporting only ticked rows — the selection list can be
    hundreds or thousands of ids which would bloat a URL query string."""
    _require_workspace(workspace_id)
    return _export_xlsx_impl(workspace_id, body.filters, body.export_comment or "",
                             ukryj_notariusza=ukrywac_notariusza(ctx))


def _describe_filters(filters: QueryFilters) -> str:
    """Krótkie textual summary dla arkusza 'Podsumowanie'."""
    parts: list[str] = []
    if filters.id_rcn_in is not None:
        parts.append(f"wybrane w koszyku ({len(filters.id_rcn_in)})")
    if filters.rodzaj_rynku:
        parts.append("rynek: " + "/".join(filters.rodzaj_rynku))
    if filters.rodzaj_transakcji:
        parts.append("typ: " + "/".join(filters.rodzaj_transakcji))
    if filters.rodzaj_nieruchomosci:
        parts.append("nieruchomość: " + "/".join(filters.rodzaj_nieruchomosci))
    if filters.miejscowosc:
        parts.append("miejscowość: " + "/".join(filters.miejscowosc))
    if filters.obreb:
        parts.append("obręb: " + "/".join(filters.obreb))
    if filters.obreb_key:
        # Klucz niesie jednostkę ewidencyjną ("106103_9|0024"); w opisie eksportu
        # liczy się samo oznaczenie obrębu, bo to ono identyfikuje teren w operacie.
        parts.append("obręb: " + "/".join(k.split("|", 1)[-1] for k in filters.obreb_key))
    if filters.data_od or filters.data_do:
        parts.append(f"data: {filters.data_od or '…'} → {filters.data_do or '…'}")
    if filters.cena_min is not None or filters.cena_max is not None:
        parts.append(f"cena: {filters.cena_min or 0}-{filters.cena_max or '∞'}")
    if filters.adres:
        parts.append(f"adres zawiera: '{filters.adres}'")
    return ", ".join(parts) if parts else ""


def _export_xlsx_impl(workspace_id: str, filters: QueryFilters, export_comment: str = "",
                      ukryj_notariusza: bool = False):
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        workspace_name = _meta_get(conn, "name") or workspace_id
        id_rcns = _matching_id_rcn(conn, filters)
        rows = _reconstruct_rows(conn, id_rcns)
        if ukryj_notariusza:
            rows["summary"] = bez_notariusza_w_wierszach(rows["summary"])
    finally:
        conn.close()

    meta = ExportMeta(
        workspace_name=workspace_name,
        workspace_id=workspace_id,
        generated_at=datetime.now(),
        total_count=len(rows["summary"]),
        filters_summary=_describe_filters(filters),
        export_comment=export_comment or "",
    )

    payload = build_workshop_xlsx(
        rows["summary"], rows["plot"], rows["building"], rows["local"],
        meta=meta,
    )

    filename = f"rcn-workshop-{workspace_id[:8]}-{datetime.now().strftime('%Y%m%d')}.xlsx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{workspace_id}/export.csv")
def export_csv(
    workspace_id: str,
    rodzaj_rynku: Optional[list[str]] = Query(None),
    rodzaj_transakcji: Optional[list[str]] = Query(None),
    cena_min: Optional[float] = Query(None),
    cena_max: Optional[float] = Query(None),
    data_od: Optional[str] = Query(None),
    data_do: Optional[str] = Query(None),
    miejscowosc: Optional[list[str]] = Query(None),
    teryt_gminy: Optional[list[str]] = Query(None),
    ctx=Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
):
    _require_workspace(workspace_id)
    filters = _build_filters(
        rodzaj_rynku, rodzaj_transakcji, cena_min, cena_max,
        data_od, data_do, miejscowosc, teryt_gminy,
    )
    return _export_csv_impl(workspace_id, filters,
                            ukryj_notariusza=ukrywac_notariusza(ctx))


@router.post("/{workspace_id}/export.csv")
def export_csv_post(
    workspace_id: str,
    body: ExportRequest = Body(...),
    ctx=Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
):
    """POST variant -- accepts full QueryFilters JSON (w tym `id_rcn_in` dla
    eksportu z koszyka). Odpowiednik `/export.xlsx` POST w prostszym formacie
    CSV, zaprojektowanym jako input dla pipeline PDF batch download
    (patrz README sekcja 'Enrichment z portalu iRzeczoznawca')."""
    _require_workspace(workspace_id)
    return _export_csv_impl(workspace_id, body.filters,
                            ukryj_notariusza=ukrywac_notariusza(ctx))


def _export_csv_impl(workspace_id: str, filters: QueryFilters,
                     ukryj_notariusza: bool = False):
    """Zwraca CSV z wybranymi transakcjami. Minimalny zestaw pól do matchowania
    z bazą enrichment (id_rcn klucz) + człowieczo-czytelne kolumny identyfikujące.
    """
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        id_rcns = _matching_id_rcn(conn, filters)
        if not id_rcns:
            payload = b"id_rcn\n"
            filename = f"rcn-selection-{workspace_id[:8]}-empty.csv"
            return Response(
                content=payload,
                media_type="text/csv; charset=utf-8",
                headers={"content-disposition": f'attachment; filename="{filename}"'},
            )

        # Bierzemy dane z transakcje + tx_cache (preagregacja adres/obreb/area/ident).
        # Zachowujemy oryginalny format GML-owego pola `dokument` (np. "2998/2025 z
        # dnia 2025-12-29") -- skrypt mergujący z enrichment może je rozparsować.
        rows: list[dict] = []
        MAX = 800
        for i in range(0, len(id_rcns), MAX):
            chunk = id_rcns[i : i + MAX]
            ph = ",".join("?" * len(chunk))
            # CSV jest INPUT dla enrichment pipeline (tools/build_pdf_batch.py),
            # wiec celowo BEZ kolumny `notatka` -- newline'y w notatkach Markdown
            # moga zlamac format dla parserow spoza Pythona (Excel, awk, etc.).
            # Notatki uzytkownika trafiaja TYLKO do XLSX (czytanego przez
            # czlowieka), patrz app/xlsx_builder.py kolumna "Notatka".
            sql = f"""
                SELECT
                    t.id_rcn,
                    t.data_transakcji,
                    tc.miejscowosc,
                    tc.adres,
                    tc.obreb,
                    tc.first_plot_ident,
                    tc.plot_idents_concat,
                    t.dokument                  AS sygnatura_dokumentu,
                    t.tworca_dokumentu          AS tworca_dokumentu,
                    t.cena_transakcji_brutto,
                    t.kwota_vat                 AS stawka_vat,
                    tc.area_m2,
                    CASE WHEN tc.area_m2 IS NOT NULL AND tc.area_m2 > 0
                         THEN t.cena_transakcji_brutto / tc.area_m2
                         ELSE NULL END          AS cena_na_m2,
                    t.rodzaj_nieruchomosci,
                    t.rodzaj_rynku,
                    t.rodzaj_transakcji,
                    t.strona_sprzedajaca,
                    t.strona_kupujaca,
                    tc.plot_count, tc.building_count, tc.local_count,
                    tc.centroid_lon, tc.centroid_lat
                FROM transakcje t
                LEFT JOIN tx_cache tc USING (id_rcn)
                WHERE t.id_rcn IN ({ph})
            """
            for r in conn.execute(sql, chunk).fetchall():
                rows.append(dict(r))
    finally:
        conn.close()

    # Stabilna kolejność -- po dacie desc, potem id_rcn.
    rows.sort(key=lambda r: (r.get("data_transakcji") or "", r.get("id_rcn") or ""), reverse=True)

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "id_rcn",                   # klucz do JOIN-u z bazą enrichment (= GNIRCN.identifier)
        "data_transakcji",
        "miejscowosc",
        "adres",
        "obreb",
        "nr_dzialki",               # pierwszy identyfikator działki
        "wszystkie_dzialki",        # | separated
        "sygnatura_dokumentu",      # "2998/2025 z dnia 2025-12-29"
        "tworca_dokumentu",         # np. "NOTARIUSZ ANNA GOŹDZIALSKA"
        "cena_brutto",
        "stawka_vat_pct",           # z kolumny kwota_vat (w GML to stawka %, nie kwota PLN)
        "powierzchnia_m2",
        "cena_na_m2",
        "rodzaj_nieruchomosci",
        "rodzaj_rynku",
        "rodzaj_transakcji",
        "strona_sprzedajaca",
        "strona_kupujaca",
        "liczba_dzialek", "liczba_budynkow", "liczba_lokali",
        "centroid_lon", "centroid_lat",
        # CSV bez kolumny `notatka` -- patrz komentarz przy SQL wyzej.
    ])
    for r in rows:
        writer.writerow([
            r.get("id_rcn") or "",
            r.get("data_transakcji") or "",
            r.get("miejscowosc") or "",
            r.get("adres") or "",
            r.get("obreb") or "",
            r.get("first_plot_ident") or "",
            r.get("plot_idents_concat") or "",
            r.get("sygnatura_dokumentu") or "",
            "" if ukryj_notariusza else (r.get("tworca_dokumentu") or ""),
            _fmt_num(r.get("cena_transakcji_brutto")),
            _fmt_num(r.get("stawka_vat")),
            _fmt_num(r.get("area_m2")),
            _fmt_num(r.get("cena_na_m2")),
            r.get("rodzaj_nieruchomosci") or "",
            r.get("rodzaj_rynku") or "",
            r.get("rodzaj_transakcji") or "",
            r.get("strona_sprzedajaca") or "",
            r.get("strona_kupujaca") or "",
            r.get("plot_count") or 0,
            r.get("building_count") or 0,
            r.get("local_count") or 0,
            _fmt_num(r.get("centroid_lon"), 6),
            _fmt_num(r.get("centroid_lat"), 6),
        ])

    # UTF-8 BOM żeby Excel poprawnie wykrył polskie znaki przy otwarciu.
    payload = b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
    filename = f"rcn-selection-{workspace_id[:8]}-{len(rows)}.csv"
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


def _fmt_num(v, decimals: int = 2) -> str:
    """Formatowanie number -- empty string dla None, kropka jako separator dziesiętny."""
    if v is None or v == "":
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f):
        return str(int(f))
    return f"{f:.{decimals}f}"


@router.get("/{workspace_id}/export.gpkg")
def export_gpkg(
    workspace_id: str,
    rodzaj_rynku: Optional[list[str]] = Query(None),
    rodzaj_transakcji: Optional[list[str]] = Query(None),
    cena_min: Optional[float] = Query(None),
    cena_max: Optional[float] = Query(None),
    data_od: Optional[str] = Query(None),
    data_do: Optional[str] = Query(None),
    miejscowosc: Optional[list[str]] = Query(None),
    teryt_gminy: Optional[list[str]] = Query(None),
    _: str = Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
):
    _require_workspace(workspace_id)
    filters = _build_filters(
        rodzaj_rynku, rodzaj_transakcji, cena_min, cena_max,
        data_od, data_do, miejscowosc, teryt_gminy,
    )
    return _export_gpkg_impl(workspace_id, filters)


@router.post("/{workspace_id}/export.gpkg")
def export_gpkg_post(
    workspace_id: str,
    body: ExportRequest = Body(...),
    _: str = Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
):
    _require_workspace(workspace_id)
    return _export_gpkg_impl(workspace_id, body.filters)


def _export_gpkg_impl(workspace_id: str, filters: QueryFilters):
    try:
        import numpy as np
        from pyogrio.raw import write as _pyogrio_write
        from shapely import wkb as shapely_wkb
    except ImportError:
        return JSONResponse(
            status_code=501,
            content={
                "detail": (
                    "GPKG export wymaga pakietu `pyogrio` (>=0.9). "
                    "Uruchom przez docker compose — obraz zawiera GDAL + pyogrio."
                ),
            },
        )

    conn = open_workspace(_workspace_db(workspace_id))
    try:
        id_rcns = set(_matching_id_rcn(conn, filters))
        if not id_rcns:
            raise HTTPException(status_code=404, detail="No matching rows to export")

        source_epsg = 2180
        row = conn.execute(
            "SELECT parser_epsg FROM imports ORDER BY upload_timestamp DESC LIMIT 1"
        ).fetchone()
        if row and row["parser_epsg"]:
            source_epsg = epsg_int(row["parser_epsg"]) or 2180

        layers = _collect_gpkg_layers(conn, id_rcns)
    finally:
        conn.close()

    # mkstemp zwraca otwarty fd -- na Windows blokuje plik, więc unlink/pyogrio
    # write nie mogą go używać. Zamykamy fd przed manipulacją.
    fd, tmp_str = tempfile.mkstemp(suffix=".gpkg", prefix="rcn_export_")
    os.close(fd)
    tmp_path = Path(tmp_str)
    if tmp_path.exists():
        tmp_path.unlink()

    try:
        wrote_any = False
        for layer_name, geom_type, records, epsg_for_layer in layers:
            records = [r for r in records if r["geom"] is not None]
            if not records:
                continue

            # Stabilna unia kluczy (rekord referencyjny może mieć None w niektórych
            # polach; iterujemy po wszystkich żeby nie zgubić kolumny).
            field_names: list[str] = []
            seen: set[str] = set()
            for rec in records:
                for k in rec["properties"]:
                    if k not in seen:
                        seen.add(k)
                        field_names.append(k)

            geometry = np.array(
                [shapely_wkb.dumps(rec["geom"]) for rec in records],
                dtype=object,
            )
            field_data = [
                _build_field_array(records, name, np)
                for name in field_names
            ]

            crs_str = f"EPSG:{epsg_for_layer if epsg_for_layer else source_epsg}"
            _pyogrio_write(
                str(tmp_path),
                geometry=geometry,
                field_data=field_data,
                fields=field_names,
                layer=layer_name,
                driver="GPKG",
                geometry_type=geom_type,
                crs=crs_str,
                append=wrote_any,
                # plots/buildings GML mają mieszane Polygon + MultiPolygon -- pyogrio
                # wymaga single geometry_type per layer, więc auto-promotujemy.
                promote_to_multi=(geom_type == "MultiPolygon"),
            )
            wrote_any = True
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    if not wrote_any or not tmp_path.exists() or tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="No matching rows to export")

    try:
        payload = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)

    filename = f"rcn-export-{workspace_id[:8]}.gpkg"
    return Response(
        content=payload,
        media_type="application/geopackage+sqlite3",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


def _collect_gpkg_layers(conn: sqlite3.Connection, id_rcns: set[str]):
    from shapely import wkt as shapely_wkt
    from shapely.geometry import Point

    if not id_rcns:
        return []

    def fetch_layer(table: str, ident_col: str, pow_col: str, id_prop: str):
        records = []
        MAX = 800
        ids = list(id_rcns)
        for i in range(0, len(ids), MAX):
            chunk = ids[i:i + MAX]
            ph = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"""SELECT id_rcn, {ident_col} AS ident, miejscowosc, adres, {pow_col} AS powierzchnia,
                           cena_brutto, wkt
                    FROM {table}
                    WHERE id_rcn IN ({ph})""",
                chunk,
            ).fetchall()
            for row in rows:
                geom = None
                if row["wkt"]:
                    try:
                        geom = shapely_wkt.loads(row["wkt"])
                    except Exception:
                        geom = None
                records.append({
                    "geom": geom,
                    "properties": {
                        "id_rcn": row["id_rcn"],
                        id_prop: row["ident"],
                        "miejscowosc": row["miejscowosc"],
                        "adres": row["adres"],
                        "powierzchnia": row["powierzchnia"],
                        "cena_brutto": row["cena_brutto"],
                    },
                })
        return records

    plots = fetch_layer("plots",     "identyfikator_dzialki", "powierzchnia_m2", "ident_dzialki")
    buildings = fetch_layer("buildings", "identyfikator_budynku", "pow_uzytkowa",    "ident_budynku")
    locals_  = fetch_layer("locals",    "identyfikator_lokalu",  "pow_uzytkowa",    "ident_lokalu")

    ph = ",".join("?" * min(len(id_rcns), 800))
    tx_rows = []
    ids = list(id_rcns)
    MAX = 800
    for i in range(0, len(ids), MAX):
        chunk = ids[i:i + MAX]
        phc = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"""WITH tx_agg AS (
                   SELECT id_rcn,
                          MIN(CASE WHEN centroid_lon IS NOT NULL THEN centroid_lon END) AS lon,
                          MIN(CASE WHEN centroid_lat IS NOT NULL THEN centroid_lat END) AS lat
                   FROM (
                     SELECT id_rcn, centroid_lon, centroid_lat FROM plots
                     UNION ALL SELECT id_rcn, centroid_lon, centroid_lat FROM buildings
                     UNION ALL SELECT id_rcn, centroid_lon, centroid_lat FROM locals
                   )
                   GROUP BY id_rcn
               )
               SELECT t.id_rcn, t.data_transakcji, t.cena_transakcji_brutto,
                      t.rodzaj_transakcji, t.rodzaj_rynku, tx_agg.lon, tx_agg.lat
               FROM transakcje t
               LEFT JOIN tx_agg USING (id_rcn)
               WHERE t.id_rcn IN ({phc})""",
            chunk,
        ).fetchall()
        tx_rows.extend(rows)

    tx_records = []
    for row in tx_rows:
        if row["lon"] is None or row["lat"] is None:
            continue
        tx_records.append({
            "geom": Point(row["lon"], row["lat"]),
            "properties": {
                "id_rcn": row["id_rcn"],
                "data_transakcji": row["data_transakcji"],
                "cena": row["cena_transakcji_brutto"],
                "rodzaj_transakcji": row["rodzaj_transakcji"],
                "rodzaj_rynku": row["rodzaj_rynku"],
            },
        })

    return [
        ("transakcje_punkty", "Point",        tx_records, 4326),
        ("dzialki",           "MultiPolygon", plots,      None),
        ("budynki",           "MultiPolygon", buildings,  None),
        ("lokale",            "MultiPolygon", locals_,    None),
    ]


def _build_field_array(records: list[dict], field_name: str, np):
    """Przygotuj 1D ndarray dla pola GPKG. Strategia:
    - Numericy (int/float, w tym None) → float64 z NaN dla None → pyogrio mapuje
      na OFTReal w GPKG. To traci int↔float distinction, ale jest bezpieczne dla
      mieszanych None'ów (powierzchnia/cena rzadko są ściśle integer).
    - String / mixed → object array (None'y jako None → pyogrio zapisuje NULL).
    """
    sample = next(
        (r["properties"].get(field_name) for r in records if r["properties"].get(field_name) is not None),
        None,
    )
    if isinstance(sample, bool):
        # bool przed int (bool jest podklasą int) — zachowaj jako int 0/1.
        return np.array(
            [int(r["properties"].get(field_name)) if r["properties"].get(field_name) is not None else None
             for r in records],
            dtype=object,
        )
    if isinstance(sample, (int, float)):
        return np.array(
            [float(r["properties"].get(field_name)) if r["properties"].get(field_name) is not None else np.nan
             for r in records],
            dtype="float64",
        )
    return np.array(
        [r["properties"].get(field_name) for r in records],
        dtype=object,
    )
