"""Nazwisko notariusza nie wychodzi do użytkownika bez uprawnień admina.

Akt notarialny niesie `tworcaDokumentu` -- imię i nazwisko. Na instancji
dostępnej publicznie (`RCN_PUBLIC_READONLY`) albo z kontem readonly nie ma
powodu, żeby nazwisko trafiało do przeglądającego. Numer repertorium
(`dokument`) ZOSTAJE: identyfikuje akt, a osoby nie wskazuje.

Maskowanie jest po stronie serwera, więc test sprawdza odpowiedzi API i pliki
eksportu, a nie to, co rysuje przeglądarka -- ukrycie w CSS byłoby pozorne.
"""
import io
import shutil
import sqlite3
import sys
import zipfile

import pytest

NOTARIUSZ = "NOTARIUSZ ANNA KOWALSKA"
REPERTORIUM = "2998/2025 z dnia 2025-12-29"


def _przeladuj_app():
    for mod in list(sys.modules):
        if mod == "app.config" or mod.startswith("app.") or mod == "app":
            del sys.modules[mod]


def _workspace_z_transakcja(data_dir):
    """Workspace z jedną transakcją niosącą nazwisko w kolumnie i w atrybutach."""
    from rcn_core.schema import apply_schema, refresh_tx_cache

    folder = data_dir / "workspaces" / "test-ws"
    folder.mkdir(parents=True)
    db = folder / "workspace.sqlite"
    conn = sqlite3.connect(db)
    apply_schema(conn)
    conn.execute(
        "INSERT INTO imports(id, original_filename, stored_filename, tryb, upload_timestamp) "
        "VALUES (1, 'x.gml', 'x.gml', 'delta', 1)"
    )
    conn.execute(
        "INSERT INTO transakcje(id_rcn, source_import_id, import_timestamp, data_transakcji, "
        "cena_transakcji_brutto, rodzaj_nieruchomosci, liczba_obiektow, dokument, "
        "tworca_dokumentu, attributes_json) VALUES "
        "('RCN-1', 1, 1, '2026-03-01', 500000, 'lokal', 1, ?, ?, ?)",
        (REPERTORIUM, NOTARIUSZ,
         '{"twórca dokumentu": "' + NOTARIUSZ + '", "oznaczenie dokumentu": "' + REPERTORIUM + '"}'),
    )
    # ⚠️ Atrybuty obiektu niosą KOPIĘ danych transakcji -- tak wygląda realny
    # GML (sprawdzone na produkcji). Fixture bez tego przepuszczał przeciek
    # w `plots[].extra`, bo maskowana była tylko sama transakcja.
    attrs_obiektu = ('{"id_RCN": "RCN-1", "twórca dokumentu": "' + NOTARIUSZ
                     + '", "dokument": "' + REPERTORIUM + '"}')
    conn.execute(
        "INSERT INTO plots(id_rcn, source_import_id, identyfikator_dzialki, teryt_gminy, "
        "obreb, obreb_numer, powierzchnia_m2, attributes_json) VALUES "
        "('RCN-1', 1, '106102_9.0042.44/1', '106102_9', '0042', '0042', 1000, ?)",
        (attrs_obiektu,),
    )
    conn.execute(
        "INSERT INTO buildings(id_rcn, source_import_id, identyfikator_budynku, teryt_gminy, "
        "obreb, obreb_numer, pow_uzytkowa, attributes_json) VALUES "
        "('RCN-1', 1, '106102_9.0042.44/1_BUD', '106102_9', '0042', '0042', 120, ?)",
        (attrs_obiektu,),
    )
    conn.execute(
        "INSERT INTO locals(id_rcn, source_import_id, identyfikator_lokalu, teryt_gminy, "
        "obreb, obreb_numer, pow_uzytkowa, attributes_json) VALUES "
        "('RCN-1', 1, '106102_9.0042.44/1_BUD.3_LOK', '106102_9', '0042', '0042', 48, ?)",
        (attrs_obiektu,),
    )
    refresh_tx_cache(conn, id_rcn_list=None)
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def public_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("RCN_DATA_DIR", str(data))
    monkeypatch.setenv("RCN_PUBLIC_READONLY", "1")
    monkeypatch.setenv("RCN_AUTH_USER", "admin")
    monkeypatch.setenv("RCN_AUTH_PASSWORD", "tajne-haslo")
    monkeypatch.setenv("RCN_ALLOW_INSECURE_DEFAULT", "1")
    _przeladuj_app()
    _workspace_z_transakcja(data)
    yield data
    _przeladuj_app()
    shutil.rmtree(data, ignore_errors=True)


@pytest.fixture
def client(public_env):
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


ADMIN = ("admin", "tajne-haslo")


