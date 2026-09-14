"""Słownik oznaczeń obrębów: plik CSV, scalanie i stosowanie do bazy.

Oznaczenie urzędowe obrębu („B-42", nazwa wsi) jest tylko w EGIB -- GML RCN
niesie sam numer. Słownik przenosi oznaczenia do workspace'u w 12 KB zamiast
70 MB warstwy, jedzie w paczce i daje się poprawić ręcznie.
"""
import sqlite3

import pytest

from rcn_core.schema import apply_schema, refresh_tx_cache
from rcn_core.slownik_obrebow import (
    SUFIKS,
    ZRODLO_RECZNY,
    WpisObrebu,
    obreby_z_bazy,
    rozbij_identyfikator,
    scal,
    sciezka_slownika,
    wczytaj,
    zapisz,
    zastosuj_do_bazy,
    znajdz_slownik,
)


def _baza(path):
    conn = sqlite3.connect(path)
    apply_schema(conn)
    conn.execute(
        "INSERT INTO imports(id, original_filename, stored_filename, tryb, upload_timestamp) "
        "VALUES (1, 'x.gml', 'x.gml', 'snapshot', 1)"
    )
    dane = [
        ("RCN-1", "106102_9.0042.44/1", "106102_9", "0042"),
        ("RCN-2", "106103_9.0024.7", "106103_9", "0024"),
        ("RCN-3", "133/12", None, None),          # niepełny identyfikator z GML
    ]
    for id_rcn, ident, teryt, numer in dane:
        conn.execute(
            "INSERT INTO transakcje(id_rcn, source_import_id, import_timestamp, "
            "data_transakcji, liczba_obiektow) VALUES (?, 1, 1, '2026-03-01', 1)",
            (id_rcn,),
        )
        conn.execute(
            "INSERT INTO plots(id_rcn, source_import_id, identyfikator_dzialki, "
            "teryt_gminy, obreb, obreb_numer) VALUES (?, 1, ?, ?, ?, ?)",
            (id_rcn, ident, teryt, numer, numer),
        )
    refresh_tx_cache(conn, id_rcn_list=None)
    conn.commit()
    return conn


def test_zapis_i_wczytanie_round_trip(tmp_path):
    plik = tmp_path / f"Kutno{SUFIKS}"
    wpisy = [
        WpisObrebu("106102_9", "0042", "B-42", "ŁÓDŹ-BAŁUTY", "egib-20260806"),
        WpisObrebu("106103_9", "0024", "G-24", "ŁÓDŹ-GÓRNA", "egib-20260806"),
    ]
    assert zapisz(plik, wpisy) == 2
    odczyt = wczytaj(plik)
    assert set(odczyt) == {"106102_9.0042", "106103_9.0024"}
    assert odczyt["106102_9.0042"].oznaczenie == "B-42"
    assert odczyt["106102_9.0042"].gmina == "ŁÓDŹ-BAŁUTY"
    # Polskie znaki muszą przeżyć zapis i odczyt (plik trafia też do Excela).
    assert "ŁÓDŹ-GÓRNA" in plik.read_text(encoding="utf-8-sig")


def test_wiersz_bez_oznaczenia_jest_pomijany(tmp_path):
    plik = tmp_path / f"x{SUFIKS}"
    plik.write_text(
        "teryt_gminy;numer_obrebu;oznaczenie;gmina;zrodlo\n"
        "106102_9;0042;;;egib\n"
        "106102_9;0043;B-43;;egib\n",
        encoding="utf-8",
    )
    odczyt = wczytaj(plik)
    assert set(odczyt) == {"106102_9.0043"}


def test_scalanie_nie_nadpisuje_recznych_poprawek(tmp_path):
    stare = {
        "106102_9.0042": WpisObrebu("106102_9", "0042", "B-42 (poprawione)", "", ZRODLO_RECZNY),
        "106102_9.0043": WpisObrebu("106102_9", "0043", "stare-z-egib", "", "egib-20260101"),
    }
    nowe = [
        WpisObrebu("106102_9", "0042", "B-42", "", "egib-20260806"),
        WpisObrebu("106102_9", "0043", "B-43", "", "egib-20260806"),
        WpisObrebu("106102_9", "0044", "B-44", "", "egib-20260806"),
    ]
    wynik = scal(stare, nowe)
    assert wynik["106102_9.0042"].oznaczenie == "B-42 (poprawione)"   # ręczne zostaje
    assert wynik["106102_9.0043"].oznaczenie == "B-43"                # z EGIB odświeżone
    assert wynik["106102_9.0044"].oznaczenie == "B-44"                # nowe dołożone


