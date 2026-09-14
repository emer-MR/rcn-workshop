"""Warianty nazwy kolumny z identyfikatorem działki w plikach EGIB.

Zgłoszenie 2026-09-09 (powiat międzychodzki): `dzialki.gpkg` z pobieraczki EGIB
miał kolumny `id_dzialki` i `ID_DZIALKI_` (z podkreśleniem na końcu - GDAL
skraca zbyt długie nazwy), a wzbogacanie szukało dokładnie `ID_DZIALKI`.
Skutek był CICHY i mylący: budynki dopasowały się normalnie (`ID_BUDYNKU`
zgadza się co do znaku), więc przebieg wyglądał na częściowy sukces, a nie na
brak dopasowania - podczas gdy z 6 687 możliwych działek weszło ZERO.
"""
import numpy as np
import pytest

pytest.importorskip("pyogrio")
pytest.importorskip("shapely")

from pyogrio.raw import write as raw_write  # noqa: E402
from shapely import wkb as shapely_wkb  # noqa: E402
from shapely.geometry import box  # noqa: E402

IDENT = "301401_2.0306.439/3"
# Działka w powiecie międzychodzkim, EPSG:2180.
EASTING, NORTHING = 288000.0, 512000.0

WARIANTY_DZIALEK = ["ID_DZIALKI", "id_dzialki", "ID_DZIALKI_"]
WARIANTY_BUDYNKOW = ["ID_BUDYNKU", "id_budynku", "ID_BUDYNKU_"]


def _gpkg(path, pole: str, wartosc: str = IDENT, layer: str = "dzialki") -> str:
    geom = np.array([shapely_wkb.dumps(box(EASTING, NORTHING, EASTING + 100, NORTHING + 100))],
                    dtype=object)
    raw_write(
        str(path),
        geometry=geom,
        field_data=[np.array([wartosc], dtype=object)],
        fields=[pole],
        driver="GPKG",
        geometry_type="Polygon",
        crs="EPSG:2180",
        layer=layer,
    )
    return str(path)


@pytest.mark.parametrize("pole", WARIANTY_DZIALEK)
def test_wykrywa_pole_identyfikatora_dzialki(tmp_path, pole):
    cli = pytest.importorskip("rcn_producer.enrich")
    plik = _gpkg(tmp_path / f"dz_{pole}.gpkg", pole)
    from pathlib import Path
    assert cli._detect_plot_ident_field(Path(plik)) == pole


@pytest.mark.parametrize("pole", WARIANTY_DZIALEK)
def test_klasyfikacja_warstwy_dzialek(tmp_path, pole):
    """Warstwa musi zostać rozpoznana jako działki po zawartości, nie po nazwie
    pliku - stąd neutralna nazwa `warstwa.gpkg`."""
    from app.gpkg_discovery import _classify
    from pathlib import Path
    plik = Path(_gpkg(tmp_path / f"warstwa_{pole}.gpkg", pole))
    kind, _label = _classify(plik)
    assert kind == "dzialki"


@pytest.mark.parametrize("pole", WARIANTY_BUDYNKOW)
def test_klasyfikacja_warstwy_budynkow(tmp_path, pole):
    from app.gpkg_discovery import _classify
    from pathlib import Path
    plik = Path(_gpkg(tmp_path / f"bud_{pole}.gpkg", pole, "301401_2.0306.439_BUD", "budynki"))
    kind, _label = _classify(plik)
    assert kind == "budynki"


@pytest.mark.parametrize("pole", WARIANTY_DZIALEK)
def test_producent_klasyfikuje_tak_samo_jak_aplikacja(tmp_path, pole):
    """`rcn_producer.cli._classify_gpkg` to lustro `app.gpkg_discovery._classify`
    (producent nie importuje `app.*`). Rozjazd oznaczałby, że paczka pakuje
    warstwę, której konsument nie rozpozna."""
    cli = pytest.importorskip("rcn_producer.cli")
    from pathlib import Path
    plik = Path(_gpkg(tmp_path / f"prod_{pole}.gpkg", pole))
    assert cli._classify_gpkg(plik) == "dzialki"


def test_enrich_dopasowuje_dzialke_mimo_wariantu_nazwy(tmp_path):
    """Pełny tor: baza z identyfikatorem działki + GPKG z kolumną `ID_DZIALKI_`
    ma dać dopasowanie, a nie ciche zero."""
    enrich = pytest.importorskip("rcn_producer.enrich")
    from pathlib import Path

    from rcn_core.ingest import open_workspace

    db = tmp_path / "Miedzychod.sqlite"
    conn = open_workspace(db)
    conn.execute("INSERT INTO imports(original_filename, stored_filename, tryb, upload_timestamp, status) "
                 "VALUES('t.gml','t.gml','snapshot',0,'completed')")
    imp = conn.execute("SELECT id FROM imports").fetchone()[0]
    conn.execute("INSERT INTO transakcje(id_rcn, source_import_id, import_timestamp, status) "
                 "VALUES('RCN-1', ?, 0, 'aktywna')", (imp,))
    conn.execute("INSERT INTO plots(id_rcn, source_import_id, identyfikator_dzialki) VALUES('RCN-1', ?, ?)",
                 (imp, IDENT))
    conn.commit()

    plik = Path(_gpkg(tmp_path / "dzialki.gpkg", "ID_DZIALKI_"))
    staty = enrich.enrich_workspace(conn, plik, None, source_import_id=None)
    assert staty["plots_matched"] == 1, staty
    lon, lat, src = conn.execute(
        "SELECT centroid_lon, centroid_lat, geom_source FROM plots WHERE id_rcn='RCN-1'").fetchone()
    assert src == "egib" and lon is not None and 14.0 < lon < 24.5 and 49.0 < lat < 55.0
    conn.close()
