import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    auth_user: str
    auth_password: str
    # Opcjonalny readonly account -- read/eksport/analiza dozwolone,
    # upload/tworzenie/usuwanie workspace'ów i edycja notatek zablokowane.
    # Włączany tylko jeśli RCN_READONLY_USER i RCN_READONLY_PASSWORD są ustawione.
    readonly_user: str | None
    readonly_password: str | None
    # Wyłączenie logowania (RCN_DISABLE_AUTH=1) -- TYLKO dla lokalnego buildu
    # desktopowego na loopbacku (jeden komputer, jeden user). Każde żądanie jest
    # wtedy traktowane jak admin, bez okienka Basic Auth w WebView2.
    # NIGDY nie włączać na instancji sieciowej/VPS.
    auth_disabled: bool
    data_dir: Path
    max_upload_mb: int
    log_level: str
    # Opcjonalna analityka (np. self-hosted Umami). Snippet w base.html renderuje
    # się TYLKO gdy oba pola ustawione (RCN_ANALYTICS_SCRIPT_URL + RCN_ANALYTICS_WEBSITE_ID).
    # Domyślnie wyłączona -- instalacja z kodu nie wysyła żadnej telemetrii.
    analytics_script_url: str | None
    analytics_website_id: str | None
    # Link "Kod źródłowy" w UI (obowiązek AGPL §13 przy udostępnianiu przez sieć).
    # Override: RCN_SOURCE_URL; pusta wartość ukrywa link (świadoma decyzja operatora).
    source_url: str
    # Opcjonalny link do ankiety/formularza opinii (RCN_SURVEY_URL). Domyślnie pusty
    # -- link nie renderuje się wcale. URL celowo NIE w kodzie: repo idzie na public,
    # a adres formularza bywa czasowy (kampania po prezentacji/szkoleniu).
    survey_url: str | None
    # Okno ostrzegawcze "wersja testowa" (RCN_TEST_WARNING=1) -- pokazywane raz
    # na wersję aplikacji po wejściu (klucz zgody w localStorage zawiera numer
    # wersji, więc aktualizacja pokazuje je ponownie). Domyślnie WYŁĄCZONE, żeby
    # instalacja produkcyjna nie straszyła użytkownika bez powodu; desktop
    # (frozen beta) włącza je sam -- patrz desktop.py.
    test_warning: bool
    # Opcjonalny link do pobrania instalatora desktop (RCN_DOWNLOAD_URL) --
    # zasila redirect /pobierz i przycisk na splashu. Jak survey_url: adres
    # kampanii bety trzymany w env, nie w kodzie (repo idzie na public).
    download_url: str | None

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def plugins_dir(self) -> Path:
        return self.data_dir / "plugins"


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("RCN_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "workspaces").mkdir(parents=True, exist_ok=True)
    (data_dir / "plugins").mkdir(parents=True, exist_ok=True)
    ro_user = os.environ.get("RCN_READONLY_USER") or None
    ro_pass = os.environ.get("RCN_READONLY_PASSWORD") or None

    auth_password = os.environ.get("RCN_AUTH_PASSWORD", "change-me")
    auth_disabled = os.environ.get("RCN_DISABLE_AUTH") == "1"

    # Fail-fast (security review 2026-04-29): nie startuj z domyślnym hasłem
    # `change-me`. Operator-misconfiguration -> public deployment z default
    # password. Override dla testów: RCN_ALLOW_INSECURE_DEFAULT=1.
    # Pomijane gdy auth_disabled (loopback desktop -- hasło nieużywane).
    if not auth_disabled and auth_password in ("change-me", ""):
        if os.environ.get("RCN_ALLOW_INSECURE_DEFAULT") != "1":
            raise RuntimeError(
                "RCN_AUTH_PASSWORD nie ustawione lub równe domyślnemu 'change-me'. "
                "Ustaw zmienną w .env. Aby pominąć (testy): RCN_ALLOW_INSECURE_DEFAULT=1."
            )

    return Settings(
        auth_user=os.environ.get("RCN_AUTH_USER", "admin"),
        auth_password=auth_password,
        readonly_user=ro_user,
        readonly_password=ro_pass,
        auth_disabled=auth_disabled,
        data_dir=data_dir,
        max_upload_mb=int(os.environ.get("RCN_MAX_UPLOAD_MB", "512")),
        log_level=os.environ.get("RCN_LOG_LEVEL", "info"),
        analytics_script_url=os.environ.get("RCN_ANALYTICS_SCRIPT_URL") or None,
        analytics_website_id=os.environ.get("RCN_ANALYTICS_WEBSITE_ID") or None,
        source_url=os.environ.get("RCN_SOURCE_URL", "https://github.com/emer-MR/rcn-workshop"),
        survey_url=os.environ.get("RCN_SURVEY_URL") or None,
        test_warning=os.environ.get("RCN_TEST_WARNING") == "1",
        download_url=os.environ.get("RCN_DOWNLOAD_URL") or None,
    )


settings = load_settings()
