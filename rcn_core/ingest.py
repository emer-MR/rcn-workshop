"""Ingest a parsed GML file into a workspace SQLite database.

Upsert policy is "newer wins" by `import_timestamp`. Child rows
(plots/buildings/locals) are replaced wholesale when a transakcja is upserted
from a newer import. Older imports are silently skipped.

Performance notes:
- Parse is the heavy phase (70-90% of wall time). lxml parser (when available)
  is ~2-3x faster than stdlib ElementTree — we use whichever is importable.
- DB writes use `executemany` on batched tuple lists instead of a per-row
  `execute`, which on 100k+ rows is ~5-10x faster.
- `progress_callback(stage, pct, msg)` fires during both parse and DB writes
  so the caller (typically a FastAPI BackgroundTask) can update `imports.progress_pct`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

_OBREB_RE = re.compile(r"^[0-9]+_[0-9]+\.([^.]+)\.")
BATCH = 5000


def _extract_obreb(ident: str | None) -> str | None:
    if not ident:
        return None
    m = _OBREB_RE.match(ident)
    return m.group(1) if m else None


from rcn_core.geo import centroid_4326, epsg_int, teryt_gminy_from_dzialka
from rcn_core.obreby import name_for as obreb_name_for
from rcn_core.parser import RcnGmlParser
from rcn_core.schema import apply_schema, refresh_tx_cache


ProgressCallback = Callable[[str, int, str], None]


@dataclass
class ImportResult:
    import_id: int
    original_filename: str
    stored_filename: str
    file_hash: str
    tryb: str
    transaction_count: int
    plot_count: int
    building_count: int
    local_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    withdrawn_count: int = 0  # liczba transakcji oznaczonych `wycofana_z_portalu` w tym snapshot-imporcie
    data_transakcji_od: str | None = None
    data_transakcji_do: str | None = None
    duration_s: float = 0.0
    parser_epsg: str = ""
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def compute_file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_workspace(db_path: Path) -> sqlite3.Connection:
    """Open SQLite + apply schema (migracje + teardown SpatiaLite v8).
    Zapytania przestrzenne liczone czystym Pythonem -- bez rozszerzeń."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    apply_schema(conn)
    return conn


