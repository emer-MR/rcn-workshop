"""RCN_DISABLE_AUTH=1 (tryb desktop) musi działać WYŁĄCZNIE dla loopbacku.

Żądanie z innego adresu dostaje 403 -- bez fallbacku do Basic Auth, bo w trybie
desktop hasło bywa domyślne `change-me` (fail-fast w config.py jest pomijany).
Patrz app/auth.py::require_auth.
"""
import sys

import pytest
from starlette.requests import Request


@pytest.fixture
def auth_disabled_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("RCN_DATA_DIR", str(data))
    monkeypatch.setenv("RCN_DISABLE_AUTH", "1")
    # Przeładuj moduły app.* żeby settings wczytały nowe env (wzorzec z conftest.client).
    for mod in list(sys.modules):
        if mod == "app.config" or mod.startswith("app.") or mod == "app":
            del sys.modules[mod]
    yield
    for mod in list(sys.modules):
        if mod == "app.config" or mod.startswith("app.") or mod == "app":
            del sys.modules[mod]


def _request(client_host: str) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/me",
        "headers": [],
        "client": (client_host, 12345),
    })


def test_loopback_dostaje_admina(auth_disabled_env):
    from app.auth import require_auth
    ctx = require_auth(_request("127.0.0.1"), credentials=None)
    assert ctx.role == "admin"
    assert ctx.username == "local"


def test_loopback_ipv6(auth_disabled_env):
    from app.auth import require_auth
    ctx = require_auth(_request("::1"), credentials=None)
    assert ctx.role == "admin"


def test_adres_sieciowy_dostaje_403(auth_disabled_env):
    from fastapi import HTTPException
    from app.auth import require_auth
    with pytest.raises(HTTPException) as exc:
        require_auth(_request("192.168.1.50"), credentials=None)
    assert exc.value.status_code == 403


def test_przez_testclient_spoza_loopbacku_403(auth_disabled_env):
    # TestClient starlette'a przedstawia się jako host "testclient" (nie-loopback),
    # więc cały stack HTTP też musi odmówić.
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    resp = client.get("/api/me")
    assert resp.status_code == 403
