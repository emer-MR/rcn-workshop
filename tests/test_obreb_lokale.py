"""Lokal też ma obręb -- niesie go w drugim segmencie identyfikatora.

Do schema v11 `locals` nie miało kolumn `obreb`/`obreb_numer`, a `teryt_gminy`
ingest wypełniał twardym `None` (zmierzone na fixture Łodzi: 604 lokale, 604
NULL-e). Skutek: transakcja mająca WYŁĄCZNIE lokal nie miała obrębu nigdzie --
ani w obiektach, ani w `tx_cache` -- więc wypadała z filtra i z katalogu.

Identyfikator lokalu `106106_9.0012.620_BUD.93_LOK` ma jednostkę i obręb
dokładnie tam, gdzie identyfikator działki `106106_9.0012.44/1`.
"""
import sqlite3

import pytest

from rcn_core.schema import apply_schema, refresh_tx_cache
from rcn_core.slownik_obrebow import WpisObrebu, obreby_z_bazy, zastosuj_do_bazy

IDENT_LOKALU = "106106_9.0012.620_BUD.93_LOK"


def _baza(path):
    """Dwie transakcje: jedna z działką, druga WYŁĄCZNIE z lokalem."""
    conn = sqlite3.connect(path)
    apply_schema(conn)
    conn.execute(
        "INSERT INTO imports(id, original_filename, stored_filename, tryb, upload_timestamp) "
        "VALUES (1, 'x.gml', 'x.gml', 'snapshot', 1)"
    )
    for id_rcn in ("RCN-DZ", "RCN-LOK"):
        conn.execute(
            "INSERT INTO transakcje(id_rcn, source_import_id, import_timestamp, "
            "data_transakcji, cena_transakcji_brutto, rodzaj_nieruchomosci, liczba_obiektow) "
            "VALUES (?, 1, 1, '2026-03-01', 100000, 'lokal', 1)",
            (id_rcn,),
        )
    conn.execute(
        "INSERT INTO plots(id_rcn, source_import_id, identyfikator_dzialki, "
        "teryt_gminy, obreb, obreb_numer) VALUES "
        "('RCN-DZ', 1, '106103_9.0024.1', '106103_9', '0024', '0024')"
    )
    conn.execute(
        "INSERT INTO locals(id_rcn, source_import_id, identyfikator_lokalu, "
        "teryt_gminy, obreb, obreb_numer, pow_uzytkowa) VALUES "
        "('RCN-LOK', 1, ?, '106106_9', '0012', '0012', 48.5)",
        (IDENT_LOKALU,),
    )
    refresh_tx_cache(conn, id_rcn_list=None)
    conn.commit()
    return conn


@pytest.fixture
def workspace(tmp_path):
    db = tmp_path / "w.sqlite"
    conn = _baza(db)
    conn.close()
    return db


def _wyniki(db, filters: dict) -> set[str]:
    """Filtry przez tę samą funkcję, której używa endpoint /query."""
    from app.query import QueryFilters, _attribute_clauses

    clauses, params = _attribute_clauses(QueryFilters(**filters))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT t.id_rcn FROM transakcje t LEFT JOIN tx_cache tc USING (id_rcn)" + where
        return {r["id_rcn"] for r in conn.execute(sql, params).fetchall()}
    finally:
        conn.close()


def test_ingest_wypelnia_obreb_lokalu(tmp_path):
    """Świeży import: lokal dostaje jednostkę i obie formy obrębu."""
    from rcn_core.ingest import _local_tuple

    wiersz = _local_tuple({"identyfikator lokalu": IDENT_LOKALU}, "RCN-1", 1, 2180)
    # (id_rcn, import_id, identyfikator, teryt, obreb, obreb_numer, ...)
    assert wiersz[3] == "106106_9"
    # Od schema v12 oznaczenie przychodzi z wbudowanego słownika krajowego
    # (106106_9.0012 = Widzew W-12); numer zostaje obok, w `obreb_numer`.
    assert wiersz[4] == "W-12"
    assert wiersz[5] == "0012"


