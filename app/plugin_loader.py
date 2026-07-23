"""Loader wtyczek -- pojedyncze pliki `.py` w `data/plugins/`, ładowane przez
`exec` do świeżego modułu BEZ wpisu do `sys.modules`:
- zero kolizji nazw między wtyczkami i z kodem aplikacji,
- reload = ponowny exec (bez mechaniki importlib.reload),
- brak `__pycache__` -> czyste delete pliku na Windows (bez file-locka).

Kontrakt wtyczki:
    PLUGIN = {"id": "moja_wtyczka", "name": "...", "version": "1.0",
              "author": "...", "kind": "analysis", "description": "..."}
    def run(rows, ctx): ...  # ctx opcjonalny (sygnatura 1-argumentowa też OK)

Wtyczka z błędem (składnia, wyjątek na top-level, zła metadana) NIE wywala
serwera -- ląduje w rejestrze jako rekord z `error` (widoczny w UI).
"""
from __future__ import annotations

import inspect
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from app.config import settings

log = logging.getLogger(__name__)

PLUGIN_ID_RE = re.compile(r"[a-z0-9_-]{1,40}")  # używane z fullmatch()
PARAM_NAME_RE = re.compile(r"[a-z0-9_]{1,30}")
VALID_KINDS = ("analysis", "export")
VALID_PARAM_TYPES = ("number", "date", "text", "select")
DISABLED_SUFFIX = ".py.disabled"

_registry: dict[str, PluginRecord] = {}
_lock = threading.Lock()
_scanned = False


@dataclass
class PluginRecord:
    plugin_id: str
    name: str = ""
    version: str = ""
    author: str = ""
    kind: str = ""
    description: str = ""
    path: Path | None = None
    enabled: bool = True
    error: str | None = None
    run: Callable | None = field(default=None, repr=False)
    accepts_ctx: bool = False
    params: list = field(default_factory=list)

    def to_api(self) -> dict:
        """Metadane dla GET /api/plugins -- bez callable `run`."""
        return {
            "id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "kind": self.kind,
            "description": self.description,
            "filename": self.path.name if self.path else None,
            "enabled": self.enabled,
            "error": self.error,
            "params": self.params,
        }


def _parse_params_spec(raw) -> tuple[list, str | None]:
    """Walidacja opcjonalnej deklaracji PLUGIN['params'] -- lista pól formularza
    renderowanego w UI przed uruchomieniem. Pole: {"name": "alpha",
    "label"?: str, "type"?: "number"|"date"|"text", "default"?: skalar,
    "min"?/"max"?/"step"?: liczby, "help"?: str}. Zwraca (lista, błąd|None)."""
    if raw is None:
        return [], None
    if not isinstance(raw, (list, tuple)):
        return [], "PLUGIN['params'] musi być listą pól"
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return [], f"PLUGIN['params'][{i}] musi być słownikiem"
        name = item.get("name")
        if not isinstance(name, str) or not PARAM_NAME_RE.fullmatch(name):
            return [], f"PLUGIN['params'][{i}]['name'] musi pasować do [a-z0-9_]{{1,30}}"
        ptype = item.get("type", "text")
        if ptype not in VALID_PARAM_TYPES:
            return [], (f"PLUGIN['params'][{i}]['type'] musi być jednym z "
                        f"{VALID_PARAM_TYPES}")
        default = item.get("default")
        if default is not None and not isinstance(default, (str, int, float, bool)):
            return [], f"PLUGIN['params'][{i}]['default'] musi być skalarem"
        spec = {"name": name, "label": str(item.get("label", name)), "type": ptype,
                "default": default}
        if ptype == "select":
            raw_opts = item.get("options")
            if not isinstance(raw_opts, (list, tuple)) or not raw_opts:
                return [], f"PLUGIN['params'][{i}]: type=select wymaga niepustej listy 'options'"
            options = []
            for opt in raw_opts:
                if isinstance(opt, dict) and isinstance(opt.get("value"), str):
                    options.append({"value": opt["value"],
                                    "label": str(opt.get("label", opt["value"]))})
                elif isinstance(opt, str):
                    options.append({"value": opt, "label": opt})
                else:
                    return [], (f"PLUGIN['params'][{i}]: opcja select musi być stringiem "
                                "albo {'value': str, 'label'?: str}")
            spec["options"] = options
        for key in ("min", "max", "step"):
            if isinstance(item.get(key), (int, float)):
                spec[key] = item[key]
        if item.get("help"):
            spec["help"] = str(item["help"])
        out.append(spec)
    return out, None


