"""Workspace + imports REST API.

Each workspace has its own directory under `data/workspaces/{id}/`:
    - `workspace.sqlite` — all parsed data + workspace_meta key/value
    - `uploads/{stored_filename}` — raw uploaded GML files (kept for audit & rollback)

Workspace metadata (name, created_at) lives in the `workspace_meta` table inside
the workspace SQLite — no separate index file, so creating/removing a workspace
is a single-directory operation.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Literal

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.auth import AuthContext, require_admin, require_auth
from rcn_core.slownik_obrebow import (
    SUFIKS as SUFIKS_OBREBOW,
    ZRODLO_RECZNY,
    WpisObrebu,
    obreby_z_bazy,
    wczytaj_krajowy,
    sciezka_slownika,
    wczytaj,
    zapisz,
    zastosuj_do_bazy,
    znajdz_slownik,
)
from app.config import settings
from rcn_core.archiwum import BlednaPaczka, PaczkaZaDuza, wypakuj_gml
from rcn_core.producer import HAS_PRODUCER
from rcn_core.ingest import (
    create_import_record,
    open_workspace,
    update_import_progress,
)
from rcn_core.schema import (
    apply_schema,
    finish_phase_run,
    has_running_phase_run,
    list_phase_runs,
    mark_stale_imports,
    mark_stale_phase_runs,
    start_phase_run,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class CreateWorkspaceRequest(BaseModel):
    name: str
    slug: str | None = None  # Opcjonalny; auto-generowany z nazwy jeśli nie podany.


class WorkspaceInfo(BaseModel):
    id: str
    slug: str | None = None
    name: str
    notes: str | None = None
    created_at: int
    transaction_count: int
    plot_count: int
    building_count: int
    local_count: int
    import_count: int


class ImportInfo(BaseModel):
    id: int
    original_filename: str
    stored_filename: str
    file_hash: str | None = None
    file_size_bytes: int
    tryb: str
    upload_timestamp: int
    parser_epsg: str | None = None
    transaction_count: int
    plot_count: int
    building_count: int
    local_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    withdrawn_count: int = 0
    data_transakcji_od: str | None = None
    data_transakcji_do: str | None = None
    duration_s: float | None = None
    status: str
    stage: str | None = None
    progress_pct: int = 0
    error_msg: str | None = None


class UploadResponse(BaseModel):
    """Returned right after the file is saved, before ingest completes."""
    import_id: int
    original_filename: str
    stored_filename: str
    tryb: str
    status: str = "processing"
    stage: str = "queued"
    progress_pct: int = 0
    # Archiwum .zip może nieść kilka GML-i (powiat bywa dzielony po gminach).
    # `import_id` wskazuje wtedy PIERWSZY z nich -- dla zgodności ze starym
    # klientem -- a pełna lista jest tutaj, w kolejności przetwarzania.
    import_ids: list[int] = []
    zrodlo_archiwum: str | None = None
    # Ustawiane, gdy serwer zmienił tryb importu (patrz `upload_gml`) -- UI ma
    # to pokazać, żeby zmiana nie była cicha.
    uwaga: str | None = None


# Tożsamość workspace'u = NAZWA FOLDERU (UUID lub dowolna czytelna, np. "Kutno").
# Bezpieczne id: bez separatorów ścieżki i bez ".." (ochrona przed traversal).
_UNSAFE_ID = re.compile(r"[\\/]|\.\.")
_NOTES_SUFFIX = ".notes.sqlite"  # nakładka notatek (D3) -- NIE jest bazą główną
_POI_SUFFIX = ".poi.sqlite"      # plik POI dla wtyczek -- NIE jest bazą główną
# Wszystkie sidecary obok bazy głównej. KAŻDY nowy sufiks dopisz też w
# rcn_producer/cli.py (_find_main_sqlite + _collect_clean_cut) -- druga kopia listy.
_SIDECAR_SUFFIXES = (
    _NOTES_SUFFIX,
    _POI_SUFFIX,
)


def _validate_id(workspace_id: str) -> None:
    if not workspace_id or len(workspace_id) > 120 or _UNSAFE_ID.search(workspace_id):
        raise HTTPException(status_code=400, detail="Invalid workspace id")


def _workspace_dir(workspace_id: str) -> Path:
    _validate_id(workspace_id)
    return settings.workspaces_dir / workspace_id


def _resolve_main_sqlite(wdir: Path) -> Path | None:
    """Główna baza workspace'u: dowolny `*.sqlite` w korzeniu folderu, poza
    sidecarami (`*.notes.sqlite`, `*.poi.sqlite`; KRYTYCZNE --
    `Kutno.poi.sqlite` sortuje się PRZED `Kutno.sqlite`). Preferuje
    `workspace.sqlite`; przy wielu kandydatach -- deterministycznie pierwszy
    alfabetycznie."""
    if not wdir.is_dir():
        return None
    cands = [p for p in sorted(wdir.glob("*.sqlite"))
             if not p.name.endswith(_SIDECAR_SUFFIXES)]
    if not cands:
        return None
    preferred = wdir / "workspace.sqlite"
    return preferred if preferred in cands else cands[0]


def _poi_sqlite(workspace_id: str) -> Path | None:
    """Plik POI workspace'u (`<nazwa>.poi.sqlite` w korzeniu folderu) -- dostarczany
    przez producenta razem z bazą (model folder=komplet). None gdy brak."""
    wdir = _workspace_dir(workspace_id)
    if not wdir.is_dir():
        return None
    cands = sorted(wdir.glob(f"*{_POI_SUFFIX}"))
    return cands[0] if cands else None




def _has_main_sqlite(wdir: Path) -> bool:
    return _resolve_main_sqlite(wdir) is not None


def _workspace_db(workspace_id: str) -> Path:
    """Ścieżka głównej bazy. Istniejący folder -> wykryty `*.sqlite`;
    nowy (tworzenie) -> kanoniczny `workspace.sqlite`."""
    wdir = _workspace_dir(workspace_id)
    main = _resolve_main_sqlite(wdir)
    return main if main is not None else wdir / "workspace.sqlite"


def _workspace_uploads(workspace_id: str) -> Path:
    return _workspace_dir(workspace_id) / "uploads"


# Polskie znaki → ASCII mapping (slugify bez zewnętrznych zależności).
_PL_MAP = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_VALID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


def _slugify(text: str) -> str:
    s = text.strip().translate(_PL_MAP)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = _SLUG_RE.sub("-", s.lower()).strip("-")
    if not s or not s[0].isalpha():
        s = "w-" + s  # slug musi zaczynać się od litery (walidacja routera)
    return s[:63]


def _existing_slugs() -> dict[str, str]:
    """Zeskanuj wszystkie workspace'y i zbierz {slug: workspace_id}.
    Używane przy rezolwie slug → UUID (GET) oraz przy wyszukiwaniu kolizji."""
    out: dict[str, str] = {}
    if not settings.workspaces_dir.exists():
        return out
    for child in settings.workspaces_dir.iterdir():
        if not child.is_dir():
            continue
        db = _resolve_main_sqlite(child)  # dowolny *.sqlite (nie tylko UUID/workspace.sqlite)
        if db is None:
            continue
        try:
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT value FROM workspace_meta WHERE key = 'slug'"
            ).fetchone()
            conn.close()
            if row and row[0]:
                out[row[0]] = child.name
        except Exception:
            continue
    return out


def _unique_slug(base: str) -> str:
    existing = set(_existing_slugs().keys())
    # Folder = id, więc unikamy też kolizji z istniejącymi NAZWAMI folderów.
    if settings.workspaces_dir.exists():
        existing |= {c.name for c in settings.workspaces_dir.iterdir() if c.is_dir()}
    if base not in existing:
        return base
    for i in range(2, 999):
        cand = f"{base}-{i}"
        if cand not in existing:
            return cand
    # Skrajność: 999 kolizji -- dorzuć UUID suffix.
    return f"{base}-{uuid.uuid4().hex[:6]}"


def resolve_workspace_id(workspace_id_or_slug: str) -> str:
    """Zwraca id workspace'u = nazwa folderu.
    1. Bezpośrednie dopasowanie folderu (id = nazwa katalogu z bazą `*.sqlite`)
       -- obsługuje UUID i dowolne czytelne nazwy (np. `Kutno`).
    2. Slug z `workspace_meta` -> nazwa folderu.
    W przeciwnym razie 404."""
    if not workspace_id_or_slug or _UNSAFE_ID.search(workspace_id_or_slug):
        raise HTTPException(status_code=400, detail="Invalid workspace id/slug")
    if _has_main_sqlite(settings.workspaces_dir / workspace_id_or_slug):
        return workspace_id_or_slug
    slugs = _existing_slugs()
    if workspace_id_or_slug in slugs:
        return slugs[workspace_id_or_slug]
    raise HTTPException(status_code=404, detail="Workspace not found")


def _require_workspace(workspace_id: str) -> Path:
    wdir = _workspace_dir(workspace_id)
    if not wdir.exists() or not _workspace_db(workspace_id).exists():
        raise HTTPException(status_code=404, detail="Workspace not found")
    return wdir


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for key, table in (
        ("transaction_count", "transakcje"),
        ("plot_count", "plots"),
        ("building_count", "buildings"),
        ("local_count", "locals"),
        ("import_count", "imports"),
    ):
        out[key] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return out


def _meta_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM workspace_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO workspace_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _workspace_info(workspace_id: str) -> WorkspaceInfo:
    conn = sqlite3.connect(_workspace_db(workspace_id))
    try:
        apply_schema(conn)
        counts = _counts(conn)
        name = _meta_get(conn, "name") or workspace_id
        created_at = int(_meta_get(conn, "created_at") or 0)
        slug = _meta_get(conn, "slug")
        notes = _meta_get(conn, "notes")
        # Lazy-migration: stare workspace'y bez sluga dostają slug wygenerowany z nazwy.
        # Unikalność sprawdzamy kosztem ponownego skanu -- OK, bo to jednorazowa operacja per workspace.
        if not slug:
            slug = _unique_slug(_slugify(name))
            _meta_set(conn, "slug", slug)
            conn.commit()
    finally:
        conn.close()
    return WorkspaceInfo(
        id=workspace_id,
        slug=slug,
        name=name,
        notes=notes,
        created_at=created_at,
        **counts,
    )


def _allocate_workspace(name: str, requested_slug: str | None) -> str:
    """Utwórz katalog + minimalny meta dla nowego workspace'a. Zwraca UUID.
    Używane przez oba endpointy tworzenia: JSON (POST '') i multipart (POST '/new')."""
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    name = name.strip()
    if requested_slug:
        requested_slug = requested_slug.strip().lower()
        if not _SLUG_VALID.match(requested_slug):
            raise HTTPException(
                status_code=400,
                detail="Slug must match ^[a-z][a-z0-9-]{0,62}$ (litera na początku, małe litery, cyfry, myślniki)",
            )
        slug = _unique_slug(requested_slug)
    else:
        slug = _unique_slug(_slugify(name))

    # Folder = czytelny slug (nie UUID) -- np. data/workspaces/kutno/. Unikalny
    # względem slugów i nazw folderów (_unique_slug). Stare foldery UUID dalej
    # obsługiwane przy odczycie (resolve_workspace_id / skan).
    workspace_id = slug
    wdir = _workspace_dir(workspace_id)
    wdir.mkdir(parents=True, exist_ok=False)
    _workspace_uploads(workspace_id).mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(_workspace_db(workspace_id))
    try:
        apply_schema(conn)
        _meta_set(conn, "name", name)
        _meta_set(conn, "slug", slug)
        _meta_set(conn, "created_at", str(int(time.time())))
        conn.commit()
    finally:
        conn.close()
    return workspace_id


@router.post("", response_model=WorkspaceInfo, status_code=status.HTTP_201_CREATED)
def create_workspace(
    body: CreateWorkspaceRequest,
    _: str = Depends(require_admin),
) -> WorkspaceInfo:
    workspace_id = _allocate_workspace(body.name, body.slug)
    return _workspace_info(workspace_id)


def _workspace_layers_dir(workspace_id: str) -> Path:
    return _workspace_dir(workspace_id) / "layers"


@router.post("/new", response_model=WorkspaceInfo, status_code=status.HTTP_201_CREATED)
async def create_workspace_with_files(
    background: BackgroundTasks,
    name: str = Form(...),
    slug: str | None = Form(None),
    # GML-e -- co najmniej 1 (używamy `=[]` jako default; walidacja runtime).
    gml_files: list[UploadFile] = File(...),
    # Warstwy GPKG -- opcjonalne. Nazwy i pliki podawane jako dwie równoległe listy.
    # Frontend wysyła każdą parę jako `gpkg_names` + `gpkg_files`.
    gpkg_names: list[str] = Form(default=[]),
    gpkg_files: list[UploadFile] = File(default=[]),
    _: str = Depends(require_admin),
) -> WorkspaceInfo:
    """Pełne tworzenie workspace'a w jednym żądaniu: name + slug + GML-e + opcjonalne GPKG.

    GPKG-i są zapisywane w `data/workspaces/{id}/layers/<slugified_name>.gpkg`
    i rejestrowane w `workspace_meta.custom_layers` jako JSON array.
    GML-e idą do background-ingest (jeden-po-drugim, każdy w osobnym BackgroundTask).
    """
    if not gml_files:
        raise HTTPException(status_code=400, detail="At least one GML file is required")
    for gml in gml_files:
        fn = (gml.filename or "").lower()
        if not fn.endswith((".gml", ".xml", ".zip")):
            raise HTTPException(
                status_code=400,
                detail=f"Plik musi mieć rozszerzenie .gml, .xml albo .zip: {gml.filename}",
            )
    if len(gpkg_names) != len(gpkg_files):
        raise HTTPException(
            status_code=400,
            detail=f"gpkg_names ({len(gpkg_names)}) and gpkg_files ({len(gpkg_files)}) must have equal length",
        )
    for gpkg in gpkg_files:
        fn = (gpkg.filename or "").lower()
        if not fn.endswith(".gpkg"):
            raise HTTPException(
                status_code=400,
                detail=f"GPKG file must have .gpkg extension: {gpkg.filename}",
            )

    workspace_id = _allocate_workspace(name, slug)
    try:
        pending_ingests = await _store_uploaded_files(
            workspace_id, gml_files, gpkg_names, gpkg_files
        )
    except Exception:
        # Upload przerwany (413, brak miejsca, zerwane połączenie) -- skasuj świeżo
        # utworzony katalog. Bez tego na liście zostaje pusty workspace, a ponowna
        # próba pod tą samą nazwą tworzy "-1", "-2"... (zgłoszenie 2026-08-06).
        shutil.rmtree(_workspace_dir(workspace_id), ignore_errors=True)
        raise

    # Ingest dopiero gdy WSZYSTKIE pliki są na dysku -- inaczej rollback powyżej
    # kasowałby bazę spod działającego już zadania w tle.
    for spec in pending_ingests:
        background.add_task(_run_ingest_in_background, **spec)

    return _workspace_info(workspace_id)


async def _store_uploaded_files(
    workspace_id: str,
    gml_files: list[UploadFile],
    gpkg_names: list[str],
    gpkg_files: list[UploadFile],
) -> list[dict]:
    """Zapisz GPKG-i i GML-e nowego workspace'a na dysk (+ rekordy importu).

    Zwraca listę kwargs dla `_run_ingest_in_background` -- zadania w tle rejestruje
    dopiero wołający, po udanym zapisie kompletu (patrz rollback w create_workspace_with_files).
    """
    # 1. GPKG -- zapis do layers/ + rejestracja w meta.
    custom_layers: list[dict] = []
    if gpkg_files:
        layers_dir = _workspace_layers_dir(workspace_id)
        layers_dir.mkdir(parents=True, exist_ok=True)
        for idx, (lname, lfile) in enumerate(zip(gpkg_names, gpkg_files)):
            label = (lname or "").strip() or f"warstwa-{idx + 1}"
            # Nazwa pliku -- slug z label-u (identyczna logika jak dla workspace-slug).
            fname_stem = _slugify(label) or f"layer-{idx + 1}"
            dst = layers_dir / f"{fname_stem}.gpkg"
            # Resolve collision (user mógł dać dwa razy tę samą nazwę).
            n = 2
            while dst.exists():
                dst = layers_dir / f"{fname_stem}-{n}.gpkg"
                n += 1
            max_bytes = settings.max_upload_bytes  # None = bez limitu (desktop)
            total = 0
            with open(dst, "wb") as handle:
                while True:
                    chunk = await lfile.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        handle.close()
                        dst.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=413,
                            detail=f"Plik GPKG przekracza limit {settings.max_upload_mb} MB: "
                                   f"{lfile.filename} (limit zmienia RCN_MAX_UPLOAD_MB)",
                        )
                    handle.write(chunk)
            await lfile.close()
            custom_layers.append({
                "name": label,
                "slug": dst.stem,
                "file": dst.name,
                "size_bytes": total,
            })

    if custom_layers:
        conn = sqlite3.connect(_workspace_db(workspace_id))
        try:
            _meta_set(conn, "custom_layers", json.dumps(custom_layers, ensure_ascii=False))
            conn.commit()
        finally:
            conn.close()

    # 2. GML-e -- każdy zapis + background ingest.
    #
    # ⚠️ Tryb `delta`, nie `snapshot` (zmiana 2026-09-14). Nowy workspace bierze
    # zwykle KILKA plików naraz (roczniki, miesiące, gminy, zawartość archiwum),
    # a snapshot wycofuje z bazy wszystko, czego nie ma w importowanym pliku
    # w zakresie jego dat -- czyli drugi plik kasowałby dorobek pierwszego.
    # Dokładnie tak powstała awaria 2026-09-12 (836 tys. transakcji ukrytych
    # na Lennym, 701 tys. na produkcji). Na pustej bazie snapshot nie miałby
    # zresztą czego wycofać, więc dla pierwszego pliku oba tryby są równoważne.
    uploads_dir = _workspace_uploads(workspace_id)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    db_path = _workspace_db(workspace_id)
    pending_ingests: list[dict] = []

    for gml in gml_files:
        original = (gml.filename or "upload.gml").strip() or "upload.gml"
        stored = _nazwa_uploadu(original)
        stored_path = uploads_dir / stored
        max_bytes = settings.max_upload_bytes  # None = bez limitu (desktop)
        total = 0
        with open(stored_path, "wb") as handle:
            while True:
                chunk = await gml.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    handle.close()
                    stored_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Plik GML przekracza limit {settings.max_upload_mb} MB: "
                               f"{original} (limit zmienia RCN_MAX_UPLOAD_MB)",
                    )
                handle.write(chunk)
        await gml.close()

        # Archiwum wchodzi tą samą drogą co GML, tylko rozpada się na kilka
        # importów -- po jednym na plik w środku.
        if original.lower().endswith(".zip"):
            try:
                rozpakowane = wypakuj_gml(
                    stored_path,
                    uploads_dir,
                    nazwa_docelowa=_nazwa_uploadu,
                    limit_bajtow=max_bytes,
                )
            except PaczkaZaDuza as exc:
                raise HTTPException(
                    status_code=413,
                    detail=f"{exc} {settings.max_upload_mb} MB dla {original} "
                           f"(limit zmienia RCN_MAX_UPLOAD_MB)",
                ) from exc
            except BlednaPaczka as exc:
                raise HTTPException(status_code=400, detail=f"{original}: {exc}") from exc
            finally:
                stored_path.unlink(missing_ok=True)

            for plik in rozpakowane:
                pending_ingests.append({
                    "db_path": db_path,
                    "gml_path": plik.sciezka,
                    "original_filename": plik.nazwa_oryginalna,
                    "stored_filename": plik.sciezka.name,
                    "tryb": "delta",
                    "import_id": create_import_record(
                        db_path,
                        original_filename=plik.nazwa_oryginalna,
                        stored_filename=plik.sciezka.name,
                        file_size_bytes=plik.rozmiar,
                        tryb="delta",
                    ),
                    "file_size": plik.rozmiar,
                })
            continue

        import_id = create_import_record(
            db_path,
            original_filename=original,
            stored_filename=stored,
            file_size_bytes=total,
            tryb="delta",
        )
        pending_ingests.append({
            "db_path": db_path,
            "gml_path": stored_path,
            "original_filename": original,
            "stored_filename": stored,
            "tryb": "delta",
            "import_id": import_id,
            "file_size": total,
        })

    return pending_ingests


@router.post("/import", response_model=WorkspaceInfo, status_code=status.HTTP_201_CREATED)
async def import_workspace(
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
    _: str = Depends(require_admin),
) -> WorkspaceInfo:
    """Wgraj gotowy workspace z paczki `.zip` (wynik `rcn pack`).

    KONSUMENCKI (Model A): to wgranie *gotowej* bazy, nie wzbogacanie -- zawsze
    dostepne adminowi, niezaleznie od HAS_PRODUCER. Paczka ma strukture
    `<nazwa>/<nazwa>.sqlite` (+ notatki/GPKG). Rozpakowanie jest **atomowe**:
    do katalogu tymczasowego -> walidacja -> rename na `workspaces/<nazwa>/`.

    409 gdy `<nazwa>` juz istnieje (chyba ze overwrite=true -> nadpisuje).
    """
    import zipfile
    from pathlib import PurePosixPath

    fn = (file.filename or "").lower()
    if not fn.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Plik musi mieć rozszerzenie .zip")

    root = settings.workspaces_dir
    root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    tmp_zip = root / f".import-{token}.zip"
    tmp_dir = root / f".import-{token}.d"

    try:
        # 1. Zapis paczki do temp (limit rozmiaru).
        max_bytes = settings.max_upload_bytes  # None = bez limitu (desktop)
        total = 0
        with open(tmp_zip, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Paczka przekracza limit {settings.max_upload_mb} MB "
                               f"(limit zmienia RCN_MAX_UPLOAD_MB)",
                    )
                handle.write(chunk)
        await file.close()

        # 2. Walidacja zawartości ZIP (ochrona zip-slip + jeden katalog korzenia).
        try:
            zf = zipfile.ZipFile(tmp_zip)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Uszkodzona paczka .zip")
        with zf:
            names = [n for n in zf.namelist() if n.strip()]
            if not names:
                raise HTTPException(status_code=400, detail="Pusta paczka .zip")
            tops: set[str] = set()
            for n in names:
                pp = PurePosixPath(n)
                if pp.is_absolute() or ".." in pp.parts or "\\" in n:
                    raise HTTPException(status_code=400, detail=f"Niedozwolona ścieżka w paczce: {n}")
                tops.add(pp.parts[0])
            if len(tops) != 1:
                raise HTTPException(
                    status_code=400,
                    detail="Paczka musi mieć dokładnie jeden katalog główny <nazwa>/ (użyj `rcn pack`)",
                )
            name = tops.pop()
            _validate_id(name)
            zf.extractall(tmp_dir)

        # 3. Walidacja zawartości: główna baza .sqlite w korzeniu <nazwa>/.
        src = tmp_dir / name
        if _resolve_main_sqlite(src) is None:
            raise HTTPException(
                status_code=400,
                detail=f"Paczka nie zawiera głównej bazy {name}/<...>.sqlite",
            )

        # 4. Kolizja nazwy.
        final = root / name
        if final.exists():
            if not overwrite:
                raise HTTPException(
                    status_code=409,
                    detail=f"Workspace '{name}' już istnieje (wyślij overwrite=true aby nadpisać)",
                )
            shutil.rmtree(final)
            log.info("import_workspace: nadpisuję istniejący workspace '%s'", name)

        # 5. Atomowy rename katalogu (ten sam filesystem -> workspaces_dir).
        src.replace(final)
        return _workspace_info(name)
    finally:
        tmp_zip.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("", response_model=list[WorkspaceInfo])
def list_workspaces(_: str = Depends(require_auth)) -> list[WorkspaceInfo]:
    out: list[WorkspaceInfo] = []
    for child in sorted(settings.workspaces_dir.iterdir()):
        if not child.is_dir() or not _has_main_sqlite(child):
            continue
        try:
            out.append(_workspace_info(child.name))
        except Exception:
            continue
    out.sort(key=lambda w: w.created_at, reverse=True)
    return out


@router.get("/{workspace_id}", response_model=WorkspaceInfo)
def get_workspace(
    workspace_id: str,
    _: str = Depends(require_auth),
) -> WorkspaceInfo:
    _require_workspace(workspace_id)
    return _workspace_info(workspace_id)


@router.get("/{workspace_id}/custom-layers")
def list_custom_layers(
    workspace_id: str,
    _: str = Depends(require_auth),
) -> dict:
    """Lista warstw GPKG per workspace. Frontend buduje z tego L.control.layers.

    Źródła (Model A): (1) auto-wykryte `*.gpkg` z korzenia folderu (konwencja,
    bez rejestracji — `app.gpkg_discovery`); (2) legacy: warstwy zarejestrowane
    w `workspace_meta.custom_layers` (stare workspace'y / `layers/`). Discovered
    mają priorytet; meta dokładane po slugu, którego nie ma wśród wykrytych."""
    from app.gpkg_discovery import discover_gpkg_layers

    _require_workspace(workspace_id)
    conn = sqlite3.connect(_workspace_db(workspace_id))
    try:
        raw = _meta_get(conn, "custom_layers", "[]") or "[]"
    finally:
        conn.close()
    try:
        meta_layers = json.loads(raw)
    except Exception:
        meta_layers = []

    discovered = discover_gpkg_layers(_workspace_dir(workspace_id))
    disc_slugs = {d["slug"] for d in discovered}
    # size_bytes: warstwy z meta niosą go z uploadu, auto-wykryte trzeba doczytać
    # z dysku -- bez tego UI pokazywało "0.0 MB" przy poprawnie wgranym pliku.
    layers = []
    for d in discovered:
        try:
            size = Path(d["path"]).stat().st_size
        except OSError:
            size = 0
        layers.append({"slug": d["slug"], "name": d["name"], "file": d["file"],
                       "size_bytes": size})
    layers += [m for m in meta_layers if isinstance(m, dict) and m.get("slug") not in disc_slugs]
    # has_poi: frontend dokłada warstwę POI (/api/layers/.../poi.geojson) tylko
    # gdy plik istnieje -- bez 404-owania na każdym workspace bez sidecara.
    wynik = {"workspace_id": workspace_id, "layers": layers,
             "has_poi": _poi_sqlite(workspace_id) is not None}
    return wynik


# --- Plik POI (warstwa + wtyczki) -- zarządzanie po stronie KONSUMENTA -------
# Plik `<nazwa>.poi.sqlite` normalnie jedzie w paczce od producenta (folder =
# komplet), ale user może też dostać go osobno -- stąd upload/usuwanie z UI
# (admin), analogicznie do importu .zip. NIE jest gatowane HAS_PRODUCER.

MAX_POI_BYTES = 50 * 1024 * 1024  # największy realny powiat (Warszawa) ~2 MB

_POI_REQUIRED_COLUMNS = {"kind", "name", "lon", "lat", "x", "y"}


def _validate_poi_sqlite(path: Path) -> dict:
    """Walidacja wgranego pliku POI: SQLite z tabelą `poi` o wymaganych
    kolumnach. Zwraca {'points': n, 'generated_at': ...} albo rzuca 400."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=400, detail=f"Plik nie jest bazą SQLite: {exc}")
    try:
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(poi)").fetchall()}
            if not cols:
                raise HTTPException(status_code=400, detail="Plik nie zawiera tabeli 'poi'")
            missing = _POI_REQUIRED_COLUMNS - cols
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Tabela 'poi' bez wymaganych kolumn: {', '.join(sorted(missing))}")
            points = conn.execute("SELECT COUNT(*) FROM poi").fetchone()[0]
            meta = {}
            try:
                meta = dict(conn.execute("SELECT key, value FROM poi_meta").fetchall())
            except sqlite3.Error:
                pass
        except sqlite3.DatabaseError as exc:
            raise HTTPException(status_code=400, detail=f"Plik nieczytelny jako SQLite: {exc}")
    finally:
        conn.close()
    return {"points": points, "generated_at": meta.get("generated_at"),
            "attribution": meta.get("attribution")}


@router.get("/{workspace_id}/poi")
def poi_info(workspace_id: str, _: str = Depends(require_auth)) -> dict:
    """Status pliku POI workspace'a (panel warstw w UI)."""
    workspace_id = resolve_workspace_id(workspace_id)
    _require_workspace(workspace_id)
    poi_path = _poi_sqlite(workspace_id)
    if poi_path is None:
        return {"present": False}
    info = _validate_poi_sqlite(poi_path)
    return {"present": True, "file": poi_path.name,
            "size_bytes": poi_path.stat().st_size, **info}


@router.post("/{workspace_id}/poi")
async def upload_poi(
    workspace_id: str,
    file: UploadFile = File(...),
    _: str = Depends(require_admin),
) -> dict:
    """Wgraj/zastąp plik POI workspace'a. Zapis atomowy pod kanoniczną nazwą
    `<stem bazy głównej>.poi.sqlite`; ewentualne inne pliki `*.poi.sqlite`
    w folderze są usuwane (jeden plik POI na workspace)."""
    workspace_id = resolve_workspace_id(workspace_id)
    _require_workspace(workspace_id)
    fn = (file.filename or "").lower()
    if not fn.endswith(".sqlite"):
        raise HTTPException(status_code=400,
                            detail="Plik POI musi mieć rozszerzenie .sqlite (np. lodz.poi.sqlite)")
    wdir = _workspace_dir(workspace_id)
    main = _resolve_main_sqlite(wdir)
    base = main.name.removesuffix(".sqlite") if main is not None else "workspace"
    target = wdir / f"{base}{_POI_SUFFIX}"

    # Suffix .tmp -- niedokończony upload nie zostanie wzięty za plik POI.
    tmp = wdir / f".poi-upload-{uuid.uuid4().hex}.tmp"
    try:
        total = 0
        with open(tmp, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_POI_BYTES:
                    raise HTTPException(status_code=413,
                                        detail=f"Plik POI przekracza limit {MAX_POI_BYTES // (1024 * 1024)} MB")
                handle.write(chunk)
        await file.close()
        info = _validate_poi_sqlite(tmp)
        for old in wdir.glob(f"*{_POI_SUFFIX}"):
            old.unlink()
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    log.info("POI %s: wgrano %s (%d punktów)", workspace_id, target.name, info["points"])
    return {"status": "ok", "file": target.name, **info}


@router.delete("/{workspace_id}/poi")
def delete_poi(workspace_id: str, _: str = Depends(require_admin)) -> dict:
    """Usuń plik POI workspace'a (warstwa i wtyczka POI przestają go widzieć)."""
    workspace_id = resolve_workspace_id(workspace_id)
    _require_workspace(workspace_id)
    wdir = _workspace_dir(workspace_id)
    removed = []
    for path in wdir.glob(f"*{_POI_SUFFIX}"):
        path.unlink()
        removed.append(path.name)
    if not removed:
        raise HTTPException(status_code=404, detail="Workspace nie ma pliku POI")
    return {"status": "deleted", "files": removed}


async def add_custom_layer(
    workspace_id: str,
    name: str = Form(...),
    file: UploadFile = File(...),
    _: str = Depends(require_admin),
) -> WorkspaceInfo:
    """Dodaj warstwę GPKG do workspace (model „folder = komplet").

    Plik zapisywany jest do **korzenia** folderu jako `dzialki.gpkg` lub
    `budynki.gpkg` (rodzaj wykryty po polach `ID_DZIALKI`/`ID_BUDYNKU` lub po
    nazwie). **Bez rejestracji w meta** — warstwa jest potem wykrywana
    automatycznie (`app.gpkg_discovery`). Jedna warstwa danego rodzaju na
    workspace (ponowny upload nadpisuje).

    Producent-only (upload warstwy EGIB = źródło wzbogacania): trasa jest
    rejestrowana TYLKO gdy obecny pakiet rcn_producer (patrz blok niżej), więc
    w buildzie konsumenta nie istnieje w ogóle (brak w OpenAPI; żądanie -> 404
    z routingu). Patrz rcn_core.producer / Model A.
    """
    workspace_id = resolve_workspace_id(workspace_id)
    _require_workspace(workspace_id)

    fn = (file.filename or "").lower()
    if not fn.endswith(".gpkg"):
        raise HTTPException(status_code=400, detail="File must have .gpkg extension")

    label = (name or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="Layer name is required")

    from app.gpkg_discovery import _classify, _norm

    wdir = _workspace_dir(workspace_id)
    wdir.mkdir(parents=True, exist_ok=True)

    # Model "folder = komplet": zapis do KORZENIA folderu, BEZ rejestracji w meta.
    # Najpierw plik do temp, potem klasyfikacja (po zawartości pól / nazwie) ->
    # docelowa nazwa dzialki.gpkg / budynki.gpkg. Jedna warstwa danego rodzaju
    # na workspace -- ponowny upload nadpisuje. Auto-wykrywanie (app.gpkg_discovery)
    # zrobi resztę -- żadnego wpisu w workspace_meta.
    tmp = wdir / ".upload.tmp.gpkg"
    max_bytes = settings.max_upload_bytes  # None = bez limitu (desktop)
    total = 0
    try:
        with open(tmp, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    handle.close()
                    tmp.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Plik GPKG przekracza limit {settings.max_upload_mb} MB "
                               f"(limit zmienia RCN_MAX_UPLOAD_MB)",
                    )
                handle.write(chunk)
        await file.close()

        # 1. po zawartości (ID_DZIALKI/ID_BUDYNKU); 2. po nazwie oryginału/etykiety.
        kind, _lbl = _classify(tmp)
        if not kind:
            hint = _norm(f"{file.filename or ''} {label}")
            if "dzialk" in hint:
                kind = "dzialki"
            elif "budynk" in hint or "budynek" in hint or "building" in hint:
                kind = "budynki"
        if not kind:
            tmp.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail="Nie rozpoznano warstwy. GPKG musi mieć pole ID_DZIALKI lub "
                       "ID_BUDYNKU, albo nazwę zawierającą 'dzialki'/'budynki'.",
            )
        dst = wdir / f"{kind}.gpkg"
        dst.unlink(missing_ok=True)
        tmp.replace(dst)
    except HTTPException:
        raise
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

    return _workspace_info(workspace_id)


def delete_custom_layer(
    workspace_id: str,
    slug: str,
    _: str = Depends(require_admin),
) -> None:
    """Usuń warstwę GPKG z workspace.
    1. Warstwa wykryta w korzeniu (slug = rodzaj: dzialki/budynki) -> kasuje plik.
    2. Legacy: warstwa z `layers/` zarejestrowana w meta -> kasuje plik + wpis.

    Producent-only -- trasa rejestrowana tylko gdy obecny rcn_producer (blok
    niżej); w buildzie konsumenta nie istnieje (404 z routingu, brak w OpenAPI).
    Patrz rcn_core.producer / Model A."""
    workspace_id = resolve_workspace_id(workspace_id)
    _require_workspace(workspace_id)

    # 1. Warstwa wykryta w korzeniu folderu (model "folder = komplet").
    from app.gpkg_discovery import discover_gpkg_layers
    disc = {d["slug"]: d["path"] for d in discover_gpkg_layers(_workspace_dir(workspace_id))}
    if slug in disc:
        Path(disc[slug]).unlink(missing_ok=True)
        return

    # 2. Legacy: layers/ + workspace_meta.
    db_path = _workspace_db(workspace_id)
    conn = sqlite3.connect(db_path)
    try:
        raw = _meta_get(conn, "custom_layers", "[]") or "[]"
        try:
            existing = json.loads(raw)
        except Exception:
            existing = []
        match = next((e for e in existing if isinstance(e, dict) and e.get("slug") == slug), None)
        if not match:
            raise HTTPException(status_code=404, detail=f"Layer '{slug}' not found")

        layers_dir = _workspace_layers_dir(workspace_id)
        target = layers_dir / (match.get("file") or f"{slug}.gpkg")
        target.unlink(missing_ok=True)

        remaining = [e for e in existing if e is not match]
        _meta_set(conn, "custom_layers", json.dumps(remaining, ensure_ascii=False))
        conn.commit()
    finally:
        conn.close()


# Model A: trasy producenta (upload/usuwanie warstw EGIB = źródło wzbogacania)
# rejestrowane TYLKO gdy obecny pakiet rcn_producer. W buildzie konsumenta nie
# powstają w ogóle -- brak ich w OpenAPI, a żądanie zwraca 404 z routingu (nie
# z guardu w handlerze). Patrz rcn_core.producer / Model A.
if HAS_PRODUCER:
    router.post(
        "/{workspace_id}/layers/add",
        response_model=WorkspaceInfo,
        status_code=status.HTTP_201_CREATED,
    )(add_custom_layer)
    router.delete(
        "/{workspace_id}/layers/{slug}",
        status_code=status.HTTP_204_NO_CONTENT,
    )(delete_custom_layer)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(
    workspace_id: str,
    _: str = Depends(require_admin),
) -> None:
    wdir = _require_workspace(workspace_id)
    shutil.rmtree(wdir)


class PatchWorkspaceRequest(BaseModel):
    """Edycja meta workspace -- wszystkie pola opcjonalne, brak = no-op.
    Slug walidowany na konflikt (409) i format (`^[a-z][a-z0-9-]+$`).
    """
    name: str | None = None
    slug: str | None = None
    notes: str | None = None


@router.patch("/{workspace_id}", response_model=WorkspaceInfo)
def patch_workspace(
    workspace_id: str,
    body: PatchWorkspaceRequest,
    _: str = Depends(require_admin),
) -> WorkspaceInfo:
    """Edycja workspace.name / .slug / .notes. Walidacja slug konfliktu
    przed zapisem (409 dla kolizji)."""
    _require_workspace(workspace_id)

    if body.slug is not None:
        if not _SLUG_VALID.match(body.slug):
            raise HTTPException(status_code=400,
                detail="Slug musi pasować do ^[a-z][a-z0-9-]{0,62}$")
        existing = _existing_slugs()
        if existing.get(body.slug) and existing[body.slug] != workspace_id:
            raise HTTPException(status_code=409,
                detail=f"Slug '{body.slug}' już zajęty przez inny workspace")
    if body.name is not None and not body.name.strip():
        raise HTTPException(status_code=400, detail="Name nie może być puste")

    conn = sqlite3.connect(_workspace_db(workspace_id))
    try:
        if body.name is not None:
            _meta_set(conn, "name", body.name.strip())
        if body.slug is not None:
            _meta_set(conn, "slug", body.slug)
        if body.notes is not None:
            _meta_set(conn, "notes", body.notes)
        conn.commit()
    finally:
        conn.close()
    return _workspace_info(workspace_id)


# ---------------------------------------------------------------------------
# Workspace lock (busy state)
# ---------------------------------------------------------------------------
# Gdy workspace ma aktywny ingest (imports.status='processing'/'queued')
# albo ulepszenie (phase_runs.status='running'), blokujemy read endpoints
# (mapa/tabela/lookups/eksporty/details/neighbors) zwracając HTTP 423.
#
# Powody:
# 1. Custom layers .geojson endpoint w app/layers.py używa Fiony
#    -- współbieżne z subprocess phase_runner (pyproj+SpatiaLite) zwiększa
#    ryzyko segfault libproj-fiona w uvicorn worker.
# 2. Read endpoints czytałyby tx_cache w trakcie refresh (przez subprocess)
#    -- niespójny snapshot.
# 3. Eksport XLSX/GPKG tworzy pliki z niespójnych stanów -- gorszy UX niż
#    czekanie na finish.
#
# Helpery: get_workspace_busy_status() zwraca słownik z opisem aktywności
# lub None. assert_workspace_idle() jako FastAPI Dependency rzuca 423
# z body {kind, label, since, duration_s, id}.
#
# Endpointy NIEzguardowane (UI musi się odpytać żeby wykryć unlock):
# - GET /api/workspaces/{id}, /imports, /enhancements/status, /busy
# - GET /api/workspaces/{id}/custom-layers
# - POST/DELETE/PATCH (mgmt) -- te albo startują nowy proces, albo są
#   admin-only i krytyczne (delete workspace).

_PHASE_LABELS = {
    "enrich_egib": "Wzbogacanie geometrii z EGIB GPKG",
    "compute_flags": "Obliczanie flag jakości",
    "geocoding": "Geocoding (Nominatim)",
}


def get_workspace_busy_status(db_path: Path) -> dict | None:
    """Zwraca słownik opisujący aktywny proces lub None gdy idle.
    Robi też cleanup stale processing (>1h) zanim sprawdzi status."""
    import time
    if not db_path.exists():
        return None
    conn = open_workspace(db_path)
    try:
        # Cleanup zombie running (>1h) -- subprocess mógł crashować bez finish
        mark_stale_imports(conn, max_age_s=3600)
        mark_stale_phase_runs(conn, max_age_s=3600)

        now = time.time()

        # Najpierw running phase_run (ulepszenie)
        row = conn.execute(
            "SELECT id, phase, started_at, triggered_by FROM phase_runs "
            "WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return {
                "kind": row[1],
                "label": _PHASE_LABELS.get(row[1], row[1]),
                "since": row[2],
                "duration_s": round(now - row[2], 1),
                "id": row[0],
                "triggered_by": row[3],
            }

        # Potem processing/queued import
        row = conn.execute(
            "SELECT id, original_filename, upload_timestamp, stage, progress_pct "
            "FROM imports WHERE status IN ('processing', 'queued') "
            "ORDER BY upload_timestamp DESC LIMIT 1"
        ).fetchone()
        if row:
            return {
                "kind": "import",
                "label": f"Import GML: {row[1]}",
                "since": float(row[2]),
                "duration_s": round(now - row[2], 1),
                "id": row[0],
                "stage": row[3],
                "progress_pct": row[4],
            }
        return None
    finally:
        conn.close()


def assert_workspace_idle(workspace_id: str) -> None:
    """FastAPI Dependency. Rzuca HTTP 423 Locked gdy workspace jest busy.
    Używaj w read endpointach (mapa/tabela/lookups/eksporty/details).
    Path validation 404 załatwiają osobne dependency / _require_workspace
    w handlerze -- tu zakładamy że workspace_id jest valid."""
    try:
        db_path = _workspace_db(workspace_id)
    except HTTPException:
        return  # invalid id -- inne dependency rzuci 400/404
    busy = get_workspace_busy_status(db_path)
    if busy:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "message": "Workspace zablokowany -- trwa przetwarzanie",
                **busy,
            },
        )


