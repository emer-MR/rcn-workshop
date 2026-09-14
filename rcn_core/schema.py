"""SQLite schema for a single workspace.

Each workspace owns its own SQLite file. The schema is derived from the plugin's
ParsedData shape (summary_rows / plot_rows / building_rows / local_rows), not
from an abstract DDL — we store the original plugin row dicts as JSON in the
`attributes` columns and extract only the fields needed for filtering, sorting
and map rendering into dedicated columns.

Upsert policy on `transakcje.id_rcn`: newer `import_timestamp` wins; older is
skipped. Child rows (plots/buildings/locals) are replaced wholesale when a
transakcja is upserted.
"""

DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
-- Performance PRAGMAs: cache 64 MB (dla DB ~1 GB), temp w RAM (GROUP BY,
-- ORDER BY), memory-mapped I/O 256 MB. Przyspiesza skanowanie CTE tx_agg
-- (UNION ALL z plots+buildings+locals, setki tysięcy wierszy).
PRAGMA cache_size = -65536;
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 268435456;

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename TEXT NOT NULL,
    stored_filename   TEXT NOT NULL,
    file_hash         TEXT,
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
    stage             TEXT,
    progress_pct      INTEGER NOT NULL DEFAULT 0,
    error_msg         TEXT,
    notes             TEXT,
    diagnostics_json  TEXT
);

CREATE INDEX IF NOT EXISTS idx_imports_timestamp ON imports(upload_timestamp);
CREATE INDEX IF NOT EXISTS idx_imports_hash ON imports(file_hash);

CREATE TABLE IF NOT EXISTS transakcje (
    id_rcn                 TEXT PRIMARY KEY,
    source_import_id       INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    import_timestamp       INTEGER NOT NULL,
    data_transakcji        TEXT,
    cena_transakcji_brutto REAL,
    kwota_vat              REAL,
    rodzaj_transakcji      TEXT,
    rodzaj_rynku           TEXT,
    rodzaj_nieruchomosci   TEXT,
    dokument               TEXT,
    tworca_dokumentu       TEXT,
    strona_sprzedajaca     TEXT,
    strona_kupujaca        TEXT,
    liczba_obiektow        INTEGER NOT NULL DEFAULT 0,
    -- Który import oznaczył tę transakcję jako wycofaną z portalu (schema v10).
    -- Bez tego cofnięcie pomyłkowego snapshotu wymaga heurystyk po datach --
    -- patrz awaria 2026-09-12 w CLAUDE.md.
    withdrawn_by_import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
    attributes_json        TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_data ON transakcje(data_transakcji);
CREATE INDEX IF NOT EXISTS idx_tx_cena ON transakcje(cena_transakcji_brutto);
CREATE INDEX IF NOT EXISTS idx_tx_rynek ON transakcje(rodzaj_rynku);
CREATE INDEX IF NOT EXISTS idx_tx_typ ON transakcje(rodzaj_transakcji);
CREATE INDEX IF NOT EXISTS idx_tx_import ON transakcje(source_import_id);
-- idx_tx_rodzaj creates in _migrate(), because rodzaj_nieruchomosci is a migrated column
-- and DDL runs before _migrate on old DBs that lack it.

CREATE TABLE IF NOT EXISTS plots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    id_rcn                TEXT NOT NULL REFERENCES transakcje(id_rcn) ON DELETE CASCADE,
    source_import_id      INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    identyfikator_dzialki TEXT,
    teryt_gminy           TEXT,
    obreb                 TEXT,
    -- Numer obrębu z identyfikatora EGIB ("0042"), obok `obreb`, które po
    -- wzbogaceniu niesie oznaczenie urzędowe ("B-24"). Dwie kolumny, bo
    -- rzeczoznawcy szukają raz tak, raz tak -- patrz `obreb_search` w query.py.
    obreb_numer           TEXT,
    miejscowosc           TEXT,
    adres                 TEXT,
    powierzchnia_m2       REAL,
    cena_brutto           REAL,
    kwota_vat             REAL,
    wkt                   TEXT,
    centroid_lon          REAL,
    centroid_lat          REAL,
    attributes_json       TEXT
);

CREATE INDEX IF NOT EXISTS idx_plots_id_rcn ON plots(id_rcn);
CREATE INDEX IF NOT EXISTS idx_plots_teryt ON plots(teryt_gminy);
CREATE INDEX IF NOT EXISTS idx_plots_miejsc ON plots(miejscowosc);
CREATE INDEX IF NOT EXISTS idx_plots_centroid ON plots(centroid_lon, centroid_lat);
-- idx_plots_obreb creates in _migrate() (migrated column).

CREATE TABLE IF NOT EXISTS buildings (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    id_rcn                TEXT NOT NULL REFERENCES transakcje(id_rcn) ON DELETE CASCADE,
    source_import_id      INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    identyfikator_budynku TEXT,
    teryt_gminy           TEXT,
    obreb                 TEXT,
    obreb_numer           TEXT,
    miejscowosc           TEXT,
    adres                 TEXT,
    rodzaj_budynku        TEXT,
    pow_uzytkowa          REAL,
    cena_brutto           REAL,
    kwota_vat             REAL,
    wkt                   TEXT,
    centroid_lon          REAL,
    centroid_lat          REAL,
    attributes_json       TEXT
);

CREATE INDEX IF NOT EXISTS idx_buildings_id_rcn ON buildings(id_rcn);
CREATE INDEX IF NOT EXISTS idx_buildings_teryt ON buildings(teryt_gminy);
CREATE INDEX IF NOT EXISTS idx_buildings_miejsc ON buildings(miejscowosc);
CREATE INDEX IF NOT EXISTS idx_buildings_centroid ON buildings(centroid_lon, centroid_lat);
-- idx_buildings_obreb creates in _migrate() (migrated column).

