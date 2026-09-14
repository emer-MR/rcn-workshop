"""Obręb identyfikuje para (jednostka ewidencyjna, numer) -- nie sam numer.

Numer obrębu w identyfikatorze EGIB jest unikalny tylko w ramach jednostki
ewidencyjnej. Łódź ma ich pięć (dzielnice), więc pozycja katalogu „0024"
reprezentowała tam cztery różne obręby, a filtr po niej zwracał transakcje
z kilku części miasta -- zgłoszenie testera 2026-09-12.

Dodatkowo: po wzbogaceniu z EGIB kolumna `obreb` niesie oznaczenie urzędowe
(`B-24`), a numer (`0024`) zostaje w `obreb_numer` -- oba mają być wyszukiwalne,
bo rzeczoznawcy używają obu form.
"""
import sqlite3

import pytest

from rcn_core.schema import apply_schema, refresh_tx_cache


def _baza(tmp_path):
    """Trzy transakcje: obręb 0024 w dwóch dzielnicach + obręb 0240 na kontrolę."""
    db = tmp_path / "w.sqlite"
    conn = sqlite3.connect(db)
    apply_schema(conn)
    conn.execute(
        "INSERT INTO imports(id, original_filename, stored_filename, tryb, upload_timestamp) "
        "VALUES (1, 'x.gml', 'x.gml', 'snapshot', 1)"
    )
    dane = [
        ("RCN-1", "106103_9.0024.1", "106103_9", "0024"),   # Górna
        ("RCN-2", "106106_9.0024.2", "106106_9", "0024"),   # Widzew, ten sam numer
        ("RCN-3", "106106_9.0240.3", "106106_9", "0240"),   # kontrola na LIKE '%24%'
    ]
    for id_rcn, ident, teryt, numer in dane:
        conn.execute(
            "INSERT INTO transakcje(id_rcn, source_import_id, import_timestamp, "
            "data_transakcji, cena_transakcji_brutto, rodzaj_nieruchomosci, liczba_obiektow) "
            "VALUES (?, 1, 1, '2026-03-01', 100000, 'grunt', 1)",
            (id_rcn,),
        )
        conn.execute(
            "INSERT INTO plots(id_rcn, source_import_id, identyfikator_dzialki, "
            "teryt_gminy, obreb, obreb_numer, powierzchnia_m2) VALUES (?, 1, ?, ?, ?, ?, 1000)",
            (id_rcn, ident, teryt, numer, numer),
        )
    refresh_tx_cache(conn, id_rcn_list=None)
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    db = _baza(tmp_path)
    return db


def _wyniki(db, filters: dict) -> set[str]:
    """Uruchom filtry przez tę samą funkcję, której używa endpoint /query."""
    from app.query import QueryFilters, _attribute_clauses

    clauses, params = _attribute_clauses(QueryFilters(**filters))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        sql = ("SELECT t.id_rcn FROM transakcje t LEFT JOIN tx_cache tc USING (id_rcn)" + where)
        return {r["id_rcn"] for r in conn.execute(sql, params).fetchall()}
    finally:
        conn.close()


def test_filtr_po_kluczu_nie_miesza_dzielnic(workspace):
    assert _wyniki(workspace, {"obreb_key": ["106103_9|0024"]}) == {"RCN-1"}
    assert _wyniki(workspace, {"obreb_key": ["106106_9|0024"]}) == {"RCN-2"}


def test_klucze_z_kilku_dzielnic_sumuja_sie(workspace):
    assert _wyniki(
        workspace, {"obreb_key": ["106103_9|0024", "106106_9|0024"]}
    ) == {"RCN-1", "RCN-2"}


def test_stary_filtr_po_samym_numerze_zostaje_zgodny(workspace):
    # Zapisane linki i wtyczki nadal działają -- z dawnym, zlewającym zachowaniem.
    assert _wyniki(workspace, {"obreb": ["0024"]}) == {"RCN-1", "RCN-2"}


def test_szukanie_numeru_nie_lapie_podobnych(workspace):
    # LIKE '%24%' zwracało też 0240; teraz numer dopasowuje się jako całość.
    assert _wyniki(workspace, {"obreb_search": "24"}) == {"RCN-1", "RCN-2"}
    assert _wyniki(workspace, {"obreb_search": "0024"}) == {"RCN-1", "RCN-2"}
    assert _wyniki(workspace, {"obreb_search": "240"}) == {"RCN-3"}