@router.get("/{workspace_id}/busy")
def workspace_busy(
    workspace_id: str,
    _: str = Depends(require_auth),
) -> dict:
    """Status zajętości workspace (dla pollingu z UI overlay).
    Zwraca: {active: bool, ...szczegóły gdy active=true}."""
    _require_workspace(workspace_id)
    busy = get_workspace_busy_status(_workspace_db(workspace_id))
    if busy:
        return {"active": True, **busy}
    return {"active": False}


# ---------------------------------------------------------------------------
# Ulepszenia (enhancements) -- subprocess-based
# ---------------------------------------------------------------------------
# Każde ulepszenie (enrich-egib, compute-flags, geocoding) uruchamia phase_runner
# jako detached subprocess. Endpoint tworzy phase_runs row z status='running'
# i przekazuje --run-id; runner sam domyka finish_phase_run(success|failed).
# Concurrency guard: odmawiamy spawn-u gdy istnieje running phase_run dla tej
# fazy w tym workspace (mark_stale_phase_runs cleanup-uje stuck running >1h).

# Model A: konsument (bez rcn_producer) ma TYLKO compute-flags (liczone z GML,
# nic producenta nie ujawnia). Wzbogacanie (enrich-egib) i geocoding to producent
# -- dostępne tylko gdy pakiet rcn_producer obecny. Patrz rcn_core.producer.
_ALLOWED_PHASES = {"compute-flags"}
if HAS_PRODUCER:
    _ALLOWED_PHASES |= {"enrich-egib", "geocoding"}
