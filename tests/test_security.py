"""Security regression tests.

XXE: GML z external entity (`<!ENTITY xxe SYSTEM "file:///...">`) NIE może
spowodować odczytu plików serwera przez parser. Test sprawdza że treść
sekretu (zapisanego w tmp file) NIE pojawia się w sparsowanych atrybutach.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest


SECRET_TOKEN = "ULTRA_SECRET_XXE_CANARY_TOKEN_SHOULD_NOT_LEAK_2026"


def _build_xxe_gml(secret_path: Path) -> str:
    """GML z DOCTYPE entity wskazującym na lokalny plik. Entity referenced
    w polu `<rcn:adres>` -- jeśli parser go rozwinie, wartość będzie w
    parsed `adres`."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file://{secret_path.as_posix()}">]>
<rcn:RCN_Adres xmlns:rcn="urn:rcn"
               xmlns:gml="http://www.opengis.net/gml/3.2"
               gml:id="ADR_xxe_test">
    <rcn:miejscowosc>Test</rcn:miejscowosc>
    <rcn:ulica>&xxe;</rcn:ulica>
    <rcn:numerPorzadkowy>1</rcn:numerPorzadkowy>
</rcn:RCN_Adres>
"""


def test_xxe_external_entity_blocked(tmp_path):
    """Parser MUSI nie rozwinąć external entity wskazującego na lokalny plik."""
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text(SECRET_TOKEN)

    gml_path = tmp_path / "xxe.gml"
    gml_path.write_text(_build_xxe_gml(secret_file))

    from rcn_core.parser import RcnGmlParser

    parser = RcnGmlParser()
    try:
        parsed = parser.parse(gml_path)
    except Exception:
        # Parse może rzucić bo plik nie ma pełnej struktury RCN. Wystarczy
        # że NIE zawiera secret tokenu.
        parsed = None

    # Konwertuj wszystko na string i sprawdź czy secret token NIE wycieka.
    haystack = ""
    if parsed is not None:
        for collection in (
            getattr(parsed, "summary_rows", []),
            getattr(parsed, "plot_rows", []),
            getattr(parsed, "building_rows", []),
            getattr(parsed, "local_rows", []),
        ):
            haystack += json.dumps(collection, default=str, ensure_ascii=False)
        haystack += json.dumps(getattr(parsed, "diagnostics", {}), default=str, ensure_ascii=False)

    assert SECRET_TOKEN not in haystack, (
        "XXE REGRESSION: parser rozwinął external entity z file:// i wpisał "
        "treść sekretu do parsed atrybutów. Sprawdź `iterparse(..., resolve_entities=False, "
        "no_network=True, load_dtd=False)` w rcn_core/parser.py."
    )