def test_katalog_rozdziela_obreby_o_tym_samym_numerze(workspace, tmp_path):
    from app.query import ObrebOption, _etykieta_obrebu

    conn = sqlite3.connect(workspace)
    try:
        pary = conn.execute(
            "SELECT DISTINCT teryt_gminy, obreb, obreb_numer FROM plots ORDER BY obreb_numer, teryt_gminy"
        ).fetchall()
    finally:
        conn.close()
    assert len(pary) == 3, pary
    klucze = {f"{t}|{o}" for t, o, _ in pary}
    assert klucze == {"106103_9|0024", "106106_9|0024", "106106_9|0240"}
    # Etykieta bez EGIB musi rozróżniać jednostkę, inaczej katalog pokazuje
    # dwie nierozróżnialne pozycje „0024".
    assert _etykieta_obrebu("106103_9", "0024", "0024") == "0024 · 106103_9"
    assert _etykieta_obrebu("106106_9", "0024", "0024") == "0024 · 106106_9"
    # Z EGIB obie formy naraz -- rzeczoznawca szuka raz oznaczenia, raz numeru.
    assert _etykieta_obrebu("106102_9", "B-24", "0024") == "B-24 · 0024"
    assert ObrebOption(key="106102_9|B-24", obreb="B-24", obreb_numer="0024",
                       teryt_gminy="106102_9", label="B-24 · 0024").obreb_numer == "0024"