_PHASE_TO_DB = {  # mapping CLI arg -> phase column value w phase_runs
    "enrich-egib": "enrich_egib",
    "compute-flags": "compute_flags",
    "geocoding": "geocoding",
}


class EnhancementResponse(BaseModel):
    run_id: int
    phase: str
    status: str = "running"


@router.post(
    "/{workspace_id}/enhancements/{op}",
    response_model=EnhancementResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_enhancement(
    workspace_id: str,
    op: str,
    ctx: AuthContext = Depends(require_admin),
) -> EnhancementResponse:
    """Spawn ulepszenia jako subprocess. Zwraca run_id do trackingu w
    GET /enhancements/status. 409 gdy poprzedni run jeszcze trwa."""
    if op not in _ALLOWED_PHASES:
        raise HTTPException(status_code=400,
            detail=f"Nieznane ulepszenie '{op}'. Dozwolone: {sorted(_ALLOWED_PHASES)}")
    _require_workspace(workspace_id)
    db_path = _workspace_db(workspace_id)
    phase_db = _PHASE_TO_DB[op]

    conn = open_workspace(db_path)
    try:
        # Cleanup zombie running runs (>1h) przed concurrency check
        mark_stale_phase_runs(conn, max_age_s=3600)
        if has_running_phase_run(conn, phase_db):
            raise HTTPException(status_code=409,
                detail=f"Ulepszenie '{op}' już trwa (poprzedni run jeszcze nie zakończony)")
        run_id = start_phase_run(conn, phase_db, triggered_by=ctx.username)
    finally:
        conn.close()

    # Spawn subprocess (frozen-safe: helper wybiera python -m / .exe --phase-runner)
    import subprocess

    from rcn_core.phase_runner import phase_runner_command
    cmd = phase_runner_command([
        "--phase", op,
        "--db", str(db_path),
        "--run-id", str(run_id),
    ])
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        # Spawn failed -- domknij phase_run jako failed
        log.exception("Failed to spawn enhancement subprocess for %s/%s", workspace_id, op)
        conn = open_workspace(db_path)
        try:
            finish_phase_run(conn, run_id, status="failed",
                error_msg=f"spawn failed: {type(exc).__name__}: {exc}")
        finally:
            conn.close()
        raise HTTPException(status_code=500,
            detail=f"Nie udało się uruchomić subprocess: {exc}") from exc

    return EnhancementResponse(run_id=run_id, phase=phase_db, status="running")


class PhaseRunInfo(BaseModel):
    id: int
    phase: str
    status: str
    started_at: float
    finished_at: float | None = None
    duration_s: float | None = None
    diagnostics_json: str | None = None
    error_msg: str | None = None
    triggered_by: str | None = None


@router.get(
    "/{workspace_id}/enhancements/status",
    response_model=list[PhaseRunInfo],
)
def get_enhancements_status(
    workspace_id: str,
    limit: int = 20,
    phase: str | None = None,
    _: AuthContext = Depends(require_auth),
) -> list[PhaseRunInfo]:
    """Lista ostatnich phase_runs (opcjonalnie filtr per phase). Najpierw
    cleanup stale running (>1h) -- subprocess mógł umrzeć bez finish."""
    _require_workspace(workspace_id)
    phase_db = _PHASE_TO_DB.get(phase, phase) if phase else None
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        mark_stale_phase_runs(conn, max_age_s=3600)
        rows = list_phase_runs(conn, phase=phase_db, limit=limit)
    finally:
        conn.close()
    return [PhaseRunInfo(**r) for r in rows]


@router.post("/{workspace_id}/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_gml(
    workspace_id: str,
    background: BackgroundTasks,
    file: UploadFile,
    # Domyślnie `delta`: snapshot wycofuje z bazy wszystko, czego nie ma
    # w pliku (w zakresie jego dat). Awaria 2026-09-12 wzięła się z tego, że
    # fragmenty zbioru poleciały jako snapshoty -- patrz CLAUDE.md.
    tryb: Literal["snapshot", "delta"] = Form("delta"),
    _: str = Depends(require_admin),
) -> UploadResponse:
    """Stream the upload to disk, create a `processing` import record, hand the
    actual parse+ingest to a FastAPI BackgroundTask, and return immediately.

    The client then polls `/api/workspaces/{id}/imports/{import_id}` for status
    and progress_pct until status leaves `processing`. Prevents browser /
    tailnet proxy from timing out on long parses of 100+ MB GML files.
    """
    _require_workspace(workspace_id)

    original_name = (file.filename or "upload.gml").strip() or "upload.gml"
    # `.zip` jest tu równoprawny z GML-em: źródła rozsyłają powiat spakowany
    # (często kilka GML-i po gminach), a ręczne rozpakowywanie przed wgraniem
    # było czystą uciążliwością. Paczka WORKSPACE'U (`rcn pack`) ma osobny
    # endpoint `/import` i tam trafia po zawartości, nie po rozszerzeniu.
    z_archiwum = original_name.lower().endswith(".zip")
    if not z_archiwum and not original_name.lower().endswith((".gml", ".xml")):
        raise HTTPException(
            status_code=400,
            detail="Plik musi mieć rozszerzenie .gml, .xml albo .zip (archiwum z GML-ami)",
        )

    uploads_dir = _workspace_uploads(workspace_id)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    stored_name = _nazwa_uploadu(original_name)
    stored_path = uploads_dir / stored_name

    max_bytes = settings.max_upload_bytes  # None = bez limitu (desktop)
    total = 0
    with open(stored_path, "wb") as handle:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                handle.close()
                stored_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"Plik przekracza limit {settings.max_upload_mb} MB "
                           f"(limit zmienia RCN_MAX_UPLOAD_MB)",
                )
            handle.write(chunk)
    await file.close()

    db_path = _workspace_db(workspace_id)

    if z_archiwum:
        try:
            pliki = wypakuj_gml(
                stored_path,
                uploads_dir,
                nazwa_docelowa=_nazwa_uploadu,
                limit_bajtow=max_bytes,
            )
        except PaczkaZaDuza as exc:
            stored_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=413,
                detail=f"{exc} {settings.max_upload_mb} MB (limit zmienia RCN_MAX_UPLOAD_MB)",
            ) from exc
        except BlednaPaczka as exc:
            stored_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            # Samo archiwum nie jest już potrzebne -- w historii importów liczą
            # się rozpakowane GML-e, każdy ze swoim rekordem.
            stored_path.unlink(missing_ok=True)

        # ⚠️ Paczka z kilkoma GML-ami w trybie `snapshot` to awaria 2026-09-12
        # rozegrana od nowa: drugi plik wycofałby wszystko, co wniósł pierwszy
        # (snapshot wycofuje z bazy wszystko, czego nie ma w pliku, w zakresie
        # jego dat). Pliki z jednej paczki są z definicji FRAGMENTAMI zbioru,
        # więc tryb jest tu wymuszany na `delta` -- z informacją dla operatora,
        # bo cicha podmiana trybu byłaby równie zła jak sama pomyłka.
        uwaga: str | None = None
        if tryb == "snapshot" and len(pliki) > 1:
            tryb = "delta"
            uwaga = (
                f"Archiwum zawiera {len(pliki)} plików, więc import poszedł w trybie "
                f"delta. W trybie snapshot każdy kolejny plik wycofywałby "
                f"transakcje wniesione przez poprzedni."
            )

        # Zadania w tle FastAPI idą sekwencyjnie, więc GML-e z jednej paczki
        # przetwarzają się po kolei -- tak jak przy wgraniu ich pojedynczo.
        importy: list[int] = []
        for gml in pliki:
            gml_import_id = create_import_record(
                db_path,
                original_filename=gml.nazwa_oryginalna,
                stored_filename=gml.sciezka.name,
                file_size_bytes=gml.rozmiar,
                tryb=tryb,
            )
            importy.append(gml_import_id)
            background.add_task(
                _run_ingest_in_background,
                db_path=db_path,
                gml_path=gml.sciezka,
                original_filename=gml.nazwa_oryginalna,
                stored_filename=gml.sciezka.name,
                tryb=tryb,
                import_id=gml_import_id,
                file_size=gml.rozmiar,
            )

        pierwszy = pliki[0]
        return UploadResponse(
            import_id=importy[0],
            original_filename=pierwszy.nazwa_oryginalna,
            stored_filename=pierwszy.sciezka.name,
            tryb=tryb,
            status="processing",
            stage="queued",
            progress_pct=0,
            import_ids=importy,
            zrodlo_archiwum=original_name,
            uwaga=uwaga,
        )

    import_id = create_import_record(
        db_path,
        original_filename=original_name,
        stored_filename=stored_name,
        file_size_bytes=total,
        tryb=tryb,
    )

    background.add_task(
        _run_ingest_in_background,
        db_path=db_path,
        gml_path=stored_path,
        original_filename=original_name,
        stored_filename=stored_name,
        tryb=tryb,
        import_id=import_id,
        file_size=total,
    )

    return UploadResponse(
        import_id=import_id,
        original_filename=original_name,
        stored_filename=stored_name,
        tryb=tryb,
        status="processing",
        stage="queued",
        progress_pct=0,
        import_ids=[import_id],
    )


