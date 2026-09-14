"""Snapshot nie może po cichu ukryć większości bazy.

Awaria 2026-09-12: pliki będące FRAGMENTAMI zbioru wgrano w trybie snapshot.
Każdy import uznał wszystko, czego nie zawierał (w zakresie swoich dat), za
zniknięte z portalu. Skutek na produkcji: Łódź pokazywała 7116 z 166 696
transakcji (96% ukryte), Warszawa 57 100 z 598 778 (90%). Dane nie ginęły,
ale domyślny widok filtruje wycofane, więc nikt tego nie zauważył.

Zabezpieczenie: `_wycofanie_nieproporcjonalne` odmawia wycofania, gdy import
wycofałby wielokrotnie więcej, niż sam wnosi.
"""
import pytest

from rcn_core.ingest import (
    PROG_WYCOFAN_BEZWZGLEDNY,
    _wycofanie_nieproporcjonalne,
)


@pytest.mark.parametrize("wycofane, w_pliku, blokuje", [
    # Realny przypadek z Łodzi: plik 7080 transakcji wycofałby 158 479.
    (158_479, 7_080, True),
    # Warszawa i olsztyński -- ta sama skala nieproporcjonalności.
    (541_678, 57_100, True),
    # Powiat olsztyński: 2,15x -- tuż nad progiem, i słusznie: to ten import
    # ukrył tam 37 454 transakcje (66% bazy).
    (37_454, 17_457, True),
    # Tuż pod progiem krotności (2,0x) przepuszczamy -- taki snapshot może być
    # prawdziwy, a nadmierna czułość blokowałaby legalne korekty.
    (34_000, 17_457, False),
    # Normalna praca: snapshot miesięczny koryguje kilka wpisów.
    (3, 500, False),
    (80, 40, False),           # poniżej progu bezwzględnego, choć 2x
    (100, 10, False),          # dokładnie na progu bezwzględnym
    (101, 10, True),           # tuż nad progiem i 10x -> blokada
    # Snapshot roczny zastępujący rok: wnosi dużo, wycofuje trochę.
    (200, 12_000, False),
])
def test_prog_wycofania(wycofane, w_pliku, blokuje):
    powod = _wycofanie_nieproporcjonalne(wycofane, w_pliku)
    assert (powod is not None) is blokuje, powod


def test_prog_bezwzgledny_przepuszcza_male_korekty():
    """Do PROG_WYCOFAN_BEZWZGLEDNY nie blokujemy niczego -- korekty w rejestrze
    bywają liczne przy małym pliku i to normalne."""
    assert _wycofanie_nieproporcjonalne(PROG_WYCOFAN_BEZWZGLEDNY, 1) is None
    assert _wycofanie_nieproporcjonalne(PROG_WYCOFAN_BEZWZGLEDNY + 1, 1) is not None


def test_powod_odmowy_mowi_co_zrobic():
    powod = _wycofanie_nieproporcjonalne(158_479, 7_080)
    assert "7080" in powod and "158479" in powod
    assert "delta" in powod, "komunikat ma wskazać wyjście, nie tylko problem"


def test_domyslnie_chronimy():
    """Sygnatura `ingest_gml` musi domyślnie ODMAWIAĆ masowego wycofania --
    import z UI idzie przez subprocess bez tej flagi, więc domyślna wartość
    jest jedyną ochroną."""
    import inspect

    from rcn_core.ingest import ingest_gml
    assert inspect.signature(ingest_gml).parameters["pozwol_masowe_wycofanie"].default is False


# --- zakres snapshotu z percentyli ---------------------------------------


def test_zakres_snapshotu_odcina_smieciowe_daty():
    """Jedna absurdalna data nie może rozciągnąć okresu wycofywania.

    Powiat olsztyński miał w pliku daty `0202-05-10` i `5202-06-03`, przez co
    zapisany zakres snapshotu objął trzy tysiące lat, a wycofanie poszło po
    całej bazie.
    """
    from rcn_core.ingest import _zakres_snapshotu

    # 100 transakcji z jednego miesiąca + dwie śmieciowe daty na skrajach.
    daty = [f"2026-03-{d:02d}" for d in range(1, 29)] * 4
    daty += ["0202-05-10", "5202-06-03"]
    od, do = _zakres_snapshotu(daty)
    assert od.startswith("2026-03"), od
    assert do.startswith("2026-03"), do


def test_maly_plik_bierze_pelny_zakres():
    """Przy kilku transakcjach odcinanie ogonów nie ma sensu -- i nie wolno
    zgubić okresu, którego plik faktycznie dotyczy."""
    from rcn_core.ingest import _zakres_snapshotu

    daty = ["2026-01-05", "2026-01-20", "2026-02-11"]
    assert _zakres_snapshotu(daty) == ("2026-01-05", "2026-02-11")


def test_schema_ma_kolumne_z_importem_ktory_wycofal(tmp_path):
    import sqlite3

    from rcn_core.schema import apply_schema

    conn = sqlite3.connect(tmp_path / "w.sqlite")
    try:
        apply_schema(conn)
        kolumny = {r[1] for r in conn.execute("PRAGMA table_info(transakcje)")}
        assert "withdrawn_by_import_id" in kolumny
        wersja = conn.execute(
            "SELECT value FROM workspace_meta WHERE key='schema_version'").fetchone()[0]
        assert int(wersja) >= 10
    finally:
        conn.close()


def test_domyslny_tryb_uploadu_to_delta():
    """Snapshot wycofuje brakujące, więc nie może być wartością startową pola."""
    import inspect

    from app.workspaces import upload_gml
    domyslny = inspect.signature(upload_gml).parameters["tryb"].default
    assert getattr(domyslny, "default", domyslny) == "delta"
