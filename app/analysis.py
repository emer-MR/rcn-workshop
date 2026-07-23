"""Analityka zbioru transakcji -- endpoint dla modułu "Σ Analiza" w koszyku.

Body zapytania: `{id_rcn_in: [...]}` (cap 5000). Endpoint zwraca:
- `stats_by_kind`: statystyki cen i powierzchni per typ nieruchomości
  (grunt niezabud. / grunt zabud. / budynek / lokal + zagregowane "wszystkie")
- `per_rynek`: osobne statystyki dla rynku pierwotnego i wtórnego
- `per_rodzaj_transakcji`: liczebność + procent (sprzedaż, darowizna, zamiana, ...)
- `top_obreb`: top 5 obrębów po liczbie transakcji + mediana cena/m²
- `quarterly`: seria kwartalna per typ (YYYY-QN → mediana cena/m²)
- `scatter`: surowe punkty do wykresu scatter (data, cena/m², typ, id_rcn, adres)
"""
from __future__ import annotations

import logging
import statistics
import sqlite3
from collections import defaultdict
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.workspaces import _require_workspace, _workspace_db
from rcn_core.ingest import open_workspace

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workspaces", tags=["analysis"])

MAX_IDS = 5000


class AnalysisRequest(BaseModel):
    id_rcn_in: list[str] = Field(..., min_length=1, max_length=MAX_IDS)


def _normalize_kind(raw: Optional[str]) -> str:
    """Normalizacja `rodzaj_nieruchomosci` do jednej z 4 kategorii + 'inne'.
    Używane do grupowania statystyk (cena/m² gruntu vs lokalu to inne parametry)."""
    s = (raw or "").lower()
    if "niezab" in s:
        return "grunt_niezab"
    if "grunt" in s:
        return "grunt_zab"
    if "budynk" in s:
        return "budynek"
    if "lokal" in s:
        return "lokal"
    return "inne"


_KIND_LABEL = {
    "grunt_niezab": "Grunt niezabud.",
    "grunt_zab":    "Grunt zabud.",
    "budynek":      "Budynek",
    "lokal":        "Lokal",
    "inne":         "Inne",
}


def _quarter(date_str: Optional[str]) -> Optional[str]:
    """`"2025-04-15"` → `"2025-Q2"`."""
    if not date_str or len(date_str) < 7:
        return None
    try:
        year = int(date_str[:4])
        month = int(date_str[5:7])
    except (ValueError, TypeError):
        return None
    q = (month - 1) // 3 + 1
    return f"{year}-Q{q}"


def _stats_from(values: list[float]) -> dict:
    """Mean/median/min/max/stdev + CV (odporne na pusty zbiór i N=1)."""
    if not values:
        return {"n": 0}
    n = len(values)
    mean = statistics.mean(values)
    median = statistics.median(values)
    mn = min(values)
    mx = max(values)
    stdev = statistics.stdev(values) if n > 1 else 0.0
    cv = (stdev / mean) if mean else None
    return {
        "n": n,
        "mean": round(mean, 2),
        "median": round(median, 2),
        "min": round(mn, 2),
        "max": round(mx, 2),
        "stdev": round(stdev, 2),
        "cv": round(cv, 3) if cv is not None else None,
    }


def _load_rows(conn: sqlite3.Connection, ids: list[str]) -> list[dict]:
    """Wiersze transakcji dla analizy (Σ Analiza + wtyczki): JOIN tx_cache
    (area_m2/centroid już policzone) + pola pochodne `cena` i `cena_m2`."""
    rows: list[dict] = []
    for i in range(0, len(ids), 800):
        chunk = ids[i : i + 800]
        ph = ",".join("?" * len(chunk))
        cur = conn.execute(
            f"""
            SELECT
                t.id_rcn,
                t.data_transakcji,
                t.cena_transakcji_brutto,
                t.rodzaj_nieruchomosci,
                t.rodzaj_rynku,
                t.rodzaj_transakcji,
                t.kwota_vat AS stawka_vat,
                tc.area_m2,
                tc.obreb,
                tc.adres,
                tc.miejscowosc,
                tc.centroid_lon,
                tc.centroid_lat
            FROM transakcje t
            LEFT JOIN tx_cache tc USING (id_rcn)
            WHERE t.id_rcn IN ({ph})
              AND COALESCE(t.status, 'aktywna') = 'aktywna'
            """,
            chunk,
        )
        rows.extend([dict(r) for r in cur.fetchall()])
    for r in rows:
        cena = r.get("cena_transakcji_brutto")
        area = r.get("area_m2")
        r["cena"] = cena
        r["cena_m2"] = (cena / area) if (cena is not None and area is not None and area > 0) else None
    return rows


