"""Mapa w oknie transakcji ma POI i miarkę -- tak jak mapa główna.

Zgłoszenie 2026-09-17: w widoku tabeli jedyną mapą jest okno „Pokaż na mapie",
a odległość do szkoły czy przystanku liczy się właśnie przy pojedynczej
transakcji. Wcześniej miarka i warstwa POI były wyłącznie na mapie głównej.

Kluczowa własność, której pilnuje ten plik: mechanika pomiaru jest JEDNA,
sparametryzowana zakresem ('main' | 'modal'). Druga kopia rozjechałaby się
przy pierwszej poprawce -- a pomiar odległości idzie potem do operatu.
"""
import re
from pathlib import Path

import pytest

KORZEN = Path(__file__).resolve().parent.parent
SKRYPT = KORZEN / "static" / "js" / "workspace.js"
SZABLON = KORZEN / "templates" / "workspace.html"
CSS = KORZEN / "static" / "css" / "app.css"


@pytest.fixture(scope="module")
def skrypt() -> str:
    return SKRYPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def szablon() -> str:
    return SZABLON.read_text(encoding="utf-8")


def test_miarka_ma_jedna_implementacje(skrypt):
    """Jedno `_measureClick`, jedno liczenie odległości, jedna etykieta."""
    assert skrypt.count("_measureClick(latlng, label, fromLayer = false, scope = 'main')") == 1
    assert skrypt.count("map.distance(a.latlng, latlng)") == 1, (
        "pomiar odległości liczony w dwóch miejscach = dwie wersje prawdy"
    )


def test_miarka_zna_oba_zakresy(skrypt):
    assert "_measureState(scope)" in skrypt
    assert "scope === 'modal' ? this.mapModal.measure : this.measure" in skrypt
    assert "scope === 'modal' ? this._modalMap : this.map" in skrypt
    # Pane markerów jest inny na każdej z map -- podanie nieistniejącego wywala Leaflet.
    assert "scope === 'modal' ? 'wb-modal-tx-pane' : 'txMarkers'" in skrypt


def test_modal_podpina_klikniecia_miarki(skrypt):
    assert "this._measureClick(e.latlng, null, false, 'modal')" in skrypt, "klik w tło mapy"
    assert "this._measureClick(e.layer.getLatLng(), null, true, 'modal')" in skrypt, "klik w transakcję"
    assert "true, 'modal');" in skrypt, "klik w POI z nazwą punktu"


def test_zamkniecie_modalu_zeruje_stan_miarki(skrypt):
    """Warstwa pomiarów ginie z mapą; zostawiony `active` dawałby martwy tryb."""
    poczatek = skrypt.index("wbCloseMapModal()")
    cialo = skrypt[poczatek:poczatek + 1400]
    assert "this.mapModal.measure = {" in cialo
    assert "active: false" in cialo


def test_poi_w_modalu_pyta_o_wycinek(skrypt):
    """bbox, nie cały powiat -- okno pokazuje kilkaset metrów."""
    assert "_modalFetchPoi()" in skrypt
    assert re.search(r"poi\.geojson\?bbox=\$\{encodeURIComponent\(bbox\)\}", skrypt)
    # Odświeżanie przy przesuwaniu mapy -- inaczej POI zostają z pierwszego widoku.
    poczatek = skrypt.index("_scheduleModalFetch()")
    assert "this._modalFetchPoi();" in skrypt[poczatek:poczatek + 400]


def test_poi_w_modalu_niesie_atrybucje_odbl(skrypt, szablon):
    assert "this.mapModal.poiAttribution = data.attribution" in skrypt
    assert "mapModal.poiAttribution" in szablon
    assert "ODbL" in szablon


def test_przelacznik_poi_tylko_gdy_workspace_ma_plik(skrypt, szablon):
    assert "this.mapModal.hasPoi = !!(this.overlays && this.overlays.poi);" in skrypt
    assert 'x-if="mapModal.hasPoi"' in szablon
    assert "toggleModalLayer('poi')" in szablon
    # Domyślnie wyłączone, jak na mapie głównej.
    assert "if (this.mapModal.hasPoi) flags.poi = false;" in skrypt


def test_ui_miarki_w_modalu(szablon):
    assert "wbToggleMeasure('modal')" in szablon
    assert "wbClearMeasure('modal')" in szablon
    assert "mapModal.measure.pending" in szablon, "podpowiedź „kliknij drugi punkt”"


def test_mapa_glowna_uzywa_tego_samego_stanu(szablon, skrypt):
    """Po refaktorze płaskie `measureActive` nie może nigdzie zostać."""
    for stare in ("measureActive", "measurePending", "measureHasResult", "measureLayer"):
        assert stare not in szablon, f"szablon mapy głównej wciąż używa {stare}"
        assert stare not in skrypt, f"skrypt wciąż używa {stare}"
    assert 'x-show="measure.active"' in szablon


def test_kursor_krzyzyka_takze_w_modalu():
    css = CSS.read_text(encoding="utf-8")
    assert "#wb-modal-map.wb-measuring { cursor: crosshair; }" in css