def test_gosc_nie_dostaje_nazwiska_w_szczegolach(client):
    dane = client.get("/api/workspaces/test-ws/transactions/details/RCN-1").json()
    tx = dane["transakcja"]

    assert not tx.get("tworca_dokumentu"), "nazwisko poszło w kolumnie"
    # Kopia w surowych atrybutach GML to druga droga wycieku -- łatwa do przeoczenia.
    assert NOTARIUSZ not in str(tx.get("extra")), "nazwisko poszło w atrybutach"
    # Cała odpowiedź, nie tylko transakcja: atrybuty działek, budynków i lokali
    # niosą kopię danych transakcji (tak wygląda realny GML).
    przecieki = [
        f"{grupa}[{i}]"
        for grupa in ("plots", "buildings", "locals")
        for i, obiekt in enumerate(dane[grupa])
        if NOTARIUSZ in str(obiekt)
    ]
    assert not przecieki, f"nazwisko poszło w: {przecieki}"
    assert NOTARIUSZ not in str(dane), "nazwisko gdziekolwiek w odpowiedzi"
    # Repertorium ma zostać -- identyfikuje akt, nie osobę.
    assert tx["dokument"] == REPERTORIUM


def test_admin_widzi_nazwisko(client):
    dane = client.get("/api/workspaces/test-ws/transactions/details/RCN-1", auth=ADMIN).json()
    assert dane["transakcja"]["tworca_dokumentu"] == NOTARIUSZ
    assert NOTARIUSZ in str(dane["transakcja"]["extra"])


def test_gosc_nie_dostaje_nazwiska_w_csv(client):
    resp = client.get("/api/workspaces/test-ws/export.csv")
    assert resp.status_code == 200
    tresc = resp.content.decode("utf-8-sig")
    assert NOTARIUSZ not in tresc
    assert REPERTORIUM in tresc          # repertorium zostaje


def test_admin_dostaje_nazwisko_w_csv(client):
    resp = client.get("/api/workspaces/test-ws/export.csv", auth=ADMIN)
    assert NOTARIUSZ in resp.content.decode("utf-8-sig")


def _teksty_z_xlsx(zawartosc: bytes) -> str:
    """XLSX to ZIP; wartości tekstowe siedzą w sharedStrings.xml."""
    with zipfile.ZipFile(io.BytesIO(zawartosc)) as zf:
        nazwy = [n for n in zf.namelist() if n.endswith(".xml")]
        return "\n".join(zf.read(n).decode("utf-8", "ignore") for n in nazwy)


def test_gosc_nie_dostaje_nazwiska_w_xlsx(client):
    resp = client.get("/api/workspaces/test-ws/export.xlsx")
    assert resp.status_code == 200
    tresc = _teksty_z_xlsx(resp.content)
    assert NOTARIUSZ not in tresc
    assert "Notariusz" in tresc          # nagłówek kolumny zostaje, wartość nie


def test_admin_dostaje_nazwisko_w_xlsx(client):
    resp = client.get("/api/workspaces/test-ws/export.xlsx", auth=ADMIN)
    assert NOTARIUSZ in _teksty_z_xlsx(resp.content)


def test_konto_readonly_tez_nie_widzi(tmp_path, monkeypatch):
    """Druga droga do roli readonly: konto z hasłem, bez trybu publicznego."""
    data = tmp_path / "data"
    (data / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("RCN_DATA_DIR", str(data))
    monkeypatch.delenv("RCN_PUBLIC_READONLY", raising=False)
    monkeypatch.setenv("RCN_AUTH_USER", "admin")
    monkeypatch.setenv("RCN_AUTH_PASSWORD", "tajne-haslo")
    monkeypatch.setenv("RCN_READONLY_USER", "widz")
    monkeypatch.setenv("RCN_READONLY_PASSWORD", "haslo-widza")
    monkeypatch.setenv("RCN_ALLOW_INSECURE_DEFAULT", "1")
    _przeladuj_app()
    try:
        _workspace_z_transakcja(data)
        from fastapi.testclient import TestClient

        from app.main import app
        c = TestClient(app)

        dane = c.get("/api/workspaces/test-ws/transactions/details/RCN-1",
                     auth=("widz", "haslo-widza")).json()
        assert not dane["transakcja"].get("tworca_dokumentu")
        assert NOTARIUSZ not in str(dane)
    finally:
        _przeladuj_app()
        shutil.rmtree(data, ignore_errors=True)


def test_gosc_nie_widzi_zrzutow_z_danymi_na_pomocy(client):
    """`/help` jest pod require_auth, które w trybie publicznym przepuszcza gościa.

    Zrzuty 2-6 pokazują realne transakcje wraz z nazwiskami notariuszy, więc
    o ich renderowaniu decyduje ROLA, nie sam fakt uwierzytelnienia.
    """
    html = client.get("/help").text
    for numer in (2, 3, 4, 5, 6):
        assert f"screenshots/{numer}.png" not in html, f"zrzut {numer} poszedł do gościa"
    # Zrzut 1 (same agregaty) i treść instrukcji zostają.
    assert "screenshots/1.png" in html
    assert "Notariusz" in html          # opis paska meta, nie dane


def test_admin_widzi_komplet_zrzutow(client):
    html = client.get("/help", auth=ADMIN).text
    for numer in (1, 2, 3, 4, 5, 6):
        assert f"screenshots/{numer}.png" in html
