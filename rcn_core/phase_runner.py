"""CLI wrapper dla faz pipeline'u rcn-workshop. Uruchamiany jako subprocess
przez `app/workspaces.py` żeby izolować pyproj/spatialite od fiony używanej
w `app/layers.py` w uvicorn process (segfault libproj-fiona vs pyproj).

Każda faza = świeży proces Python, własna PROJ context. Ingest używa tabeli
`imports` (już istnieje, status/stage/progress_pct), ulepszenia używają
nowej tabeli `phase_runs` (schema v7).

Wywołania:
    python -m rcn_core.phase_runner --phase ingest \\
        --db ... --gml ... --import-id N \\
        --tryb snapshot --original-filename ... --stored-filename ... [--file-size N]

    python -m rcn_core.phase_runner --phase enrich-egib \\
        --db ... --run-id N [--dzialki ...] [--budynki ...]

    python -m rcn_core.phase_runner --phase compute-flags \\
        --db ... --run-id N

    python -m rcn_core.phase_runner --phase geocoding \\
        --db ... --run-id N
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import traceback
from pathlib import Path

# Pakiet zawsze instalowany jako -e (Dockerfile), więc rcn_core dostępne na
# sys.path. Nie ruszamy ścieżki -- to rolą instalacji.


def phase_runner_command(extra_args: list[str]) -> list[str]:
    """Komenda uruchamiająca phase_runner jako podproces -- działa w dev i w
    zamrożonym `.exe` (PyInstaller).

    - dev: `python -m rcn_core.phase_runner ...`
    - frozen (.exe): `sys.executable --phase-runner ...` -- bo `sys.executable`
      to wtedy nasz exe (nie Python), a `-m` nie zadziała. Flagę `--phase-runner`
      rozpoznaje punkt wejścia aplikacji (desktop.py) i woła `main()`.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--phase-runner", *extra_args]
    return [sys.executable, "-m", "rcn_core.phase_runner", *extra_args]


def _phase_ingest(args: argparse.Namespace) -> int:
    """Faza 1: parse GML + bulk INSERT + refresh_tx_cache.
    ingest_gml sam ustawia imports.status='success'/'failed' (linie 594-617
    rcn_core/ingest.py). Tu tylko forward progress + opakowanie except.
    """
    from rcn_core.ingest import ingest_gml, update_import_progress

    db_path = Path(args.db)
    import_id = args.import_id

    def progress_cb(stage: str, pct: int, msg: str = "") -> None:
        try:
            update_import_progress(
                db_path,
                import_id,
                stage=f"{stage}: {msg}" if msg else stage,
                progress_pct=pct,
            )
        except Exception:
            pass  # progress nie-krytyczne

    try:
        ingest_gml(
            db_path=db_path,
            gml_path=Path(args.gml),
            original_filename=args.original_filename,
            stored_filename=args.stored_filename,
            tryb=args.tryb,
            import_id=import_id,
            progress=progress_cb,
        )
        return 0
    except Exception as exc:
        # ingest_gml nie zdążył ustawić success -- robimy failed tu
        update_import_progress(
            db_path,
            import_id,
            status="failed",
            error_msg=f"{type(exc).__name__}: {exc}",
        )
        traceback.print_exc()
        return 1


