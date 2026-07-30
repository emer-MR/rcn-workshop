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


def test_modal_ma_minutnik_i_zamkniecie_bez_reguly_hidden(app_env, monkeypatch):
    """Zgłoszenie testera 2026-07-30: przy zablokowanym CDN (brak preflightu
    Tailwinda z regułą [hidden]) modal był widoczny na stałe, a klik nic nie
    robił. Modal musi mieć minutnik-bezpiecznik i zamykać się przez inline
    display:none, nie tylko atrybut hidden."""
    monkeypatch.setenv("RCN_TEST_WARNING", "1")
    _reload_app_modules()

    resp = _client().get("/workspaces", headers=_AUTH)

    assert resp.status_code == 200
    assert "rcn-tw-count" in resp.text                      # minutnik w modalu
    assert "box.style.display = 'none'" in resp.text        # zamknięcie inline
    assert "setInterval" in resp.text                       # auto-przejście


def test_regula_hidden_w_lokalnym_css(app_env):
    """Atrybut [hidden] musi działać bez CSS z CDN -- reguła lokalna w reset.css
    (inaczej .modal-backdrop { display:flex } trzyma modale widoczne offline)."""
    resp = _client().get("/static/css/reset.css")

    assert resp.status_code == 200
    assert "[hidden]" in resp.text
    assert "display: none !important" in resp.text


def test_kontakt_mailowy_w_modalu_i_nav(app_env, monkeypatch):
    """RCN_CONTACT_EMAIL ustawione: link "Zgłoś błąd" w nav + wzmianka w modalu,
    mailto z tematem niosącym numer wersji."""
    monkeypatch.setenv("RCN_TEST_WARNING", "1")
    monkeypatch.setenv("RCN_CONTACT_EMAIL", "zgloszenia@example.com")
    _reload_app_modules()

    resp = _client().get("/workspaces", headers=_AUTH)

    assert resp.status_code == 200
    assert "mailto:zgloszenia@example.com" in resp.text
    assert "Zgłoś błąd" in resp.text
    from app.version import __version__

    assert f"subject=RCN%20Workshop%20{__version__}" in resp.text


def test_kontakt_mailowy_ukryty_bez_zmiennej(app_env, monkeypatch):
    monkeypatch.setenv("RCN_TEST_WARNING", "1")
    monkeypatch.delenv("RCN_CONTACT_EMAIL", raising=False)
    _reload_app_modules()

    resp = _client().get("/workspaces", headers=_AUTH)

    assert resp.status_code == 200
    assert "mailto:" not in resp.text
    assert "Zgłoś błąd" not in resp.text


def test_konto_testowe_nie_moze_importowac(app_env, monkeypatch):
    """Konto test/test ma rolę readonly -- operacje admina zablokowane."""
    monkeypatch.setenv("RCN_TEST_WARNING", "1")
    _reload_app_modules()

    resp = _client().post(
        "/api/workspaces/import", headers=_AUTH, files={"file": ("x.zip", b"x")}
    )

    assert resp.status_code == 403
