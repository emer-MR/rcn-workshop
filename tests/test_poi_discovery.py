"""Testy wykluczenia `*.poi.sqlite` z discovery bazy głównej.

Plik POI (`<nazwa>.poi.sqlite`) leży w korzeniu folderu workspace'a obok bazy
głównej (model folder=komplet). KRYTYCZNE: `Kutno.poi.sqlite` sortuje się
alfabetycznie PRZED `Kutno.sqlite` -- bez wykluczenia zostałby wzięty za bazę.
"""
from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path

from tests.test_import_workspace import _make_workspace_sqlite


def _make_poi_stub(path: Path) -> None:
    """Minimalny plik `*.poi.sqlite` (pusta tabela poi) -- NIE schemat workspace."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE poi (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, "
                 "lon REAL, lat REAL, x REAL, y REAL, osm_id INTEGER, source TEXT)")
    conn.commit()
    conn.close()


def test_resolve_main_sqlite_skips_poi(client, tmp_data_dir):
    from app.workspaces import _poi_sqlite, _resolve_main_sqlite
    wdir = tmp_data_dir / "workspaces" / "Kutno"
    wdir.mkdir(parents=True)
    _make_workspace_sqlite(wdir / "Kutno.sqlite")
    _make_poi_stub(wdir / "Kutno.poi.sqlite")

    main = _resolve_main_sqlite(wdir)
    assert main is not None and main.name == "Kutno.sqlite"
    poi = _poi_sqlite("Kutno")
    assert poi is not None and poi.name == "Kutno.poi.sqlite"


def test_poi_sqlite_none_when_absent(client, tmp_data_dir):
    from app.workspaces import _poi_sqlite
    wdir = tmp_data_dir / "workspaces" / "Sieradz"
    wdir.mkdir(parents=True)
    _make_workspace_sqlite(wdir / "Sieradz.sqlite")
    assert _poi_sqlite("Sieradz") is None


def test_producer_find_main_sqlite_skips_poi(tmp_path):
    # Pakiet producenta nieobecny w drzewie publicznym (Model A) -> skip.
    import pytest
    cli = pytest.importorskip("rcn_producer.cli")
    _find_main_sqlite = cli._find_main_sqlite
    _make_workspace_sqlite(tmp_path / "Kutno.sqlite")
    _make_poi_stub(tmp_path / "Kutno.poi.sqlite")
    main = _find_main_sqlite(tmp_path)
    assert main is not None and main.name == "Kutno.sqlite"


def test_import_zip_with_poi_file(client, auth, tmp_path):
    """Paczka `rcn pack` zawiera plik POI -- import ma przejść, a baza główna
    ma być poprawnie wykryta (nie plik POI)."""
    db = tmp_path / "src.sqlite"
    _make_workspace_sqlite(db)
    poi = tmp_path / "src.poi.sqlite"
    _make_poi_stub(poi)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(db, "Kutno/Kutno.sqlite")
        zf.write(poi, "Kutno/Kutno.poi.sqlite")

    r = client.post(
        "/api/workspaces/import", auth=auth,
        files={"file": ("Kutno.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 201, r.text
    # transaction_count=0 z PRAWDZIWEJ bazy -- gdyby discovery wzięło plik POI,
    # _workspace_info wywaliłby się na braku tabel schematu workspace.
    assert r.json()["id"] == "Kutno"
    assert r.json()["transaction_count"] == 0


def _make_poi_with_data(path: Path) -> None:
    _make_poi_stub(path)
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO poi(kind, name, lon, lat, x, y) VALUES(?,?,?,?,?,?)",
        [("szkola", "SP nr 1", 19.46, 51.76, 0.0, 0.0),
         ("apteka", None, 19.45, 51.75, 0.0, 0.0)])
    conn.execute("CREATE TABLE poi_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO poi_meta VALUES('attribution', '(c) autorzy OpenStreetMap, ODbL')")
    conn.commit()
    conn.close()


def test_poi_geojson_layer_endpoint(client, auth, tmp_data_dir):
    """Warstwa POI dla mapy: GeoJSON z kind/name + atrybucja ODbL."""
    wdir = tmp_data_dir / "workspaces" / "Lodz"
    wdir.mkdir(parents=True)
    _make_workspace_sqlite(wdir / "Lodz.sqlite")
    _make_poi_with_data(wdir / "Lodz.poi.sqlite")

    r = client.get("/api/layers/workspaces/Lodz/poi.geojson", auth=auth)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["type"] == "FeatureCollection" and data["source"] == "poi"
    assert "OpenStreetMap" in data["attribution"]
    assert len(data["features"]) == 2 and data["capped"] is False
    f = data["features"][0]
    assert f["geometry"] == {"type": "Point", "coordinates": [19.46, 51.76]}
    assert f["properties"] == {"kind": "szkola", "name": "SP nr 1"}


def test_poi_geojson_404_without_file(client, auth, tmp_data_dir):
    wdir = tmp_data_dir / "workspaces" / "Sieradz2"
    wdir.mkdir(parents=True)
    _make_workspace_sqlite(wdir / "Sieradz2.sqlite")
    r = client.get("/api/layers/workspaces/Sieradz2/poi.geojson", auth=auth)
    assert r.status_code == 404


def test_custom_layers_reports_has_poi(client, auth, tmp_data_dir):
    """Frontend dokleja warstwę POI na podstawie flagi has_poi z /custom-layers."""
    for name, with_poi in (("ZPoi", True), ("BezPoi", False)):
        wdir = tmp_data_dir / "workspaces" / name
        wdir.mkdir(parents=True)
        _make_workspace_sqlite(wdir / f"{name}.sqlite")
        if with_poi:
            _make_poi_stub(wdir / f"{name}.poi.sqlite")
        r = client.get(f"/api/workspaces/{name}/custom-layers", auth=auth)
        assert r.status_code == 200
        assert r.json()["has_poi"] is with_poi


def test_poi_upload_replace_delete(client, auth, tmp_data_dir, tmp_path):
    """Zarzadzanie plikiem POI z UI (konsument, admin): upload pod kanoniczna
    nazwa, zastapienie sprzata stare pliki, delete usuwa, walidacja odrzuca smieci."""
    wdir = tmp_data_dir / "workspaces" / "Zgierz"
    wdir.mkdir(parents=True)
    _make_workspace_sqlite(wdir / "Zgierz.sqlite")

    # Status przed: brak POI.
    r = client.get("/api/workspaces/Zgierz/poi", auth=auth)
    assert r.status_code == 200 and r.json() == {"present": False}

    # Upload poprawnego pliku (inna nazwa zrodlowa -> zapis pod <baza>.poi.sqlite).
    src = tmp_path / "cokolwiek.poi.sqlite"
    _make_poi_with_data(src)
    r = client.post("/api/workspaces/Zgierz/poi", auth=auth,
                    files={"file": ("cokolwiek.poi.sqlite", src.read_bytes(),
                                    "application/octet-stream")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["file"] == "Zgierz.poi.sqlite" and out["points"] == 2
    assert (wdir / "Zgierz.poi.sqlite").exists()

    # Status po uploadzie + warstwa dziala.
    r = client.get("/api/workspaces/Zgierz/poi", auth=auth)
    assert r.json()["present"] is True and r.json()["points"] == 2
    r = client.get("/api/layers/workspaces/Zgierz/poi.geojson", auth=auth)
    assert r.status_code == 200 and len(r.json()["features"]) == 2

    # Osierocony plik POI o innej nazwie znika przy zastapieniu.
    _make_poi_stub(wdir / "stary.poi.sqlite")
    r = client.post("/api/workspaces/Zgierz/poi", auth=auth,
                    files={"file": ("nowy.sqlite", src.read_bytes(),
                                    "application/octet-stream")})
    assert r.status_code == 200
    assert not (wdir / "stary.poi.sqlite").exists()
    assert (wdir / "Zgierz.poi.sqlite").exists()

    # Walidacja: nie-SQLite -> 400; zle rozszerzenie -> 400; brak kolumn -> 400.
    r = client.post("/api/workspaces/Zgierz/poi", auth=auth,
                    files={"file": ("smieci.sqlite", b"to nie jest sqlite",
                                    "application/octet-stream")})
    assert r.status_code == 400
    r = client.post("/api/workspaces/Zgierz/poi", auth=auth,
                    files={"file": ("plik.txt", src.read_bytes(), "text/plain")})
    assert r.status_code == 400
    bad = tmp_path / "bez_kolumn.sqlite"
    conn = sqlite3.connect(bad)
    conn.execute("CREATE TABLE poi (id INTEGER PRIMARY KEY, kind TEXT)")
    conn.commit()
    conn.close()
    r = client.post("/api/workspaces/Zgierz/poi", auth=auth,
                    files={"file": ("bez_kolumn.sqlite", bad.read_bytes(),
                                    "application/octet-stream")})
    assert r.status_code == 400 and "kolumn" in r.json()["detail"]
    # Nieudane uploady nie zostawiaja smieci ani nie psuja istniejacego pliku.
    assert (wdir / "Zgierz.poi.sqlite").exists()
    assert list(wdir.glob(".poi-upload-*")) == []

    # Delete.
    r = client.delete("/api/workspaces/Zgierz/poi", auth=auth)
    assert r.status_code == 200 and r.json()["files"] == ["Zgierz.poi.sqlite"]
    assert not (wdir / "Zgierz.poi.sqlite").exists()
    r = client.delete("/api/workspaces/Zgierz/poi", auth=auth)
    assert r.status_code == 404