def _load_plugin_file(path: Path) -> PluginRecord:
    """Wykonaj plik wtyczki i zwaliduj kontrakt. Każdy błąd -> rekord z `error`
    (plugin_id z metadanych jeśli się dało, inaczej ze stemu pliku)."""
    fallback_id = path.name.removesuffix(DISABLED_SUFFIX).removesuffix(".py")
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        return PluginRecord(plugin_id=fallback_id, path=path, error=f"Nie można odczytać pliku: {exc}")

    module = ModuleType(f"rcn_plugin_{fallback_id}")
    module.__dict__["__file__"] = str(path)
    try:
        exec(compile(src, str(path), "exec"), module.__dict__)
    except BaseException as exc:  # wtyczka NIE może położyć serwera
        log.warning("Wtyczka %s: błąd wykonania: %s", path.name, exc)
        return PluginRecord(plugin_id=fallback_id, path=path,
                            error=f"Błąd wykonania: {type(exc).__name__}: {exc}")

    meta = module.__dict__.get("PLUGIN")
    if not isinstance(meta, dict):
        return PluginRecord(plugin_id=fallback_id, path=path, error="Brak słownika PLUGIN")

    plugin_id = meta.get("id")
    if not isinstance(plugin_id, str) or not PLUGIN_ID_RE.fullmatch(plugin_id):
        return PluginRecord(plugin_id=fallback_id, path=path,
                            error="PLUGIN['id'] musi pasować do ^[a-z0-9_-]{1,40}$")

    kind = meta.get("kind")
    if kind not in VALID_KINDS:
        return PluginRecord(plugin_id=plugin_id, path=path,
                            error=f"PLUGIN['kind'] musi być jednym z {VALID_KINDS}")

    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        return PluginRecord(plugin_id=plugin_id, path=path, error="PLUGIN['name'] wymagane")

    run = module.__dict__.get("run")
    if not callable(run):
        return PluginRecord(plugin_id=plugin_id, path=path,
                            error="Brak funkcji run(rows, ctx)")

    try:
        accepts_ctx = len(inspect.signature(run).parameters) >= 2
    except (TypeError, ValueError):
        accepts_ctx = False

    params, params_error = _parse_params_spec(meta.get("params"))
    if params_error:
        return PluginRecord(plugin_id=plugin_id, path=path, error=params_error)

    return PluginRecord(
        plugin_id=plugin_id,
        name=name.strip(),
        version=str(meta.get("version", "")),
        author=str(meta.get("author", "")),
        kind=kind,
        description=str(meta.get("description", "")),
        path=path,
        enabled=True,
        run=run,
        accepts_ctx=accepts_ctx,
        params=params,
    )


def scan_plugins() -> dict[str, PluginRecord]:
    """Pełny rebuild rejestru z `plugins_dir`: `*.py` wykonywane, `*.py.disabled`
    tylko listowane (BEZ wykonywania). Kolizja id: pierwszy alfabetycznie wygrywa,
    drugi widoczny z błędem."""
    global _scanned
    pdir = settings.plugins_dir
    records: dict[str, PluginRecord] = {}
    if pdir.is_dir():
        for path in sorted(pdir.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.name.endswith(DISABLED_SUFFIX):
                rec = PluginRecord(
                    plugin_id=path.name.removesuffix(DISABLED_SUFFIX),
                    name=path.name.removesuffix(DISABLED_SUFFIX),
                    path=path, enabled=False,
                )
            elif path.suffix == ".py":
                rec = _load_plugin_file(path)
            else:
                continue
            if rec.plugin_id in records:
                rec.error = f"Duplikat id '{rec.plugin_id}' (wygrywa {records[rec.plugin_id].path.name})"
                rec.run = None
                records[f"{rec.plugin_id}#{path.name}"] = rec
            else:
                records[rec.plugin_id] = rec
    with _lock:
        _registry.clear()
        _registry.update(records)
        _scanned = True
    return records


def get_registry() -> dict[str, PluginRecord]:
    """Rejestr wtyczek; pierwszy dostęp robi lazy scan (start serwera bez kosztu)."""
    with _lock:
        if _scanned:
            return dict(_registry)
    return scan_plugins()


def get_plugin(plugin_id: str) -> PluginRecord | None:
    return get_registry().get(plugin_id)