def test_niepelny_identyfikator_nie_wywraca_rozbicia():
    assert rozbij_identyfikator("106102_9.0042.44/1") == ("106102_9", "0042")
    assert rozbij_identyfikator("133/12") is None
    assert rozbij_identyfikator(None) is None


def test_stosowanie_do_bazy_zachowuje_numer(tmp_path):
    conn = _baza(tmp_path / "w.sqlite")
    try:
        slownik = {
            "106102_9.0042": WpisObrebu("106102_9", "0042", "B-42"),
            "106103_9.0024": WpisObrebu("106103_9", "0024", "G-24"),
        }
        stat = zastosuj_do_bazy(conn, slownik, wykonaj=True)
        assert stat["zmienione"] == 2
        wiersze = dict(conn.execute(
            "SELECT id_rcn, obreb FROM plots WHERE obreb IS NOT NULL").fetchall())
        assert wiersze == {"RCN-1": "B-42", "RCN-2": "G-24"}
        # Numer zostaje -- inaczej wyszukiwanie po „0042" przestałoby działać.
        numery = dict(conn.execute(
            "SELECT id_rcn, obreb_numer FROM plots WHERE obreb_numer IS NOT NULL").fetchall())
        assert numery == {"RCN-1": "0042", "RCN-2": "0024"}
        # tx_cache odświeżony, więc tabela i filtry widzą oznaczenia.
        assert dict(conn.execute(
            "SELECT id_rcn, obreb FROM tx_cache WHERE obreb IS NOT NULL").fetchall()) == {
            "RCN-1": "B-42", "RCN-2": "G-24"}
    finally:
        conn.close()


def test_dry_run_nic_nie_zapisuje(tmp_path):
    conn = _baza(tmp_path / "w.sqlite")
    try:
        slownik = {"106102_9.0042": WpisObrebu("106102_9", "0042", "B-42")}
        stat = zastosuj_do_bazy(conn, slownik, wykonaj=False)
        assert stat["zmienione"] == 1
        assert stat["bez_dopasowania"] == 1     # RCN-2 nie ma wpisu w słowniku
        obecne = conn.execute(
            "SELECT obreb FROM plots WHERE id_rcn = 'RCN-1'").fetchone()[0]
        assert obecne == "0042", "dry-run nie może ruszyć bazy"
    finally:
        conn.close()


def test_obreby_z_bazy_to_pary_bez_smieci(tmp_path):
    conn = _baza(tmp_path / "w.sqlite")
    try:
        pary = obreby_z_bazy(conn)
        # Rekord z niepełnym identyfikatorem („133/12") nie ma numeru, więc
        # nie zaśmieca listy pokazywanej operatorowi.
        assert pary == [("106102_9", "0042", "0042"), ("106103_9", "0024", "0024")]
    finally:
        conn.close()


def test_sciezka_i_wyszukiwanie_pliku(tmp_path):
    assert sciezka_slownika(tmp_path, "Kutno.sqlite").name == f"Kutno{SUFIKS}"
    assert znajdz_slownik(tmp_path) is None
    (tmp_path / f"Kutno{SUFIKS}").write_text("teryt_gminy;numer_obrebu;oznaczenie\n", encoding="utf-8")
    assert znajdz_slownik(tmp_path).name == f"Kutno{SUFIKS}"


def test_slownik_wchodzi_do_paczki(tmp_path):
    """Oznaczenia MAJĄ dojechać do odbiorcy -- inaczej niż sidecary generowane
    per instancja, które paczka pomija."""
    cli = pytest.importorskip("rcn_producer.cli")
    conn = _baza(tmp_path / "Kutno.sqlite")
    conn.close()
    zapisz(tmp_path / f"Kutno{SUFIKS}", [WpisObrebu("106102_9", "0042", "B-42")])

    items = cli._collect_clean_cut(tmp_path, "Kutno")
    names = sorted(arc for _, arc in items)
    assert f"Kutno/Kutno{SUFIKS}" in names, names


# --- API -----------------------------------------------------------------


