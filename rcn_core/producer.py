"""Wykrywanie trybu PRODUCENT vs KONSUMENT (Model A).

Producent = ma zainstalowany/wbudowany pakiet `rcn_producer` (wzbogacanie:
geometria EGIB, dziedziczenie, docelowo adresy/POI). Konsument = nie ma go
(np. dystrybuowany `.exe`, gdzie PyInstaller wyklucza `rcn_producer`).

Decyzja świadoma: gating przez **obecność kodu**, nie env-flagę -- konsument nie
może włączyć produkcji, bo pakietu fizycznie nie ma.

Co zostaje u konsumenta niezależnie od tego: import GML, mapa, filtry, eksport,
notatki, flagi jakości (`compute-flags` -- liczone z GML), odczyt wzbogaconych
danych z gotowej bazy.
"""
from __future__ import annotations

import importlib.util


def has_producer() -> bool:
    """True jeśli pakiet wzbogacania `rcn_producer` jest dostępny."""
    return importlib.util.find_spec("rcn_producer") is not None


HAS_PRODUCER: bool = has_producer()
