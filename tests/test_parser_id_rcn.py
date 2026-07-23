"""Regresja: parser id_RCN z lokalnyId (a nie oznaczenieTransakcji).

Niektorzy operatorzy RCN (np. Poznan) wpisuja w `oznaczenieTransakcji` numery
pozycji w obrebie aktu (1, 2, 3, ...) zamiast unikalnego identyfikatora.
Skutkuje to masowymi kolizjami `id_rcn` w bazie i utrata danych.

Standard GUGiK: `IdRCN/RCN_IdentyfikatorIIP/lokalnyId` jest stabilnym UUID
per transakcja. Parser preferuje go z fallbackiem na `oznaczenieTransakcji`.
"""
from __future__ import annotations
from pathlib import Path

from rcn_core.parser import RcnGmlParser


GML_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<gml:FeatureCollection gml:id="LST_RCN.TEST"
  xmlns:rcn="urn:gugik:specyfikacje:gmlas:rejestrcennieruchomosci:1.0"
  xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:xlink="http://www.w3.org/1999/xlink">
<gml:boundedBy xsi:nil="true" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"/>
"""

GML_TAIL = "</gml:FeatureCollection>\n"


def _tx(local_id: str, oznaczenie: str, doc_id: str, cena: str = "100000.0") -> str:
    return f"""<gml:featureMember>
<rcn:RCN_Transakcja gml:id="TX_{local_id}">
 <rcn:IdRCN>
  <rcn:RCN_IdentyfikatorIIP>
   <rcn:przestrzenNazw>PL.PZGiK.TEST.RCN</rcn:przestrzenNazw>
   <rcn:lokalnyId>{local_id}</rcn:lokalnyId>
   <rcn:wersjaId>2026-01-01T00:00:00</rcn:wersjaId>
  </rcn:RCN_IdentyfikatorIIP>
 </rcn:IdRCN>
 <rcn:oznaczenieTransakcji>{oznaczenie}</rcn:oznaczenieTransakcji>
 <rcn:rodzajTransakcji>1</rcn:rodzajTransakcji>
 <rcn:rodzajRynku>2</rcn:rodzajRynku>
 <rcn:stronaSprzedajaca>3</rcn:stronaSprzedajaca>
 <rcn:stronaKupujaca>3</rcn:stronaKupujaca>
 <rcn:cenaTransakcjiBrutto>{cena}</rcn:cenaTransakcjiBrutto>
 <rcn:podstawaPrawna xlink:href="DOC_{doc_id}"/>
</rcn:RCN_Transakcja>
</gml:featureMember>
"""


def _doc(doc_id: str, oznaczenie: str, data: str) -> str:
    return f"""<gml:featureMember>
<rcn:RCN_Dokument gml:id="DOC_{doc_id}">
 <rcn:oznaczenieDokumentu>{oznaczenie}</rcn:oznaczenieDokumentu>
 <rcn:dataSporzadzeniaDokumentu>{data}</rcn:dataSporzadzeniaDokumentu>
 <rcn:tworcaDokumentu>Test Notariusz</rcn:tworcaDokumentu>
</rcn:RCN_Dokument>
</gml:featureMember>
"""


def _build_gml(transactions: list[tuple[str, str, str]]) -> str:
    """transactions: list of (lokalnyId, oznaczenieTransakcji, doc_id)."""
    parts = [GML_HEAD]
    seen_docs: set[str] = set()
    for _, _, doc_id in transactions:
        if doc_id not in seen_docs:
            parts.append(_doc(doc_id, f"AKT/{doc_id}", "2026-01-15"))
            seen_docs.add(doc_id)
    for local_id, oznaczenie, doc_id in transactions:
        parts.append(_tx(local_id, oznaczenie, doc_id))
    parts.append(GML_TAIL)
    return "".join(parts)


def test_id_rcn_uses_lokalnyid_when_oznaczenie_collides(tmp_path: Path):
    """Poznan-style: oznaczenieTransakcji=1,2,3 (kolidujace) ale lokalnyId=UUID.

    Parser musi zwrocic 3 unikalne id_RCN (z lokalnyId), bez kolizji."""
    txs = [
        ("00000113-0000-0000-0000-000000417939", "1", "DOC1"),
        ("00000113-0000-0000-0000-000000417940", "2", "DOC2"),
        ("00000113-0000-0000-0000-000000417941", "1", "DOC3"),  # kolizja na oznaczeniu!
    ]
    gml_path = tmp_path / "poznan_style.gml"
    gml_path.write_text(_build_gml(txs), encoding="utf-8")

    parsed = RcnGmlParser().parse(str(gml_path))
    ids = {r.get("id_RCN") for r in parsed.summary_rows}
    expected = {
        "00000113000000000000000000417939",
        "00000113000000000000000000417940",
        "00000113000000000000000000417941",
    }
    assert ids == expected, f"oczekiwano 3 unikalnych UUID-stripped id, dostalismy: {ids}"


def test_id_rcn_lodz_style_stays_unchanged(tmp_path: Path):
    """Lodz-style: oznaczenieTransakcji = lokalnyId bez myslnikow.

    Po fixie parser uzywa lokalnyId.replace('-',''), co dla Lodzi daje
    DOKLADNIE to samo id co aktualnie -- brak migracji potrzebnej."""
    txs = [
        ("85433F4B-8194-4692-8635-0891BEB31006", "85433F4B8194469286350891BEB31006", "DOC1"),
        ("825C4E8E-32D0-4B80-9B77-61FFB16ABDC6", "825C4E8E32D04B809B7761FFB16ABDC6", "DOC2"),
    ]
    gml_path = tmp_path / "lodz_style.gml"
    gml_path.write_text(_build_gml(txs), encoding="utf-8")

    parsed = RcnGmlParser().parse(str(gml_path))
    ids = sorted(r.get("id_RCN") for r in parsed.summary_rows)
    assert ids == [
        "825C4E8E32D04B809B7761FFB16ABDC6",
        "85433F4B8194469286350891BEB31006",
    ]


def test_id_rcn_fallback_to_oznaczenie_when_no_lokalnyid(tmp_path: Path):
    """Gdy lokalnyId brak (nietypowy plik), fallback na oznaczenieTransakcji."""
    gml = GML_HEAD + _doc("DOC1", "AKT/1", "2026-01-15") + """<gml:featureMember>
<rcn:RCN_Transakcja gml:id="TX_FALLBACK">
 <rcn:oznaczenieTransakcji>FALLBACK-ID-123</rcn:oznaczenieTransakcji>
 <rcn:rodzajTransakcji>1</rcn:rodzajTransakcji>
 <rcn:rodzajRynku>2</rcn:rodzajRynku>
 <rcn:stronaSprzedajaca>3</rcn:stronaSprzedajaca>
 <rcn:stronaKupujaca>3</rcn:stronaKupujaca>
 <rcn:cenaTransakcjiBrutto>50000.0</rcn:cenaTransakcjiBrutto>
 <rcn:podstawaPrawna xlink:href="DOC_DOC1"/>
</rcn:RCN_Transakcja>
</gml:featureMember>
""" + GML_TAIL

    gml_path = tmp_path / "no_lokalnyid.gml"
    gml_path.write_text(gml, encoding="utf-8")

    parsed = RcnGmlParser().parse(str(gml_path))
    ids = [r.get("id_RCN") for r in parsed.summary_rows]
    assert ids == ["FALLBACK-ID-123"]
