"""Basic auth z dwiema rolami:
- admin: pełen dostęp (upload GML, create/delete workspace, edit notes)
- readonly: tylko odczyt + eksporty + analityka

Każdy endpoint może użyć:
- `Depends(require_auth)` -- wymaga bycia zalogowanym (admin lub readonly)
- `Depends(require_admin)` -- wymaga admina (zwraca 403 dla readonly)

`require_auth` zwraca teraz obiekt `AuthContext` (username + role), nie sam string.
Istniejące handlery używające `_: str = Depends(require_auth)` działają dalej bez zmian.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

# auto_error=False: brak nagłówka Authorization daje credentials=None zamiast
# natychmiastowego 401 -- pozwala obsłużyć tryb auth_disabled (loopback desktop)
# zanim wymusimy logowanie.
_security = HTTPBasic(auto_error=False)

Role = Literal["admin", "readonly"]


@dataclass(frozen=True)
class AuthContext:
    username: str
    role: Role

    def __str__(self) -> str:
        # Backwards-compat: istniejące handlery pisały `_: str = Depends(require_auth)`
        # i wciąż dostają string-like object.
        return self.username


def require_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> AuthContext:
    # Tryb desktopowy (RCN_DISABLE_AUTH=1): bez logowania, każdy jako admin --
    # ale WYŁĄCZNIE dla połączeń z loopbacku. Żądanie z innego adresu dostaje 403
    # (celowo NIE spada do Basic Auth: w trybie desktop hasło bywa domyślne
    # `change-me`, bo fail-fast w config.py jest wtedy pomijany). Patrz config.py.
    if settings.auth_disabled:
        client_host = request.client.host if request.client else None
        if client_host in ("127.0.0.1", "::1"):
            return AuthContext(username="local", role="admin")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="RCN_DISABLE_AUTH obsługuje wyłącznie połączenia z localhost "
            "(127.0.0.1). Dla dostępu sieciowego wyłącz tę flagę i skonfiguruj "
            "RCN_AUTH_PASSWORD.",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Basic"},
        )

    user_bytes = credentials.username.encode("utf-8")
    pass_bytes = credentials.password.encode("utf-8")

    # 1. Spróbuj dopasować do admina.
    admin_user_ok = secrets.compare_digest(user_bytes, settings.auth_user.encode("utf-8"))
    admin_pass_ok = secrets.compare_digest(pass_bytes, settings.auth_password.encode("utf-8"))
    if admin_user_ok and admin_pass_ok:
        return AuthContext(username=credentials.username, role="admin")

    # 2. Spróbuj readonly (jeśli skonfigurowany).
    if settings.readonly_user and settings.readonly_password:
        ro_user_ok = secrets.compare_digest(user_bytes, settings.readonly_user.encode("utf-8"))
        ro_pass_ok = secrets.compare_digest(pass_bytes, settings.readonly_password.encode("utf-8"))
        if ro_user_ok and ro_pass_ok:
            return AuthContext(username=credentials.username, role="readonly")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Basic"},
    )


def require_admin(ctx: AuthContext = Depends(require_auth)) -> AuthContext:
    """Dependency dla endpointów modyfikujących stan (upload, create, delete, edit).
    Zwraca 403 dla usera readonly -- informuje frontend żeby ukrył kontrolki."""
    if ctx.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja dostępna tylko dla admina (obecny użytkownik ma rolę readonly)",
        )
    return ctx
