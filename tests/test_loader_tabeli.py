"""Tabela w trakcie ładowania nie udaje pustego wyniku.

Zgłoszenie 2026-09-17: po wejściu na workspace metropolii przez kilkanaście
sekund widniało „Brak transakcji spełniających kryteria". To nie był błąd
zapytania -- zapytanie jeszcze nie poszło. `boot()` robi przed nim `loadInfo`,
`loadLookups` (katalog obrębów!) i `loadCustomLayers`, a `loadingQuery` wstaje
dopiero w `runQuery()`, więc pusty `queryResult` wyglądał jak wynik wyszukiwania.

Test jest statyczny (czyta szablon i skrypt), bo logika siedzi w Alpine --
uruchamianie przeglądarki dla trzech warunków `x-show` byłoby nieproporcjonalne.
"""
from pathlib import Path

import pytest

KORZEN = Path(__file__).resolve().parent.parent
TABELA = KORZEN / "templates" / "partials" / "_data_table.html"
SKRYPT = KORZEN / "static" / "js" / "workspace.js"
CSS = KORZEN / "static" / "css" / "app.css"


@pytest.fixture(scope="module")
def tabela() -> str:
    return TABELA.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def skrypt() -> str:
    return SKRYPT.read_text(encoding="utf-8")


def test_pusty_wynik_ukryty_na_czas_bootu(tabela):
    assert 'x-show="!booting && !loadingQuery && queryResult.items.length === 0"' in tabela, (
        "empty state musi być ukryty także w trakcie boot()u, nie tylko podczas runQuery"
    )


def test_komunikat_ladowania_mowi_ile_to_potrwa(tabela):
    assert 'x-show="booting || loadingQuery"' in tabela
    assert "Trwa ładowanie danych" in tabela
    assert "kilkanaście sekund" in tabela
    assert "Nie odświeżaj strony" in tabela
    # Dla czytników ekranu: komunikat ma być ogłoszony, nie tylko narysowany.
    assert 'role="status"' in tabela and 'aria-live="polite"' in tabela


def test_skeleton_widoczny_od_pierwszej_chwili(tabela):
    assert tabela.count('x-show="booting || loadingQuery"') >= 2, (
        "skeleton i komunikat mają ten sam warunek -- inaczej user widzi pustą tabelę"
    )


def test_flaga_bootu_startuje_wlaczona(skrypt):
    assert "booting: true," in skrypt, "przed pierwszym zapytaniem stan to „ładowanie”"


def test_boot_zawsze_gasi_flage(skrypt):
    """Także gdy któryś krok padnie -- inaczej tabela zostaje z komunikatem na zawsze."""
    poczatek = skrypt.index("async boot()")
    koniec = skrypt.index("async _checkBusy()")
    cialo = skrypt[poczatek:koniec]
    assert "} finally {" in cialo and "this.booting = false;" in cialo
    # Gałąź „workspace zajęty" kończy się `return` przed tym try -- musi gasić osobno.
    assert cialo.count("this.booting = false;") >= 2


def test_style_komunikatu_istnieja():
    css = CSS.read_text(encoding="utf-8")
    assert ".wb-loading-note {" in css
    assert ".wb-loading-spinner {" in css
    # Animacja reużyta z istniejącego spinera importu -- bez drugiej definicji.
    assert css.count("@keyframes wb-spin") == 1
