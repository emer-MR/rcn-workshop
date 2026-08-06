"""Limit rozmiaru uploadu: 512 MB w sieci, bez limitu na desktopie (loopback).

Zgłoszenie testera 2026-08-06: GML powiatu poznańskiego (~610 MB) odbijał się
od limitu 512 MB w lokalnej instalacji, gdzie limit nie ma czego chronić.
Detekcja środowiska idzie przez `auth_disabled` (RCN_DISABLE_AUTH=1 == desktop
na loopbacku) -- patrz app/config.py::Settings.max_upload_mb.
"""
import sys

import pytest


def _reload_app_modules():
    for mod in list(sys.modules):
        if mod == "app.config" or mod.startswith("app.") or mod == "app":
            del sys.modules[mod]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Czyste środowisko: katalog danych w tmp, bez odziedziczonego limitu."""
    data = tmp_path / "data"
    (data / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("RCN_DATA_DIR", str(data))
    monkeypatch.setenv("RCN_ALLOW_INSECURE_DEFAULT", "1")
    monkeypatch.delenv("RCN_MAX_UPLOAD_MB", raising=False)
    monkeypatch.delenv("RCN_DISABLE_AUTH", raising=False)
    _reload_app_modules()
    yield monkeypatch
    _reload_app_modules()


def test_domyslnie_512_mb_na_instancji_sieciowej(env):
    from app.config import load_settings
    s = load_settings()
    assert s.max_upload_mb == 512
    assert s.max_upload_bytes == 512 * 1024 * 1024


def test_desktop_loopback_bez_limitu(env):
    env.setenv("RCN_DISABLE_AUTH", "1")
    from app.config import load_settings
    s = load_settings()
    assert s.max_upload_mb == 0
    assert s.max_upload_bytes is None


@pytest.mark.parametrize("disable_auth", ["1", None])
def test_jawny_env_wygrywa_w_obu_trybach(env, disable_auth):
    if disable_auth:
        env.setenv("RCN_DISABLE_AUTH", disable_auth)
    env.setenv("RCN_MAX_UPLOAD_MB", "2048")
    from app.config import load_settings
    s = load_settings()
    assert s.max_upload_mb == 2048
    assert s.max_upload_bytes == 2048 * 1024 * 1024


def test_zero_znaczy_bez_limitu_takze_jawnie(env):
    env.setenv("RCN_MAX_UPLOAD_MB", "0")
    from app.config import load_settings
    assert load_settings().max_upload_bytes is None


# --- Zachowanie end-to-end: limit dalej działa tam, gdzie jest ustawiony -------

@pytest.fixture
def client_z_limitem_1mb(env):
    env.setenv("RCN_MAX_UPLOAD_MB", "1")
    _reload_app_modules()
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_za_duzy_gml_dostaje_413(client_z_limitem_1mb, auth):
    duzy = b"<xml>" + b"x" * (2 * 1024 * 1024)
    resp = client_z_limitem_1mb.post(
        "/api/workspaces/new",
        data={"name": "Poznanski"},
        files=[("gml_files", ("wielki.gml", duzy, "application/xml"))],
        auth=auth,
    )
    assert resp.status_code == 413
    assert "1 MB" in resp.json()["detail"]


def test_po_413_nie_zostaje_pusty_workspace(client_z_limitem_1mb, auth):
    """Odrzucony upload nie może zostawiać śmiecia na liście -- inaczej kolejne
    próby tej samej nazwy tworzą 'poznanski-1', 'poznanski-2'..."""
    duzy = b"<xml>" + b"x" * (2 * 1024 * 1024)
    for _ in range(2):
        resp = client_z_limitem_1mb.post(
            "/api/workspaces/new",
            data={"name": "Poznanski"},
            files=[("gml_files", ("wielki.gml", duzy, "application/xml"))],
            auth=auth,
        )
        assert resp.status_code == 413

    lista = client_z_limitem_1mb.get("/api/workspaces", auth=auth)
    assert lista.status_code == 200
    assert lista.json() == []


def test_maly_gml_przechodzi(client_z_limitem_1mb, auth):
    maly = b"<?xml version='1.0'?><root/>"
    resp = client_z_limitem_1mb.post(
        "/api/workspaces/new",
        data={"name": "Maly"},
        files=[("gml_files", ("maly.gml", maly, "application/xml"))],
        auth=auth,
    )
    assert resp.status_code == 201
