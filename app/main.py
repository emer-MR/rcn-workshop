import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.analysis import router as analysis_router
from app.auth import require_auth
from app.config import settings
from app.metrics import snapshot
from app.exports import router as exports_router
from app.layers import router as layers_router
from app.notes import router as notes_router
from app.plugins import router as plugins_router
from app.quality_routes import router as quality_router
from app.query import router as query_router
from app.transactions import router as transactions_router
from app.version import IS_BETA, __version__
from app.workspaces import resolve_workspace_id, router as workspaces_router
from rcn_core import obreby as _obreby

_obreby.set_layers_dir(settings.data_dir / "layers")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app = FastAPI(
    title="RCN Workshop",
    version=__version__,
    description="Web tool for merging, filtering and exporting RCN GML files.",
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["static_version"] = str(int(time.time()))
# Analityka opcjonalna (oba pola wymagane) + link do źródeł (AGPL §13) -- patrz config.py.
templates.env.globals["analytics_script_url"] = settings.analytics_script_url
templates.env.globals["analytics_website_id"] = settings.analytics_website_id
templates.env.globals["source_url"] = settings.source_url
templates.env.globals["survey_url"] = settings.survey_url
templates.env.globals["test_warning"] = settings.test_warning
# Wersja + kanał: badge BETA w topbarach i klucz zgody modala (raz na wersję).
templates.env.globals["app_version"] = __version__
templates.env.globals["is_beta"] = IS_BETA
templates.env.globals["download_url"] = settings.download_url

app.include_router(workspaces_router)
app.include_router(query_router)
app.include_router(exports_router)
app.include_router(layers_router)
app.include_router(notes_router)
app.include_router(transactions_router)
app.include_router(analysis_router)
app.include_router(quality_router)
app.include_router(plugins_router)


@app.get("/healthz", response_class=JSONResponse)
def healthz() -> dict:
    # Endpoint publiczny (monitoring) -- bez ścieżek serwera w odpowiedzi.
    return {"status": "ok", "version": app.version}


@app.get("/metrics", response_class=JSONResponse)
def metrics(_: str = Depends(require_auth)) -> dict:
    return snapshot()


@app.get("/api/me", response_class=JSONResponse)
def whoami(ctx=Depends(require_auth)) -> dict:
    """Zwraca tożsamość zalogowanego usera -- frontend używa żeby
    ukryć/zablokować przyciski edycji dla roli readonly."""
    return {"username": ctx.username, "role": ctx.role}


@app.get("/", response_class=HTMLResponse)
def splash(request: Request):
    """Publiczna zaślepka -- info o aplikacji bez ujawniania danych workspace.
    Zalogowani user-zy mogą wejść na /workspaces (basic auth + lista).

    Tryb lokalny (RCN_DISABLE_AUTH=1 -- desktop/.exe): brak logowania, więc
    zaślepka jest zbędna -> redirect od razu na listę workspace'ów. Na VPS
    (auth włączony) zaślepka zostaje jako publiczny landing."""
    if settings.auth_disabled:
        return RedirectResponse(url="/workspaces", status_code=307)
    return templates.TemplateResponse(request, "splash.html", {})


@app.get("/instrukcja", response_class=HTMLResponse)
def instrukcja(request: Request):
    """Publiczna instrukcja obsługi -- 9 slajdów krok-po-kroku z mockupami UI.
    Bez auth (przed-logowaniowy onboarding dla nowych użytkowników)."""
    return templates.TemplateResponse(request, "instrukcja.html", {})


@app.get("/ankieta")
def ankieta():
    """Krótki, zapamiętywalny adres przekierowujący na formularz opinii.

    Sens: adres do podyktowania ze slajdu/na wizytówce (rcn.example.com/ankieta)
    zamiast nieczytelnego forms.gle/xK3jQ2mP. Cel ustawia RCN_SURVEY_URL, więc
    formularz da się podmienić bez zmiany kodu. Bez auth -- ankietę wypełniają
    także osoby bez konta. Brak zmiennej -> 404 (żadna kampania nie trwa)."""
    if not settings.survey_url:
        raise HTTPException(status_code=404, detail="Brak aktywnej ankiety.")
    return RedirectResponse(url=settings.survey_url, status_code=307)


@app.get("/pobierz")
def pobierz():
    """Krótki adres przekierowujący na instalator wersji desktop (beta).

    Bliźniak /ankieta: cel w RCN_DOWNLOAD_URL (podmiana pliku/wersji bez zmiany
    kodu i bez unieważniania linku rozesłanego testerom). Bez auth -- instalator
    pobierają osoby, które konta jeszcze nie mają. Brak zmiennej -> 404."""
    if not settings.download_url:
        raise HTTPException(status_code=404, detail="Brak dostępnej wersji do pobrania.")
    return RedirectResponse(url=settings.download_url, status_code=307)


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request, _: str = Depends(require_auth)):
    """Pełna instrukcja obsługi z prawdziwymi screenshotami aplikacji.
    Wymaga auth (screenshoty zawierają dane RCN nieujawniane publicznie)."""
    return templates.TemplateResponse(request, "help.html", {})


