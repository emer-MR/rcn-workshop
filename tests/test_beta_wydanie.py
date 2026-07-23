"""Elementy wydania beta: wersja z jednego źródła, badge, /pobierz, rcn.env.

Patrz app/version.py (źródło prawdy) + desktop.py::_load_rcn_env.
"""
import base64
import sys

import pytest


def _reload_app_modules():
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("RCN_DATA_DIR", str(data))
    monkeypatch.setenv("RCN_ALLOW_INSECURE_DEFAULT", "1")
    monkeypatch.setenv("RCN_READONLY_USER", "test")
    monkeypatch.setenv("RCN_READONLY_PASSWORD", "test")
    _reload_app_modules()
    yield
    _reload_app_modules()


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


_AUTH = {"Authorization": "Basic " + base64.b64encode(b"test:test").decode()}


# --- wersja -----------------------------------------------------------------

def test_healthz_zwraca_wersje_ze_zrodla(app_env):
    from app.version import __version__

    body = _client().get("/healthz").json()
    assert body["version"] == __version__


def test_badge_beta_w_ui_gdy_wydanie_beta(app_env):
    """Badge pokazuje się wszędzie tam, gdzie użytkownik pracuje -- w becie."""
    from app.version import IS_BETA

    html = _client().get("/workspaces", headers=_AUTH).text
    assert ("rcn-beta-badge" in html) == IS_BETA


# --- /pobierz ---------------------------------------------------------------

def test_pobierz_przekierowuje_na_download_url(app_env, monkeypatch):
    monkeypatch.setenv("RCN_DOWNLOAD_URL", "https://example.com/setup.exe")
    _reload_app_modules()

    resp = _client().get("/pobierz", follow_redirects=False)

    assert resp.status_code == 307
    assert resp.headers["location"] == "https://example.com/setup.exe"
    assert resp.status_code != 401  # publiczny -- pobierają osoby bez konta


def test_pobierz_404_bez_zmiennej(app_env, monkeypatch):
    monkeypatch.delenv("RCN_DOWNLOAD_URL", raising=False)
    _reload_app_modules()

    assert _client().get("/pobierz", follow_redirects=False).status_code == 404


# --- rcn.env (konfiguracja testera w desktopie) ------------------------------

def test_load_rcn_env_wczytuje_i_nie_nadpisuje(tmp_path, monkeypatch):
    """Plik ustawia brakujące zmienne; już ustawione w env mają pierwszeństwo."""
    from desktop import _load_rcn_env

    (tmp_path / "rcn.env").write_text(
        "# komentarz\n"
        "RCN_TEST_FAKE_A=z_pliku\n"
        "RCN_TEST_FAKE_B=przegrywa_z_env\n"
        "  \n"
        "linia-bez-znaku-rownosci\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("RCN_TEST_FAKE_A", raising=False)
    monkeypatch.setenv("RCN_TEST_FAKE_B", "z_env")

    _load_rcn_env(str(tmp_path))

    import os
    assert os.environ["RCN_TEST_FAKE_A"] == "z_pliku"
    assert os.environ["RCN_TEST_FAKE_B"] == "z_env"
    monkeypatch.delenv("RCN_TEST_FAKE_A", raising=False)


def test_load_rcn_env_brak_pliku_nie_wybucha(tmp_path):
    from desktop import _load_rcn_env

    _load_rcn_env(str(tmp_path / "nie-ma-takiego-katalogu"))
    _load_rcn_env(None)