def _phase_enrich_egib(args: argparse.Namespace) -> int:
    """Ulepszenie: lookup geometrii działek/budynków z EGIB GPKG +
    inheritance lokali + refresh tx_cache.
    Caller (endpoint) tworzy phase_run z status='running' i przekazuje
    --run-id; tu domyka."""
    from rcn_core.ingest import open_workspace
    from rcn_core.schema import finish_phase_run, refresh_tx_cache
    # Pakiet producenta -- nieobecny w buildzie konsumenta. Import lazy: faza
    # enrich-egib i tak jest niedostępna gdy brak rcn_producer (gating w
    # app/workspaces.py _ALLOWED_PHASES wg rcn_core.producer.HAS_PRODUCER).
    from rcn_producer.enrich import enrich_workspace, find_workspace_layers

    db_path = Path(args.db)
    run_id = args.run_id
    conn = open_workspace(db_path)
    try:
        dzialki = Path(args.dzialki) if args.dzialki else None
        budynki = Path(args.budynki) if args.budynki else None
        if dzialki is None and budynki is None:
            discovered = find_workspace_layers(db_path)
            dzialki = discovered.get("dzialki")
            budynki = discovered.get("budynki")
        if dzialki is None:
            finish_phase_run(
                conn, run_id,
                status="failed",
                error_msg="Brak GPKG działek (auto-discover nie znalazł, brak --dzialki)",
            )
            return 1

        stats = enrich_workspace(conn, dzialki, budynki, source_import_id=None)
        # Centroidy się zmieniły -> full refresh tx_cache
        refresh_tx_cache(conn, id_rcn_list=None)
        conn.commit()
        finish_phase_run(conn, run_id, status="success", diagnostics=stats)
        return 0
    except Exception as exc:
        finish_phase_run(
            conn, run_id,
            status="failed",
            error_msg=f"{type(exc).__name__}: {exc}",
        )
        traceback.print_exc()
        return 1
    finally:
        conn.close()


def _phase_compute_flags(args: argparse.Namespace) -> int:
    """Ulepszenie: oblicz quality flags dla wszystkich transakcji.
    Idempotent -- recompute nadpisuje data_quality_flags."""
    from rcn_core.ingest import open_workspace
    from rcn_core.schema import finish_phase_run
    from rcn_core.quality import compute_flags

    db_path = Path(args.db)
    run_id = args.run_id
    conn = open_workspace(db_path)
    try:
        stats = compute_flags(conn, id_rcn_list=None)
        conn.commit()
        finish_phase_run(conn, run_id, status="success", diagnostics=stats)
        return 0
    except Exception as exc:
        finish_phase_run(
            conn, run_id,
            status="failed",
            error_msg=f"{type(exc).__name__}: {exc}",
        )
        traceback.print_exc()
        return 1
    finally:
        conn.close()


def _phase_geocoding(args: argparse.Namespace) -> int:
    """Stub fazy geocoding (Nominatim). Faktyczna implementacja w osobnej
    sesji (Priorytet #2 z roadmapy). Tu tylko zapisuje 'failed' z
    explanation, żeby UI mogło pokazać status."""
    from rcn_core.ingest import open_workspace
    from rcn_core.schema import finish_phase_run

    db_path = Path(args.db)
    run_id = args.run_id
    conn = open_workspace(db_path)
    try:
        finish_phase_run(
            conn, run_id,
            status="failed",
            error_msg="Geocoding (Nominatim) nieaktywny — w przygotowaniu (Priorytet #2 roadmap)",
        )
        return 1
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rcn_core.phase_runner")
    p.add_argument("--phase", required=True,
                   choices=["ingest", "enrich-egib", "compute-flags", "geocoding"])
    p.add_argument("--db", required=True, help="Path to workspace.sqlite")

    # ingest args
    p.add_argument("--gml", help="Path to RCN.gml (phase=ingest)")
    p.add_argument("--import-id", type=int, help="imports.id (phase=ingest)")
    p.add_argument("--tryb", default="snapshot", choices=["snapshot", "delta"])
    p.add_argument("--original-filename", default="")
    p.add_argument("--stored-filename", default="")
    p.add_argument("--file-size", type=int, default=0)

    # enhancement args
    p.add_argument("--run-id", type=int, help="phase_runs.id (phase != ingest)")
    p.add_argument("--dzialki", help="Path to dzialki.gpkg (phase=enrich-egib, optional)")
    p.add_argument("--budynki", help="Path to budynki.gpkg (phase=enrich-egib, optional)")

    args = p.parse_args(argv)

    if args.phase == "ingest":
        if not args.gml or not args.import_id:
            p.error("phase=ingest wymaga --gml i --import-id")
        return _phase_ingest(args)
    if args.phase == "enrich-egib":
        if not args.run_id:
            p.error("phase=enrich-egib wymaga --run-id")
        return _phase_enrich_egib(args)
    if args.phase == "compute-flags":
        if not args.run_id:
            p.error("phase=compute-flags wymaga --run-id")
        return _phase_compute_flags(args)
    if args.phase == "geocoding":
        if not args.run_id:
            p.error("phase=geocoding wymaga --run-id")
        return _phase_geocoding(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