def _workspace_z_danymi(client, auth, tmp_data_dir):
    """Pusty workspace z API + wiersze wstawione wprost do jego bazy."""
    r = client.post("/api/workspaces", json={"name": "Testowy"}, auth=auth)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    baza = next((tmp_data_dir / "workspaces" / wid).glob("*.sqlite"))
    conn = _baza_dopisz(baza)
    conn.close()
    return wid, baza


def _baza_dopisz(baza):
    conn = sqlite3.connect(baza)
    conn.execute(
        "INSERT INTO imports(id, original_filename, stored_filename, tryb, upload_timestamp) "
        "VALUES (1, 'x.gml', 'x.gml', 'snapshot', 1)"
    )
    for id_rcn, ident, teryt, numer in (
        ("RCN-1", "106102_9.0042.44/1", "106102_9", "0042"),
        ("RCN-2", "106103_9.0024.7", "106103_9", "0024"),
    ):
        conn.execute(
            "INSERT INTO transakcje(id_rcn, source_import_id, import_timestamp, "
            "data_transakcji, liczba_obiektow) VALUES (?, 1, 1, '2026-03-01', 1)",
            (id_rcn,),
        )
        conn.execute(
            "INSERT INTO plots(id_rcn, source_import_id, identyfikator_dzialki, "
            "teryt_gminy, obreb, obreb_numer) VALUES (?, 1, ?, ?, ?, ?)",
            (id_rcn, ident, teryt, numer, numer),
        )
    refresh_tx_cache(conn, id_rcn_list=None)
    conn.commit()
    return conn


def test_api_lista_obrebow_pokazuje_stan(client, auth, tmp_data_dir):
    wid, _ = _workspace_z_danymi(client, auth, tmp_data_dir)
    d = client.get(f"/api/workspaces/{wid}/obreby", auth=auth).json()
    assert d["obrebow_w_bazie"] == 2
    # Bez własnego pliku pokrycie daje słownik WBUDOWANY w aplikację (v12):
    # oba obręby to Łódź, więc oznaczenia są znane od razu.
    assert d["plik"] is None
    assert d["pokrytych"] == 2 and d["z_wbudowanego"] == 2
    assert all(o["zrodlo"] == "wbudowany" for o in d["obreby"])
    assert {o["numer_obrebu"] for o in d["obreby"]} == {"0042", "0024"}


def test_api_zapis_edycji_i_zastosowanie(client, auth, tmp_data_dir):
    wid, baza = _workspace_z_danymi(client, auth, tmp_data_dir)
    wpisy = {"wpisy": [
        {"teryt_gminy": "106102_9", "numer_obrebu": "0042", "oznaczenie": "B-42", "gmina": "BAŁUTY"},
        {"teryt_gminy": "106103_9", "numer_obrebu": "0024", "oznaczenie": "G-24", "gmina": ""},
    ]}
    r = client.put(f"/api/workspaces/{wid}/obreby", json=wpisy, auth=auth)
    assert r.status_code == 200, r.text
    assert r.json()["wpisow"] == 2

    d = client.get(f"/api/workspaces/{wid}/obreby", auth=auth).json()
    assert d["pokrytych"] == 2 and d["zastosowanych"] == 0
    # Edycja z UI musi być chroniona przed nadpisaniem z EGIB.
    assert all(o["zrodlo"] == ZRODLO_RECZNY for o in d["obreby"])

    r = client.post(f"/api/workspaces/{wid}/obreby/zastosuj", auth=auth)
    assert r.status_code == 200, r.text
    assert r.json()["zmienione"] == 2

    conn = sqlite3.connect(baza)
    try:
        assert dict(conn.execute("SELECT id_rcn, obreb FROM plots").fetchall()) == {
            "RCN-1": "B-42", "RCN-2": "G-24"}
        assert dict(conn.execute("SELECT id_rcn, obreb_numer FROM plots").fetchall()) == {
            "RCN-1": "0042", "RCN-2": "0024"}
    finally:
        conn.close()


def test_api_zastosuj_bez_slownika_mowi_co_zrobic(client, auth, tmp_data_dir):
    wid, _ = _workspace_z_danymi(client, auth, tmp_data_dir)
    r = client.post(f"/api/workspaces/{wid}/obreby/zastosuj", auth=auth)
    assert r.status_code == 400
    assert "obreby.csv" in r.json()["detail"]


