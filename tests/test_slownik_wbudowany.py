"""Wbudowany słownik krajowy: oznaczenia obrębów działają zaraz po instalacji.

GML RCN nie zawiera oznaczeń urzędowych („B-42") -- są wyłącznie w EGIB,
którego konsument nie ma. Dlatego aplikacja niesie słownik dla całej Polski
(`rcn_core/resources/obreby-polska.csv.gz`, zrzut EGIB 2026-08-06) i używa go:

- przy imporcie GML-a (nowe dane od razu z oznaczeniami),
- przy migracji do schema v12 (bazy sprzed aktualizacji).

Zasada nadrzędna: **wbudowany słownik nigdy nie nadpisuje oznaczenia, które
już jest** -- ani poprawki operatora z panelu, ani danych z warstw EGIB.
"""
import sqlite3

from rcn_core.schema import apply_schema, refresh_tx_cache
from rcn_core.slownik_obrebow import oznaczenie_wbudowane, wczytaj_krajowy


def test_zasob_jest_wbudowany_i_kompletny():
    slownik = wczytaj_krajowy()
    # Zrzut krajowy: 377 powiatów, ~49,6 tys. obrębów. Luźny próg, żeby test
    # nie padał przy odświeżeniu zrzutu, ale łapał brak zasobu w buildzie.
    assert len(slownik) > 40_000, f"wbudowany słownik ma tylko {len(slownik)} wpisów"
    assert oznaczenie_wbudowane("106103_9", "0024") == "G-24"      # Łódź-Górna
    assert oznaczenie_wbudowane("106102_9", "0042") == "B-42"      # Łódź-Bałuty
    assert oznaczenie_wbudowane("999999_9", "0001") is None        # nie ma = None
    assert oznaczenie_wbudowane(None, "0001") is None


def test_import_nadaje_oznaczenia_bez_zadnych_plikow():
    """Użytkownik importuje własny GML, nie ma żadnego słownika obok bazy."""
    from rcn_core.ingest import _plot_tuple

    wiersz = _plot_tuple({"identyfikator działki": "106102_9.0042.44/1"}, "RCN-1", 1, 2180)
    assert wiersz[4] == "B-42"      # obreb (oznaczenie)
    assert wiersz[5] == "0042"      # obreb_numer -- numer nie ginie


def _baza_sprzed_v12(path):
    """Baza w stanie sprzed aktualizacji: w kolumnie `obreb` same numery."""
    conn = sqlite3.connect(path)
    apply_schema(conn)
    conn.execute(
        "INSERT INTO imports(id, original_filename, stored_filename, tryb, upload_timestamp) "
        "VALUES (1, 'x.gml', 'x.gml', 'delta', 1)"
    )
    dane = [
        ("RCN-1", "106102_9.0042.44/1", "106102_9", "0042"),   # Bałuty -> B-42
        ("RCN-2", "106103_9.0024.7", "106103_9", "0024"),      # Górna -> G-24
        ("RCN-3", "106104_9.0019.3", "106104_9", "0019"),      # Polesie
    ]
    for id_rcn, ident, teryt, numer in dane:
        conn.execute(
            "INSERT INTO transakcje(id_rcn, source_import_id, import_timestamp, "
            "data_transakcji, liczba_obiektow) VALUES (?, 1, 1, '2026-03-01', 1)", (id_rcn,))
        conn.execute(
            "INSERT INTO plots(id_rcn, source_import_id, identyfikator_dzialki, teryt_gminy, "
            "obreb, obreb_numer) VALUES (?, 1, ?, ?, ?, ?)",
            (id_rcn, ident, teryt, numer, numer))       # obreb = numer, jak przed v12
    conn.execute(
        "INSERT INTO locals(id_rcn, source_import_id, identyfikator_lokalu, teryt_gminy, "
        "obreb, obreb_numer) VALUES ('RCN-1', 1, '106102_9.0042.44/1_BUD.2_LOK', "
        "'106102_9', '0042', '0042')")
    refresh_tx_cache(conn, id_rcn_list=None)
    conn.execute("UPDATE workspace_meta SET value = '11' WHERE key LIKE '%schema%'")
    conn.commit()
    return conn