CREATE TABLE IF NOT EXISTS locals (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    id_rcn                TEXT NOT NULL REFERENCES transakcje(id_rcn) ON DELETE CASCADE,
    source_import_id      INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    identyfikator_lokalu  TEXT,
    teryt_gminy           TEXT,
    -- Obręb lokalu: drugi segment identyfikatora (`106106_9.0012.620_BUD.93_LOK`)
    -- -- ten sam, co dla działki i budynku tej samej transakcji. Bez tych dwóch
    -- kolumn transakcja lokalowa bez działki i budynku nie miała obrębu wcale.
    obreb                 TEXT,
    obreb_numer           TEXT,
    miejscowosc           TEXT,
    adres                 TEXT,
    funkcja               TEXT,
    pow_uzytkowa          REAL,
    liczba_izb            INTEGER,
    kondygnacja           INTEGER,
    cena_brutto           REAL,
    kwota_vat             REAL,
    wkt                   TEXT,
    centroid_lon          REAL,
    centroid_lat          REAL,
    attributes_json       TEXT
);

CREATE INDEX IF NOT EXISTS idx_locals_id_rcn ON locals(id_rcn);
CREATE INDEX IF NOT EXISTS idx_locals_teryt ON locals(teryt_gminy);
CREATE INDEX IF NOT EXISTS idx_locals_miejsc ON locals(miejscowosc);
CREATE INDEX IF NOT EXISTS idx_locals_centroid ON locals(centroid_lon, centroid_lat);

CREATE TABLE IF NOT EXISTS workspace_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    id_rcn     TEXT NOT NULL UNIQUE REFERENCES transakcje(id_rcn) ON DELETE CASCADE,
    body       TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_id_rcn ON notes(id_rcn);

-- Materialized cache dla tx-level agregacji (adres z plots/buildings/locals,
-- centroid, counts, area). Refreshowana przy każdym ingest dla affected_ids.
-- Istnieje żeby /query i /geojson nie musiały robić multi-CTE UNION GROUP BY
-- na setkach tysięcy wierszy w czasie żądania -- to były słabe punkty przy
-- DB >100k transakcji (8-17 s per request, nie do zaakceptowania).
CREATE TABLE IF NOT EXISTS tx_cache (
    id_rcn             TEXT PRIMARY KEY REFERENCES transakcje(id_rcn) ON DELETE CASCADE,
    miejscowosc        TEXT,
    adres              TEXT,
    obreb              TEXT,
    obreb_numer        TEXT,
    teryt_gminy        TEXT,
    centroid_lon       REAL,
    centroid_lat       REAL,
    plot_count         INTEGER NOT NULL DEFAULT 0,
    building_count     INTEGER NOT NULL DEFAULT 0,
    local_count        INTEGER NOT NULL DEFAULT 0,
    area_m2            REAL,
    first_plot_ident   TEXT,
    plot_idents_concat TEXT
);
CREATE INDEX IF NOT EXISTS idx_tx_cache_miejsc ON tx_cache(miejscowosc);
CREATE INDEX IF NOT EXISTS idx_tx_cache_obreb  ON tx_cache(obreb);
-- idx_tx_cache_obreb_numer tworzy _migrate() (kolumna migrowana, v9).
CREATE INDEX IF NOT EXISTS idx_tx_cache_centroid ON tx_cache(centroid_lon, centroid_lat);

-- Tracking ulepszeń (enrich_egib, compute_flags, geocoding) uruchamianych
-- z UI Settings page. Każde ulepszenie spawnuje subprocess; ten zapisuje
-- start (status='running') i finish (success|failed). Concurrency guard:
-- endpoint /enhancements/{op} odmawia gdy istnieje running phase_run dla
-- tego workspace+phase. Pozwala śledzić historię ulepszeń per workspace.
-- Schema v7. Ingest używa istniejącej tabeli imports — nie phase_runs.
CREATE TABLE IF NOT EXISTS phase_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phase TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at REAL NOT NULL,
    finished_at REAL,
    duration_s REAL,
    diagnostics_json TEXT,
    error_msg TEXT,
    triggered_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_phase_runs_phase_status ON phase_runs(phase, status);
CREATE INDEX IF NOT EXISTS idx_phase_runs_started ON phase_runs(started_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    body,
    content='notes',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, body) VALUES (new.id, new.body);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, body) VALUES('delete', old.id, old.body);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, body) VALUES('delete', old.id, old.body);
    INSERT INTO notes_fts(rowid, body) VALUES (new.id, new.body);
