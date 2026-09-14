"""Basic auth z dwiema rolami:
- admin: pełen dostęp (upload GML, create/delete workspace, edit notes)
- readonly: tylko odczyt + eksporty + analityka

Każdy endpoint może użyć:
- `Depends(require_auth)` -- wymaga bycia zalogowanym (admin lub readonly)
- `Depends(require_admin)` -- wymaga admina (zwraca 403 dla readonly)

`require_auth` zwraca teraz obiekt `AuthContext` (username + role), nie sam string.
Istniejące handlery używające `_: str = Depends(require_auth)` działają dalej bez zmian.

Tryb publicznego odczytu (`RCN_PUBLIC_READONLY=1`): żądanie BEZ nagłówka
Authorization dostaje rolę `readonly` zamiast 401, więc instancja jest do
przeglądania dla każdego, a admin wchodzi jak dotąd -- hasłem (`/login`).
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
# oraz publiczny odczyt, zanim wymusimy logowanie.
_security = HTTPBasic(auto_error=False)

Role = Literal["admin", "readonly"]

# Nazwa użytkownika dla żądań bez logowania w trybie publicznego odczytu.
# Frontend poznaje po niej (`/api/me`), że warto pokazać link „Zaloguj się".
PUBLIC_USERNAME = "gość"


@dataclass(frozen=True)
class AuthContext:
    username: str
    role: Role

    def __str__(self) -> str:
        # Backwards-compat: istniejące handlery pisały `_: str = Depends(require_auth)`
        # i wciąż dostają string-like object.
        return self.username

    @property
    def is_anonymous(self) -> bool:
        """Gość w trybie publicznego odczytu -- nie podał żadnych poświadczeń."""
        return self.username == PUBLIC_USERNAME


def verify_credentials(credentials: HTTPBasicCredentials | None) -> AuthContext | None:
    """Dopasuj poświadczenia do konta admina albo readonly.

    Zwraca `None`, gdy poświadczeń nie ma ALBO nie pasują do żadnego konta.
    Co z tego wynika -- 401 czy publiczny odczyt -- rozstrzyga wołający.
    """
    if credentials is None:
        return None

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

    return None


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

    ctx = verify_credentials(credentials)
    if ctx is not None:
        return ctx

    # Publiczny odczyt (RCN_PUBLIC_READONLY=1): gość bez nagłówka Authorization
    # dostaje rolę readonly. BŁĘDNE poświadczenia nadal dają 401 -- literówka
    # w haśle admina ma być widoczna od razu, a nie cicho degradować do gościa
    # (inaczej admin klikałby po UI, dziwiąc się, czemu znikły przyciski).
    if credentials is None and settings.public_readonly:
        return AuthContext(username=PUBLIC_USERNAME, role="readonly")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated" if credentials is None else "Invalid credentials",
        headers={"WWW-Authenticate": "Basic"},
    )


def require_login(
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> AuthContext:
    """Wymusza podanie poświadczeń, IGNORUJĄC tryb publicznego odczytu.

    Potrzebne dla `/login`: gdy `RCN_PUBLIC_READONLY=1`, żaden endpoint nie
    zwraca już 401, więc przeglądarka nie ma powodu pokazać okienka logowania.
    Ta zależność ten powód daje.
    """
    if settings.auth_disabled:
        return AuthContext(username="local", role="admin")

    ctx = verify_credentials(credentials)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated" if credentials is None else "Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return ctx


def require_admin(ctx: AuthContext = Depends(require_auth)) -> AuthContext:
    """Dependency dla endpointów modyfikujących stan (upload, create, delete, edit).
    Zwraca 403 dla usera readonly -- informuje frontend żeby ukrył kontrolki."""
    if ctx.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja dostępna tylko dla admina (obecny użytkownik ma rolę readonly)",
        )
    return ctx