@app.get("/logout", response_class=HTMLResponse)
def logout(request: Request):
    """Wylogowanie z basic auth -- przeglądarka cache'uje credentials per
    realm. Trick: strona JS robi fetch z explicit invalid Authorization
    header, co zmusza przeglądarkę do oczyszczenia cache, potem redirect
    do publicznej zaślepki `/`. User dostaje 'Wylogowano' splash + link
    do ponownego logowania."""
    return templates.TemplateResponse(request, "logout.html", {})


@app.get("/workspaces", response_class=HTMLResponse)
def index(request: Request, _: str = Depends(require_auth)):
    """Lista workspace'ów -- wymaga zalogowania. Stary endpoint `/` był tu;
    przeniesione 2026-04-29 żeby `/` mogło być publiczną zaślepką na
    instancji sieciowej."""
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/workspaces/{workspace_id_or_slug}/settings", response_class=HTMLResponse)
def workspace_settings_page(
    request: Request,
    workspace_id_or_slug: str,
    _: str = Depends(require_auth),
):
    """Settings page per workspace: edycja meta, custom layers, historia
    importów, ulepszenia (enrich-egib / compute-flags / geocoding),
    quality thresholds, strefa niebezpieczna (delete)."""
    workspace_id = resolve_workspace_id(workspace_id_or_slug)
    workspace_name = workspace_id
    try:
        from app.workspaces import _workspace_db, _meta_get
        import sqlite3
        conn = sqlite3.connect(_workspace_db(workspace_id))
        try:
            workspace_name = _meta_get(conn, "name") or workspace_id
        finally:
            conn.close()
    except Exception:
        pass
    from rcn_core.producer import HAS_PRODUCER
    return templates.TemplateResponse(
        request,
        "workspace_settings.html",
        {
            "workspace_id": workspace_id,
            "workspace_name": workspace_name,
            # Model A: gdy brak producenta (build konsumenta), sekcje wzbogacania
            # NIE są renderowane w ogóle (zero śladu w źródle strony).
            "has_producer": HAS_PRODUCER,
        },
    )


@app.get("/workspaces/{workspace_id_or_slug}", response_class=HTMLResponse)
def workspace_page(
    request: Request,
    workspace_id_or_slug: str,
    _: str = Depends(require_auth),
):
    # Akceptuje zarówno UUID, jak i slug (np. /workspaces/lodz). Frontend dalej
    # operuje na UUID przez API -- to ten resolve pozwala skracać URL-e w pasku przeglądarki.
    workspace_id = resolve_workspace_id(workspace_id_or_slug)
    # Pobierz nazwę workspace dla <title> w przeglądarce -- żeby zakładka
    # pokazywała "Łódź — RCN Workshop" zamiast UUID.
    workspace_name = workspace_id
    try:
        from app.workspaces import _workspace_db, _meta_get
        import sqlite3
        conn = sqlite3.connect(_workspace_db(workspace_id))
        try:
            workspace_name = _meta_get(conn, "name") or workspace_id
        finally:
            conn.close()
    except Exception:
        pass
    return templates.TemplateResponse(
        request,
        "workspace.html",
        {"workspace_id": workspace_id, "workspace_name": workspace_name},
    )
