"""Regression test for the idempotent schema migration.

Scenario: an older workspace DB (pre-2026-04-20) lacks `rodzaj_nieruchomosci`
on `transakcje` and `obreb` on `plots` / `buildings`. Opening such a DB used
to crash in `apply_schema`'s DDL (`CREATE INDEX ... ON transakcje(rodzaj_nieruchomosci)`)
before `_migrate()` had a chance to add the column. This test builds a minimal
legacy DB, runs the current `apply_schema`, and checks that all columns and
indexes exist afterwards.
"""
import json
import sqlite3

from rcn_core.schema import apply_schema


LEGACY_DDL = """
CREATE TABLE imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename TEXT NOT NULL,
    stored_filename   TEXT NOT NULL,
    file_hash         TEXT NOT NULL,
    file_size_bytes   INTEGER NOT NULL DEFAULT 0,
    tryb              TEXT NOT NULL CHECK (tryb IN ('snapshot','delta')),
    upload_timestamp  INTEGER NOT NULL,
    parser_epsg       TEXT,
    transaction_count INTEGER NOT NULL DEFAULT 0,
    plot_count        INTEGER NOT NULL DEFAULT 0,
    building_count    INTEGER NOT NULL DEFAULT 0,
    local_count       INTEGER NOT NULL DEFAULT 0,
    inserted_count    INTEGER NOT NULL DEFAULT 0,
    updated_count     INTEGER NOT NULL DEFAULT 0,
    skipped_count     INTEGER NOT NULL DEFAULT 0,
    duration_s        REAL,
    status            TEXT NOT NULL DEFAULT 'processing',
    notes             TEXT,
    diagnostics_json  TEXT
);

-- Legacy transakcje (no rodzaj_nieruchomosci).
CREATE TABLE transakcje (
    id_rcn                 TEXT PRIMARY KEY,
    source_import_id       INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    import_timestamp       INTEGER NOT NULL,
    data_transakcji        TEXT,
    cena_transakcji_brutto REAL,
    rodzaj_transakcji      TEXT,
    rodzaj_rynku           TEXT,
    dokument               TEXT,
    tworca_dokumentu       TEXT,
    strona_sprzedajaca     TEXT,
    strona_kupujaca        TEXT,
    liczba_obiektow        INTEGER NOT NULL DEFAULT 0,
    attributes_json        TEXT
);

-- Legacy plots / buildings (no obreb).
CREATE TABLE plots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    id_rcn                TEXT NOT NULL REFERENCES transakcje(id_rcn) ON DELETE CASCADE,
    source_import_id      INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    identyfikator_dzialki TEXT,
    teryt_gminy           TEXT,
    miejscowosc           TEXT,
    adres                 TEXT,
    powierzchnia_m2       REAL,
    cena_brutto           REAL,
    wkt                   TEXT,
    centroid_lon          REAL,
    centroid_lat          REAL,
    attributes_json       TEXT
);

CREATE TABLE buildings (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    id_rcn                TEXT NOT NULL REFERENCES transakcje(id_rcn) ON DELETE CASCADE,
    source_import_id      INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    identyfikator_budynku TEXT,
    teryt_gminy           TEXT,
    miejscowosc           TEXT,
    adres                 TEXT,
    rodzaj_budynku        TEXT,
    pow_uzytkowa          REAL,
    cena_brutto           REAL,
    wkt                   TEXT,
    centroid_lon          REAL,
    centroid_lat          REAL,
    attributes_json       TEXT
);
"""


def _has_column(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _has_index(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def test_migration_on_legacy_db_adds_columns_and_indexes(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(LEGACY_DDL)
    conn.execute(
        "INSERT INTO imports(original_filename, stored_filename, file_hash, tryb, upload_timestamp) "
        "VALUES (?,?,?,?,?)",
        ("legacy.gml", "legacy.gml", "deadbeef", "snapshot", 1712000000),
    )
    conn.execute(
        "INSERT INTO transakcje(id_rcn, source_import_id, import_timestamp, attributes_json) "
        "VALUES (?,?,?,?)",
        ("T1", 1, 1712000000, json.dumps({
            "rodzaj nier.": "Nieruchomosc Lokalowa",
            "kwota podatku VAT": "23000.50",
        })),
    )
    conn.execute(
        "INSERT INTO plots(id_rcn, source_import_id, identyfikator_dzialki, attributes_json) "
        "VALUES (?,?,?,?)",
        ("T1", 1, "106201_1.G-42.100/1", json.dumps({"dz. - kwota vat": "1500.0"})),
    )
    conn.execute(
        "INSERT INTO buildings(id_rcn, source_import_id, identyfikator_budynku, attributes_json) "
        "VALUES (?,?,?,?)",
        ("T1", 1, "106201_1.G-42.1", json.dumps({"bud. - kwota vat": "2500.0"})),
    )
    conn.commit()

    apply_schema(conn)

    assert _has_column(conn, "transakcje", "rodzaj_nieruchomosci")
    assert _has_column(conn, "plots", "obreb")
    assert _has_column(conn, "buildings", "obreb")
    assert _has_column(conn, "imports", "stage")
    assert _has_column(conn, "imports", "progress_pct")
    assert _has_column(conn, "imports", "error_msg")
    # Kolumny kwota_vat w 4 tabelach (2026-04-23)
    assert _has_column(conn, "transakcje", "kwota_vat")
    assert _has_column(conn, "plots", "kwota_vat")
    assert _has_column(conn, "buildings", "kwota_vat")
    assert _has_column(conn, "locals", "kwota_vat")

    assert _has_index(conn, "idx_tx_rodzaj")
    assert _has_index(conn, "idx_plots_obreb")
    assert _has_index(conn, "idx_buildings_obreb")

    row = conn.execute(
        "SELECT rodzaj_nieruchomosci, kwota_vat FROM transakcje WHERE id_rcn = 'T1'"
    ).fetchone()
    assert row[0] == "Nieruchomosc Lokalowa"
    assert row[1] == 23000.50

    # Backfill VAT z attributes_json w plots/buildings
    assert conn.execute("SELECT kwota_vat FROM plots WHERE id_rcn = 'T1'").fetchone()[0] == 1500.0
    assert conn.execute("SELECT kwota_vat FROM buildings WHERE id_rcn = 'T1'").fetchone()[0] == 2500.0

    conn.close()


def test_migration_is_idempotent(tmp_path):
    """apply_schema should be safe to run multiple times."""
    db_path = tmp_path / "fresh.db"
    conn = sqlite3.connect(db_path)
    apply_schema(conn)
    apply_schema(conn)
    apply_schema(conn)

    assert _has_column(conn, "transakcje", "rodzaj_nieruchomosci")
    assert _has_index(conn, "idx_tx_rodzaj")
    conn.close()