def _run_ingest_in_background(
    db_path,
    gml_path,
    original_filename: str,
    stored_filename: str,
    tryb: str,
    import_id: int,
    file_size: int,
) -> None:
    """Spawn ingest jako detached subprocess (rcn_core.phase_runner).
    Izoluje pyproj/spatialite od fiony używanej w app/layers.py w uvicorn
    process -- bez tego segfault libproj-fiona blokuje upload nowych powiatów.

    Subprocess sam ustawia imports.status (success/failed) i pisze
    progress_pct/stage przez update_import_progress (subprocess-safe -- otwiera
    własną sqlite3.connection). Funkcja zwraca natychmiast po Popen.
    """
    import subprocess

    from rcn_core.phase_runner import phase_runner_command
    cmd = phase_runner_command([
        "--phase", "ingest",
        "--db", str(db_path),
        "--gml", str(gml_path),
        "--import-id", str(import_id),
        "--tryb", tryb,
        "--original-filename", original_filename,
        "--stored-filename", stored_filename,
        "--file-size", str(file_size),
    ])
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        log.exception("Failed to spawn ingest subprocess for import %s", import_id)
        try:
            update_import_progress(
                db_path,
                import_id,
                status="failed",
                stage="failed",
                error_msg=f"spawn failed: {type(exc).__name__}: {exc}",
            )
        except Exception:
            pass


