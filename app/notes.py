"""Per-transakcja notes + FTS5 search.

Notes live in the workspace SQLite (one row per `id_rcn`, FK CASCADE — deleting
a transakcja or a workspace drops its notes). A virtual `notes_fts` table
mirrors the body via triggers and powers `notes_search` filter.

On first read of a note that doesn't exist yet the API returns a seed rendered
from the transakcja data (summary + plots + buildings + locals). The user can
save the seed as-is, or replace it — the default is "pre-fill with facts, let
user add opinions beneath".
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_admin, require_auth
from app.prywatnosc import bez_notariusza_w_notatce, ukrywac_notariusza
from app.workspaces import _require_workspace, _workspace_db
from rcn_core.ingest import open_workspace

router = APIRouter(prefix="/api/workspaces", tags=["notes"])


class NoteResponse(BaseModel):
    id_rcn: str
    body: str
    is_seed: bool = False
    exists: bool
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


class NoteSaveRequest(BaseModel):
    body: str = Field(..., min_length=0)


@router.get("/{workspace_id}/transactions/note/{id_rcn:path}", response_model=NoteResponse)
def get_note(
    workspace_id: str,
    id_rcn: str,
    ctx=Depends(require_auth),
) -> NoteResponse:
    _require_workspace(workspace_id)
    ukryj = ukrywac_notariusza(ctx)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        existing = conn.execute(
            "SELECT body, created_at, updated_at FROM notes WHERE id_rcn = ?",
            (id_rcn,),
        ).fetchone()
        if existing:
            # Notatka zapisana wcześniej może być seedem przyjętym „jak był",
            # czyli nieść wiersz z nazwiskiem -- poprawienie samego generatora
            # nie sięgnęłoby tego, co już leży w bazie.
            zapisana = existing["body"]
            if ukryj:
                zapisana = bez_notariusza_w_notatce(zapisana)
            return NoteResponse(
                id_rcn=id_rcn,
                body=zapisana,
                exists=True,
                is_seed=False,
                created_at=existing["created_at"],
                updated_at=existing["updated_at"],
            )

        tx_row = conn.execute(
            "SELECT * FROM transakcje WHERE id_rcn = ?", (id_rcn,)
        ).fetchone()
        if tx_row is None:
            raise HTTPException(status_code=404, detail="Transakcja nie istnieje")

        seed = _render_seed(conn, dict(tx_row), ukryj_notariusza=ukryj)
        return NoteResponse(id_rcn=id_rcn, body=seed, exists=False, is_seed=True)
    finally:
        conn.close()


@router.put("/{workspace_id}/transactions/note/{id_rcn:path}", response_model=NoteResponse)
def save_note(
    workspace_id: str,
    id_rcn: str,
    body: NoteSaveRequest = Body(...),
    _: str = Depends(require_admin),
) -> NoteResponse:
    _require_workspace(workspace_id)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        exists = conn.execute(
            "SELECT 1 FROM transakcje WHERE id_rcn = ?", (id_rcn,)
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Transakcja nie istnieje")

        now = int(time.time())
        conn.execute(
            """
            INSERT INTO notes (id_rcn, body, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id_rcn) DO UPDATE SET
                body       = excluded.body,
                updated_at = excluded.updated_at
            """,
            (id_rcn, body.body, now, now),
        )
        conn.commit()

        row = conn.execute(
            "SELECT body, created_at, updated_at FROM notes WHERE id_rcn = ?",
            (id_rcn,),
        ).fetchone()
    finally:
        conn.close()

    return NoteResponse(
        id_rcn=id_rcn,
        body=row["body"],
        exists=True,
        is_seed=False,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.delete(
    "/{workspace_id}/transactions/note/{id_rcn:path}",
    status_code=204,
)
def delete_note(
    workspace_id: str,
    id_rcn: str,
    _: str = Depends(require_admin),
) -> None:
    _require_workspace(workspace_id)
    conn = open_workspace(_workspace_db(workspace_id))
    try:
        conn.execute("DELETE FROM notes WHERE id_rcn = ?", (id_rcn,))
        conn.commit()
    finally:
        conn.close()


def _render_seed(conn: sqlite3.Connection, tx: dict, ukryj_notariusza: bool = False) -> str:
    """Domyślna treść notatki złożona z faktów o transakcji.

    `ukryj_notariusza` = rola bez uprawnień admina (gość instancji publicznej,
    konto readonly). Wtedy wiersz „Twórca dokumentu" w ogóle nie powstaje --
    seed jest zwykłą odpowiedzią API, więc podlega tej samej zasadzie co
    `details` i eksporty: maskowanie po stronie serwera, patrz `app/prywatnosc.py`.
    """
    id_rcn = tx["id_rcn"]
    plots = conn.execute(
        "SELECT identyfikator_dzialki, obreb, miejscowosc, adres, powierzchnia_m2, cena_brutto "
        "FROM plots WHERE id_rcn = ? ORDER BY COALESCE(powierzchnia_m2, 0) DESC",
        (id_rcn,),
    ).fetchall()
    buildings = conn.execute(
        "SELECT identyfikator_budynku, rodzaj_budynku, miejscowosc, adres, pow_uzytkowa, cena_brutto "
        "FROM buildings WHERE id_rcn = ?",
        (id_rcn,),
    ).fetchall()
    locals_ = conn.execute(
        "SELECT identyfikator_lokalu, funkcja, miejscowosc, adres, pow_uzytkowa, liczba_izb, kondygnacja, cena_brutto "
        "FROM locals WHERE id_rcn = ?",
        (id_rcn,),
    ).fetchall()

    lines: list[str] = []

    lines.append(f"# Transakcja `{id_rcn}`")
    lines.append("")
    lines.append(f"- **Data:** {tx.get('data_transakcji') or '—'}")
    lines.append(f"- **Rodzaj:** {tx.get('rodzaj_transakcji') or '—'} · {tx.get('rodzaj_rynku') or '—'}")
    lines.append(f"- **Rodzaj nieruchomości:** {tx.get('rodzaj_nieruchomosci') or '—'}")
    cena = tx.get("cena_transakcji_brutto")
    lines.append(f"- **Cena brutto:** {_money(cena)}")
    lines.append(f"- **Sprzedający:** {tx.get('strona_sprzedajaca') or '—'}")
    lines.append(f"- **Kupujący:** {tx.get('strona_kupujaca') or '—'}")
    lines.append(f"- **Dokument:** {tx.get('dokument') or '—'}")
    if tx.get("tworca_dokumentu") and not ukryj_notariusza:
        lines.append(f"- **Twórca dokumentu:** {tx['tworca_dokumentu']}")
    lines.append("")

    if plots:
        lines.append("## Działki")
        for p in plots:
            ident = p["identyfikator_dzialki"] or "(brak id)"
            obreb = f" · obręb **{p['obreb']}**" if p["obreb"] else ""
            addr = " · " + _addr(p) if _addr(p) else ""
            pow_m2_val = int(round(p["powierzchnia_m2"])) if p["powierzchnia_m2"] else None
            pow_txt = f" · {pow_m2_val:,} m²".replace(",", " ") if pow_m2_val else ""
            cena_txt = f" · {_money(p['cena_brutto'])}" if p["cena_brutto"] else ""
            lines.append(f"- `{ident}`{obreb}{addr}{pow_txt}{cena_txt}")
        lines.append("")

    if buildings:
        lines.append("## Budynki")
        for b in buildings:
            ident = b["identyfikator_budynku"] or "(brak id)"
            rodz = f" · {b['rodzaj_budynku']}" if b["rodzaj_budynku"] else ""
            addr = " · " + _addr(b) if _addr(b) else ""
            pow_txt = f" · pow. uż. {b['pow_uzytkowa']} m²" if b["pow_uzytkowa"] else ""
            cena_txt = f" · {_money(b['cena_brutto'])}" if b["cena_brutto"] else ""
            lines.append(f"- `{ident}`{rodz}{addr}{pow_txt}{cena_txt}")
        lines.append("")

    if locals_:
        lines.append("## Lokale")
        for lok in locals_:
            ident = lok["identyfikator_lokalu"] or "(brak id)"
            funkcja = f" · {lok['funkcja']}" if lok["funkcja"] else ""
            addr = " · " + _addr(lok) if _addr(lok) else ""
            pow_txt = f" · {lok['pow_uzytkowa']} m²" if lok["pow_uzytkowa"] else ""
            izby = f" · {lok['liczba_izb']} izb" if lok["liczba_izb"] else ""
            kond = f" · p. {lok['kondygnacja']}" if lok["kondygnacja"] else ""
            cena_txt = f" · {_money(lok['cena_brutto'])}" if lok["cena_brutto"] else ""
            lines.append(f"- `{ident}`{funkcja}{addr}{pow_txt}{izby}{kond}{cena_txt}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Uwagi rzeczoznawcy")
    lines.append("")
    lines.append("_tu dopisz spostrzeżenia, porównania, wnioski…_")
    lines.append("")

    return "\n".join(lines)


def _money(value) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
        return f"{v:,.0f} zł".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def _addr(row) -> str:
    parts = [row["miejscowosc"], row["adres"]] if isinstance(row, (sqlite3.Row, dict)) else []
    return " ".join(str(p) for p in parts if p)
