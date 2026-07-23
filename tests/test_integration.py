"""End-to-end integration tests against the real Łódź delta fixture.

Requires `tests/fixtures/od14do27marca2026.gml` (extract from the zip in
`Referencje/RCN Łódź/`).  Skipped otherwise.
"""
from __future__ import annotations

import zipfile


def test_create_list_delete_workspace(client, auth):
    r = client.post("/api/workspaces", auth=auth, json={"name": "pytest-1"})
    assert r.status_code == 201
    wid = r.json()["id"]

    r = client.get("/api/workspaces", auth=auth)
    assert r.status_code == 200
    assert any(ws["id"] == wid for ws in r.json())

    r = client.delete(f"/api/workspaces/{wid}", auth=auth)
    assert r.status_code == 204


def test_auth_required(client):
    assert client.get("/api/workspaces").status_code == 401
    assert client.get("/api/workspaces", auth=("bad", "creds")).status_code == 401
    assert client.get("/healthz").status_code == 200  # public


def _upload_and_wait(client, auth, wid: str, gml_path, timeout_s: int = 60):
    """POST /upload (202 Accepted) + poll GET /imports/{id} aż status leave 'processing'.

    Po refactorze 2026-05-01 ingest jest spawnowany jako subprocess.Popen
    (rcn_core.phase_runner) zamiast inline w BackgroundTask -- żeby izolować
    pyproj/spatialite od fiony w uvicorn process (segfault libproj-fiona).
    Dla małej delty (~700 tx) subprocess kończy w <10s; timeout 60s
    bezpieczny dla zatłoczonego CI.
    """
    import time
    with open(gml_path, "rb") as f:
        r = client.post(
            f"/api/workspaces/{wid}/upload",
            auth=auth,
            files={"file": (gml_path.name, f, "application/gml+xml")},
            data={"tryb": "delta"},
        )
    assert r.status_code == 202, f"upload status={r.status_code}: {r.text[:200]}"
    import_id = r.json()["import_id"]

    deadline = time.time() + timeout_s
    info = None
    while time.time() < deadline:
        r = client.get(f"/api/workspaces/{wid}/imports/{import_id}", auth=auth)
        assert r.status_code == 200
        info = r.json()
        if info["status"] not in ("processing", "queued"):
            break
        time.sleep(0.5)
    assert info is not None
    assert info["status"] == "success", f"import status={info['status']}: {info.get('error_msg')}"
    return info


def test_upload_and_query(client, auth, delta_gml):
    wid = client.post("/api/workspaces", auth=auth, json={"name": "pytest-2"}).json()["id"]

    info = _upload_and_wait(client, auth, wid, delta_gml)
    assert info["transaction_count"] == 674
    assert info["inserted_count"] == 674
    assert info["updated_count"] == 0
    assert info["parser_epsg"] == "EPSG:2177"

    r = client.post(f"/api/workspaces/{wid}/query", auth=auth, json={"page": 1, "pageSize": 10})
    q = r.json()
    assert q["total"] == 674
    assert len(q["items"]) == 10
    assert all(it["id_rcn"] for it in q["items"])

    r = client.post(
        f"/api/workspaces/{wid}/query",
        auth=auth,
        json={"filters": {"geometry": {"type": "bbox", "bbox": [19.40, 51.70, 19.55, 51.85]}}, "page": 1, "pageSize": 5},
    )
    assert 400 < r.json()["total"] < 700  # większość rekordów Łodzi mieści się w bbox miasta

    r = client.post(
        f"/api/workspaces/{wid}/query",
        auth=auth,
        json={"filters": {"geometry": {"type": "bbox", "bbox": [0.0, 0.0, 1.0, 1.0]}}},
    )
    assert r.json()["total"] == 0


def test_upsert_newer_wins(client, auth, delta_gml):
    wid = client.post("/api/workspaces", auth=auth, json={"name": "pytest-3"}).json()["id"]

    first = _upload_and_wait(client, auth, wid, delta_gml)
    assert first["inserted_count"] == 674

    second = _upload_and_wait(client, auth, wid, delta_gml)
    assert second["inserted_count"] == 0
    assert second["updated_count"] + second["skipped_count"] == 674

    total = client.get(f"/api/workspaces/{wid}", auth=auth).json()["transaction_count"]
    assert total == 674

    imports = client.get(f"/api/workspaces/{wid}/imports", auth=auth).json()
    assert len(imports) == 2


def test_export_xlsx(client, auth, delta_gml):
    wid = client.post("/api/workspaces", auth=auth, json={"name": "pytest-4"}).json()["id"]
    _upload_and_wait(client, auth, wid, delta_gml)

    r = client.get(f"/api/workspaces/{wid}/export.xlsx", auth=auth)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    data = r.content
    assert len(data) > 10_000

    from io import BytesIO
    with zipfile.ZipFile(BytesIO(data)) as z:
        sheets = [n for n in z.namelist() if n.startswith("xl/worksheets/")]
        # 6 sheets: Podsumowanie + Raport zbiorczy + Transakcje + Działki + Budynki + Lokale
        assert len(sheets) == 6


def test_export_gpkg(client, auth, delta_gml):
    """Test pyogrio multi-layer GPKG export. Łódź delta nie ma geometrii w GML
    (tylko identyfikatory EGIB), więc plots/buildings/locals są bez WKT i
    zostaną pominięte. transakcje_punkty również puste (bez tx_cache geom).
    Eksport nie powinien crashować -- albo 404 (nic do zapisania), albo 200
    z minimalnym GPKG. Smoke test sprawdza że pyogrio writer nie segfaultuje
    i odpowiedź jest spójna."""
    wid = client.post("/api/workspaces", auth=auth, json={"name": "pytest-gpkg"}).json()["id"]
    _upload_and_wait(client, auth, wid, delta_gml)

    r = client.get(f"/api/workspaces/{wid}/export.gpkg", auth=auth)
    # Bez enrichment EGIB Łódź delta ma tylko identyfikatory -- żaden plot/building
    # nie ma WKT, transakcje_punkty bez centroid → 404 "No matching rows".
    # Po enrichment (testowane manualnie w prod) zwraca 200 + GPKG bytes.
    assert r.status_code in (200, 404), f"unexpected status: {r.status_code} body={r.text[:200]}"
    if r.status_code == 200:
        assert r.headers["content-type"] == "application/geopackage+sqlite3"
        assert len(r.content) > 1000  # nawet pusty GPKG ma ~10KB header
        # Verify multi-layer GPKG (pyogrio.list_layers).
        import os, tempfile
        from pyogrio import list_layers
        fd, tmp = tempfile.mkstemp(suffix=".gpkg")
        try:
            os.close(fd)
            with open(tmp, "wb") as f:
                f.write(r.content)
            layers = list_layers(tmp)
            layer_names = {row[0] for row in layers}
            assert "transakcje_punkty" in layer_names or len(layer_names) > 0
        finally:
            os.unlink(tmp)


def test_lookups(client, auth, delta_gml):
    wid = client.post("/api/workspaces", auth=auth, json={"name": "pytest-5"}).json()["id"]
    _upload_and_wait(client, auth, wid, delta_gml)

    lookups = client.get(f"/api/workspaces/{wid}/lookups", auth=auth).json()
    assert len(lookups["rodzaj_rynku"]) >= 1
    assert len(lookups["rodzaj_transakcji"]) >= 1
    assert len(lookups["teryt_gminy"]) >= 1