def create_import_record(
    db_path: Path,
    *,
    original_filename: str,
    stored_filename: str,
    file_size_bytes: int,
    tryb: str,
) -> int:
    """Insert a 'processing' import row immediately so the client can poll
    progress while the background task does the actual work.

    `file_hash` starts as empty string (older workspaces have a NOT NULL
    constraint on this column); ingest_gml updates it with the real SHA-256
    once the hash is computed."""
    conn = open_workspace(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO imports (
                original_filename, stored_filename, file_hash, file_size_bytes,
                tryb, upload_timestamp, status, stage, progress_pct
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                original_filename,
                stored_filename,
                "",
                file_size_bytes,
                tryb,
                int(time.time()),
                "processing",
                "queued",
                0,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_import_progress(
    db_path: Path,
    import_id: int,
    *,
    stage: Optional[str] = None,
    progress_pct: Optional[int] = None,
    status: Optional[str] = None,
    error_msg: Optional[str] = None,
) -> None:
    updates = []
    params: list = []
    if stage is not None:
        updates.append("stage = ?")
        params.append(stage)
    if progress_pct is not None:
        updates.append("progress_pct = ?")
        params.append(max(0, min(100, int(progress_pct))))
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if error_msg is not None:
        updates.append("error_msg = ?")
        params.append(error_msg)
    if not updates:
        return
    params.append(import_id)
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute(f"UPDATE imports SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def ingest_gml(
    db_path: Path,
    gml_path: Path,
    *,
    original_filename: str,
    stored_filename: str,
    tryb: Literal["snapshot", "delta"] = "snapshot",
    import_id: Optional[int] = None,
    now: float | None = None,
    progress: Optional[ProgressCallback] = None,
) -> ImportResult:
    """Parse and ingest a GML. If `import_id` is given, updates that row;
    otherwise inserts a new one (legacy synchronous path)."""
    t0 = time.time()
    now = now if now is not None else t0
    import_ts = int(now)

    def emit(stage: str, pct: int, msg: str = "") -> None:
        if progress:
            try:
                progress(stage, pct, msg)
            except Exception:
                pass  # progress is best-effort

    emit("hashing", 1, "Liczenie hash pliku")
    file_size = gml_path.stat().st_size
    file_hash = compute_file_hash(gml_path)

    # Parser progress → global pct (parsing covers 2-78% of total progress)
    def parser_cb(stage: str, cur: int, total: int, msg: str) -> None:
        total = max(total, 1)
        if stage == "start":
            pct = 3
        elif stage == "scan":
            pct = 3 + int(25 * cur / total)
        elif stage == "transactions":
            pct = 28 + int(50 * cur / total)
        elif stage == "done":
            pct = 78
        else:
            return
        emit(stage, pct, msg)

    emit("parsing", 3, "Parsowanie GML")
    parser = RcnGmlParser()
    parsed = parser.parse(str(gml_path), progress_callback=parser_cb)
    source_epsg_int = epsg_int(parsed.epsg) or 2180

    conn = open_workspace(db_path)
    try:
        cur = conn.cursor()

        if import_id is None:
            cur.execute(
                """
                INSERT INTO imports (
                    original_filename, stored_filename, file_hash, file_size_bytes,
                    tryb, upload_timestamp, parser_epsg,
                    transaction_count, plot_count, building_count, local_count,
                    status, stage, progress_pct
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    original_filename,
                    stored_filename,
                    file_hash,
                    file_size,
                    tryb,
                    import_ts,
                    parsed.epsg,
                    parsed.transaction_count,
                    len(parsed.plot_rows),
                    len(parsed.building_rows),
                    len(parsed.local_rows),
                    "processing",
                    "ingest",
                    80,
                ),
            )
            import_id = cur.lastrowid
        else:
            cur.execute(
                """
                UPDATE imports SET
                    file_hash         = ?,
                    file_size_bytes   = ?,
                    parser_epsg       = ?,
                    transaction_count = ?,
                    plot_count        = ?,
                    building_count    = ?,
                    local_count       = ?,
                    stage             = 'ingest',
                    progress_pct      = 80
                WHERE id = ?
                """,
                (
                    file_hash,
                    file_size,
                    parsed.epsg,
                    parsed.transaction_count,
                    len(parsed.plot_rows),
                    len(parsed.building_rows),
                    len(parsed.local_rows),
                    import_id,
                ),
            )
        conn.commit()

        # Group parsed data by id_rcn
        summary_by_id: dict[str, dict] = {}
        for row in parsed.summary_rows:
            id_rcn = row.get("id_RCN")
            if id_rcn and id_rcn not in summary_by_id:
                summary_by_id[id_rcn] = row

        plots_by_id: dict[str, list] = {}
        for row in parsed.plot_rows:
            id_rcn = row.get("id_RCN")
            if id_rcn:
                plots_by_id.setdefault(id_rcn, []).append(row)

        buildings_by_id: dict[str, list] = {}
        for row in parsed.building_rows:
            id_rcn = row.get("id_RCN")
            if id_rcn:
                buildings_by_id.setdefault(id_rcn, []).append(row)

        locals_by_id: dict[str, list] = {}
        for row in parsed.local_rows:
            id_rcn = row.get("id_RCN")
            if id_rcn:
                locals_by_id.setdefault(id_rcn, []).append(row)

        emit("classify", 82, f"Klasyfikacja {len(summary_by_id)} transakcji")

        ids = list(summary_by_id.keys())
        existing_ts: dict[str, int] = {}
        for i in range(0, len(ids), 800):
            chunk = ids[i : i + 800]
            ph = ",".join("?" * len(chunk))
            rows = cur.execute(
                f"SELECT id_rcn, import_timestamp FROM transakcje WHERE id_rcn IN ({ph})",
                chunk,
            ).fetchall()
            for r in rows:
                existing_ts[r["id_rcn"]] = r["import_timestamp"]

        tx_rows: list[tuple] = []
        affected_ids: list[str] = []
        inserted = 0
        updated = 0
        skipped = 0

        for id_rcn, summary in summary_by_id.items():
            prev = existing_ts.get(id_rcn)
            if prev is not None and prev >= import_ts:
                skipped += 1
                continue

            liczba_obj = (
                len(plots_by_id.get(id_rcn, []))
                + len(buildings_by_id.get(id_rcn, []))
                + len(locals_by_id.get(id_rcn, []))
            ) or 1

            tx_rows.append((
                id_rcn,
                import_id,
                import_ts,
                summary.get("data transakcji"),
                _num(summary.get("cena transakcji brutto")),
                _num(summary.get("kwota podatku VAT")),
                summary.get("rodzaj transakcji"),
                summary.get("rodzaj rynku"),
                summary.get("rodzaj nier."),
                summary.get("dokument"),
                summary.get("twórca dokumentu"),
                summary.get("Strona sprzedająca"),
                summary.get("Strona kupująca"),
                liczba_obj,
                _attrs_json(summary),
            ))
            affected_ids.append(id_rcn)
            if prev is None:
                inserted += 1
            else:
                updated += 1

        emit("upsert", 85, f"UPSERT {len(tx_rows)} transakcji")

        # Phase 1: UPSERT transakcje (own transaction, so progress writer
        # can update imports.progress_pct between phases).
        if tx_rows:
            conn.execute("BEGIN")
            for i in range(0, len(tx_rows), BATCH):
                cur.executemany(
                    """
                    INSERT INTO transakcje (
                        id_rcn, source_import_id, import_timestamp,
                        data_transakcji, cena_transakcji_brutto, kwota_vat,
                        rodzaj_transakcji, rodzaj_rynku, rodzaj_nieruchomosci,
                        dokument, tworca_dokumentu,
                        strona_sprzedajaca, strona_kupujaca,
                        liczba_obiektow, attributes_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id_rcn) DO UPDATE SET
                        source_import_id       = excluded.source_import_id,
                        import_timestamp       = excluded.import_timestamp,
                        data_transakcji        = excluded.data_transakcji,
                        cena_transakcji_brutto = excluded.cena_transakcji_brutto,
                        kwota_vat              = excluded.kwota_vat,
                        rodzaj_transakcji      = excluded.rodzaj_transakcji,
                        rodzaj_rynku           = excluded.rodzaj_rynku,
                        rodzaj_nieruchomosci   = excluded.rodzaj_nieruchomosci,
                        dokument               = excluded.dokument,
                        tworca_dokumentu       = excluded.tworca_dokumentu,
                        strona_sprzedajaca     = excluded.strona_sprzedajaca,
                        strona_kupujaca        = excluded.strona_kupujaca,
                        liczba_obiektow        = excluded.liczba_obiektow,
                        attributes_json        = excluded.attributes_json
                    """,
                    tx_rows[i : i + BATCH],
                )
            conn.commit()

        # Phase 2: DELETE stale children (own transaction)
        emit("cleanup", 88, "Usuwanie starych obiektów")
        if affected_ids:
            conn.execute("BEGIN")
            for i in range(0, len(affected_ids), 800):
                chunk = affected_ids[i : i + 800]
                ph = ",".join("?" * len(chunk))
                cur.execute(f"DELETE FROM plots     WHERE id_rcn IN ({ph})", chunk)
                cur.execute(f"DELETE FROM buildings WHERE id_rcn IN ({ph})", chunk)
                cur.execute(f"DELETE FROM locals    WHERE id_rcn IN ({ph})", chunk)
            conn.commit()

        emit("children", 90, "Przygotowanie obiektów do zapisu")

        plot_rows: list[tuple] = []
        building_rows: list[tuple] = []
        local_rows: list[tuple] = []
        for id_rcn in affected_ids:
            for plot in plots_by_id.get(id_rcn, []):
                plot_rows.append(_plot_tuple(plot, id_rcn, import_id, source_epsg_int))
            for b in buildings_by_id.get(id_rcn, []):
                building_rows.append(_building_tuple(b, id_rcn, import_id, source_epsg_int))
            for lok in locals_by_id.get(id_rcn, []):
                local_rows.append(_local_tuple(lok, id_rcn, import_id, source_epsg_int))

        if plot_rows:
            emit("plots", 92, f"Zapis {len(plot_rows)} działek")
            conn.execute("BEGIN")
            for i in range(0, len(plot_rows), BATCH):
                cur.executemany(
                    """
                    INSERT INTO plots (
                        id_rcn, source_import_id,
                        identyfikator_dzialki, teryt_gminy, obreb, miejscowosc, adres,
                        powierzchnia_m2, cena_brutto, kwota_vat,
                        wkt, centroid_lon, centroid_lat, attributes_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    plot_rows[i : i + BATCH],
                )
            conn.commit()

        if building_rows:
            emit("buildings", 95, f"Zapis {len(building_rows)} budynków")
            conn.execute("BEGIN")
            for i in range(0, len(building_rows), BATCH):
                cur.executemany(
                    """
                    INSERT INTO buildings (
                        id_rcn, source_import_id,
                        identyfikator_budynku, teryt_gminy, obreb, miejscowosc, adres,
                        rodzaj_budynku, pow_uzytkowa, cena_brutto, kwota_vat,
                        wkt, centroid_lon, centroid_lat, attributes_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    building_rows[i : i + BATCH],
                )
            conn.commit()

        if local_rows:
            emit("locals", 97, f"Zapis {len(local_rows)} lokali")
            conn.execute("BEGIN")
            for i in range(0, len(local_rows), BATCH):
                cur.executemany(
                    """
                    INSERT INTO locals (
                        id_rcn, source_import_id,
                        identyfikator_lokalu, teryt_gminy, miejscowosc, adres,
                        funkcja, pow_uzytkowa, liczba_izb, kondygnacja, cena_brutto, kwota_vat,
                        wkt, centroid_lon, centroid_lat, attributes_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    local_rows[i : i + BATCH],
                )
            conn.commit()

        # Snapshot-safe: jeśli tryb=snapshot, oznacz transakcje z zakresu dat
        # które są w bazie ale brakują w tym pliku jako wycofane z portalu.
        # Auto-wykrycie zakresu dat z summary (MIN/MAX data_transakcji w pliku).
        # Celowo NIE kasujemy rekordów -- notatki + historia zostają, można
        # odzyskać przez ponowny snapshot zawierający transakcję (ressurect).
        snapshot_date_min: str | None = None
        snapshot_date_max: str | None = None
        withdrawn_count = 0
        if tryb == "snapshot" and summary_by_id:
            dates = [s.get("data transakcji") for s in summary_by_id.values() if s.get("data transakcji")]
            if dates:
                snapshot_date_min = min(dates)
                snapshot_date_max = max(dates)
                emit("snapshot", 97, f"Snapshot: oznaczam brakujące w zakresie {snapshot_date_min} – {snapshot_date_max}")
                conn.execute("BEGIN")
                cur.execute("CREATE TEMP TABLE IF NOT EXISTS _snapshot_ids (id_rcn TEXT PRIMARY KEY)")
                cur.execute("DELETE FROM _snapshot_ids")
                cur.executemany(
                    "INSERT INTO _snapshot_ids (id_rcn) VALUES (?)",
                    [(i,) for i in summary_by_id.keys()],
                )
                withdrawn_count = cur.execute(
                    "SELECT COUNT(*) FROM transakcje "
                    "WHERE data_transakcji BETWEEN ? AND ? "
                    "AND status = 'aktywna' "
                    "AND id_rcn NOT IN (SELECT id_rcn FROM _snapshot_ids)",
                    (snapshot_date_min, snapshot_date_max),
                ).fetchone()[0]
                if withdrawn_count > 0:
                    cur.execute(
                        "UPDATE transakcje SET status = 'wycofana_z_portalu' "
                        "WHERE data_transakcji BETWEEN ? AND ? "
                        "AND status = 'aktywna' "
                        "AND id_rcn NOT IN (SELECT id_rcn FROM _snapshot_ids)",
                        (snapshot_date_min, snapshot_date_max),
                    )
                # Ressurect -- jeśli transakcja wróciła do portalu (korekta korekty).
                cur.execute(
                    "UPDATE transakcje SET status = 'aktywna' "
                    "WHERE status != 'aktywna' "
                    "AND id_rcn IN (SELECT id_rcn FROM _snapshot_ids)"
                )
                cur.execute("DELETE FROM _snapshot_ids")
                conn.commit()

        # Odśwież tx_cache dla transakcji dotkniętych przez ten import.
        # Dla małych delt (do ~5000 id_rcn) chunked refresh per id_rcn jest
        # tańszy niż full rebuild. Dla dużych snapshotów (np. Warszawa: 586k tx,
        # 1.9M obiektów) chunki po 800 prowadzą do 700+ powtarzających się
        # materializacji CTE — godziny zamiast minut. Powyżej progu robimy
        # jeden full rebuild (id_rcn_list=None), który materializuje CTE raz.
        #
        # Auto-enrich z EGIB GPKG ORAZ compute_flags wycofane z ingest_gml
        # (2026-05-01). Powód: Fiona+pyproj segfault w jednym Python procesie
        # (libproj-fiona statycznie zlinkowana, kolizja PROJ context z pyproj).
        # Nowy pipeline 2-fazowy: ingest_gml zostawia tylko niezbędne minimum
        # (parse + INSERT + refresh_tx_cache), a ulepszenia
        # (enrich_egib, compute_flags) uruchamiane są jako osobne subprocess
        # przyciskami w Settings page. Każdy subprocess = świeży PROJ context.
        if affected_ids:
            emit("cache", 98, f"Odświeżanie cache dla {len(affected_ids)} transakcji")
            conn.execute("BEGIN")
            FULL_REBUILD_THRESHOLD = 5000
            if len(affected_ids) > FULL_REBUILD_THRESHOLD:
                refresh_tx_cache(conn, id_rcn_list=None)
            else:
                # Chunki po 800, żeby nie przekroczyć limitu parametrów SQLite (domyślnie 999).
                for i in range(0, len(affected_ids), 800):
                    refresh_tx_cache(conn, id_rcn_list=affected_ids[i : i + 800])
            conn.commit()

        duration = round(time.time() - t0, 3)
        diagnostics_json = json.dumps(parsed.diagnostics, ensure_ascii=False, default=str)

        conn.execute("BEGIN")
        cur.execute(
            """
            UPDATE imports SET
                inserted_count      = ?,
                updated_count       = ?,
                skipped_count       = ?,
                withdrawn_count     = ?,
                data_transakcji_od  = ?,
                data_transakcji_do  = ?,
                duration_s          = ?,
                diagnostics_json    = ?,
                status              = 'success',
                stage               = 'done',
                progress_pct        = 100,
                error_msg           = NULL
            WHERE id = ?
            """,
            (
                inserted, updated, skipped, withdrawn_count,
                snapshot_date_min, snapshot_date_max,
                duration, diagnostics_json, import_id,
            ),
        )
        conn.commit()
        emit("done", 100, "Zakończono")
    except Exception:
        conn.rollback()
        raise
    finally:
        # WAL checkpoint przed close -- wymusz flush na dysk. Bez tego
        # kolejne otwarcia DB (szczególnie w osobnym procesie na Windows)
        # mogą zobaczyć "database disk image is malformed" gdy WAL nie
        # zostanie zmergowany do głównego pliku. Bezpieczne także gdy
        # journal_mode jest inny niż WAL (wtedy to no-op).
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        conn.close()

    return ImportResult(
        import_id=import_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_hash=file_hash,
        tryb=tryb,
        transaction_count=parsed.transaction_count,
        plot_count=len(parsed.plot_rows),
        building_count=len(parsed.building_rows),
        local_count=len(parsed.local_rows),
        inserted_count=inserted,
        updated_count=updated,
        skipped_count=skipped,
        withdrawn_count=withdrawn_count,
        data_transakcji_od=snapshot_date_min,
        data_transakcji_do=snapshot_date_max,
        duration_s=duration,
        parser_epsg=parsed.epsg,
        diagnostics={"summary_count": parsed.diagnostics.get("summary_count")},
    )


def _plot_tuple(plot: dict, id_rcn: str, import_id: int, source_epsg: int) -> tuple:
    wkt = plot.get("_wkt")
    centroid = centroid_4326(wkt, source_epsg) if wkt else None
    ident = plot.get("identyfikator działki")
    teryt_g = teryt_gminy_from_dzialka(ident)
    obreb_num = _extract_obreb(ident)
    obreb_display = obreb_name_for(teryt_g, obreb_num) or obreb_num
    return (
        id_rcn,
        import_id,
        ident,
        teryt_g,
        obreb_display,
        plot.get("dz. - miejscowość"),
        plot.get("dz. - adres"),
        _num(plot.get("dz. - pole pow. ewid.")),
        _num(plot.get("dz. - cena brutto")),
        _num(plot.get("dz. - kwota vat")),
        wkt,
        centroid[0] if centroid else None,
        centroid[1] if centroid else None,
        _attrs_json(plot),
    )


def _building_tuple(building: dict, id_rcn: str, import_id: int, source_epsg: int) -> tuple:
    wkt = building.get("_wkt")
    centroid = centroid_4326(wkt, source_epsg) if wkt else None
    ident_b = building.get("identyfikator budynku")
    teryt_b = teryt_gminy_from_dzialka(ident_b)
    obreb_num_b = _extract_obreb(ident_b)
    obreb_display_b = obreb_name_for(teryt_b, obreb_num_b) or obreb_num_b
    return (
        id_rcn,
        import_id,
        ident_b,
        teryt_b,
        obreb_display_b,
        building.get("bud. - miejscowość"),
        building.get("bud. - adres"),
        building.get("bud. - rodzaj bud."),
        _num(building.get("bud. - pow. uż.")),
        _num(building.get("bud. - cena brutto")),
        _num(building.get("bud. - kwota vat")),
        wkt,
        centroid[0] if centroid else None,
        centroid[1] if centroid else None,
        _attrs_json(building),
    )


def _local_tuple(local: dict, id_rcn: str, import_id: int, source_epsg: int) -> tuple:
    wkt = local.get("_wkt")
    centroid = centroid_4326(wkt, source_epsg) if wkt else None
    return (
        id_rcn,
        import_id,
        local.get("identyfikator lokalu"),
        None,
        local.get("lok. - miejscowość"),
        local.get("lok. - adres"),
        local.get("lok. - funkcja"),
        _num(local.get("lok. - pow. uż.")),
        _int(local.get("lok. - l. izb")),
        _int(local.get("lok. - kondygnacja")),
        _num(local.get("lok. - cena brutto")),
        _num(local.get("lok. - kwota vat")),
        wkt,
        centroid[0] if centroid else None,
        centroid[1] if centroid else None,
        _attrs_json(local),
    )


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _attrs_json(row: dict) -> str:
    public = {k: v for k, v in row.items() if not k.startswith("_")}
    return json.dumps(public, ensure_ascii=False, default=str)
