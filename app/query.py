"""Query + filter API.

Operates on a single workspace SQLite.  Filters span both transakcje-level
attributes (type, market, price, date) and object-level attributes (TERYT,
city, geometry).  A transakcja matches the spatial filter if ANY of its
objects' centroids fall inside the given polygon/bbox.

Query results are joined with a representative object (first non-null
centroid / address) so the table UI can render the row without another
round-trip.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from shapely.geometry import Polygon, shape

from app.auth import require_auth
from app.workspaces import _require_workspace, _workspace_db, assert_workspace_idle
from rcn_core.ingest import open_workspace

router = APIRouter(prefix="/api/workspaces", tags=["query"])


class BBoxFilter(BaseModel):
    type: Literal["bbox"] = "bbox"
    bbox: list[float] = Field(..., min_length=4, max_length=4,
                              description="[min_lon, min_lat, max_lon, max_lat] in EPSG:4326")


class PolygonFilter(BaseModel):
    type: Literal["polygon"] = "polygon"
    coordinates: list[list[list[float]]] = Field(
        ...,
        description="GeoJSON Polygon coordinates in EPSG:4326 — [[[lon,lat], ...]]",
    )


class QueryFilters(BaseModel):
    rodzaj_rynku: Optional[list[str]] = None
    rodzaj_transakcji: Optional[list[str]] = None
    rodzaj_nieruchomosci: Optional[list[str]] = None
    miejscowosc: Optional[list[str]] = None
    teryt_gminy: Optional[list[str]] = None
    obreb: Optional[list[str]] = None
    # Obręb jednoznacznie: numer w identyfikancie EGIB jest unikalny TYLKO w ramach
    # jednostki ewidencyjnej, więc samo "0024" w Łodzi znaczy cztery różne obręby
    # (B-24 Bałuty, G-24 Górna, P-24 Polesie, W-24 Widzew). Klucz ma postać
    # "<teryt_gminy>|<obreb>"; pusty teryt (dane bez identyfikatora) = "|<obreb>".
    # Stary `obreb` zostaje dla zgodności zapisanych linków i wtyczek.
    obreb_key: Optional[list[str]] = None
    obreb_search: Optional[str] = None  # LIKE w nazwie obrębu, np. "Bałuty" -- filtr z headera tabeli
    adres: Optional[str] = None
    # Wiki-linki: klik w identyfikator obiektu w tabeli filtruje tabelę do wszystkich
    # transakcji powiązanych z tym samym obiektem (ta sama działka/budynek/lokal była
    # przedmiotem wielu sprzedaży w różnym czasie).
    plot_ident: Optional[str] = None
    building_ident: Optional[str] = None
    local_ident: Optional[str] = None
    # Snapshot-safe update (tryb='snapshot'): transakcje które znikły z kolejnego
    # snapshot-u są oznaczone status='wycofana_z_portalu'. Domyślnie ukryte;
    # chip "Pokaż wycofane" podmienia filter na True.
    include_withdrawn: bool = False
    only_verified: bool = False  # v6: tylko transakcje bez data_quality_flags
    cena_min: Optional[float] = None
    cena_max: Optional[float] = None
    cena_m2_min: Optional[float] = None
    cena_m2_max: Optional[float] = None
    # Faza 4: filtry per-kolumna (header-row tabeli) -- dodane obok cena_*.
    area_min: Optional[float] = None
    area_max: Optional[float] = None
    plot_ident_search: Optional[str] = None  # LIKE '%X%' po plot_idents
    data_od: Optional[str] = None
    data_do: Optional[str] = None
    geometry: Optional[BBoxFilter | PolygonFilter] = None
    id_rcn_in: Optional[list[str]] = None
    has_note: Optional[bool] = None
    notes_search: Optional[str] = None


class SortSpec(BaseModel):
    column: str = "data_transakcji"
    order: Literal["asc", "desc"] = "desc"


class QueryRequest(BaseModel):
    filters: QueryFilters = Field(default_factory=QueryFilters)
    sort: SortSpec = Field(default_factory=SortSpec)
    page: int = Field(1, ge=1)
    pageSize: int = Field(100, ge=1, le=500)


class IdsRequest(BaseModel):
    filters: QueryFilters = Field(default_factory=QueryFilters)


class QueryItem(BaseModel):
    id_rcn: str
    data_transakcji: str | None
    rodzaj_transakcji: str | None
    rodzaj_rynku: str | None
    rodzaj_nieruchomosci: str | None = None
    cena_transakcji_brutto: float | None
    stawka_vat: float | None = None  # z kwota_vat -- w praktyce stawka % (0/8/23)
    area_m2: float | None = None
    cena_na_m2: float | None = None
    liczba_obiektow: int
    plot_count: int
    building_count: int
    local_count: int
    obreb: str | None = None
    # Numer obrębu z identyfikatora EGIB -- pokazywany obok oznaczenia, gdy
    # baza ma nazwy z EGIB ("B-24 · 0024"); inaczej równy `obreb`.
    obreb_numer: str | None = None
    # Jednostka ewidencyjna obrębu -- bez niej klik w obręb w tabeli nie potrafi
    # ustawić jednoznacznego filtra (ten sam numer bywa w kilku dzielnicach).
    teryt_gminy: str | None = None
    plot_idents: list[str] = []
    miejscowosc: str | None
    adres: str | None
    centroid_lon: float | None
    centroid_lat: float | None
    has_note: bool = False
    status: str | None = None  # 'aktywna' | 'wycofana_z_portalu' (po snapshot-update)
    data_quality_flags: list[str] = []  # v6: flagi jakości (badge ⚠ w UI)


class QueryResponse(BaseModel):
    total: int
    page: int
    pageSize: int
    items: list[QueryItem]


_SORT_WHITELIST = {
    "data_transakcji",
    "cena_transakcji_brutto",
    "cena_na_m2",
    "area_m2",
    "rodzaj_transakcji",
    "rodzaj_rynku",
    "rodzaj_nieruchomosci",
    "liczba_obiektow",
    "obreb",
    "miejscowosc",
    "adres",
    "first_plot_ident",
    "id_rcn",
}

_OBJECTS_CTE = """
-- MATERIALIZED wymusza stworzenie temp tabeli raz; bez tego SQLite inlinuje
-- CTE w każde użycie i robi scany wielokrotnie (przy 160k transakcji daje 10+s).
WITH objects AS MATERIALIZED (
    SELECT id_rcn, miejscowosc, adres, centroid_lon, centroid_lat, teryt_gminy FROM plots
    UNION ALL
    SELECT id_rcn, miejscowosc, adres, centroid_lon, centroid_lat, teryt_gminy FROM buildings
    UNION ALL
    SELECT id_rcn, miejscowosc, adres, centroid_lon, centroid_lat, teryt_gminy FROM locals
),
tx_agg AS MATERIALIZED (
    SELECT
        id_rcn,
        MIN(miejscowosc)   AS miejscowosc,
        MIN(adres)         AS adres,
        MIN(centroid_lon)  AS centroid_lon,
        MIN(centroid_lat)  AS centroid_lat,
        MIN(teryt_gminy)   AS teryt_gminy,
        MIN(CASE WHEN centroid_lon IS NOT NULL THEN centroid_lon END) AS any_lon,
        MIN(CASE WHEN centroid_lat IS NOT NULL THEN centroid_lat END) AS any_lat,
        MAX(CASE WHEN centroid_lon IS NOT NULL THEN centroid_lon END) AS any_lon_hi,
        MAX(CASE WHEN centroid_lat IS NOT NULL THEN centroid_lat END) AS any_lat_hi
    FROM objects
    GROUP BY id_rcn
),
counts AS MATERIALIZED (
    SELECT id_rcn, COUNT(*) AS n FROM plots GROUP BY id_rcn
),
bcounts AS MATERIALIZED (
    SELECT id_rcn, COUNT(*) AS n FROM buildings GROUP BY id_rcn
),
lcounts AS MATERIALIZED (
    SELECT id_rcn, COUNT(*) AS n FROM locals GROUP BY id_rcn
),
obreby_agg AS MATERIALIZED (
    SELECT id_rcn, MIN(obreb) AS obreb FROM (
        SELECT id_rcn, obreb FROM plots WHERE obreb IS NOT NULL
        UNION ALL SELECT id_rcn, obreb FROM buildings WHERE obreb IS NOT NULL
    ) GROUP BY id_rcn
),
plot_idents_agg AS MATERIALIZED (
    SELECT id_rcn,
           GROUP_CONCAT(identyfikator_dzialki, '|') AS idents,
           MIN(identyfikator_dzialki) AS first_ident
    FROM plots WHERE identyfikator_dzialki IS NOT NULL AND identyfikator_dzialki <> ''
    GROUP BY id_rcn
),
tx_area_locals    AS MATERIALIZED (SELECT id_rcn, SUM(COALESCE(pow_uzytkowa, 0))       AS a FROM locals    GROUP BY id_rcn),
tx_area_buildings AS MATERIALIZED (SELECT id_rcn, SUM(COALESCE(pow_uzytkowa, 0))       AS a FROM buildings GROUP BY id_rcn),
tx_area_plots     AS MATERIALIZED (SELECT id_rcn, SUM(COALESCE(powierzchnia_m2, 0)) AS a FROM plots GROUP BY id_rcn),
tx_area AS (
    -- Pre-aggregacja z LEFT JOIN (nie correlated subquery!) -- inaczej
    -- SQLite wykonuje 3 subselecty per wiersz transakcji = 160k x 3 =
    -- 480k subqueries. Z JOIN: 3 GROUP BY skany raz, potem hash-join.
    SELECT t.id_rcn,
           CASE
             WHEN t.rodzaj_nieruchomosci LIKE '%okal%'   THEN tal.a
             WHEN t.rodzaj_nieruchomosci LIKE '%udynk%'  THEN tab.a
             WHEN t.rodzaj_nieruchomosci LIKE '%runt%'   THEN tap.a
             ELSE COALESCE(tal.a, tab.a, tap.a)
           END AS area_m2
    FROM transakcje t
    LEFT JOIN tx_area_locals    tal ON tal.id_rcn = t.id_rcn
    LEFT JOIN tx_area_buildings tab ON tab.id_rcn = t.id_rcn
    LEFT JOIN tx_area_plots     tap ON tap.id_rcn = t.id_rcn
)
"""


def _build_filter_clauses(filters: QueryFilters) -> tuple[list[str], list, set[str]]:
    clauses: list[str] = []
    params: list = []
    spatial_object_ids: set[str] = set()
    return clauses, params, spatial_object_ids


def _attribute_clauses(filters: QueryFilters) -> tuple[list[str], list]:
    clauses: list[str] = []
    params: list = []

    if filters.rodzaj_rynku:
        clauses.append(f"t.rodzaj_rynku IN ({_placeholders(filters.rodzaj_rynku)})")
        params.extend(filters.rodzaj_rynku)
    if filters.rodzaj_transakcji:
        clauses.append(f"t.rodzaj_transakcji IN ({_placeholders(filters.rodzaj_transakcji)})")
        params.extend(filters.rodzaj_transakcji)
    if filters.rodzaj_nieruchomosci:
        clauses.append(f"t.rodzaj_nieruchomosci IN ({_placeholders(filters.rodzaj_nieruchomosci)})")
        params.extend(filters.rodzaj_nieruchomosci)
    if filters.obreb:
        # Dopasowanie po oznaczeniu ALBO po numerze: zapisane linki i wtyczki
        # trzymają zwykle numer ("0024"), a kolumna `obreb` po wzbogaceniu
        # z EGIB niesie oznaczenie ("G-24") -- bez drugiej kolumny stary filtr
        # przestawałby cokolwiek znajdować po każdym wzbogaceniu bazy.
        # Uwaga: ten filtr z natury ZWIJA obręby o tym samym numerze z różnych
        # jednostek ewidencyjnych; jednoznaczny jest `obreb_key`.
        placeholders = _placeholders(filters.obreb)
        warunek = f"(obreb IN ({placeholders}) OR obreb_numer IN ({placeholders}))"
        clauses.append(_id_rcn_po_obrebie(warunek))
        # Dwie listy placeholderów (oznaczenie, numer) na każdą tabelę.
        for _ in TABELE_Z_OBREBEM:
            params.extend(filters.obreb)
            params.extend(filters.obreb)
    if filters.obreb_key:
        pary = [_rozbij_klucz_obrebu(k) for k in filters.obreb_key]
        pary = [p for p in pary if p is not None]
        if pary:
            warunek = " OR ".join(
                "(teryt_gminy IS NULL AND obreb = ?)" if teryt == "" else "(teryt_gminy = ? AND obreb = ?)"
                for teryt, _ in pary
            )
            plaskie: list = []
            for teryt, obreb in pary:
                if teryt:
                    plaskie.extend([teryt, obreb])
                else:
                    plaskie.append(obreb)
            clauses.append(_id_rcn_po_obrebie(warunek))
            for _ in TABELE_Z_OBREBEM:
                params.extend(plaskie)
    if filters.obreb_search:
        fraza = filters.obreb_search.strip()
        if fraza.isdigit():
            # Sam numer: "24" ma znaleźć obręb 24 -- niezależnie od tego, czy
            # baza trzyma go jako "24", "0024", czy jako oznaczenie "B-24".
            # LIKE '%24%' łapało przy okazji 0240 i 1024 (zgłoszenie 2026-09-12).
            warunek = ("(obreb_numer = ? OR obreb_numer = ? "
                       " OR obreb = ? OR obreb = ? OR obreb LIKE ?)")
            wartosci: list = [fraza, fraza.zfill(4), fraza, fraza.zfill(4), f"%-{int(fraza)}"]
        else:
            # Oznaczenie literowe: "B-24", "b24" i "B 24" to jedno i to samo,
            # a tester wpisuje raz tak, raz tak. Obok zwykłego LIKE po nazwie
            # sprawdzamy dopasowanie po usunięciu separatorów.
            znorm = re.sub(r"[^0-9A-ZĄĆĘŁŃÓŚŹŻ]", "", fraza.upper())
            warunek = ("(obreb LIKE ? "
                       " OR REPLACE(REPLACE(UPPER(obreb), '-', ''), ' ', '') = ?)")
            wartosci = [f"%{fraza}%", znorm]
        # Szukamy we WSZYSTKICH obiektach transakcji, nie w `tx_cache`.
        # Cache trzyma jednego reprezentanta (MIN po działkach i budynkach), więc
        # transakcja z działkami w G-41, G-42 i G-43 była znajdowana tylko pod
        # „G-41" -- mimo że rozwinięcie wiersza pokazuje wszystkie trzy.
        # Zgłoszenie testera 2026-09-12; dotyczyło 83 aktywnych transakcji
        # w Łodzi i 173 w powiecie zgierskim. Filtry z katalogu (`obreb`,
        # `obreb_key`) zawsze szukały po obiektach -- teraz jest to spójne.
        clauses.append(_id_rcn_po_obrebie(warunek))
        for _ in TABELE_Z_OBREBEM:
            params.extend(wartosci)
    if filters.plot_ident:
        clauses.append(
            "t.id_rcn IN (SELECT id_rcn FROM plots WHERE identyfikator_dzialki = ?)"
        )
        params.append(filters.plot_ident.strip())
    if filters.building_ident:
        clauses.append(
            "t.id_rcn IN (SELECT id_rcn FROM buildings WHERE identyfikator_budynku = ?)"
        )
        params.append(filters.building_ident.strip())
    if filters.local_ident:
        clauses.append(
            "t.id_rcn IN (SELECT id_rcn FROM locals WHERE identyfikator_lokalu = ?)"
        )
        params.append(filters.local_ident.strip())
    # Domyślnie ukrywamy wycofane z portalu (status != 'aktywna'). COALESCE
    # chroni przed starymi rekordami przed migracją (status IS NULL → traktujemy jak aktywne).
    if not filters.include_withdrawn:
        clauses.append("COALESCE(t.status, 'aktywna') = 'aktywna'")
    if filters.only_verified:
        # Tylko transakcje bez flag jakości (NULL lub pusta lista). NULL =
        # jeszcze nie obliczone (legacy DB) -- traktowane jak "verified" żeby
        # nie ukrywać rzędów na świeżo zmigrowanej bazie do czasu compute_flags.
        clauses.append("(t.data_quality_flags IS NULL OR t.data_quality_flags = '[]')")
    if filters.cena_min is not None:
        clauses.append("t.cena_transakcji_brutto >= ?")
        params.append(filters.cena_min)
    if filters.cena_max is not None:
        clauses.append("t.cena_transakcji_brutto <= ?")
        params.append(filters.cena_max)
    if filters.cena_m2_min is not None:
        clauses.append(
            "(tc.area_m2 IS NOT NULL AND tc.area_m2 > 0 AND "
            "t.cena_transakcji_brutto / tc.area_m2 >= ?)"
        )
        params.append(filters.cena_m2_min)
    if filters.cena_m2_max is not None:
        clauses.append(
            "(tc.area_m2 IS NOT NULL AND tc.area_m2 > 0 AND "
            "t.cena_transakcji_brutto / tc.area_m2 <= ?)"
        )
        params.append(filters.cena_m2_max)
    # Faza 4: area_min/max + plot_ident_search (filtry per-kolumna w nagłówku tabeli).
    if filters.area_min is not None:
        clauses.append("tc.area_m2 IS NOT NULL AND tc.area_m2 >= ?")
        params.append(filters.area_min)
    if filters.area_max is not None:
        clauses.append("tc.area_m2 IS NOT NULL AND tc.area_m2 <= ?")
        params.append(filters.area_max)
    if filters.plot_ident_search and filters.plot_ident_search.strip():
        clauses.append(
            "EXISTS (SELECT 1 FROM plots WHERE plots.id_rcn = t.id_rcn "
            "AND plots.identyfikator_dzialki LIKE ?)"
        )
        params.append(f"%{filters.plot_ident_search.strip()}%")
    if filters.data_od:
        clauses.append("t.data_transakcji >= ?")
        params.append(filters.data_od)
    if filters.data_do:
        clauses.append("t.data_transakcji <= ?")
        params.append(filters.data_do)
    if filters.miejscowosc:
        clauses.append(f"tc.miejscowosc IN ({_placeholders(filters.miejscowosc)})")
        params.extend(filters.miejscowosc)
    if filters.teryt_gminy:
        clauses.append(f"tc.teryt_gminy IN ({_placeholders(filters.teryt_gminy)})")
        params.extend(filters.teryt_gminy)
    if filters.adres:
        clauses.append("tc.adres LIKE ?")
        params.append(f"%{filters.adres.strip()}%")
    if filters.id_rcn_in is not None:
        if not filters.id_rcn_in:
            clauses.append("1=0")
        else:
            clause, chunk_params = _in_clause("t.id_rcn", set(filters.id_rcn_in))
            clauses.append(clause)
            params.extend(chunk_params)
    if filters.has_note is True:
        clauses.append("EXISTS (SELECT 1 FROM notes n WHERE n.id_rcn = t.id_rcn)")
    elif filters.has_note is False:
        clauses.append("NOT EXISTS (SELECT 1 FROM notes n WHERE n.id_rcn = t.id_rcn)")
    if filters.notes_search:
        clauses.append(
            "t.id_rcn IN ("
            "SELECT n.id_rcn FROM notes n "
            "JOIN notes_fts fts ON fts.rowid = n.id "
            "WHERE notes_fts MATCH ?"
            ")"
        )
        params.append(_sanitize_fts_query(filters.notes_search))

    return clauses, params


def _sanitize_fts_query(q: str) -> str:
    """FTS5 query safe-builder — quote each term, join with AND so a plain
    multi-word search behaves like "all terms must match".

    FTS5 special chars (" - * ( ) AND OR NOT NEAR) are neutralised by quoting.
    """
    terms = [t for t in q.strip().split() if t]
    if not terms:
        return ""
    return " ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _placeholders(values: list) -> str:
    return ",".join("?" * len(values))


# Obręb niosą wszystkie trzy rodzaje obiektów -- lokale od schema v11. Lista
# jest wspólna dla trzech filtrów (`obreb`, `obreb_key`, `obreb_search`) i dla
# katalogu w `lookups`, bo rozjazd między nimi znaczyłby, że katalog pokazuje
# obręb, którego filtr nie znajduje.
TABELE_Z_OBREBEM = ("plots", "buildings", "locals")


# Katalog jednoznaczny: para (jednostka ewidencyjna, obręb). Bez tego jedna
# pozycja „0024" reprezentuje w Łodzi cztery różne obręby. Zbudowany z tych
# samych tabel co filtry (`TABELE_Z_OBREBEM`) -- stała, bo sprawdza go test.
OBREBY_LOOKUP_SQL = """
    SELECT DISTINCT teryt_gminy, obreb, obreb_numer FROM (
        SELECT teryt_gminy, obreb, obreb_numer FROM plots     WHERE obreb IS NOT NULL
        UNION SELECT teryt_gminy, obreb, obreb_numer FROM buildings WHERE obreb IS NOT NULL
        UNION SELECT teryt_gminy, obreb, obreb_numer FROM locals    WHERE obreb IS NOT NULL
    ) ORDER BY COALESCE(obreb_numer, obreb), teryt_gminy
"""


def _id_rcn_po_obrebie(warunek: str) -> str:
    """`t.id_rcn IN (...)` -- ten sam warunek zadany każdej tabeli obiektów.

    Parametry trzeba powtórzyć tyle razy, ile jest tabel (`TABELE_Z_OBREBEM`).
    """
    return "t.id_rcn IN (" + " UNION ".join(
        f"SELECT id_rcn FROM {t} WHERE {warunek}" for t in TABELE_Z_OBREBEM
    ) + ")"


def _rozbij_klucz_obrebu(klucz: str) -> tuple[str, str] | None:
    """"<teryt>|<obreb>" -> ("106103_9", "0024"). Zwraca None dla śmieci.

    Pusty teryt ("|0024") znaczy „rekordy bez identyfikatora jednostki"
    i jest dopasowywany przez `teryt_gminy IS NULL`.
    """
    if not klucz or "|" not in klucz:
        return None
    teryt, _, obreb = klucz.partition("|")
    obreb = obreb.strip()
    if not obreb:
        return None
    return teryt.strip(), obreb


def _etykieta_obrebu(teryt: str | None, obreb: str, numer: str | None = None) -> str:
    """Etykieta pozycji katalogu obrębów.

    Pokazuje OBIE formy, bo rzeczoznawcy szukają raz oznaczenia urzędowego,
    raz numeru z identyfikatora działki:
    - z EGIB    -> „B-24 · 0024"
    - bez EGIB  -> „0024 · 106102_9" (numer sam nie identyfikuje obrębu,
      bo powtarza się między jednostkami ewidencyjnymi jednego miasta)
    """
    if numer and numer != obreb:
        return f"{obreb} · {numer}"
    if not teryt:
        return obreb
    return f"{obreb} · {teryt}"


def _spatial_filter_ids(conn: sqlite3.Connection, geometry: BBoxFilter | PolygonFilter) -> set[str]:
    """Return set of id_rcn whose ANY object has a centroid inside the given geometry.

    Czysty Python (bez SpatiaLite): bbox po indeksowanych `centroid_lon/lat`
    (idx_<tbl>_centroid). Dla wielokąta dokładny test `shapely polygon.covers
    (Point)` na kandydatach z bboxa -- precyzja bez R-tree."""
    if isinstance(geometry, BBoxFilter):
        min_lon, min_lat, max_lon, max_lat = geometry.bbox
        ids: set[str] = set()
        sql = (
            "SELECT DISTINCT id_rcn FROM {tbl} "
            "WHERE centroid_lon BETWEEN ? AND ? AND centroid_lat BETWEEN ? AND ?"
        )
        params = (min_lon, max_lon, min_lat, max_lat)
        for tbl in ("plots", "buildings", "locals"):
            rows = conn.execute(sql.format(tbl=tbl), params).fetchall()
            ids.update(r[0] for r in rows)
        return ids

    polygon: Polygon = shape({"type": "Polygon", "coordinates": geometry.coordinates})
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    min_lon, min_lat, max_lon, max_lat = polygon.bounds

    ids = set()
    sql = (
        "SELECT id_rcn, centroid_lon, centroid_lat FROM {tbl} "
        "WHERE centroid_lon BETWEEN ? AND ? AND centroid_lat BETWEEN ? AND ?"
    )
    from shapely.geometry import Point

    for tbl in ("plots", "buildings", "locals"):
        rows = conn.execute(
            sql.format(tbl=tbl),
            (min_lon, max_lon, min_lat, max_lat),
        ).fetchall()
        for row in rows:
            if row[1] is None or row[2] is None:
                continue
            if polygon.covers(Point(row[1], row[2])):
                ids.add(row[0])
    return ids


@router.post("/{workspace_id}/query", response_model=QueryResponse)
def query_workspace(
    workspace_id: str,
    body: QueryRequest,
    _: str = Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
) -> QueryResponse:
    _require_workspace(workspace_id)

    if body.sort.column not in _SORT_WHITELIST:
        raise HTTPException(status_code=400, detail=f"Sort column must be one of {sorted(_SORT_WHITELIST)}")

    conn = open_workspace(_workspace_db(workspace_id))
    try:
        attr_clauses, attr_params = _attribute_clauses(body.filters)
        spatial_ids: Optional[set[str]] = None
        if body.filters.geometry is not None:
            spatial_ids = _spatial_filter_ids(conn, body.filters.geometry)
            if not spatial_ids:
                return QueryResponse(total=0, page=body.page, pageSize=body.pageSize, items=[])

        where_clauses = list(attr_clauses)
        params = list(attr_params)

        if spatial_ids is not None:
            chunk_sql, chunk_params = _in_clause("t.id_rcn", spatial_ids)
            where_clauses.append(chunk_sql)
            params.extend(chunk_params)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        # Po migracji v3: tx_cache ma wszystkie preagregowane pola --
        # nie trzeba wielu CTE, wystarczy JOIN. Drastyczna poprawa (sekundy → ms).
        base_cte = f"""
        SELECT
            t.id_rcn, t.data_transakcji, t.rodzaj_transakcji, t.rodzaj_rynku,
            t.rodzaj_nieruchomosci,
            t.cena_transakcji_brutto, t.kwota_vat, t.liczba_obiektow,
            tc.area_m2,
            CASE WHEN tc.area_m2 IS NOT NULL AND tc.area_m2 > 0
                 THEN t.cena_transakcji_brutto / tc.area_m2
                 ELSE NULL END AS cena_na_m2,
            tc.plot_count, tc.building_count, tc.local_count,
            tc.obreb,
            tc.obreb_numer,
            tc.teryt_gminy,
            tc.plot_idents_concat,
            tc.first_plot_ident,
            tc.miejscowosc, tc.adres,
            tc.centroid_lon, tc.centroid_lat,
            COALESCE(t.status, 'aktywna') AS status,
            t.data_quality_flags AS data_quality_flags,
            (SELECT 1 FROM notes n WHERE n.id_rcn = t.id_rcn) AS has_note_raw
        FROM transakcje t
        LEFT JOIN tx_cache tc USING (id_rcn)
        {where_sql}
        """

        total = conn.execute(
            "SELECT COUNT(*) FROM (" + base_cte + ")",
            params,
        ).fetchone()[0]

        offset = (body.page - 1) * body.pageSize
        kierunek = body.sort.order.upper()
        if body.sort.column == "obreb":
            # Obręb sortujemy po jednostce ewidencyjnej i NUMERZE, nie po tekście
            # oznaczenia: alfabetycznie „B-10" wypada przed „B-2", a po
            # wzbogaceniu z EGIB cała kolumna to oznaczenia. Grupowanie po
            # jednostce daje kolejność, która wygląda naturalnie (B-1, B-2, …,
            # B-10, potem G-1), bo prefiks jest w niej stały. Numer ma zera
            # wiodące („0001"), więc porównanie tekstowe jest tu numeryczne.
            # Oznaczenie zostaje trzecim kluczem -- dla baz, gdzie kilka nazw
            # dzieli numer w jednej jednostce.
            kolumny = (f"tc.teryt_gminy {kierunek} NULLS LAST, "
                       f"tc.obreb_numer {kierunek} NULLS LAST, "
                       f"tc.obreb {kierunek} NULLS LAST")
        else:
            kolumny = f"{body.sort.column} {kierunek} NULLS LAST"
        order_sql = f"ORDER BY {kolumny}, t.id_rcn ASC"

        page_sql = base_cte + f" {order_sql} LIMIT ? OFFSET ?"
        rows = conn.execute(page_sql, [*params, body.pageSize, offset]).fetchall()

    finally:
        conn.close()

    items = [
        QueryItem(
            id_rcn=r["id_rcn"],
            data_transakcji=r["data_transakcji"],
            rodzaj_transakcji=r["rodzaj_transakcji"],
            rodzaj_rynku=r["rodzaj_rynku"],
            rodzaj_nieruchomosci=r["rodzaj_nieruchomosci"],
            cena_transakcji_brutto=r["cena_transakcji_brutto"],
            stawka_vat=r["kwota_vat"],
            area_m2=r["area_m2"],
            cena_na_m2=r["cena_na_m2"],
            liczba_obiektow=r["liczba_obiektow"] or 0,
            plot_count=r["plot_count"] or 0,
            building_count=r["building_count"] or 0,
            local_count=r["local_count"] or 0,
            obreb=r["obreb"],
            obreb_numer=r["obreb_numer"],
            teryt_gminy=r["teryt_gminy"],
            plot_idents=(r["plot_idents_concat"].split("|") if r["plot_idents_concat"] else []),
            miejscowosc=r["miejscowosc"],
            adres=r["adres"],
            centroid_lon=r["centroid_lon"],
            centroid_lat=r["centroid_lat"],
            has_note=bool(r["has_note_raw"]),
            status=r["status"],
            data_quality_flags=(
                json.loads(r["data_quality_flags"])
                if r["data_quality_flags"] else []
            ),
        )
        for r in rows
    ]

    return QueryResponse(total=total, page=body.page, pageSize=body.pageSize, items=items)


def _in_clause(column: str, values: set[str]) -> tuple[str, list]:
    values_list = list(values)
    if not values_list:
        return "1=0", []
    MAX_VARS = 900
    if len(values_list) <= MAX_VARS:
        return f"{column} IN ({_placeholders(values_list)})", values_list
    chunks = [values_list[i : i + MAX_VARS] for i in range(0, len(values_list), MAX_VARS)]
    parts = [f"{column} IN ({_placeholders(c)})" for c in chunks]
    params = [v for c in chunks for v in c]
    return "(" + " OR ".join(parts) + ")", params


@router.post("/{workspace_id}/query/ids")
def query_ids(
    workspace_id: str,
    body: IdsRequest,
    _: str = Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
) -> dict:
    """Return the full list of id_rcn that match the filters (without pagination).

    Used by the UI's "Select all results" action so the client can build a
    persistent selection set without re-fetching full rows."""
    _require_workspace(workspace_id)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        attr_clauses, attr_params = _attribute_clauses(body.filters)
        spatial_ids: Optional[set[str]] = None
        if body.filters.geometry is not None:
            spatial_ids = _spatial_filter_ids(conn, body.filters.geometry)
            if not spatial_ids:
                return {"ids": [], "total": 0}

        where_clauses = list(attr_clauses)
        params = list(attr_params)
        if spatial_ids is not None:
            chunk_sql, chunk_params = _in_clause("t.id_rcn", spatial_ids)
            where_clauses.append(chunk_sql)
            params.extend(chunk_params)

        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        sql = f"""
            SELECT t.id_rcn
            FROM transakcje t
            LEFT JOIN tx_cache tc USING (id_rcn)
            {where_sql}
            ORDER BY t.data_transakcji DESC NULLS LAST, t.id_rcn ASC
        """
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    ids = [r[0] for r in rows]
    return {"ids": ids, "total": len(ids)}


@router.get("/{workspace_id}/geojson")
def workspace_geojson(
    workspace_id: str,
    rodzaj_rynku: Optional[list[str]] = Query(None),
    rodzaj_transakcji: Optional[list[str]] = Query(None),
    rodzaj_nieruchomosci: Optional[list[str]] = Query(None),
    cena_min: Optional[float] = Query(None),
    cena_max: Optional[float] = Query(None),
    cena_m2_min: Optional[float] = Query(None),
    cena_m2_max: Optional[float] = Query(None),
    area_min: Optional[float] = Query(None),
    area_max: Optional[float] = Query(None),
    plot_ident_search: Optional[str] = Query(None),
    data_od: Optional[str] = Query(None),
    data_do: Optional[str] = Query(None),
    miejscowosc: Optional[list[str]] = Query(None),
    teryt_gminy: Optional[list[str]] = Query(None),
    obreb: Optional[list[str]] = Query(None),
    obreb_key: Optional[list[str]] = Query(None),
    obreb_search: Optional[str] = Query(None),
    adres: Optional[str] = Query(None),
    plot_ident: Optional[str] = Query(None),
    building_ident: Optional[str] = Query(None),
    local_ident: Optional[str] = Query(None),
    include_withdrawn: bool = Query(False),
    # BBox widoku mapy -- jeśli podany, ograniczamy geojson do tego prostokąta.
    # Frontend wysyła przy każdym pan/zoom (debounced), żeby nawet przy 160k
    # transakcji pokazać wszystkie w widocznym obszarze.
    min_lon: Optional[float] = Query(None),
    min_lat: Optional[float] = Query(None),
    max_lon: Optional[float] = Query(None),
    max_lat: Optional[float] = Query(None),
    limit: int = Query(10000, ge=1, le=20000),
    _: str = Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
) -> dict:
    """One Feature per transakcja.

    The marker's point geometry is a representative centroid chosen from the
    transakcja's objects, in this priority: largest plot by area, then first
    building with a centroid, then first local. The properties carry the full
    summary the UI needs for the popup without a second round-trip.
    """
    _require_workspace(workspace_id)
    filters = QueryFilters(
        rodzaj_rynku=rodzaj_rynku,
        rodzaj_transakcji=rodzaj_transakcji,
        rodzaj_nieruchomosci=rodzaj_nieruchomosci,
        cena_min=cena_min,
        cena_max=cena_max,
        cena_m2_min=cena_m2_min,
        cena_m2_max=cena_m2_max,
        area_min=area_min,
        area_max=area_max,
        plot_ident_search=plot_ident_search,
        data_od=data_od,
        data_do=data_do,
        miejscowosc=miejscowosc,
        teryt_gminy=teryt_gminy,
        obreb=obreb,
        obreb_key=obreb_key,
        obreb_search=obreb_search,
        adres=adres,
        plot_ident=plot_ident,
        building_ident=building_ident,
        local_ident=local_ident,
        include_withdrawn=include_withdrawn,
    )
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        # Optional bbox filter dla widoku mapy (pan/zoom aware) -- po
        # indeksowanych centroidach (idx_tx_cache_centroid), bez SpatiaLite.
        bbox_clause = ""
        bbox_params: list = []
        if all(v is not None for v in (min_lon, min_lat, max_lon, max_lat)):
            bbox_clause = (
                " AND tc.centroid_lon BETWEEN ? AND ? "
                " AND tc.centroid_lat BETWEEN ? AND ?"
            )
            bbox_params = [min_lon, max_lon, min_lat, max_lat]

        attr_clauses, attr_params = _attribute_clauses(filters)

        # Pre-aggreg w tx_cache -- jedno JOIN zamiast wielu CTE.
        sql = f"""
            SELECT t.id_rcn,
                   tc.centroid_lon, tc.centroid_lat,
                   'cache' AS rep_kind,
                   tc.miejscowosc,
                   tc.adres,
                   tc.obreb,
                   t.data_transakcji, t.cena_transakcji_brutto,
                   t.rodzaj_rynku, t.rodzaj_transakcji, t.rodzaj_nieruchomosci,
                   tc.plot_count, tc.building_count, tc.local_count,
                   tc.plot_idents_concat AS plot_idents,
                   tc.area_m2,
                   CASE WHEN tc.area_m2 IS NOT NULL AND tc.area_m2 > 0
                        THEN t.cena_transakcji_brutto / tc.area_m2
                        ELSE NULL END AS cena_na_m2
            FROM transakcje t
            JOIN tx_cache tc USING (id_rcn)
            WHERE tc.centroid_lon IS NOT NULL AND tc.centroid_lat IS NOT NULL
            {bbox_clause}
            {' AND ' + ' AND '.join(attr_clauses) if attr_clauses else ''}
            LIMIT ?
        """
        params = list(bbox_params) + list(attr_params) + [limit + 1]
        rows = conn.execute(sql, params).fetchall()
        capped = len(rows) > limit
        rows = rows[:limit]

        features = []
        for r in rows:
            idents_raw = r["plot_idents"]
            plot_idents = idents_raw.split("|") if idents_raw else []
            features.append({
                "type": "Feature",
                "id": r["id_rcn"],
                "geometry": {
                    "type": "Point",
                    "coordinates": [r["centroid_lon"], r["centroid_lat"]],
                },
                "properties": {
                    "id_rcn": r["id_rcn"],
                    "rep_kind": r["rep_kind"],
                    "data_transakcji": r["data_transakcji"],
                    "cena_transakcji_brutto": r["cena_transakcji_brutto"],
                    "rodzaj_rynku": r["rodzaj_rynku"],
                    "rodzaj_transakcji": r["rodzaj_transakcji"],
                    "rodzaj_nieruchomosci": r["rodzaj_nieruchomosci"],
                    "miejscowosc": r["miejscowosc"],
                    "adres": r["adres"],
                    "obreb": r["obreb"],
                    "plot_count": r["plot_count"] or 0,
                    "building_count": r["building_count"] or 0,
                    "local_count": r["local_count"] or 0,
                    "plot_idents": plot_idents,
                    # Faza 4 fix: dodajemy area_m2 + computed cena_na_m2 -- popup
                    # markera w modalu mapy używa tych pól (wcześniej zawsze "—").
                    "area_m2": r["area_m2"],
                    "cena_na_m2": r["cena_na_m2"],
                },
            })
    finally:
        conn.close()

    return {
        "type": "FeatureCollection",
        "features": features,
        "capped": capped,
        "limit": limit,
    }


# PR8 fix2: endpoint /geojson/aggregate USUNIETY -- agregaty zastapione
# plansza overlay "Przybliz mape...". Faza 5 (po PR10) wprowadzi alternatywne
# podejscie: klik w obiekt RCN (dzialka/budynek) -> modal z lista transakcji
# powiazanych z tym obiektem (po plot_ident/building_ident/local_ident).


class ObrebOption(BaseModel):
    """Jedna pozycja katalogu obrębów -- jednoznaczna, bo z jednostką ewidencyjną."""
    key: str            # "106103_9|0024" -- wartość filtra `obreb_key`
    obreb: str          # "0024" albo "G-24", gdy baza ma nazwy z EGIB
    obreb_numer: str | None = None   # "0024" -- zawsze numer, także gdy `obreb` to nazwa
    teryt_gminy: str | None = None
    label: str          # to, co widzi użytkownik


class LookupsResponse(BaseModel):
    rodzaj_rynku: list[str]
    rodzaj_transakcji: list[str]
    rodzaj_nieruchomosci: list[str]
    miejscowosc: list[str]
    teryt_gminy: list[str]
    # Płaska lista oznaczeń -- ZWIJA obręby o tym samym numerze z różnych
    # jednostek ewidencyjnych, więc do filtrowania służy `obreby` niżej.
    # Zostaje dla zgodności (zapisane linki, wtyczki czytające lookups).
    obreb: list[str]
    obreby: list[ObrebOption]


@router.get("/{workspace_id}/lookups", response_model=LookupsResponse)
def workspace_lookups(
    workspace_id: str,
    _: str = Depends(require_auth),
    __: None = Depends(assert_workspace_idle),
) -> LookupsResponse:
    _require_workspace(workspace_id)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        def distinct(sql: str) -> list[str]:
            return [r[0] for r in conn.execute(sql).fetchall() if r[0]]

        rodzaj_rynku = distinct("SELECT DISTINCT rodzaj_rynku FROM transakcje ORDER BY rodzaj_rynku")
        rodzaj_transakcji = distinct(
            "SELECT DISTINCT rodzaj_transakcji FROM transakcje ORDER BY rodzaj_transakcji"
        )
        rodzaj_nier = distinct(
            "SELECT DISTINCT rodzaj_nieruchomosci FROM transakcje "
            "WHERE rodzaj_nieruchomosci IS NOT NULL ORDER BY rodzaj_nieruchomosci"
        )
        obreb_sql = """
            SELECT DISTINCT obreb FROM (
                SELECT obreb FROM plots WHERE obreb IS NOT NULL
                UNION SELECT obreb FROM buildings WHERE obreb IS NOT NULL
                UNION SELECT obreb FROM locals WHERE obreb IS NOT NULL
            ) ORDER BY obreb
        """
        obreby_sql = OBREBY_LOOKUP_SQL
        miejscowosc_sql = """
            SELECT DISTINCT miejscowosc FROM (
                SELECT miejscowosc FROM plots
                UNION SELECT miejscowosc FROM buildings
                UNION SELECT miejscowosc FROM locals
            ) WHERE miejscowosc IS NOT NULL ORDER BY miejscowosc
        """
        teryt_sql = """
            SELECT DISTINCT teryt_gminy FROM (
                SELECT teryt_gminy FROM plots
                UNION SELECT teryt_gminy FROM buildings
                UNION SELECT teryt_gminy FROM locals
            ) WHERE teryt_gminy IS NOT NULL ORDER BY teryt_gminy
        """
        obreby = [
            ObrebOption(
                key=f"{r[0] or ''}|{r[1]}",
                obreb=r[1],
                obreb_numer=r[2],
                teryt_gminy=r[0],
                label=_etykieta_obrebu(r[0], r[1], r[2]),
            )
            for r in conn.execute(obreby_sql).fetchall()
            if r[1]
        ]
        return LookupsResponse(
            rodzaj_rynku=rodzaj_rynku,
            rodzaj_transakcji=rodzaj_transakcji,
            rodzaj_nieruchomosci=rodzaj_nier,
            miejscowosc=distinct(miejscowosc_sql),
            teryt_gminy=distinct(teryt_sql),
            obreb=distinct(obreb_sql),
            obreby=obreby,
        )
    finally:
        conn.close()