END;
"""


def _to_number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def apply_schema(conn) -> None:
    conn.executescript(DDL)
    _migrate(conn)
    conn.commit()


# Aktualna wersja schema -- zwiększaj po każdej migracji wymagającej
# kosztownego backfillu. Wartość zapisywana w workspace_meta po wykonaniu.
# 1 = pierwsza wersja z kolumną rodzaj_nieruchomosci + obreb
# 2 = kolumna kwota_vat w transakcje/plots/buildings/locals
# 3 = materialized cache table tx_cache (eliminuje slow CTE w /query)
# 4 = SpatiaLite geometry column `geom` POINT 4326 + spatial index (R-tree)
#     dla plots/buildings/locals/tx_cache. Eliminuje slow `centroid_lon BETWEEN`
#     przy bbox-aware query, odblokowuje ST_Intersects/ST_DWithin (Faza 5).
# 5 = kolumna `geom_source` w plots/buildings/locals -- pochodzenie geometrii:
#     'gml' (z RCN GML), 'egib' (lookup z EGIB GPKG po identyfikatorze EGIB),
#     'inherited' (lokal przejmuje centroid od budynku tej samej transakcji).
#     Default 'gml' dla istniejących wierszy.
# 6 = kolumna `data_quality_flags` w transakcje -- JSON array stringów flag
#     (no_objects, multi_object_act, extreme_price_per_m2, zero_area,
#     total_price_split_suspect). Wypełniana przy ingest_gml + recompute tool
#     dla istniejących DB. NULL/[] = brak flag (zweryfikowane).
# 7 = tabela `phase_runs` (tracking ulepszeń enrich_egib/compute_flags/
#     geocoding uruchamianych z Settings page jako subprocess).
#     CREATE TABLE IF NOT EXISTS w DDL -- automatyczne na otwarciu DB,
#     bump version tylko deklaratywny. Brak backfillu.
# 8 = teardown SpatiaLite (2026-06-08). Usunięcie zależności od mod_spatialite:
#     zapytania przestrzenne liczone czystym Pythonem (shapely + pyproj.Geod),
#     filtry bbox/wielokąt po `centroid_lon/lat` (indeksy B-tree już są).
#     Migracja kasuje triggery SpatiaLite (`*_geom`) i wirtualne tabele R-tree
#     (`idx_*_geom`) z baz zbudowanych kiedyś ze SpatiaLite -- bez tego INSERT
#     do plots/buildings/locals/tx_cache rzucałby 'no such function:
#     GeometryConstraints'. Kolumna `geom` i spatial_ref_sys zostają nieużywane
#     (pełne odchudzenie + VACUUM = osobny krok).
# 9 = kolumna `obreb_numer` w plots/buildings/tx_cache (2026-09-12). Numer
#     obrębu z identyfikatora EGIB trzymany OBOK oznaczenia w `obreb`: po
#     wzbogaceniu `obreb` niesie "B-24", a numer "0042" przestawał być
#     wyszukiwalny. Zgłoszenie testera z Łodzi -- używa się tam obu form.
# 10 = kolumna `withdrawn_by_import_id` w transakcje (2026-09-12). Snapshot
#      zapisuje, który import wycofał transakcję -- bez tego cofnięcie pomyłki
#      wymagało zgadywania po datach (awaria: 836 tys. transakcji ukrytych
#      na Lennym, 701 tys. na produkcji).
# 11 = kolumny `obreb` i `obreb_numer` w locals (2026-09-14) + backfill
#      `locals.teryt_gminy`, które ingest do tej pory zostawiał pustym
#      (zmierzone na fixture Łodzi: 604 lokale, 604 NULL-e). Identyfikator
#      lokalu niesie obręb w drugim segmencie dokładnie tak samo jak
#      identyfikator działki, więc filtr po obrębie ma od tej wersji komplet
#      obiektów -- także transakcje mające WYŁĄCZNIE lokal.
# 12 = oznaczenia obrębów z wbudowanego słownika krajowego (2026-09-14).
#      GML RCN nie zawiera oznaczeń urzędowych („B-42") -- są tylko w EGIB,
#      którego konsument nie ma. Aplikacja niesie więc słownik dla całej Polski
#      (`rcn_core/resources/obreby-polska.csv.gz`) i przy migracji uzupełnia
#      nim bazy, które mają w kolumnie `obreb` sam numer. Wpisy z własnym
#      oznaczeniem (poprawki operatora, dane z EGIB) NIE są ruszane.
CURRENT_SCHEMA_VERSION = 12


def _migrate_v8(conn) -> None:
    """Teardown SpatiaLite -- czysty SQL, idempotentny, NIE wymaga rozszerzenia.

    Bazy zbudowane kiedyś ze SpatiaLite mają triggery `ggi_/ggu_/..._geom`
    (BEFORE INSERT/UPDATE wołające `GeometryConstraints`) oraz wirtualne tabele
    R-tree `idx_<tbl>_geom`. Po wyłączeniu rozszerzenia te triggery łamią każdy
    zapis. Kasujemy triggery i tabele R-tree (moduł `rtree` jest wbudowany w
    sqlite3, więc DROP działa bez SpatiaLite). Świeże bazy i bazy z Windows nigdy
    ich nie miały -> no-op. Kolumnę `geom` zostawiamy (NULL/nieużywana)."""
    # 1. Triggery SpatiaLite na geom (BEFORE INSERT/UPDATE/DELETE + utrzymanie R-tree).
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE '%\\_geom' ESCAPE '\\'"
    ).fetchall():
        conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    # 2. Wirtualne tabele R-tree (drop kaskaduje tabele-cienie *_geom_node/parent/rowid).
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'idx\\_%\\_geom' ESCAPE '\\'"
    ).fetchall():
        conn.execute(f'DROP TABLE IF EXISTS "{name}"')


def _backfill_obreb_numer(conn) -> None:
    """Wypełnij `obreb_numer` z identyfikatora EGIB (migracje v9 i v11).

    Numer obrębu to drugi segment identyfikatora `106102_9.0042.44/1`.
    Identyfikator lokalu (`106106_9.0012.620_BUD.93_LOK`) ma go w tym samym
    miejscu, więc od v11 lokale idą tą samą ścieżką co działki i budynki.
    Liczymy go jednym UPDATE per tabela, w SQL -- pętla w Pythonie po ~2 mln
    wierszach działek Warszawy trwałaby minuty, a backfill biegnie w ścieżce
    `apply_schema`, czyli przy pierwszym żądaniu po wdrożeniu.

    Idempotentne: rusza tylko wiersze z NULL-em.
    """
    for tabela, kol in (("plots", "identyfikator_dzialki"),
                        ("buildings", "identyfikator_budynku"),
                        ("locals", "identyfikator_lokalu")):
        conn.execute(f"""
            UPDATE {tabela}
               SET obreb_numer = substr(
                       {kol},
                       instr({kol}, '.') + 1,
                       instr(substr({kol}, instr({kol}, '.') + 1), '.') - 1)
             WHERE obreb_numer IS NULL
               AND {kol} IS NOT NULL
               AND instr({kol}, '.') > 0
               AND instr(substr({kol}, instr({kol}, '.') + 1), '.') > 1
        """)
    # `obreb` lokalu bez słownika to sam numer -- dokładnie to, co ingest
    # zapisuje dla działki, gdy `obreby.name_for()` nic nie zwróci. Zastosowanie
    # słownika oznaczeń podmieni to potem na "B-42".
    conn.execute("""
        UPDATE locals SET obreb = obreb_numer
         WHERE obreb IS NULL AND obreb_numer IS NOT NULL
    """)

    # teryt_gminy lokali (v11): `_local_tuple` wstawiał tam twarde None, więc
    # kolumna istniała pusta od początku (fixture Łodzi: 604 lokale, 604 NULL-e).
    # Pierwszy segment identyfikatora, ten sam co `teryt_gminy_from_dzialka`.
    conn.execute("""
        UPDATE locals
           SET teryt_gminy = substr(identyfikator_lokalu, 1,
                                    instr(identyfikator_lokalu, '.') - 1)
         WHERE teryt_gminy IS NULL
           AND identyfikator_lokalu IS NOT NULL
           AND instr(identyfikator_lokalu, '.') > 1
    """)
    # tx_cache: numer reprezentanta transakcji. TRZY skorelowane UPDATE-y, każdy
    # trafiający w `idx_plots_id_rcn` / `idx_buildings_id_rcn` / `idx_locals_id_rcn`
    # -- najpierw działki, potem budynki i lokale dla tego, co zostało puste.
    # Lokale są ostatnie, bo mają być uzupełnieniem, a nie reprezentantem
    # transakcji, która ma działkę.
    #
    # ⚠️ NIE łączyć tego w jedno zapytanie z podzapytaniem `UNION ALL` po obu
    # tabelach: takie podzapytanie jest materializowane bez indeksu i wykonywane
    # raz na wiersz cache, czyli O(n²). Zmierzone na skali Warszawy (600 tys. tx,
    # 1,2 mln działek): wariant z `UNION ALL` **91 376 s = 25,4 h**, ten poniżej
    # **4,5 s** — ponad 20 000×. Pierwsze żądanie po wdrożeniu wisiałoby dobę.
    #
    # Różnica semantyczna wobec `refresh_tx_cache` (MIN po obu tabelach naraz):
    # transakcja mająca działkę i budynek w RÓŻNYCH obrębach dostanie tu numer
    # działki, nie mniejszy z dwóch. To anomalia danych (osobno oznaczana flagą
    # `multi_object_act`), a najbliższy `refresh_tx_cache` i tak przeliczy cache.
    for tabela in ("plots", "buildings", "locals"):
        conn.execute(f"""
            UPDATE tx_cache SET obreb_numer = (
                SELECT MIN(o.obreb_numer) FROM {tabela} o
                 WHERE o.id_rcn = tx_cache.id_rcn AND o.obreb_numer IS NOT NULL
            )
            WHERE obreb_numer IS NULL
        """)
    # To samo dla oznaczenia: cache transakcji mającej WYŁĄCZNIE lokal miał do
    # v11 `obreb` pusty, bo lokale nie wnosiły obrębu do `refresh_tx_cache`.
    conn.execute("""
        UPDATE tx_cache SET obreb = (
            SELECT MIN(l.obreb) FROM locals l
             WHERE l.id_rcn = tx_cache.id_rcn AND l.obreb IS NOT NULL
        )
        WHERE obreb IS NULL
    """)


def _backfill_oznaczenia_wbudowane(conn) -> int:
    """Uzupełnij `obreb` oznaczeniami z wbudowanego słownika (migracja v12).

    Rusza WYŁĄCZNIE wiersze, w których `obreb` to wciąż sam numer -- czyli te,
    którym nikt nie nadał jeszcze oznaczenia. Poprawki operatora z panelu
    i dane wprowadzone z warstw EGIB zostają nietknięte.

    SQL przez tabelę tymczasową, nie pętla w Pythonie: to biegnie u użytkownika
    przy pierwszym uruchomieniu po aktualizacji, a `zastosuj_do_bazy` (pętla)
    szła na produkcji ~15 tys. wierszy/s, czyli minuty przy dużym mieście.
    """
    from rcn_core.slownik_obrebow import wczytaj_krajowy

    slownik = wczytaj_krajowy()
    if not slownik:
        return 0

    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _slownik_obrebow ("
                 "teryt TEXT NOT NULL, numer TEXT NOT NULL, oznaczenie TEXT NOT NULL, "
                 "PRIMARY KEY (teryt, numer))")
    conn.execute("DELETE FROM _slownik_obrebow")
    conn.executemany(
        "INSERT OR REPLACE INTO _slownik_obrebow(teryt, numer, oznaczenie) VALUES (?,?,?)",
        [(w.teryt_gminy, w.numer_obrebu, w.oznaczenie) for w in slownik.values()],
    )

    zmienione = 0
    for tabela in ("plots", "buildings", "locals"):
        cur = conn.execute(f"""
            UPDATE {tabela} SET obreb = (
                SELECT s.oznaczenie FROM _slownik_obrebow s
                 WHERE s.teryt = {tabela}.teryt_gminy AND s.numer = {tabela}.obreb_numer
            )
            WHERE obreb IS NOT NULL AND obreb_numer IS NOT NULL
              AND obreb = obreb_numer
              AND EXISTS (SELECT 1 FROM _slownik_obrebow s
                           WHERE s.teryt = {tabela}.teryt_gminy AND s.numer = {tabela}.obreb_numer)
        """)
        zmienione += cur.rowcount or 0

    if zmienione:
        # Cache transakcji musi pójść za zmianą -- tak samo jak przy stosowaniu
        # słownika z panelu. Lokale ostatnie: uzupełniają, nie przejmują.
        conn.execute("""
            UPDATE tx_cache SET obreb = COALESCE(
                (SELECT MIN(p.obreb) FROM plots p
                  WHERE p.id_rcn = tx_cache.id_rcn AND p.obreb IS NOT NULL),
                (SELECT MIN(b.obreb) FROM buildings b
                  WHERE b.id_rcn = tx_cache.id_rcn AND b.obreb IS NOT NULL),
                (SELECT MIN(l.obreb) FROM locals l
                  WHERE l.id_rcn = tx_cache.id_rcn AND l.obreb IS NOT NULL),
                obreb)
        """)
    conn.execute("DROP TABLE IF EXISTS _slownik_obrebow")
    return zmienione


def refresh_tx_cache(conn, id_rcn_list: list[str] | None = None) -> None:
    """Przebuduj wiersze `tx_cache` dla podanych transakcji (po ingeście)
    albo dla wszystkich (id_rcn_list=None, one-shot migracyjne).

    Źródła danych (UNION ALL z 3 tabel obiektów):
    - MIN(miejscowosc), MIN(adres), MIN(obreb) -- reprezentant
    - MIN(centroid_lon/lat) spośród niezerowych centroidów
    - COUNT per tabela (plot_count, building_count, local_count)
    - SUM(pow_uzytkowa) dla locals/buildings lub SUM(powierzchnia_m2) dla plots (wszędzie m²)
    - first_plot_ident + plot_idents_concat z plots
    """
    if id_rcn_list is not None and not id_rcn_list:
        return

    filter_sql = ""
    params: tuple = ()
    if id_rcn_list is not None:
        placeholders = ",".join("?" * len(id_rcn_list))
        filter_sql = f"WHERE t.id_rcn IN ({placeholders})"
        params = tuple(id_rcn_list)

    # Jedno zapytanie upsert. Używa MATERIALIZED CTE, żeby SQLite je wyliczył
    # raz. Dla id_rcn_list po ingeście (np. kilkaset) to szybkie;
    # dla pełnego rebuild (id_rcn_list=None) to trwa kilka sekund.
    sql = f"""
    WITH objects AS MATERIALIZED (
        SELECT id_rcn, miejscowosc, adres, obreb, obreb_numer, teryt_gminy, centroid_lon, centroid_lat FROM plots
        UNION ALL
        SELECT id_rcn, miejscowosc, adres, obreb, obreb_numer, teryt_gminy, centroid_lon, centroid_lat FROM buildings
        UNION ALL
        SELECT id_rcn, miejscowosc, adres, obreb, obreb_numer, teryt_gminy, centroid_lon, centroid_lat FROM locals
    ),
    tx_agg AS MATERIALIZED (
        SELECT id_rcn,
               MIN(miejscowosc) AS miejscowosc,
               MIN(adres)       AS adres,
               MIN(obreb)       AS obreb,
               MIN(obreb_numer) AS obreb_numer,
               MIN(teryt_gminy) AS teryt_gminy,
               MIN(centroid_lon) AS centroid_lon,
               MIN(centroid_lat) AS centroid_lat
        FROM objects GROUP BY id_rcn
    ),
    counts AS MATERIALIZED (
        SELECT id_rcn, COUNT(*) AS n FROM plots     GROUP BY id_rcn
    ),
    bcounts AS MATERIALIZED (
        SELECT id_rcn, COUNT(*) AS n FROM buildings GROUP BY id_rcn
    ),
    lcounts AS MATERIALIZED (
        SELECT id_rcn, COUNT(*) AS n FROM locals    GROUP BY id_rcn
    ),
    area_l AS MATERIALIZED (
        SELECT id_rcn, SUM(COALESCE(pow_uzytkowa, 0)) AS a FROM locals    GROUP BY id_rcn
    ),
    area_b AS MATERIALIZED (
        SELECT id_rcn, SUM(COALESCE(pow_uzytkowa, 0)) AS a FROM buildings GROUP BY id_rcn
    ),
    area_p AS MATERIALIZED (
        SELECT id_rcn, SUM(COALESCE(powierzchnia_m2, 0)) AS a FROM plots GROUP BY id_rcn
    ),
    plot_idents AS MATERIALIZED (
        SELECT id_rcn,
               GROUP_CONCAT(identyfikator_dzialki, '|') AS idents,
               MIN(identyfikator_dzialki)               AS first_ident
        FROM plots
        WHERE identyfikator_dzialki IS NOT NULL AND identyfikator_dzialki <> ''
        GROUP BY id_rcn
    )
    INSERT INTO tx_cache (
        id_rcn, miejscowosc, adres, obreb, obreb_numer, teryt_gminy,
        centroid_lon, centroid_lat,
        plot_count, building_count, local_count, area_m2,
        first_plot_ident, plot_idents_concat
    )
    SELECT
        t.id_rcn,
        tx_agg.miejscowosc,
        tx_agg.adres,
        tx_agg.obreb,
        tx_agg.obreb_numer,
        tx_agg.teryt_gminy,
        tx_agg.centroid_lon,
        tx_agg.centroid_lat,
        COALESCE(counts.n, 0),
        COALESCE(bcounts.n, 0),
        COALESCE(lcounts.n, 0),
        CASE
          WHEN t.rodzaj_nieruchomosci LIKE '%okal%'  THEN area_l.a
          WHEN t.rodzaj_nieruchomosci LIKE '%udynk%' THEN area_b.a
          WHEN t.rodzaj_nieruchomosci LIKE '%runt%'  THEN area_p.a
          ELSE COALESCE(area_l.a, area_b.a, area_p.a)
        END,
        plot_idents.first_ident,
        plot_idents.idents
    FROM transakcje t
    LEFT JOIN tx_agg       USING (id_rcn)
    LEFT JOIN counts       USING (id_rcn)
    LEFT JOIN bcounts      USING (id_rcn)
    LEFT JOIN lcounts      USING (id_rcn)
    LEFT JOIN area_l       USING (id_rcn)
    LEFT JOIN area_b       USING (id_rcn)
    LEFT JOIN area_p       USING (id_rcn)
    LEFT JOIN plot_idents  USING (id_rcn)
    {filter_sql}
    ON CONFLICT(id_rcn) DO UPDATE SET
        miejscowosc        = excluded.miejscowosc,
        adres              = excluded.adres,
        obreb              = excluded.obreb,
        obreb_numer        = excluded.obreb_numer,
        teryt_gminy        = excluded.teryt_gminy,
        centroid_lon       = excluded.centroid_lon,
        centroid_lat       = excluded.centroid_lat,
        plot_count         = excluded.plot_count,
        building_count     = excluded.building_count,
        local_count        = excluded.local_count,
        area_m2            = excluded.area_m2,
        first_plot_ident   = excluded.first_plot_ident,
        plot_idents_concat = excluded.plot_idents_concat
    """
    conn.execute(sql, params)



def _get_schema_version(conn) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM workspace_meta WHERE key = 'schema_version'"
        ).fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception:
        return 0


def _set_schema_version(conn, version: int) -> None:
    conn.execute(
        "INSERT INTO workspace_meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


def _migrate(conn) -> None:
    """Idempotent migrations. Ciężki backfill (skanowanie attributes_json na
    setkach tysięcy wierszy) uruchamiany TYLKO raz na DB -- po udanym
    backfillu `workspace_meta.schema_version` dostaje aktualną wartość,
    kolejne otwarcia DB skipują backfill.

    ALTER TABLE + CREATE INDEX pozostają bezwarunkowe, bo są bardzo tanie.
    """
    import json
    import re

    def has_column(table: str, column: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any((r[1] if not isinstance(r, dict) else r["name"]) == column for r in rows)

    # imports: stage / progress / error for background-processing imports
    for col, ddl in (
        ("stage",        "ALTER TABLE imports ADD COLUMN stage TEXT"),
        ("progress_pct", "ALTER TABLE imports ADD COLUMN progress_pct INTEGER NOT NULL DEFAULT 0"),
        ("error_msg",    "ALTER TABLE imports ADD COLUMN error_msg TEXT"),
    ):
        if not has_column("imports", col):
            conn.execute(ddl)

    if not has_column("transakcje", "rodzaj_nieruchomosci"):
        conn.execute("ALTER TABLE transakcje ADD COLUMN rodzaj_nieruchomosci TEXT")
    if not has_column("plots", "obreb"):
        conn.execute("ALTER TABLE plots ADD COLUMN obreb TEXT")
    if not has_column("buildings", "obreb"):
        conn.execute("ALTER TABLE buildings ADD COLUMN obreb TEXT")

    # kwota_vat w transakcje + plots + buildings + locals (2026-04-23).
    # UWAGA: mimo nazwy "kwota" w schemie GML, wartości w plikach Łodzi
    # to w praktyce STAWKA VAT w procentach (0.0, 8.0, 23.0) a nie kwota PLN.
    # Pole bywa puste — w próbie ~90% transakcji NULL (operator nie wypełnia).
    for table in ("transakcje", "plots", "buildings", "locals"):
        if not has_column(table, "kwota_vat"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN kwota_vat REAL")

    # Status transakcji (2026-04-24). Pomyślane pod tryb `snapshot`:
    # gdy nowy plik GML zawiera pełny snapshot zakresu dat Y1–Y2 i brakuje w nim
    # transakcji id_rcn X z bazy (w tym samym zakresie), ingest oznacza ją jako
    # `wycofana_z_portalu` (operator skasował ją z RCN -- np. korekta).
    # Domyślnie `aktywna` -- wszystkie istniejące transakcje zostają widoczne.
    if not has_column("transakcje", "status"):
        conn.execute("ALTER TABLE transakcje ADD COLUMN status TEXT DEFAULT 'aktywna'")

    # imports: zakres dat dla snapshot-ów (jawnie od usera lub auto-wykryty z pliku).
    # Gdy znany, snapshot-delete-logic działa ograniczona do tego okresu.
    for col, ddl in (
        ("data_transakcji_od", "ALTER TABLE imports ADD COLUMN data_transakcji_od TEXT"),
        ("data_transakcji_do", "ALTER TABLE imports ADD COLUMN data_transakcji_do TEXT"),
        ("withdrawn_count",    "ALTER TABLE imports ADD COLUMN withdrawn_count INTEGER NOT NULL DEFAULT 0"),
    ):
        if not has_column("imports", col):
            conn.execute(ddl)

    # geom_source w plots/buildings/locals (v5, 2026-04-29). 'gml' default --
    # istniejące wiersze RCN_GML, 'egib' nadpisywane przez tools/enrich_geom_from_egib.py
    # gdy lookup w lokalnym GPKG da rezultat. 'inherited' dla locals przejmujących
    # centroid od budynku tej samej transakcji.
    for table in ("plots", "buildings", "locals"):
        if not has_column(table, "geom_source"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN geom_source TEXT DEFAULT 'gml'")

    # obreb_numer w plots/buildings/tx_cache (v9, 2026-09-12) -- numer obrębu
    # z identyfikatora EGIB trzymany obok oznaczenia w `obreb`, żeby po
    # wzbogaceniu ("B-24") numer ("0042") nadal dawał się wyszukać.
    for table in ("plots", "buildings", "tx_cache"):
        if not has_column(table, "obreb_numer"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN obreb_numer TEXT")

    # obreb + obreb_numer w locals (v11, 2026-09-14) -- identyfikator lokalu
    # niesie obręb w tym samym miejscu co identyfikator działki, a bez tych
    # kolumn transakcja mająca wyłącznie lokal wypadała z filtra obrębów.
    for col in ("obreb", "obreb_numer"):
        if not has_column("locals", col):
            conn.execute(f"ALTER TABLE locals ADD COLUMN {col} TEXT")

    # data_quality_flags w transakcje (v6, 2026-04-29) -- JSON array stringów flag
    # (no_objects, multi_object_act, extreme_price_per_m2, zero_area,
    # total_price_split_suspect). NULL = jeszcze nie obliczone, [] = OK,
    # ["..."] = lista flag. Wypełniana przez compute_flags() w rcn_core/quality.py.
    if not has_column("transakcje", "data_quality_flags"):
        conn.execute("ALTER TABLE transakcje ADD COLUMN data_quality_flags TEXT")

    # withdrawn_by_import_id (v10, 2026-09-12) -- ślad, który import wycofał
    # transakcję. Istniejące wycofania zostają bez znacznika (NULL): nie da się
    # ich już przypisać do importu, bo ta informacja nigdy nie była zapisywana.
    if not has_column("transakcje", "withdrawn_by_import_id"):
        conn.execute("ALTER TABLE transakcje ADD COLUMN withdrawn_by_import_id INTEGER")

    # Indeksy na migrowanych kolumnach (bezwarunkowo, IF NOT EXISTS).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_rodzaj ON transakcje(rodzaj_nieruchomosci)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plots_obreb ON plots(obreb)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_buildings_obreb ON buildings(obreb)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_cache_obreb_numer ON tx_cache(obreb_numer)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plots_obreb_numer ON plots(obreb_numer)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_buildings_obreb_numer ON buildings(obreb_numer)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_locals_obreb ON locals(obreb)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_locals_obreb_numer ON locals(obreb_numer)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_status ON transakcje(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plots_geom_source ON plots(geom_source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_buildings_geom_source ON buildings(geom_source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_locals_geom_source ON locals(geom_source)")

    # Backfill uruchamiany TYLKO raz (kontrolka w workspace_meta).
    # Wcześniej backfill skanował ~150 tys. transakcji w każdym requestcie
    # (bo NULL-e zawsze są, operator ich nie wypełnia) -- dawało 9-17 s
    # lag w `/query`, `/geojson` i `/workspaces`.
    version = _get_schema_version(conn)
    if version >= CURRENT_SCHEMA_VERSION:
        return

    # Istniejące bazy (v3-v7): tylko teardown SpatiaLite, BEZ pełnego
    # refresh_tx_cache (Warszawa 586k tx = >25s lag w pierwszym requeście po
    # deploy). Backfill rodzaj/vat już zrobione wcześniej. _migrate_v8 kasuje
    # triggery+R-tree (obowiązkowe, inaczej zapisy pękają na GeometryConstraints).
    if version >= 3:
        _migrate_v8(conn)
        _backfill_obreb_numer(conn)
        _backfill_oznaczenia_wbudowane(conn)
        _set_schema_version(conn, CURRENT_SCHEMA_VERSION)
        return

    backfill_tx = conn.execute(
        "SELECT id_rcn, attributes_json FROM transakcje "
        "WHERE rodzaj_nieruchomosci IS NULL OR kwota_vat IS NULL"
    ).fetchall()
    for row in backfill_tx:
        try:
            attrs = json.loads(row[1] or "{}")
        except Exception:
            continue
        v_rodzaj = attrs.get("rodzaj nier.")
        v_vat = _to_number(attrs.get("kwota podatku VAT"))
        sets = []
        params: list = []
        if v_rodzaj:
            sets.append("rodzaj_nieruchomosci = COALESCE(rodzaj_nieruchomosci, ?)")
            params.append(v_rodzaj)
        if v_vat is not None:
            sets.append("kwota_vat = COALESCE(kwota_vat, ?)")
            params.append(v_vat)
        if sets:
            params.append(row[0])
            conn.execute(
                f"UPDATE transakcje SET {', '.join(sets)} WHERE id_rcn = ?",
                params,
            )

    # Backfill kwota_vat per-obiekt (plots/buildings/locals) — klucze w
    # attributes_json różnią się (parser używa prefiksów "dz. -", "bud. -",
    # "lok. -").
    for table, attr_key in (
        ("plots", "dz. - kwota vat"),
        ("buildings", "bud. - kwota vat"),
        ("locals", "lok. - kwota vat"),
    ):
        rows = conn.execute(
            f"SELECT id, attributes_json FROM {table} WHERE kwota_vat IS NULL"
        ).fetchall()
        for pk, attrs_json in rows:
            try:
                attrs = json.loads(attrs_json or "{}")
            except Exception:
                continue
            v = _to_number(attrs.get(attr_key))
            if v is not None:
                conn.execute(f"UPDATE {table} SET kwota_vat = ? WHERE id = ?", (v, pk))

    from rcn_core.obreby import looks_like_numeric, name_for as _obreb_name_for

    ident_re = re.compile(r"^([0-9]+_[0-9]+)\.([^.]+)\.")
    numeric_re = re.compile(r"^\d{1,6}$")
    for table, ident_col in (("plots", "identyfikator_dzialki"), ("buildings", "identyfikator_budynku")):
        # Rows that are NULL or still carry a bare numeric obreb — if the EGIB
        # dict now has a matching name, upgrade them. This lets us display
        # `G-42` after the user drops an EGIB GPKG into `data/layers/`.
        rows = conn.execute(
            f"SELECT id, {ident_col}, obreb FROM {table} WHERE {ident_col} IS NOT NULL "
            "AND (obreb IS NULL OR obreb NOT LIKE '%-%')"
        ).fetchall()
        for pk, ident, current in rows:
            m = ident_re.match(ident or "")
            if not m:
                continue
            teryt = m.group(1)
            num = m.group(2)
            name = _obreb_name_for(teryt, num)
            new_value = name or num
            if new_value != current:
                conn.execute(f"UPDATE {table} SET obreb = ? WHERE id = ?", (new_value, pk))

    # Full refresh tx_cache (version 3 -- materialized view). Robimy to raz
    # w ramach migracji; dalsze odświeżanie jest częściowe (per affected_ids)
    # w `ingest_gml`.
    _backfill_obreb_numer(conn)

    refresh_tx_cache(conn, id_rcn_list=None)

    # Teardown SpatiaLite (v8). Świeża baza nie ma triggerów/R-tree -> no-op;
    # idempotentny dla baz przeniesionych ze SpatiaLite.
    _migrate_v8(conn)

    # Zapisz aktualną wersję schema -- następne wywołania `apply_schema`
    # skipują cały backfill.
    _set_schema_version(conn, CURRENT_SCHEMA_VERSION)


# ---------------------------------------------------------------------------
# phase_runs helpers (v7)
# ---------------------------------------------------------------------------
# Wywoływane z phase_runner.py (subprocess) i app/workspaces.py (concurrency
# guard + listing). Każdy helper otwiera własną connection przez sqlite3.connect
# gdy dostaje db_path; przy przekazaniu istniejącego conn działa w jego
# transakcji.

def start_phase_run(conn, phase: str, triggered_by: str | None = None) -> int:
    """Wstaw row 'running' do phase_runs i zwróć id. Caller (subprocess)
    używa tego id w finish_phase_run."""
    import time
    cur = conn.execute(
        "INSERT INTO phase_runs(phase, status, started_at, triggered_by) "
        "VALUES (?, 'running', ?, ?)",
        (phase, time.time(), triggered_by),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_phase_run(
    conn,
    run_id: int,
    *,
    status: str,
    diagnostics: dict | None = None,
    error_msg: str | None = None,
) -> None:
    """Domknij phase_run -- ustaw status (success|failed), finished_at,
    duration_s, opcjonalne diagnostics_json (z enrich_workspace/compute_flags
    return value) lub error_msg."""
    import time
    import json as _json
    now = time.time()
    diag_json = _json.dumps(diagnostics, ensure_ascii=False) if diagnostics else None
    conn.execute(
        "UPDATE phase_runs SET status = ?, finished_at = ?, "
        "duration_s = ? - started_at, diagnostics_json = ?, error_msg = ? "
        "WHERE id = ?",
        (status, now, now, diag_json, error_msg, run_id),
    )
    conn.commit()


def list_phase_runs(conn, *, phase: str | None = None, limit: int = 20) -> list[dict]:
    """Lista ostatnich phase_runs (wszystkie albo dla konkretnej fazy).
    Sortowane po started_at DESC."""
    sql = (
        "SELECT id, phase, status, started_at, finished_at, duration_s, "
        "diagnostics_json, error_msg, triggered_by FROM phase_runs"
    )
    params: tuple = ()
    if phase:
        sql += " WHERE phase = ?"
        params = (phase,)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params = params + (int(limit),)
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": r[0],
            "phase": r[1],
            "status": r[2],
            "started_at": r[3],
            "finished_at": r[4],
            "duration_s": r[5],
            "diagnostics_json": r[6],
            "error_msg": r[7],
            "triggered_by": r[8],
        }
        for r in rows
    ]


def has_running_phase_run(conn, phase: str) -> bool:
    """True jeśli istnieje running phase_run dla podanej fazy.
    Używane jako concurrency guard w endpoint /enhancements/{op} -- odmawiamy
    spawn-u nowego subprocess gdy poprzedni jeszcze pracuje."""
    row = conn.execute(
        "SELECT 1 FROM phase_runs WHERE phase = ? AND status = 'running' LIMIT 1",
        (phase,),
    ).fetchone()
    return row is not None


def mark_stale_phase_runs(conn, max_age_s: int = 3600) -> int:
    """Oznacz running phase_runs starsze niż max_age_s jako failed.
    Subprocess może umrzeć (SIGSEGV od Fiona+pyproj, OOM kill, restart
    kontenera) bez wywołania finish_phase_run -- bez tego stale 'running'
    blokuje concurrency guard na zawsze. Wywoływane przed listingiem
    i przed concurrency check."""
    import time
    cutoff = time.time() - max_age_s
    cur = conn.execute(
        "UPDATE phase_runs SET status = 'failed', "
        "finished_at = ?, duration_s = ? - started_at, "
        "error_msg = COALESCE(error_msg, 'timeout (subprocess crashed lub watchdog cleanup)') "
        "WHERE status = 'running' AND started_at < ?",
        (time.time(), time.time(), cutoff),
    )
    conn.commit()
    return cur.rowcount


def mark_stale_imports(conn, max_age_s: int = 3600) -> int:
    """Analogicznie dla imports -- segfault w ingest subprocess zostawia
    imports.status='processing' na zawsze. Cutoff 1h -- typowy ingest
    GML powiatu trwa <30 min, więc 1h to bezpieczny próg."""
    import time
    cutoff = time.time() - max_age_s
    cur = conn.execute(
        "UPDATE imports SET status = 'failed', "
        "error_msg = COALESCE(error_msg, 'timeout (subprocess crashed lub watchdog cleanup)') "
        "WHERE status IN ('processing', 'queued') AND upload_timestamp < ?",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount
