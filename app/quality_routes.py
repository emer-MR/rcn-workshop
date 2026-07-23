"""Endpointy do konfiguracji thresholdów quality flags per workspace.

GET /api/workspaces/{id}/quality-thresholds  (require_auth) -- aktualne progi
PUT /api/workspaces/{id}/quality-thresholds  (require_admin) -- save + recompute

Po zapisie thresholdów wykonujemy compute_flags(None) -- recompute całej bazy.
Dla Łodzi/Bełchatów ~1-5s, Warszawy ~25s. Akceptowalny dla rzadkiej operacji.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_admin, require_auth
from app.workspaces import _require_workspace, _workspace_db
from rcn_core.ingest import open_workspace
from rcn_core.quality import (
    DEFAULT_THRESHOLDS,
    compute_auto_thresholds,
    compute_flags,
    load_thresholds,
    save_thresholds,
)

router = APIRouter(prefix="/api/workspaces", tags=["quality"])


@router.get("/{workspace_id}/quality-thresholds")
def get_quality_thresholds(
    workspace_id: str,
    _: str = Depends(require_auth),
) -> dict:
    """Aktualne thresholdy + defaulty (na readonly też -- info-only)."""
    _require_workspace(workspace_id)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        return {
            "current": load_thresholds(conn),
            "defaults": dict(DEFAULT_THRESHOLDS),
        }
    finally:
        conn.close()


@router.post("/{workspace_id}/quality-thresholds/auto")
def auto_detect_quality_thresholds(
    workspace_id: str,
    percentile_low: int = 5,
    percentile_high: int = 95,
    _: str = Depends(require_admin),
) -> dict:
    """Auto-detect thresholds z faktycznych danych workspace (bez zapisu).

    Zwraca sugerowane progi + diagnostics (liczba próbek per typ + percentyle).
    UI pokazuje user-owi, on może edytować, dopiero PUT zapisuje + recompute.

    Query params: percentile_low (default 5), percentile_high (default 95).
    Sieradz przy 5/95 dla gruntu daje ~1-50 zł/m² (zamiast default 5-5000).
    """
    if not (1 <= percentile_low < percentile_high <= 99):
        raise HTTPException(
            status_code=400,
            detail="percentile_low musi być w 1-98, percentile_high w 2-99, low<high",
        )
    _require_workspace(workspace_id)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        auto = compute_auto_thresholds(conn, percentile_low=percentile_low, percentile_high=percentile_high)
        return {
            "auto": auto,
            "current": load_thresholds(conn),
            "defaults": dict(DEFAULT_THRESHOLDS),
        }
    finally:
        conn.close()


@router.put("/{workspace_id}/quality-thresholds")
def put_quality_thresholds(
    workspace_id: str,
    body: dict,
    _: str = Depends(require_admin),
) -> dict:
    """Zapisz thresholdy + recompute flag dla całej bazy. Zwraca current
    + counters z compute_flags."""
    _require_workspace(workspace_id)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        save_thresholds(conn, body or {})
        stats = compute_flags(conn, id_rcn_list=None)
        return {
            "current": load_thresholds(conn),
            "stats": stats,
        }
    finally:
        conn.close()
