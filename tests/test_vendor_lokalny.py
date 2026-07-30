"""Zależności frontendu serwowane lokalnie (static/vendor/) — bez CDN.

Zgłoszenie testera 2026-07-30: przy zablokowanych CDN-ach (firewall/antywirus/
offline) aplikacja desktop traciła Tailwinda (w tym regułę [hidden] chowającą
modale), Alpine.js (cała interaktywność) i Leafleta (mapa). Frontend ma nie
zależeć od sieci — jedyny dopuszczalny ruch zewnętrzny to kafelki OSM (dane).
"""
import base64
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

ZAKAZANE_HOSTY = (
    "cdn.tailwindcss.com",
    "unpkg.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
)


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
    _reload_app_modules()
    yield
    _reload_app_modules()


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_szablony_bez_cdn():
    """Żaden szablon nie może referować bibliotek z zewnętrznych hostów."""
    zlapane = []
    for szablon in (REPO / "templates").glob("*.html"):
        if szablon.name == "diagnostyka.html":
            continue  # celowo referuje CDN-y -- to strona testująca ich blokadę
        tekst = szablon.read_text(encoding="utf-8")
        for host in ZAKAZANE_HOSTY:
            if host in tekst:
                zlapane.append(f"{szablon.name}: {host}")
    assert not zlapane, "CDN w szablonach: " + ", ".join(zlapane)


def test_geist_css_bez_zewnetrznych_urli():
    """geist.css musi wskazywać lokalne woff2 (URL-e przepisane przy vendorowaniu)."""
    css = (REPO / "static" / "vendor" / "fonts" / "geist.css").read_text(encoding="utf-8")
    assert "fonts.gstatic.com" not in css
    assert "latin-ext" in css  # polskie znaki
    assert ".woff2" in css


def test_diagnostyka_publiczna(app_env):
    """Strona /diagnostyka jest publiczna (bez auth) i samowystarczalna."""
    resp = _client().get("/diagnostyka")

    assert resp.status_code == 200
    assert "Diagnostyka połączenia" in resp.text
    assert "Skopiuj wynik" in resp.text
    # Samowystarczalność: żadnych <script src>/<link href> poza testami JS.
    assert '<script src=' not in resp.text
    assert '<link rel="stylesheet"' not in resp.text


def test_vendor_serwowany(app_env):
    """Kluczowe pliki vendor muszą być dostępne pod /static/vendor/."""
    client = _client()
    for sciezka in (
        "/static/vendor/tailwind-play-3.4.17.min.js",
        "/static/vendor/alpine-3.14.3.min.js",
        "/static/vendor/marked-14.1.2.min.js",
        "/static/vendor/purify-3.1.7.min.js",
        "/static/vendor/fonts/geist.css",
        "/static/vendor/leaflet/leaflet.css",
        "/static/vendor/leaflet/leaflet.js",
        "/static/vendor/leaflet/images/marker-icon.png",
        "/static/vendor/leaflet-draw/leaflet.draw.js",
        "/static/vendor/markercluster/leaflet.markercluster.js",
    ):
        resp = client.get(sciezka)
        assert resp.status_code == 200, f"{sciezka} -> {resp.status_code}"