@router.post("/{workspace_id}/analysis")
def workspace_analysis(
    workspace_id: str,
    body: AnalysisRequest,
    _: str = Depends(require_auth),
) -> dict:
    _require_workspace(workspace_id)
    ids = list({i for i in body.id_rcn_in if i})
    if not ids:
        raise HTTPException(status_code=400, detail="id_rcn_in must not be empty")
    if len(ids) > MAX_IDS:
        ids = ids[:MAX_IDS]

    conn = open_workspace(_workspace_db(workspace_id))
    try:
        rows = _load_rows(conn, ids)
    finally:
        conn.close()

    # --- grupowanie w Pythonie ---
    # Podział po znormalizowanym typie (grunt niezab./zab./budynek/lokal/inne).
    # Cena/m² liczymy tylko dla rekordów z `area_m2 > 0`.
    cena_by_kind: dict[str, list[float]] = defaultdict(list)
    cena_m2_by_kind: dict[str, list[float]] = defaultdict(list)
    area_by_kind: dict[str, list[float]] = defaultdict(list)

    cena_by_rynek: dict[str, list[float]] = defaultdict(list)
    cena_m2_by_rynek: dict[str, list[float]] = defaultdict(list)
    vat_by_rynek: dict[str, list[float]] = defaultdict(list)

    trans_kind_count: dict[str, int] = defaultdict(int)
    obreb_count: dict[str, int] = defaultdict(int)
    obreb_m2: dict[str, list[float]] = defaultdict(list)

    # Kwartalna seria -- per typ × kwartał.
    quarterly: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    scatter: list[dict] = []

    for r in rows:
        kind = _normalize_kind(r.get("rodzaj_nieruchomosci"))
        cena = r.get("cena_transakcji_brutto")
        area = r.get("area_m2")
        rynek = (r.get("rodzaj_rynku") or "").strip() or None
        rodzaj_tx = (r.get("rodzaj_transakcji") or "").strip() or None
        obreb = (r.get("obreb") or "").strip() or None
        vat = r.get("stawka_vat")

        if cena is not None:
            cena_by_kind[kind].append(cena)
            if rynek:
                cena_by_rynek[rynek].append(cena)
        if area is not None and area > 0:
            area_by_kind[kind].append(area)
            if cena is not None:
                cena_m2 = cena / area
                cena_m2_by_kind[kind].append(cena_m2)
                if rynek:
                    cena_m2_by_rynek[rynek].append(cena_m2)
                q = _quarter(r.get("data_transakcji"))
                if q:
                    quarterly[kind][q].append(cena_m2)
                scatter.append({
                    "id_rcn": r["id_rcn"],
                    "data": r.get("data_transakcji"),
                    "cena_m2": round(cena_m2, 2),
                    "area_m2": round(area, 2),
                    "cena": cena,
                    "kind": kind,
                    "adres": r.get("adres"),
                    "obreb": obreb,
                })
        if rodzaj_tx:
            trans_kind_count[rodzaj_tx] += 1
        if obreb:
            obreb_count[obreb] += 1
            if area is not None and area > 0 and cena is not None:
                obreb_m2[obreb].append(cena / area)
        if rynek and vat is not None:
            vat_by_rynek[rynek].append(vat)

    # --- budowanie odpowiedzi ---
    stats_by_kind = []
    for k in ("grunt_niezab", "grunt_zab", "budynek", "lokal", "inne"):
        if k not in cena_by_kind and k not in area_by_kind:
            continue
        entry = {
            "kind": k,
            "label": _KIND_LABEL[k],
            "cena": _stats_from(cena_by_kind.get(k, [])),
            "cena_m2": _stats_from(cena_m2_by_kind.get(k, [])),
            "area": _stats_from(area_by_kind.get(k, [])),
        }
        stats_by_kind.append(entry)
    # "Wszystkie" -- bez cena_m2 (mieszanie jabłek z gruszkami -- rzeczoznawca
    # powinien to widzieć tylko świadomie).
    all_cen = [c for values in cena_by_kind.values() for c in values]
    stats_by_kind.append({
        "kind": "all",
        "label": "Wszystkie łącznie",
        "cena": _stats_from(all_cen),
        "cena_m2": None,
        "area": None,
    })

    per_rynek = []
    for r_name, cena_list in sorted(cena_by_rynek.items()):
        entry = {
            "rynek": r_name,
            "cena": _stats_from(cena_list),
            "cena_m2": _stats_from(cena_m2_by_rynek.get(r_name, [])),
            "avg_vat_rate": round(statistics.mean(vat_by_rynek[r_name]), 2) if vat_by_rynek.get(r_name) else None,
        }
        per_rynek.append(entry)

    total_tx = len(rows)
    per_rodzaj_tx = [
        {
            "rodzaj": name,
            "count": cnt,
            "pct": round(100.0 * cnt / total_tx, 1) if total_tx else 0,
        }
        for name, cnt in sorted(trans_kind_count.items(), key=lambda x: -x[1])
    ]

    top_obreb = sorted(
        [
            {
                "obreb": name,
                "count": cnt,
                "median_cena_m2": round(statistics.median(obreb_m2[name]), 2) if obreb_m2.get(name) else None,
            }
            for name, cnt in obreb_count.items()
        ],
        key=lambda x: -x["count"],
    )[:5]

    # Kwartalna seria -- konsoliduj do [{q, kind, median_cena_m2, n}], sort po dacie.
    quarterly_list: list[dict] = []
    for kind, by_q in quarterly.items():
        for q, vals in by_q.items():
            quarterly_list.append({
                "q": q,
                "kind": kind,
                "kind_label": _KIND_LABEL[kind],
                "median_cena_m2": round(statistics.median(vals), 2),
                "n": len(vals),
            })
    quarterly_list.sort(key=lambda x: (x["q"], x["kind"]))

    return {
        "workspace_id": workspace_id,
        "total_requested": len(body.id_rcn_in),
        "total_matched": total_tx,
        "capped": len(body.id_rcn_in) > MAX_IDS,
        "stats_by_kind": stats_by_kind,
        "per_rynek": per_rynek,
        "per_rodzaj_transakcji": per_rodzaj_tx,
        "top_obreb": top_obreb,
        "quarterly": quarterly_list,
        "scatter": scatter[:5000],  # hard cap na Chart.js perf
    }
