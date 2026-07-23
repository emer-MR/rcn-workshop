"""Data quality flags dla transakcji RCN.

Polityka (2026-04-29): NIE modyfikujemy danych źródłowych GML, tylko flagujemy
podejrzane transakcje. Rzeczoznawca decyduje. Sygnały wizualne w UI (badge ⚠
+ tooltip + filter) ostrzegają przed użyciem do wyceny porównawczej.

6 reguł:
1. no_objects -- liczba_obiektow=0 AND cena_brutto>0
2. multi_object_act -- 1 dokument, >1 transakcji z tą samą ceną (operator
   wpisał total aktu na każdą tx zamiast podzielić)
3. extreme_price_per_m2 -- cena/area BARDZO WYSOKA lub BARDZO NISKA per typ:
     lokal:   high=30 000 zł/m², low=300 zł/m²    (low = high/100)
     budynek: high=50 000 zł/m², low=500 zł/m²    (low = high/100)
     grunt:   high=5 000 zł/m², low=5 zł/m²       (low = high/1000 -- bo
              grunty rolne typowo 10-30 zł/m², próg 50 łapałby je jako
              "extreme low" -- false positives. 5 zł/m² łapie tylko
              darowizny/symboliczne)
4. zero_area -- area_m2 IS NULL OR =0 AND cena_brutto>0
5. total_price_split_suspect -- SUM(cena_brutto child) << cena_tx_brutto
   (operator wpisał total aktu w cena_tx, ale child rows mają indywidualne
   ceny -- np. 18A6CE4 lokal 756 450 zł vs 756 450 000 cena_tx)
6. zero_or_null_price -- cena_transakcji_brutto IS NULL OR <= 0
   (darowizny, zniesienie współwłasności, bugi operatora -- niemożliwa
   wycena porównawcza)

Thresholdy hardcoded; TODO: configurable per workspace przez workspace_meta.
"""
from __future__ import annotations
import json
import sqlite3
from typing import Optional


# Domyślne thresholdy. Per-workspace override przez workspace_meta klucz
# 'quality_thresholds_json' (Admin → ⚙ Ustawienia jakości).
DEFAULT_THRESHOLDS = {
    # extreme_price_per_m2 high (>X = anomalia, np. apartament 30k zł/m²)
    "lokal_high": 30_000.0,
    "budynek_high": 50_000.0,
    "grunt_high": 5_000.0,
    # extreme_price_per_m2 low (<high/ratio = symboliczne, darowizny, bug skali ÷100)
    # Grunt rolny realnie 10-30 zł/m², więc /1000 (=5 zł/m²) zamiast /100 (=50).
    "lokal_low_ratio": 100.0,
    "budynek_low_ratio": 100.0,
    "grunt_low_ratio": 1000.0,
    # total_price_split_suspect: cena_tx vs SUM(child cen). >ratio× sumy -> flag.
    "split_suspect_ratio": 2.0,
}


def load_thresholds(conn) -> dict:
    """Wczytaj thresholdy per-workspace z workspace_meta z fallbackiem do default.
    Brakujące klucze uzupełniane defaultami (forward-compat dla nowych reguł)."""
    import sqlite3
    try:
        row = conn.execute(
            "SELECT value FROM workspace_meta WHERE key = 'quality_thresholds_json'"
        ).fetchone()
        if row and row[0]:
            user = json.loads(row[0])
            merged = dict(DEFAULT_THRESHOLDS)
            for k, v in user.items():
                if k in DEFAULT_THRESHOLDS:
                    try:
                        merged[k] = float(v)
                    except (TypeError, ValueError):
                        pass
            return merged
    except (sqlite3.Error, json.JSONDecodeError):
        pass
    return dict(DEFAULT_THRESHOLDS)