def test_transakcja_z_samym_lokalem_jest_znajdowana(workspace):
    """Trzy drogi do tego samego obrębu: katalog, klucz i filtr tekstowy."""
    assert _wyniki(workspace, {"obreb_key": ["106106_9|0012"]}) == {"RCN-LOK"}
    assert _wyniki(workspace, {"obreb": ["0012"]}) == {"RCN-LOK"}
    assert _wyniki(workspace, {"obreb_search": "12"}) == {"RCN-LOK"}
    # Kontrola: obręb działki nie łapie transakcji lokalowej i odwrotnie.
    assert _wyniki(workspace, {"obreb_key": ["106103_9|0024"]}) == {"RCN-DZ"}


def test_lokal_wnosi_obreb_do_tx_cache(workspace):
    conn = sqlite3.connect(workspace)
    try:
        wiersz = conn.execute(
            "SELECT obreb, obreb_numer, teryt_gminy FROM tx_cache WHERE id_rcn = 'RCN-LOK'"
        ).fetchone()
    finally:
        conn.close()
    assert wiersz == ("0012", "0012", "106106_9")


def test_katalog_obrebow_widzi_obreb_znany_tylko_z_lokalu(workspace):
    """Ten sam SQL, którym `workspace_lookups` buduje listę dla UI."""
    from app.query import OBREBY_LOOKUP_SQL

    conn = sqlite3.connect(workspace)
    try:
        pozycje = {(r[0], r[1]) for r in conn.execute(OBREBY_LOOKUP_SQL).fetchall()}
    finally:
        conn.close()
    assert ("106106_9", "0012") in pozycje     # znany wyłącznie z lokalu
    assert ("106103_9", "0024") in pozycje


def test_katalog_i_filtry_pytaja_te_same_tabele():
    """Rozjazd znaczyłby, że katalog pokazuje obręb, którego filtr nie znajduje."""
    from app.query import OBREBY_LOOKUP_SQL, TABELE_Z_OBREBEM

    for tabela in TABELE_Z_OBREBEM:
        assert f"FROM {tabela}" in OBREBY_LOOKUP_SQL


def test_slownik_oznaczen_obejmuje_lokale(tmp_path):
    """„Zastosuj oznaczenia" ma podmienić numer na „W-12" także w lokalach."""
    conn = _baza(tmp_path / "w.sqlite")
    try:
        assert ("106106_9", "0012", "0012") in obreby_z_bazy(conn)

        slownik = {
            "106106_9.0012": WpisObrebu("106106_9", "0012", "W-12", "ŁÓDŹ-WIDZEW", "egib-20260806")
        }
        stat = zastosuj_do_bazy(conn, slownik, wykonaj=True)
        assert stat["tabele"]["locals"]["zmienione"] == 1

        assert conn.execute("SELECT obreb FROM locals").fetchone()[0] == "W-12"
        # Numer nie ginie -- obie formy zostają wyszukiwalne.
        assert conn.execute("SELECT obreb_numer FROM locals").fetchone()[0] == "0012"
        # Cache transakcji bez działki i budynku idzie za zmianą.
        assert conn.execute(
            "SELECT obreb FROM tx_cache WHERE id_rcn = 'RCN-LOK'"
        ).fetchone()[0] == "W-12"
    finally:
        conn.close()


def test_migracja_v10_backfilluje_lokale(tmp_path):
    """Stara baza: kolumny dochodzą, a wartości wyliczają się z identyfikatora."""
    db = tmp_path / "stara.sqlite"
    conn = _baza(db)
    # Cofnięcie do stanu sprzed v11: wartości puste, wersja schematu niższa.
    conn.execute("UPDATE locals SET teryt_gminy = NULL, obreb = NULL, obreb_numer = NULL")
    conn.execute("UPDATE tx_cache SET obreb = NULL, obreb_numer = NULL WHERE id_rcn = 'RCN-LOK'")
    conn.execute("UPDATE workspace_meta SET value = '10' WHERE key LIKE '%schema%'")
    conn.commit()

    apply_schema(conn)

    # Migracja uzupełnia numer z identyfikatora, a oznaczenie ze słownika
    # wbudowanego (v12) -- stąd „W-12" zamiast samego numeru.
    assert conn.execute(
        "SELECT teryt_gminy, obreb, obreb_numer FROM locals"
    ).fetchone() == ("106106_9", "W-12", "0012")
    assert conn.execute(
        "SELECT obreb, obreb_numer FROM tx_cache WHERE id_rcn = 'RCN-LOK'"
    ).fetchone() == ("W-12", "0012")
    conn.close()