def test_po_wzbogaceniu_szukanie_dziala_na_oba_zapisy(tmp_path):
    """Gdy `obreb` to już „B-24", numer „0024" nadal musi znajdować -- i odwrotnie."""
    db = _baza(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE plots SET obreb = 'G-24' WHERE obreb_numer = '0024' AND teryt_gminy = '106103_9'")
    conn.execute("UPDATE plots SET obreb = 'W-24' WHERE obreb_numer = '0024' AND teryt_gminy = '106106_9'")
    refresh_tx_cache(conn, id_rcn_list=None)
    conn.commit()
    conn.close()

    assert _wyniki(db, {"obreb_search": "24"}) == {"RCN-1", "RCN-2"}       # po numerze
    assert _wyniki(db, {"obreb_search": "G-24"}) == {"RCN-1"}              # po oznaczeniu
    assert _wyniki(db, {"obreb_search": "g24"}) == {"RCN-1"}               # zapis bez myślnika
    assert _wyniki(db, {"obreb_key": ["106106_9|W-24"]}) == {"RCN-2"}      # katalog po wzbogaceniu
    # Zapisany link z czasów przed wzbogaceniem nadal działa (numer w `obreb`).
    assert _wyniki(db, {"obreb": ["0024"]}) == {"RCN-1", "RCN-2"}
    assert _wyniki(db, {"obreb": ["W-24"]}) == {"RCN-2"}


def test_migracja_uzupelnia_numer_ze_starej_bazy(tmp_path):
    """Baza bez `obreb_numer` (v8) dostaje numer z identyfikatora przy migracji."""
    db = tmp_path / "stara.sqlite"
    conn = sqlite3.connect(db)
    apply_schema(conn)
    conn.execute(
        "INSERT INTO imports(id, original_filename, stored_filename, tryb, upload_timestamp) "
        "VALUES (1, 'x.gml', 'x.gml', 'snapshot', 1)"
    )
    conn.execute(
        "INSERT INTO transakcje(id_rcn, source_import_id, import_timestamp, "
        "data_transakcji, liczba_obiektow) VALUES ('RCN-9', 1, 1, '2026-03-01', 1)"
    )
    # Symulacja stanu przed v9: numer pusty, oznaczenie już wzbogacone z EGIB.
    conn.execute(
        "INSERT INTO plots(id_rcn, source_import_id, identyfikator_dzialki, teryt_gminy, obreb) "
        "VALUES ('RCN-9', 1, '106102_9.0042.44/1', '106102_9', 'B-42')"
    )
    conn.execute("UPDATE workspace_meta SET value = '8' WHERE key = 'schema_version'")
    conn.commit()

    apply_schema(conn)   # migracja v8 -> v9

    numer = conn.execute("SELECT obreb_numer FROM plots WHERE id_rcn = 'RCN-9'").fetchone()[0]
    wersja = conn.execute("SELECT value FROM workspace_meta WHERE key = 'schema_version'").fetchone()[0]
    conn.close()
    assert numer == "0042", numer
    # Wersja porównywana z bieżącą, nie zapisana na sztywno -- inaczej test
    # łamie się przy każdej kolejnej migracji, nie mówiąc nic o obrębach.
    from rcn_core.schema import CURRENT_SCHEMA_VERSION
    assert int(wersja) == CURRENT_SCHEMA_VERSION


def test_sortowanie_obrebu_jest_numeryczne_w_ramach_jednostki(tmp_path):
    """„B-10" nie może wypadać przed „B-2".

    Po wzbogaceniu z EGIB kolumna `obreb` to tekst, więc sortowanie
    alfabetyczne daje B-1, B-10, B-11, B-2… Sortujemy po jednostce
    ewidencyjnej i numerze -- prefiks jest w ramach jednostki stały, więc
    kolejność wygląda naturalnie.
    """
    from app.query import QueryFilters, _attribute_clauses  # noqa: F401

    db = tmp_path / "w.sqlite"
    conn = sqlite3.connect(db)
    apply_schema(conn)
    conn.execute(
        "INSERT INTO imports(id, original_filename, stored_filename, tryb, upload_timestamp) "
        "VALUES (1, 'x.gml', 'x.gml', 'snapshot', 1)"
    )
    # Bałuty 1, 2, 10 + Górna 1 -- w kolejności losowej, żeby test nie przechodził przypadkiem.
    dane = [
        ("RCN-B10", "106102_9", "B-10", "0010"),
        ("RCN-G1", "106103_9", "G-1", "0001"),
        ("RCN-B2", "106102_9", "B-2", "0002"),
        ("RCN-B1", "106102_9", "B-1", "0001"),
    ]
    for id_rcn, teryt, oznaczenie, numer in dane:
        conn.execute(
            "INSERT INTO transakcje(id_rcn, source_import_id, import_timestamp, "
            "data_transakcji, liczba_obiektow) VALUES (?, 1, 1, '2026-03-01', 1)",
            (id_rcn,),
        )
        conn.execute(
            "INSERT INTO plots(id_rcn, source_import_id, identyfikator_dzialki, "
            "teryt_gminy, obreb, obreb_numer) VALUES (?, 1, ?, ?, ?, ?)",
            (id_rcn, f"{teryt}.{numer}.1", teryt, oznaczenie, numer),
        )
    refresh_tx_cache(conn, id_rcn_list=None)
    conn.commit()
    try:
        # Tak, jak sortuje endpoint /query dla kolumny „obreb".
        kolejnosc = [r[0] for r in conn.execute(
            "SELECT obreb FROM tx_cache "
            "ORDER BY teryt_gminy ASC NULLS LAST, obreb_numer ASC NULLS LAST, obreb ASC NULLS LAST"
        ).fetchall()]
        assert kolejnosc == ["B-1", "B-2", "B-10", "G-1"], kolejnosc
        # Kontrola: samo sortowanie po tekście dawało złą kolejność.
        alfabetycznie = [r[0] for r in conn.execute(
            "SELECT obreb FROM tx_cache ORDER BY obreb ASC").fetchall()]
        assert alfabetycznie == ["B-1", "B-10", "B-2", "G-1"], alfabetycznie
    finally:
        conn.close()


def test_szukanie_znajduje_transakcje_z_dzialka_w_drugim_obrebie(tmp_path):
    """Transakcja z działkami w dwóch obrębach musi wyjść pod OBOMA.

    `tx_cache` trzyma jednego reprezentanta (MIN), więc filtr tekstowy oparty
    o cache znajdował taką transakcję tylko pod obrębem alfabetycznie
    pierwszym -- choć rozwinięcie wiersza pokazuje oba. Zgłoszenie testera
    2026-09-12 („reszta ma w opisie g42, ale się nie wyszukuje").
    """
    db = tmp_path / "w.sqlite"
    conn = sqlite3.connect(db)
    apply_schema(conn)
    conn.execute(
        "INSERT INTO imports(id, original_filename, stored_filename, tryb, upload_timestamp) "
        "VALUES (1, 'x.gml', 'x.gml', 'snapshot', 1)"
    )
    conn.execute(
        "INSERT INTO transakcje(id_rcn, source_import_id, import_timestamp, "
        "data_transakcji, liczba_obiektow) VALUES ('RCN-2DZ', 1, 1, '2026-03-01', 2)"
    )
    # Dwie działki jednej transakcji: G-41 (trafi do cache jako MIN) i G-42.
    for oznaczenie, numer in (("G-41", "0041"), ("G-42", "0042")):
        conn.execute(
            "INSERT INTO plots(id_rcn, source_import_id, identyfikator_dzialki, "
            "teryt_gminy, obreb, obreb_numer) VALUES ('RCN-2DZ', 1, ?, '106103_9', ?, ?)",
            (f"106103_9.{numer}.7", oznaczenie, numer),
        )
    # Budynek w trzecim obrębie -- filtr ma zaglądać i tu.
    conn.execute(
        "INSERT INTO buildings(id_rcn, source_import_id, identyfikator_budynku, "
        "teryt_gminy, obreb, obreb_numer) "
        "VALUES ('RCN-2DZ', 1, '106103_9.0050.7.1', '106103_9', 'G-50', '0050')"
    )
    refresh_tx_cache(conn, id_rcn_list=None)
    conn.commit()
    reprezentant = conn.execute(
        "SELECT obreb FROM tx_cache WHERE id_rcn = 'RCN-2DZ'").fetchone()[0]
    conn.close()
    assert reprezentant == "G-41", "założenie testu: cache trzyma MIN"

    # Wszystkie trzy formy zapytania muszą trafić w tę samą transakcję.
    for fraza in ("G-41", "G-42", "g42", "G-50", "42", "0042", "50"):
        assert _wyniki(db, {"obreb_search": fraza}) == {"RCN-2DZ"}, fraza
    # Kontrola: obcy obręb nadal nie trafia.
    assert _wyniki(db, {"obreb_search": "G-43"}) == set()
    assert _wyniki(db, {"obreb_search": "43"}) == set()
