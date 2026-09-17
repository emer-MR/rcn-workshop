"""Strona powitalna: kafelki akcji, w tym instrukcja INSTALACJI.

Przycisk „Instrukcja obsługi" prowadził do `/instrukcja` (onboarding w aplikacji).
Zastąpiła go instrukcja INSTALACJI (PDF), a wezwania do działania są teraz
kafelkami w jednym rzędzie: wejście do aplikacji, pobranie, instrukcja, ankieta.

Link renderuje się TYLKO przy ustawionym `RCN_INSTALL_GUIDE_URL`: plik leży na
serwerze obok instalatorów i nie istnieje w trybie desktopowym, więc bez tego
warunku byłby martwym odnośnikiem.
"""
import shutil
import sys

import pytest

ADRES_PDF = "/static/download/RCN_Workshop_instrukcja_instalacji.pdf"


def _przeladuj_app():
    for mod in list(sys.modules):
        if mod == "app.config" or mod.startswith("app.") or mod == "app":
            del sys.modules[mod]


def _klient(tmp_path, monkeypatch, guide_url=None):
    data = tmp_path / "data"
    (data / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("RCN_DATA_DIR", str(data))
    monkeypatch.setenv("RCN_ALLOW_INSECURE_DEFAULT", "1")
    monkeypatch.setenv("RCN_AUTH_PASSWORD", "tajne-haslo")
    monkeypatch.delenv("RCN_DISABLE_AUTH", raising=False)
    if guide_url:
        monkeypatch.setenv("RCN_INSTALL_GUIDE_URL", guide_url)
    else:
        monkeypatch.delenv("RCN_INSTALL_GUIDE_URL", raising=False)
    _przeladuj_app()
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app), data


@pytest.fixture(autouse=True)
def _sprzataj():
    yield
    _przeladuj_app()


def test_splash_pokazuje_instrukcje_instalacji(tmp_path, monkeypatch):
    client, data = _klient(tmp_path, monkeypatch, guide_url=ADRES_PDF)
    try:
        html = client.get("/").text
        assert ADRES_PDF in html
        assert "Instrukcja instalacji" in html
        # Stary przycisk zniknął z wezwań do działania.
        assert 'href="/instrukcja"' not in html
        # Jedno wejście do PDF-a, nie dwa (kafelek zastąpił link w stopce).
        assert html.count(ADRES_PDF) == 1
    finally:
        shutil.rmtree(data, ignore_errors=True)


def test_splash_uklada_akcje_w_kafelki(tmp_path, monkeypatch):
    """Cztery kafelki obok siebie, gdy wszystkie adresy są ustawione."""
    import os

    os.environ["RCN_SURVEY_URL"] = "https://example.com/ankieta"
    os.environ["RCN_DOWNLOAD_URL"] = "https://example.com/setup.exe"
    try:
        client, data = _klient(tmp_path, monkeypatch, guide_url=ADRES_PDF)
        try:
            html = client.get("/").text
            assert html.count('class="splash-tile-icon"') == 4, "spodziewane 4 kafelki"
            for tytul in ("Pobierz na komputer", "Instrukcja instalacji",
                          "Podziel się opinią"):
                assert tytul in html
            # Pierwszy kafelek jest wyróżniony jako główna akcja.
            assert "splash-tile splash-tile-primary" in html
        finally:
            shutil.rmtree(data, ignore_errors=True)
    finally:
        os.environ.pop("RCN_SURVEY_URL", None)
        os.environ.pop("RCN_DOWNLOAD_URL", None)


def test_kafelki_znikaja_bez_adresow(tmp_path, monkeypatch):
    """Bez zmiennych env zostaje sam kafelek wejścia -- żadnych pustych ramek."""
    import os

    os.environ.pop("RCN_SURVEY_URL", None)
    os.environ.pop("RCN_DOWNLOAD_URL", None)
    client, data = _klient(tmp_path, monkeypatch, guide_url=None)
    try:
        html = client.get("/").text
        assert html.count('class="splash-tile-icon"') == 1
    finally:
        shutil.rmtree(data, ignore_errors=True)


def test_bez_zmiennej_brak_martwego_linku(tmp_path, monkeypatch):
    """Tryb desktopowy: pliku PDF nie ma, więc linku też nie powinno być."""
    client, data = _klient(tmp_path, monkeypatch, guide_url=None)
    try:
        html = client.get("/").text
        assert "Instrukcja instalacji" not in html
        assert ADRES_PDF not in html
    finally:
        shutil.rmtree(data, ignore_errors=True)


def test_strona_instrukcji_obslugi_nadal_dziala(tmp_path, monkeypatch):
    """Zdejmujemy przycisk ze splasha, ale samej strony nie usuwamy."""
    client, data = _klient(tmp_path, monkeypatch, guide_url=ADRES_PDF)
    try:
        assert client.get("/instrukcja").status_code == 200
    finally:
        shutil.rmtree(data, ignore_errors=True)
