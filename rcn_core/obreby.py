"""Obreby (geodetic precinct) name dictionary built from local EGIB GPKG files.

Each EGIB feature has both a numeric obreb code (part of `ID_DZIALKI`) and a
human-readable `NAZWA_OBREBU` (e.g. `B-42`, `G-12`, `S-6` for Łódź — the prefix
identifies the historic city district). We build a dict that maps
`{teryt_gminy}.{obreb_num_4digit}` → `NAZWA_OBREBU` so the rcn-workshop can
display meaningful labels instead of raw numeric codes.

The dict is cached globally after the first build. Call `set_layers_dir()` at
application startup to point at the `data/layers/` directory; call it again
(or with `None`) to invalidate the cache.

Pyogrio (NIE Fiona) -- patrz `rcn_core/enrich.py` dla powodu (libproj segfault).
Falls back gracefully when pyogrio isn't installed or no EGIB data is present —
ingest then stores the raw 4-digit obreb number.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from threading import Lock
from typing import Optional

log = logging.getLogger(__name__)

_LAYERS_DIR: Optional[Path] = None
_CACHE: Optional[dict[str, str]] = None
_LOCK = Lock()

# Warianty nazwy pola z pełnym identyfikatorem działki. `ID_DZIALKI_`
# (z podkreśleniem na końcu) NIE jest literówką: tak nazywa je część plików
# z pobieraczki EGIB, gdy GDAL skraca zbyt długą nazwę kolumny. Powiat
# międzychodzki (2026-09-09) miał wyłącznie `id_dzialki` i `ID_DZIALKI_`,
# przez co wzbogacanie dopasowało 0 działek z 6 687 możliwych.
_IDENT_KEYS = ("ID_DZIALKI", "id_dzialki", "ID_DZIALKI_", "IDDZIALKI",
               "identyfikator", "IDENTYFIKATOR")
_NAZWA_KEYS = ("NAZWA_OBREBU", "nazwa_obrebu", "NAZWAOBREBU", "nazwa_obr", "OBREB_NAZW")
_IDENT_RE = re.compile(r"^([0-9]+_[0-9]+)\.([^.]+)\.")


def set_layers_dir(path: Optional[Path]) -> None:
    global _LAYERS_DIR, _CACHE
    with _LOCK:
        _LAYERS_DIR = path
        _CACHE = None


def _load() -> dict[str, str]:
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        _CACHE = _build()
        return _CACHE


def _build() -> dict[str, str]:
    if _LAYERS_DIR is None or not _LAYERS_DIR.exists():
        return {}
    try:
        import pyogrio
        from pyogrio.raw import read as _pyogrio_read_raw
    except ImportError:
        log.debug("pyogrio not available — obreby dict empty")
        return {}

    result: dict[str, str] = {}
    for sub in sorted(_LAYERS_DIR.iterdir()):
        if not sub.is_dir():
            continue
        gpkg = sub / "dzialki.gpkg"
        if not gpkg.exists():
            continue
        try:
            info = pyogrio.read_info(str(gpkg))
            available = list(info.get("fields", ()))
            ident_field = _first_present(available, _IDENT_KEYS)
            nazwa_field = _first_present(available, _NAZWA_KEYS)
            if not ident_field or not nazwa_field:
                log.debug("obreby skip %s — missing ident/nazwa columns", gpkg)
                continue
            _meta, _fids, _geoms, field_data = _pyogrio_read_raw(
                str(gpkg),
                columns=[ident_field, nazwa_field],
                read_geometry=False,
            )
            idents, nazwy = field_data[0], field_data[1]
            for ident, nazwa in zip(idents, nazwy):
                if ident is None or nazwa is None or ident == "" or nazwa == "":
                    continue
                m = _IDENT_RE.match(str(ident))
                if not m:
                    continue
                key = f"{m.group(1)}.{m.group(2)}"
                if key not in result:
                    result[key] = str(nazwa).strip()
        except Exception as exc:
            log.warning("obreby build: failed to read %s: %s", gpkg, exc)
            continue
    log.info("obreby dict built: %d entries from %s", len(result), _LAYERS_DIR)
    return result


def _first_present(available: list[str], keys: tuple[str, ...]) -> Optional[str]:
    """Zwróć pierwszy z `keys` który występuje w liście `available`."""
    for k in keys:
        if k in available:
            return k
    return None


def name_for(teryt_gminy: Optional[str], obreb_num: Optional[str]) -> Optional[str]:
    """Return the human-readable obreb name (`G-42`) or None if unknown."""
    if not teryt_gminy or not obreb_num:
        return None
    return _load().get(f"{teryt_gminy}.{obreb_num}")


def looks_like_numeric(value: Optional[str]) -> bool:
    """True when value is a bare numeric obreb code (4 digits or similar)."""
    if not value:
        return False
    return bool(re.fullmatch(r"\d{1,6}", value))