def save_thresholds(conn, thresholds: dict) -> None:
    """Zapisz thresholdy do workspace_meta. Ignoruje nieznane klucze."""
    cleaned = {}
    for k, v in thresholds.items():
        if k in DEFAULT_THRESHOLDS:
            try:
                cleaned[k] = float(v)
            except (TypeError, ValueError):
                pass
    conn.execute(
        "INSERT INTO workspace_meta (key, value) VALUES ('quality_thresholds_json', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (json.dumps(cleaned, ensure_ascii=False),),
    )
    conn.commit()


def compute_auto_thresholds(
    conn,
    percentile_low: int = 5,
    percentile_high: int = 95,
    min_samples: int = 10,
) -> dict:
    """Auto-detect quality thresholds z faktycznych danych workspace.

    Dla każdego typu (lokal/budynek/grunt) liczy percentyle ceny/m² z transakcji
    z tx_cache (gdzie area_m2 > 0 i cena > 0). Zwraca dict z 6 kluczami
    (lokal_high, lokal_low_ratio, budynek_high, budynek_low_ratio,
    grunt_high, grunt_low_ratio) + meta `_diagnostics` z liczbą próbek per typ.

    Default: percentyle 5/95 oznaczone jako extreme. high zaokrąglony w górę
    do najbliższej 100 (grunt) lub 1000 (lokal/budynek). low_ratio = high/p_low
    zaokrąglone w dół (więcej entry pasuje jako OK = mniej false positives).

    Brak próbek (<min_samples) per typ → dla tego typu zostaje DEFAULT.
    Sieradz przy P5/P95 dla gruntu daje ~1-50 zł/m², zamiast default 5-5000.
    """
    out = dict(DEFAULT_THRESHOLDS)
    diagnostics: dict[str, dict] = {}

    type_map = {
        "lokal": "%okal%",
        "budynek": "%udynk%",
        "grunt": "%runt%",
    }
    for typ, pattern in type_map.items():
        rows = conn.execute(
            """
            SELECT t.cena_transakcji_brutto * 1.0 / NULLIF(tc.area_m2, 0) AS pm2
            FROM transakcje t
            JOIN tx_cache tc ON t.id_rcn = tc.id_rcn
            WHERE t.cena_transakcji_brutto > 0
              AND tc.area_m2 > 0
              AND t.rodzaj_nieruchomosci LIKE ?
              AND t.cena_transakcji_brutto * 1.0 / tc.area_m2 IS NOT NULL
            ORDER BY pm2 ASC
            """,
            (pattern,),
        ).fetchall()
        n = len(rows)
        if n < min_samples:
            diagnostics[typ] = {"samples": n, "skipped": True}
            continue

        # Percentile via ordinal (no numpy dependency)
        low_idx = max(0, int(n * percentile_low / 100) - 1)
        high_idx = min(n - 1, int(n * percentile_high / 100) - 1)
        low_val = float(rows[low_idx][0])
        high_val = float(rows[high_idx][0])

        # Round high up. Grunt ma niższy zakres (typowo 1-100 zł/m²) → krok 100.
        # Lokal/budynek zazwyczaj kilka tysięcy zł/m² → krok 1000.
        round_step = 100 if typ == "grunt" else 1000
        high_rounded = float(((int(high_val) // round_step) + 1) * round_step)
        # low_ratio = high / low_value. floor zł/m² = high_rounded / low_ratio.
        # Round half-up (zwykłe zaokrąglenie). Sanity cap 10000.
        # BUG FIX: poprzednio "int(x // 10) * 10" dawał 0 dla x<10 (np. lokal
        # Sieradza: 7.28 → 0 → fallback 1 → floor=high czyli WSZYSTKO extreme).
        low_ratio = max(1.0, round(high_rounded / max(low_val, 0.0001)))
        low_ratio = min(10000.0, low_ratio)

        out[f"{typ}_high"] = high_rounded
        out[f"{typ}_low_ratio"] = low_ratio
        diagnostics[typ] = {
            "samples": n,
            "p_low_value": round(low_val, 2),
            "p_high_value": round(high_val, 2),
            "high_rounded": high_rounded,
            "low_ratio": low_ratio,
            "low_floor_zlm2": round(high_rounded / low_ratio, 2),
        }

    out["_diagnostics"] = diagnostics
    out["_percentile_low"] = percentile_low
    out["_percentile_high"] = percentile_high
    return out


def _classify_typ(rodzaj: Optional[str]) -> str:
    """Mapuj rodzaj_nieruchomosci na bucket: 'lokal'|'budynek'|'grunt'|'unknown'."""
    if not rodzaj:
        return "unknown"
    r = rodzaj.lower()
    if "okal" in r:
        return "lokal"
    if "udynk" in r:
        return "budynek"
    if "runt" in r:
        return "grunt"
    return "unknown"


def compute_flags(
    conn: sqlite3.Connection,
    id_rcn_list: Optional[list[str]] = None,
) -> dict[str, int]:
    """Oblicz `data_quality_flags` dla transakcji i zapisz w DB.

    id_rcn_list=None -> recompute dla wszystkich (offline tool).
    id_rcn_list=[...] -> recompute dla podanych + tych z tym samym dokumentem
    (bo multi_object_act wymaga lookup grupy aktu, który mógł się zmienić
    przez delta-import).

    Zwraca dict z licznikami flag.
    """
    cur = conn.cursor()

    # Rozszerz id_rcn_list o transakcje z tym samym dokumentem -- multi_object_act
    # to flag grupowy, więc nowo wstawiona tx może zmienić flag istniejących.
    if id_rcn_list is not None:
        if not id_rcn_list:
            return {}
        placeholders = ",".join("?" * len(id_rcn_list))
        affected = cur.execute(
            f"SELECT DISTINCT t2.id_rcn FROM transakcje t1 "
            f"JOIN transakcje t2 ON t1.dokument = t2.dokument "
            f"WHERE t1.id_rcn IN ({placeholders})",
            id_rcn_list,
        ).fetchall()
        target_ids = list({r[0] for r in affected} | set(id_rcn_list))
    else:
        target_ids = None  # all

    # Step 1: zbierz wszystkie transakcje + agregaty z tx_cache.
    where_clause = ""
    params: list = []
    if target_ids is not None:
        ph = ",".join("?" * len(target_ids))
        where_clause = f"WHERE t.id_rcn IN ({ph})"
        params = list(target_ids)

    rows = cur.execute(
        f"""SELECT
            t.id_rcn,
            t.cena_transakcji_brutto AS cena,
            t.liczba_obiektow AS lobj,
            t.rodzaj_nieruchomosci AS rodzaj,
            t.dokument AS dok,
            t.data_transakcji AS data_tx,
            tc.area_m2 AS area,
            tc.plot_count + tc.building_count + tc.local_count AS objs,
            (SELECT COALESCE(SUM(cena_brutto), 0) FROM plots     WHERE id_rcn = t.id_rcn)
          + (SELECT COALESCE(SUM(cena_brutto), 0) FROM buildings WHERE id_rcn = t.id_rcn)
          + (SELECT COALESCE(SUM(cena_brutto), 0) FROM locals    WHERE id_rcn = t.id_rcn) AS child_sum
        FROM transakcje t
        LEFT JOIN tx_cache tc USING (id_rcn)
        {where_clause}
        """,
        params,
    ).fetchall()

    # Step 2: zbierz "multi_object_act" set -- (dokument, cena) z >1 distinct id_rcn.
    multi_act_keys = set()
    multi_rows = cur.execute(
        """SELECT dokument, cena_transakcji_brutto, COUNT(DISTINCT id_rcn) AS n
           FROM transakcje
           WHERE dokument IS NOT NULL AND cena_transakcji_brutto IS NOT NULL
           GROUP BY dokument, cena_transakcji_brutto
           HAVING n > 1"""
    ).fetchall()
    for r in multi_rows:
        multi_act_keys.add((r[0], r[1]))

    # Step 3: ewaluuj reguły per row + UPDATE.
    counters = {
        "no_objects": 0,
        "multi_object_act": 0,
        "extreme_price_per_m2": 0,
        "zero_area": 0,
        "total_price_split_suspect": 0,
        "zero_or_null_price": 0,
        "suspicious_date": 0,
        "no_flags": 0,
    }

    # Per-workspace thresholdy (workspace_meta) lub defaults.
    thresholds = load_thresholds(conn)
    type_high = {
        "lokal": thresholds["lokal_high"],
        "budynek": thresholds["budynek_high"],
        "grunt": thresholds["grunt_high"],
    }
    type_low_ratio = {
        "lokal": thresholds["lokal_low_ratio"],
        "budynek": thresholds["budynek_low_ratio"],
        "grunt": thresholds["grunt_low_ratio"],
    }
    split_ratio = thresholds["split_suspect_ratio"]

    conn.execute("BEGIN")
    for row in rows:
        flags: list[str] = []
        cena = row["cena"]
        rodzaj = row["rodzaj"]
        area = row["area"] or 0.0
        objs = row["objs"] or 0
        dok = row["dok"]
        child_sum = row["child_sum"] or 0.0

        # Rule 6 (najpierw -- jeśli cena 0/NULL, większość pozostałych reguł
        # pomija): cena_brutto IS NULL OR <= 0. Darowizny, zniesienie
        # współwłasności, bugi operatora.
        if cena is None or cena <= 0:
            flags.append("zero_or_null_price")

        # Rule 1: no_objects (cena>0 wymagana -- inaczej zero_or_null_price wystarczy).
        if cena and cena > 0 and objs == 0:
            flags.append("no_objects")

        # Rule 2: multi_object_act
        if cena is not None and cena > 0 and dok and (dok, cena) in multi_act_keys:
            flags.append("multi_object_act")

        # Rule 3: extreme_price_per_m2 -- HIGH (>threshold) lub LOW (<threshold/ratio).
        if cena and cena > 0 and area > 0:
            ppm2 = cena / area
            bucket = _classify_typ(rodzaj)
            threshold = type_high.get(bucket)
            if threshold is not None:
                low_ratio = type_low_ratio.get(bucket, 100.0)
                if ppm2 > threshold or ppm2 < (threshold / low_ratio):
                    flags.append("extreme_price_per_m2")

        # Rule 4: zero_area
        if cena and cena > 0 and (area is None or area == 0):
            flags.append("zero_area")

        # Rule 5: total_price_split_suspect -- jeśli child rows mają sumę cen
        # >0 i cena_tx jest >= 2× tej sumy, podejrzane.
        if cena and cena > 0 and child_sum > 0 and cena / child_sum >= split_ratio:
            flags.append("total_price_split_suspect")

        # Rule 7: suspicious_date -- data_transakcji poza realnym zakresem
        # (typo operatora w dataSporzadzeniaDokumentu, np. "3007-04-05" zamiast
        # "2007-04-05"). 1990-01-01 jako dolna granica (RCN startuje od 2010+,
        # ale starsze akty mogą być wpisywane retroaktywnie); 2030-12-31 jako
        # gorna granica (forward-looking margines vs current_year).
        data_tx = row["data_tx"]
        if data_tx and (data_tx < "1990-01-01" or data_tx > "2030-12-31"):
            flags.append("suspicious_date")

        flags_json = json.dumps(flags, ensure_ascii=False) if flags else None
        cur.execute(
            "UPDATE transakcje SET data_quality_flags = ? WHERE id_rcn = ?",
            (flags_json, row["id_rcn"]),
        )

        if flags:
            for f in flags:
                counters[f] = counters.get(f, 0) + 1
        else:
            counters["no_flags"] += 1

    conn.commit()
    return counters
