"""Ostrzeżenie o wersji testowej (RCN_TEST_WARNING=1).

Modal ma się pokazywać w aplikacji, ale NIE na splashu (strona przed wejściem),
i tylko gdy zmienna jest ustawiona -- instalacja produkcyjna/desktopowa nie
powinna straszyć użytkownika bez powodu.

Patrz templates/base.html::block test_warning_modal.
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


def test_modal_widoczny_w_aplikacji(app_env, monkeypatch):
    monkeypatch.setenv("RCN_TEST_WARNING", "1")
    _reload_app_modules()

    resp = _client().get("/workspaces", headers=_AUTH)

    assert resp.status_code == 200
    assert "rcn-test-warning" in resp.text
    assert "Wersja testowa" in resp.text


def test_modal_ukryty_bez_zmiennej(app_env, monkeypatch):
    monkeypatch.delenv("RCN_TEST_WARNING", raising=False)
    _reload_app_modules()

    resp = _client().get("/workspaces", headers=_AUTH)

    assert resp.status_code == 200
    assert "rcn-test-warning" not in resp.text


def test_modal_nieobecny_na_splashu(app_env, monkeypatch):
    """Splash jest przed logowaniem -- ostrzeżenie dotyczy pracy z danymi."""
    monkeypatch.setenv("RCN_TEST_WARNING", "1")
    _reload_app_modules()

    resp = _client().get("/")

    assert resp.status_code == 200
    assert "rcn-test-warning" not in resp.text


def test_konto_testowe_nie_moze_importowac(app_env, monkeypatch):
    """Konto test/test ma rolę readonly -- operacje admina zablokowane."""
    monkeypatch.setenv("RCN_TEST_WARNING", "1")
    _reload_app_modules()

    resp = _client().post(
        "/api/workspaces/import", headers=_AUTH, files={"file": ("x.zip", b"x")}
    )

    assert resp.status_code == 403