_IMPORT_COLUMNS = """
    id, original_filename, stored_filename, file_hash, file_size_bytes,
    tryb, upload_timestamp, parser_epsg,
    transaction_count, plot_count, building_count, local_count,
    inserted_count, updated_count, skipped_count, withdrawn_count,
    data_transakcji_od, data_transakcji_do,
    duration_s, status, stage, progress_pct, error_msg
"""


class RegisterImportRequest(BaseModel):
    """Register a GML file already placed in the workspace `uploads/` dir
    (e.g. via SCP/rsync) and kick off background ingest.

    Useful when the browser / HTTP upload through Tailscale is too slow for
    large files (100+ MB) — direct SCP over the same tailnet is typically
    3-5× faster than a multipart HTTP POST."""
    filename: str
    original_filename: str | None = None
    tryb: Literal["snapshot", "delta"] = "delta"


@router.post(
    "/{workspace_id}/imports/register",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def register_import(
    workspace_id: str,
    body: RegisterImportRequest,
    background: BackgroundTasks,
    _: str = Depends(require_admin),
) -> UploadResponse:
    _require_workspace(workspace_id)

    uploads_dir = _workspace_uploads(workspace_id)
    safe_name = Path(body.filename).name  # prevent path traversal
    file_path = uploads_dir / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"File not found in uploads dir: {safe_name}. "
                   f"Place the GML in {uploads_dir} (e.g. via scp) first.",
        )

    original_name = body.original_filename or safe_name
    if not safe_name.lower().endswith((".gml", ".xml")):
        raise HTTPException(status_code=400, detail="File must have .gml or .xml extension")

    file_size = file_path.stat().st_size
    db_path = _workspace_db(workspace_id)
    import_id = create_import_record(
        db_path,
        original_filename=original_name,
        stored_filename=safe_name,
        file_size_bytes=file_size,
        tryb=body.tryb,
    )

    background.add_task(
        _run_ingest_in_background,
        db_path=db_path,
        gml_path=file_path,
        original_filename=original_name,
        stored_filename=safe_name,
        tryb=body.tryb,
        import_id=import_id,
        file_size=file_size,
    )

    return UploadResponse(
        import_id=import_id,
        original_filename=original_name,
        stored_filename=safe_name,
        tryb=body.tryb,
        status="processing",
        stage="queued",
        progress_pct=0,
    )


