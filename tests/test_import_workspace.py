"""Testy konsumenckiego endpointu importu gotowego workspace (.zip z `rcn pack`).

Endpoint POST /api/workspaces/import: rozpakowanie atomowe paczki <nazwa>/<nazwa>.sqlite
do workspaces_dir. Konsumencki (Model A) -- zawsze dostepny adminowi.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path


def _make_workspace_sqlite(path: Path) -> None:
    """Utworz prawdziwa baze workspace (ze schematem) -- _workspace_info ja odpyta."""
    from rcn_core.ingest import open_workspace
    conn = open_workspace(path)
    conn.close()


def _zip_bytes(arcname_to_path: dict[str, Path]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, p in arcname_to_path.items():
            zf.write(p, arc)
    return buf.getvalue()


def _zip_bytes_raw(arcname_to_data: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, data in arcname_to_data.items():
            zf.writestr(arc, data)
    return buf.getvalue()


def test_import_workspace_roundtrip(client, auth, tmp_path):
    db = tmp_path / "src.sqlite"
    _make_workspace_sqlite(db)
    payload = _zip_bytes({"Kutno/Kutno.sqlite": db})

    # 1. Import -> 201, pojawia sie na liscie.
    r = client.post(
        "/api/workspaces/import", auth=auth,
        files={"file": ("Kutno.zip", payload, "application/zip")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["id"] == "Kutno"
    assert any(ws["id"] == "Kutno" for ws in client.get("/api/workspaces", auth=auth).json())

    # 2. Ponowny import bez overwrite -> 409.
    r = client.post(
        "/api/workspaces/import", auth=auth,
        files={"file": ("Kutno.zip", payload, "application/zip")},
    )
    assert r.status_code == 409

    # 3. overwrite=true -> 201 (nadpisuje).
    r = client.post(
        "/api/workspaces/import", auth=auth,
        files={"file": ("Kutno.zip", payload, "application/zip")},
        data={"overwrite": "true"},
    )
    assert r.status_code == 201


def test_import_rejects_zip_slip(client, auth, tmp_path):
    db = tmp_path / "src.sqlite"
    _make_workspace_sqlite(db)
    payload = _zip_bytes_raw({"../evil.sqlite": db.read_bytes()})
    r = client.post(
        "/api/workspaces/import", auth=auth,
        files={"file": ("evil.zip", payload, "application/zip")},
    )
    assert r.status_code == 400


def test_import_rejects_multiple_roots(client, auth, tmp_path):
    db = tmp_path / "src.sqlite"
    _make_workspace_sqlite(db)
    payload = _zip_bytes_raw({"a/a.sqlite": db.read_bytes(), "b/b.sqlite": db.read_bytes()})
    r = client.post(
        "/api/workspaces/import", auth=auth,
        files={"file": ("multi.zip", payload, "application/zip")},
    )
    assert r.status_code == 400


def test_import_rejects_missing_sqlite(client, auth):
    payload = _zip_bytes_raw({"Kutno/notes.txt": b"brak bazy"})
    r = client.post(
        "/api/workspaces/import", auth=auth,
        files={"file": ("nodb.zip", payload, "application/zip")},
    )
    assert r.status_code == 400


def test_import_requires_admin(client, tmp_path):
    db = tmp_path / "src.sqlite"
    _make_workspace_sqlite(db)
    payload = _zip_bytes({"Kutno/Kutno.sqlite": db})
    # Bez auth -> 401.
    r = client.post(
        "/api/workspaces/import",
        files={"file": ("Kutno.zip", payload, "application/zip")},
    )
    assert r.status_code == 401