def test_migracja_uzupelnia_oznaczenia(tmp_path):
    conn = _baza_sprzed_v12(tmp_path / "w.sqlite")
    try:
        apply_schema(conn)      # aktualizacja aplikacji
        assert conn.execute(
            "SELECT value FROM workspace_meta WHERE key LIKE '%schema%'").fetchone()[0] == "12"
        oznaczenia = dict(conn.execute("SELECT obreb_numer, obreb FROM plots"))
        assert oznaczenia["0042"] == "B-42"
        assert oznaczenia["0024"] == "G-24"
        # Lokale też, i cache transakcji idzie za zmianą.
        assert conn.execute("SELECT obreb FROM locals").fetchone()[0] == "B-42"
        assert conn.execute(
            "SELECT obreb FROM tx_cache WHERE id_rcn='RCN-1'").fetchone()[0] == "B-42"
        # Numer nie ginie -- wyszukiwanie po obu formach ma dalej działać.
        assert conn.execute(
            "SELECT obreb_numer FROM plots WHERE obreb='B-42'").fetchone()[0] == "0042"
    finally:
        conn.close()


def test_reczna_poprawka_operatora_przezywa_migracje(tmp_path):
    """Wpis zmieniony w panelu jest nadrzędny wobec wbudowanego słownika."""
    conn = _baza_sprzed_v12(tmp_path / "w.sqlite")
    try:
        conn.execute("UPDATE plots SET obreb = 'Moja nazwa obrębu' WHERE obreb_numer = '0042'")
        conn.commit()

        apply_schema(conn)

        assert conn.execute(
            "SELECT obreb FROM plots WHERE obreb_numer = '0042'").fetchone()[0] == "Moja nazwa obrębu"
        # Obręby BEZ własnego oznaczenia mają zostać uzupełnione mimo to.
        assert conn.execute(
            "SELECT obreb FROM plots WHERE obreb_numer = '0024'").fetchone()[0] == "G-24"
    finally:
        conn.close()


def test_migracja_jest_idempotentna(tmp_path):
    conn = _baza_sprzed_v12(tmp_path / "w.sqlite")
    try:
        apply_schema(conn)
        przed = conn.execute("SELECT obreb_numer, obreb FROM plots ORDER BY 1").fetchall()
        apply_schema(conn)
        assert conn.execute("SELECT obreb_numer, obreb FROM plots ORDER BY 1").fetchall() == przed
    finally:
        conn.close()


def test_panel_pokazuje_oznaczenia_z_wbudowanego(client, auth, tmp_data_dir):
    """Bez pliku słownika panel i tak ma pokazać, skąd wzięły się oznaczenia."""
    folder = tmp_data_dir / "workspaces" / "ws-test"
    folder.mkdir(parents=True)
    conn = _baza_sprzed_v12(folder / "workspace.sqlite")
    apply_schema(conn)
    conn.close()

    dane = client.get("/api/workspaces/ws-test/obreby", auth=auth).json()
    assert dane["plik"] is None                    # żadnego pliku obok bazy
    assert dane["z_wbudowanego"] >= 3              # a oznaczenia są
    oznaczenia = {w["numer_obrebu"]: w for w in dane["obreby"]}
    assert oznaczenia["0042"]["oznaczenie"] == "B-42"
    assert oznaczenia["0042"]["zrodlo"] == "wbudowany"


def test_edycja_w_panelu_nadpisuje_wbudowany(client, auth, tmp_data_dir):
    """Operator poprawia oznaczenie -> zapis do pliku ze źródłem `reczny`."""
    folder = tmp_data_dir / "workspaces" / "ws-edycja"
    folder.mkdir(parents=True)
    conn = _baza_sprzed_v12(folder / "workspace.sqlite")
    apply_schema(conn)
    conn.close()

    resp = client.put(
        "/api/workspaces/ws-edycja/obreby", auth=auth,
        json={"wpisy": [{"teryt_gminy": "106102_9", "numer_obrebu": "0042",
                         "oznaczenie": "Bałuty-Centrum", "gmina": "ŁÓDŹ"}]},
    )
    assert resp.status_code == 200, resp.text[:200]

    dane = client.get("/api/workspaces/ws-edycja/obreby", auth=auth).json()
    wpis = next(w for w in dane["obreby"] if w["numer_obrebu"] == "0042")
    assert wpis["oznaczenie"] == "Bałuty-Centrum"
    assert wpis["zrodlo"] == "reczny"

    # Zastosowanie przenosi poprawkę do bazy...
    client.post("/api/workspaces/ws-edycja/obreby/zastosuj?wykonaj=true", auth=auth)
    conn = sqlite3.connect(folder / "workspace.sqlite")
    try:
        assert conn.execute(
            "SELECT obreb FROM plots WHERE obreb_numer='0042'").fetchone()[0] == "Bałuty-Centrum"
        # ...i kolejna migracja jej nie cofa.
        apply_schema(conn)
        assert conn.execute(
            "SELECT obreb FROM plots WHERE obreb_numer='0042'").fetchone()[0] == "Bałuty-Centrum"
    finally:
        conn.close()
