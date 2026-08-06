"""GPKG z urzędową kolejnością osi (northing, easting) musi rysować się poprawnie.

Zgłoszenie 2026-08-06: `dzialki.gpkg` powiatu poznańskiego deklaruje EPSG:2180,
ale zapisuje osie odwrotnie. Czytnik warstw (kolejność tradycyjna GIS) pokazywał
działki ~200 km na wschód, pod Łodzią, a filtr bbox nad właściwym terenem zwracał
zero obiektów. Przesunięty punkt WCIĄŻ wypada w Polsce, więc `detect_gpkg_epsg`
(sprawdza tylko granice kraju) tego nie wyłapuje -- rozstrzyga dopiero zgodność
z zasięgiem danych workspace'u.
"""
import numpy as np
import pytest

pytest.importorskip("pyogrio")
pytest.importorskip("shapely")

from pyogrio.raw import write as raw_write  # noqa: E402
from shapely import wkb as shapely_wkb  # noqa: E402
from shapely.geometry import box  # noqa: E402

from rcn_core.geo import gpkg_axis_swap  # noqa: E402

# Działka pod Komornikami (powiat poznański) w EPSG:2180: easting 383418,
# northing 514371. Odczytana odwrotnie ląduje pod Łodzią (~19.2E/51.3N).
EASTING, NORTHING = 383418.0, 514371.0
REF_BBOX_POZNAN = (16.46, 52.15, 17.38, 52.67)


def _zapisz_gpkg(path, coords_xy):
    """GPKG z jednym kwadratem 100 m, współrzędne podane wprost (bez zamiany)."""
    x, y = coords_xy
    geom = np.array([shapely_wkb.dumps(box(x, y, x + 100, y + 100))], dtype=object)
    raw_write(
        path,
        geometry=geom,
        field_data=[np.array(["302107_2.0003.1057/21"], dtype=object)],
        fields=["ID_DZIALKI"],
        driver="GPKG",
        geometry_type="Polygon",
        crs="EPSG:2180",
        layer="dzialki",
    )
    return path


def test_wykrywa_odwrocone_osie(tmp_path):
    plik = _zapisz_gpkg(str(tmp_path / "odwrocone.gpkg"), (NORTHING, EASTING))
    assert gpkg_axis_swap(plik, 2180, REF_BBOX_POZNAN) is True


def test_nie_rusza_poprawnego_pliku(tmp_path):
    plik = _zapisz_gpkg(str(tmp_path / "poprawny.gpkg"), (EASTING, NORTHING))
    assert gpkg_axis_swap(plik, 2180, REF_BBOX_POZNAN) is False


def test_bez_odniesienia_ufa_plikowi(tmp_path):
    plik = _zapisz_gpkg(str(tmp_path / "odwrocone.gpkg"), (NORTHING, EASTING))
    assert gpkg_axis_swap(plik, 2180, None) is False


def test_czytnik_zwraca_geometrie_nad_wlasciwym_terenem(tmp_path, tmp_data_dir):
    """Pełny tor: bbox nad powiatem poznańskim ma zwrócić działkę z odwróconego pliku."""
    from app.layers import _read_gpkg_features_pyogrio

    plik = _zapisz_gpkg(str(tmp_path / "odwrocone.gpkg"), (NORTHING, EASTING))
    bbox_poznan = (17.20, 52.44, 17.36, 52.52)

    # Bez odniesienia -- zachowanie jak dotąd: nad Poznaniem pusto.
    features, _capped, _epsg = _read_gpkg_features_pyogrio(plik, bbox_poznan, 100)
    assert features == []

    # Z odniesieniem -- działka znajduje się i ląduje w powiecie poznańskim.
    features, _capped, _epsg = _read_gpkg_features_pyogrio(
        plik, bbox_poznan, 100, ref_bbox=REF_BBOX_POZNAN
    )
    assert len(features) == 1
    lon, lat = features[0]["geometry"]["coordinates"][0][0]
    assert 17.2 < lon < 17.4, f"lon={lon} poza powiatem poznańskim"
    assert 52.4 < lat < 52.6, f"lat={lat} poza powiatem poznańskim"


def test_poprawny_plik_czyta_sie_tak_samo_z_odniesieniem_i_bez(tmp_path, tmp_data_dir):
    from app.layers import _read_gpkg_features_pyogrio

    plik = _zapisz_gpkg(str(tmp_path / "poprawny.gpkg"), (EASTING, NORTHING))
    bbox_poznan = (17.20, 52.44, 17.36, 52.52)

    bez = _read_gpkg_features_pyogrio(plik, bbox_poznan, 100)[0]
    z_ref = _read_gpkg_features_pyogrio(plik, bbox_poznan, 100, ref_bbox=REF_BBOX_POZNAN)[0]
    assert len(bez) == 1 and len(z_ref) == 1
    assert bez[0]["geometry"] == z_ref[0]["geometry"]
