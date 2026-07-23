"""Endpoint /ankieta -- krótki adres przekierowujący na formularz opinii.

Trzy własności warte ochrony przed regresją:
  1. PUBLICZNY (bez Basic Auth) -- ankietę wypełniają też osoby bez konta,
     np. uczestnicy prezentacji, którzy zapamiętali sam adres serwisu.
  2. Cel z RCN_SURVEY_URL -- formularz wymienny bez zmiany kodu (i bez
     wyciekania adresu kampanii do publicznego repo).
  3. Brak zmiennej -> 404, a nie redirect w pustkę.

Patrz app/main.py::ankieta.
"""
import sys

import pytest


def _reload_app_modules():
    """Wymuś ponowne wczytanie settings z podmienionego env (wzorzec z conftest)."""
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("RCN_DATA_DIR", str(data))
    monkeypatch.setenv("RCN_ALLOW_INSECURE_DEFAULT", "1")
    _reload_app_modules()
    yield
    _reload_app_modules()


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_ankieta_przekierowuje_na_survey_url(app_env, monkeypatch):
    monkeypatch.setenv("RCN_SURVEY_URL", "https://forms.gle/PRZYKLAD")
    _reload_app_modules()

    resp = _client().get("/ankieta", follow_redirects=False)

    assert resp.status_code == 307
    assert resp.headers["location"] == "https://forms.gle/PRZYKLAD"


def test_ankieta_jest_publiczna(app_env, monkeypatch):
    """Bez nagłówka Authorization -- ma przekierować, nie zwrócić 401."""
    monkeypatch.setenv("RCN_SURVEY_URL", "https://forms.gle/PRZYKLAD")
    _reload_app_modules()

    resp = _client().get("/ankieta", follow_redirects=False)

    assert resp.status_code != 401, "Ankieta musi być dostępna bez logowania"


def test_brak_zmiennej_daje_404(app_env, monkeypatch):
    monkeypatch.delenv("RCN_SURVEY_URL", raising=False)
    _reload_app_modules()

    assert _client().get("/ankieta", follow_redirects=False).status_code == 404
