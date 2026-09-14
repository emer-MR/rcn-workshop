"""RCN_PUBLIC_READONLY=1 -- instancja otwarta do przeglądania, admin na hasło.

Gość bez nagłówka Authorization dostaje rolę `readonly` zamiast 401, ale:
- operacje admina nadal odpowiadają 403,
- BŁĘDNE hasło nadal daje 401 (żadnego cichego zejścia do roli gościa),
- `/login` wymusza okienko Basic Auth, bo w tym trybie nikt inny już tego nie robi.

Domyślnie (bez zmiennej) zachowanie jest niezmienione -- pilnuje tego
`test_domyslnie_bez_zmiennej_nadal_401`.
"""
import shutil
import sys

import pytest


def _przeladuj_app():
    for mod in list(sys.modules):
        if mod == "app.config" or mod.startswith("app.") or mod == "app":
            del sys.modules[mod]


@pytest.fixture
def public_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("RCN_DATA_DIR", str(data))
    monkeypatch.setenv("RCN_PUBLIC_READONLY", "1")
    monkeypatch.setenv("RCN_AUTH_USER", "admin")
    monkeypatch.setenv("RCN_AUTH_PASSWORD", "tajne-haslo")
    _przeladuj_app()
    yield data
    _przeladuj_app()
    shutil.rmtree(data, ignore_errors=True)


@pytest.fixture
def public_client(public_env):
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def test_gosc_bez_logowania_dostaje_readonly(public_client):
    resp = public_client.get("/api/me")
    assert resp.status_code == 200
    dane = resp.json()
    assert dane["role"] == "readonly"
    assert dane["anonymous"] is True


def test_gosc_widzi_liste_workspaceow(public_client):
    assert public_client.get("/workspaces").status_code == 200
    assert public_client.get("/api/workspaces").status_code == 200


def test_gosc_nie_tworzy_workspaceu(public_client):
    # 403, nie 401: gość JEST uwierzytelniony (jako readonly), tylko bez uprawnień.
    resp = public_client.post("/api/workspaces", json={"name": "proba"})
    assert resp.status_code == 403


def test_gosc_nie_widzi_metryk(public_client):
    assert public_client.get("/metrics").status_code == 403


def test_admin_z_haslem_nadal_adminem(public_client):
    resp = public_client.get("/api/me", auth=("admin", "tajne-haslo"))
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
    assert resp.json()["anonymous"] is False


def test_bledne_haslo_to_401_a_nie_ciche_zejscie_do_goscia(public_client):
    # Kluczowe dla diagnozowalności: literówka w haśle admina ma być widoczna
    # od razu, a nie objawiać się znikającymi przyciskami w UI.
    resp = public_client.get("/api/me", auth=("admin", "nie-to-haslo"))
    assert resp.status_code == 401


def test_login_bez_haslo_wymusza_okienko(public_client):
    resp = public_client.get("/login")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Basic"


def test_login_z_haslem_przekierowuje_na_liste(public_client):
    resp = public_client.get("/login", auth=("admin", "tajne-haslo"), follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/workspaces"


def test_splash_zaprasza_bez_logowania(public_client):
    tresc = public_client.get("/").text
    assert "Przejdź do aplikacji" in tresc
    assert "Dostęp wyłącznie dla zalogowanych" not in tresc


def test_domyslnie_bez_zmiennej_nadal_401(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("RCN_DATA_DIR", str(data))
    monkeypatch.delenv("RCN_PUBLIC_READONLY", raising=False)
    monkeypatch.setenv("RCN_AUTH_PASSWORD", "tajne-haslo")
    _przeladuj_app()
    try:
        from fastapi.testclient import TestClient

        from app.main import app
        client = TestClient(app)
        assert client.get("/api/me").status_code == 401
        assert client.get("/workspaces").status_code == 401
        assert "Dostęp wyłącznie dla zalogowanych" in client.get("/").text
    finally:
        _przeladuj_app()
        shutil.rmtree(data, ignore_errors=True)