@router.get("/{workspace_id}/imports", response_model=list[ImportInfo])
def list_imports(
    workspace_id: str,
    _: str = Depends(require_auth),
) -> list[ImportInfo]:
    _require_workspace(workspace_id)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        # Cleanup stale processing -- subprocess ingest mógł umrzeć (segfault,
        # OOM, restart kontenera) bez ustawienia status='failed'. Bez tego
        # banner postępu animuje się w nieskończoność.
        mark_stale_imports(conn, max_age_s=3600)
        rows = conn.execute(
            f"SELECT {_IMPORT_COLUMNS} FROM imports ORDER BY upload_timestamp DESC, id DESC"
        ).fetchall()
    finally:
        conn.close()
    return [ImportInfo(**dict(row)) for row in rows]


@router.get("/{workspace_id}/imports/{import_id}", response_model=ImportInfo)
def get_import(
    workspace_id: str,
    import_id: int,
    _: str = Depends(require_auth),
) -> ImportInfo:
    """Polled by the UI upload widget to track background-ingest progress."""
    _require_workspace(workspace_id)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        row = conn.execute(
            f"SELECT {_IMPORT_COLUMNS} FROM imports WHERE id = ?", (import_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Import not found")
    finally:
        conn.close()
    return ImportInfo(**dict(row))


@router.delete("/{workspace_id}/imports/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_import(
    workspace_id: str,
    import_id: int,
    _: str = Depends(require_admin),
) -> None:
    _require_workspace(workspace_id)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        row = conn.execute(
            "SELECT stored_filename FROM imports WHERE id = ?",
            (import_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Import not found")
        stored_filename = row["stored_filename"]
        conn.execute("DELETE FROM imports WHERE id = ?", (import_id,))
        conn.commit()
    finally:
        conn.close()

    upload_path = _workspace_uploads(workspace_id) / stored_filename
    upload_path.unlink(missing_ok=True)


def _nazwa_uploadu(original: str) -> str:
    """Nazwa pliku w `uploads/`: znacznik czasu + losowy sufiks + bezpieczna nazwa.

    Znacznik czasu porządkuje katalog chronologicznie, uuid rozstrzyga kolizje
    przy wgraniu dwóch plików o tej samej nazwie w tej samej milisekundzie
    (realne przy rozpakowaniu archiwum).
    """
    return f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}_{_safe_filename(original)}"


def _safe_filename(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    return safe[-120:] or "upload.gml"


# --- Słownik oznaczeń obrębów -------------------------------------------------
# Numer obrębu z GML nie niesie oznaczenia urzędowego („B-24" w Łodzi); ono jest
# tylko w EGIB. Słownik `<nazwa>.obreby.csv` przenosi je do workspace'u (12 KB
# zamiast 70 MB warstwy) i daje operatorowi możliwość poprawienia wpisu.
# Szczegóły formatu: `rcn_core/slownik_obrebow.py`.


class WpisSlownikaObrebow(BaseModel):
    teryt_gminy: str
    numer_obrebu: str
    oznaczenie: str
    gmina: str = ""
    zrodlo: str = ""


class SlownikObrebowRequest(BaseModel):
    wpisy: list[WpisSlownikaObrebow]


@router.get("/{workspace_id}/obreby")
def get_slownik_obrebow(workspace_id: str, _: str = Depends(require_auth)) -> dict:
    """Obręby WYSTĘPUJĄCE W BAZIE + oznaczenie ze słownika, jeśli jest.

    UI pokazuje dokładnie te obręby, których dotyczą dane workspace'u -- nie
    cały powiat z EGIB -- żeby operator widział, co jest jeszcze nieopisane.
    """
    workspace_id = resolve_workspace_id(workspace_id)
    _require_workspace(workspace_id)
    wdir = _workspace_dir(workspace_id)
    plik = znajdz_slownik(wdir)
    slownik = wczytaj(plik) if plik else {}

    conn = open_workspace(_workspace_db(workspace_id))
    try:
        pary = obreby_z_bazy(conn)
    finally:
        conn.close()

    # Kolejność źródeł: plik workspace'u (w tym poprawki operatora) przykrywa
    # słownik wbudowany w aplikację. Bez tego drugiego panel pokazywałby puste
    # pole przy obrębie, który w bazie ma już oznaczenie.
    krajowy = wczytaj_krajowy()
    wiersze = []
    for teryt, numer, obecne in pary:
        klucz = f"{teryt}.{numer}"
        wpis = slownik.get(klucz) or krajowy.get(klucz)
        wiersze.append({
            "teryt_gminy": teryt,
            "numer_obrebu": numer,
            "obreb_w_bazie": obecne,
            "oznaczenie": wpis.oznaczenie if wpis else "",
            "gmina": wpis.gmina if wpis else "",
            "zrodlo": wpis.zrodlo if wpis else "",
        })
    return {
        "plik": plik.name if plik else None,
        # Ile pozycji pokrywa wbudowany słownik krajowy -- UI mówi wtedy
        # operatorowi, skąd wzięły się oznaczenia, których sam nie wgrywał.
        "z_wbudowanego": sum(1 for w in wiersze
                             if w["zrodlo"] == "wbudowany" and w["oznaczenie"]),
        "wpisow_w_slowniku": len(slownik),
        "obrebow_w_bazie": len(wiersze),
        "pokrytych": sum(1 for w in wiersze if w["oznaczenie"]),
        "zastosowanych": sum(1 for w in wiersze
                             if w["oznaczenie"] and w["oznaczenie"] == w["obreb_w_bazie"]),
        "obreby": wiersze,
    }


@router.put("/{workspace_id}/obreby")
def put_slownik_obrebow(
    workspace_id: str,
    body: SlownikObrebowRequest,
    _: str = Depends(require_admin),
) -> dict:
    """Zapisz słownik. Wpisy przychodzące z UI dostają `zrodlo=reczny`, więc
    kolejne generowanie z EGIB ich nie nadpisze."""
    workspace_id = resolve_workspace_id(workspace_id)
    _require_workspace(workspace_id)
    wdir = _workspace_dir(workspace_id)
    plik = znajdz_slownik(wdir)
    if plik is None:
        main = _resolve_main_sqlite(wdir)
        plik = sciezka_slownika(wdir, main.name if main is not None else "workspace.sqlite")

    stare = wczytaj(plik)
    wynik = dict(stare)
    for w in body.wpisy:
        teryt = w.teryt_gminy.strip()
        numer = w.numer_obrebu.strip()
        oznaczenie = w.oznaczenie.strip()
        if not teryt or not numer:
            continue
        klucz = f"{teryt}.{numer}"
        if not oznaczenie:
            # Puste oznaczenie = usunięcie wpisu; zostawione w słowniku
            # wyczyściłoby oznaczenie w bazie przy następnym „zastosuj".
            wynik.pop(klucz, None)
            continue
        obecny = stare.get(klucz)
        wynik[klucz] = WpisObrebu(
            teryt_gminy=teryt, numer_obrebu=numer, oznaczenie=oznaczenie,
            gmina=(w.gmina or (obecny.gmina if obecny else "")).strip(),
            zrodlo=ZRODLO_RECZNY,
        )
    ile = zapisz(plik, wynik.values())
    log.info("Słownik obrębów %s: zapisano %d wpisów (%s)", workspace_id, ile, plik.name)
    return {"status": "ok", "plik": plik.name, "wpisow": ile}


@router.post("/{workspace_id}/obreby/zastosuj")
def zastosuj_slownik_obrebow(
    workspace_id: str,
    wykonaj: bool = True,
    _: str = Depends(require_admin),
    __: None = Depends(assert_workspace_idle),
) -> dict:
    """Przepisz oznaczenia ze słownika do kolumny `obreb` i odśwież `tx_cache`.

    Numer obrębu zostaje w `obreb_numer`, więc po zastosowaniu wyszukiwanie
    działa i po oznaczeniu („B-24"), i po numerze („0042").
    """
    workspace_id = resolve_workspace_id(workspace_id)
    _require_workspace(workspace_id)
    wdir = _workspace_dir(workspace_id)
    plik = znajdz_slownik(wdir)
    slownik = wczytaj(plik) if plik else {}
    if not slownik:
        raise HTTPException(
            status_code=400,
            detail="Workspace nie ma słownika obrębów (pliku *.obreby.csv) "
                   "albo jest on pusty. Wgraj słownik albo uzupełnij oznaczenia.",
        )
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        stat = zastosuj_do_bazy(conn, slownik, wykonaj=wykonaj)
    finally:
        conn.close()
    log.info("Słownik obrębów %s: zastosowano (wykonaj=%s) -> %s",
             workspace_id, wykonaj, stat)
    return {"status": "ok" if wykonaj else "dry-run", **stat}


@router.post("/{workspace_id}/obreby/plik")
async def upload_slownik_obrebow(
    workspace_id: str,
    file: UploadFile = File(...),
    _: str = Depends(require_admin),
) -> dict:
    """Wgraj gotowy słownik CSV (wygenerowany z EGIB narzędziem producenta)."""
    workspace_id = resolve_workspace_id(workspace_id)
    _require_workspace(workspace_id)
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Słownik obrębów musi być plikiem .csv")
    wdir = _workspace_dir(workspace_id)
    main = _resolve_main_sqlite(wdir)
    docelowy = sciezka_slownika(wdir, main.name if main is not None else "workspace.sqlite")

    tmp = wdir / f".obreby-upload-{uuid.uuid4().hex}.tmp"
    try:
        tresc = await file.read()
        await file.close()
        if len(tresc) > 8 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Słownik obrębów przekracza 8 MB")
        tmp.write_bytes(tresc)
        wpisy = wczytaj(tmp)
        if not wpisy:
            raise HTTPException(
                status_code=400,
                detail="Plik nie zawiera ani jednego poprawnego wiersza "
                       "(oczekiwane kolumny: teryt_gminy;numer_obrebu;oznaczenie;gmina;zrodlo)",
            )
        for stary in wdir.glob(f"*{SUFIKS_OBREBOW}"):
            stary.unlink()
        tmp.replace(docelowy)
    finally:
        tmp.unlink(missing_ok=True)
    return {"status": "ok", "plik": docelowy.name, "wpisow": len(wpisy)}