def test_api_upload_csv(client, auth, tmp_data_dir):
    wid, _ = _workspace_z_danymi(client, auth, tmp_data_dir)
    csv_tresc = (
        "teryt_gminy;numer_obrebu;oznaczenie;gmina;zrodlo\n"
        "106102_9;0042;B-42;ŁÓDŹ-BAŁUTY;egib-20260806\n"
    ).encode()
    r = client.post(
        f"/api/workspaces/{wid}/obreby/plik",
        files={"file": ("lodz.obreby.csv", csv_tresc, "text/csv")},
        auth=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["wpisow"] == 1
    d = client.get(f"/api/workspaces/{wid}/obreby", auth=auth).json()
    assert d["plik"].endswith(SUFIKS)
    # Wgrany plik przykrywa słownik wbudowany dla swojego obrębu; drugi obręb
    # nadal pokrywa wbudowany, stąd 2 pokryte przy 1 wpisie w pliku.
    assert d["wpisow_w_slowniku"] == 1
    assert d["pokrytych"] == 2
    wgrany = next(o for o in d["obreby"] if o["numer_obrebu"] == "0042")
    assert wgrany["zrodlo"] == "egib-20260806"


def test_api_pusty_csv_odrzucony(client, auth, tmp_data_dir):
    wid, _ = _workspace_z_danymi(client, auth, tmp_data_dir)
    r = client.post(
        f"/api/workspaces/{wid}/obreby/plik",
        files={"file": ("puste.csv", b"cos;innego\n1;2\n", "text/csv")},
        auth=auth,
    )
    assert r.status_code == 400
    assert "poprawnego wiersza" in r.json()["detail"]


# --- kontrola jakości oznaczeń z EGIB -------------------------------------


@pytest.mark.parametrize("oznaczenie, teryt, numer, oczekiwane", [
    # Prawdziwe oznaczenia -- przechodzą.
    ("B-1", "106102_9", "0001", True),
    ("6-06-15", "146502_8", "0615", True),
    ("RASZEW  PIASKI", "100201_1", "0003", True),
    ("OBRĘB 2", "106201_1", "0002", True),
    ("Czarna Woda", "221301_1", "0001", True),
    ("Głowno 1", "102001_1", "0001", True),
    # Śmieci ze zrzutu 2026-08-06 -- odrzucane.
    ("(101401_1.0001)", "101401_1", "0001", False),   # powiat sieradzki
    ("01", "100101_1", "0001", False),                # powiat bełchatowski
    ("0001", "320301_4", "0001", False),              # powiat drawski
    ("", "106102_9", "0001", False),
    ("   ", "106102_9", "0001", False),
])
def test_oznaczenie_musi_wnosic_informacje(oznaczenie, teryt, numer, oczekiwane):
    from rcn_core.slownik_obrebow import oznaczenie_wnosi_informacje
    assert oznaczenie_wnosi_informacje(oznaczenie, teryt, numer) is oczekiwane


def test_tx_cache_idzie_za_oznaczeniem_takze_dla_samych_budynkow(tmp_path):
    """Transakcja bez działek (tylko budynek) też musi dostać oznaczenie w cache.

    `zastosuj_do_bazy` aktualizuje `tx_cache.obreb` celowanym UPDATE-em
    (COALESCE plots -> buildings), a nie pełnym `refresh_tx_cache` -- ten na
    Warszawie idzie w kwadranse. Test pilnuje gałęzi „tylko budynki".
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
        "data_transakcji, liczba_obiektow) VALUES ('RCN-B', 1, 1, '2026-03-01', 1)"
    )
    conn.execute(
        "INSERT INTO buildings(id_rcn, source_import_id, identyfikator_budynku, "
        "teryt_gminy, obreb, obreb_numer) "
        "VALUES ('RCN-B', 1, '106102_9.0042.44.1', '106102_9', '0042', '0042')"
    )
    refresh_tx_cache(conn, id_rcn_list=None)
    conn.commit()
    try:
        zastosuj_do_bazy(conn, {"106102_9.0042": WpisObrebu("106102_9", "0042", "B-42")})
        assert conn.execute(
            "SELECT obreb FROM buildings WHERE id_rcn = 'RCN-B'").fetchone()[0] == "B-42"
        assert conn.execute(
            "SELECT obreb FROM tx_cache WHERE id_rcn = 'RCN-B'").fetchone()[0] == "B-42"
    finally:
        conn.close()
