"""Słownik oznaczeń obrębów: `teryt_gminy` + numer -> oznaczenie urzędowe.

Po co osobny słownik, skoro `rcn_core.obreby` czyta nazwy wprost z EGIB:
- **konsument nie ma EGIB.** Build .exe nie zawiera warstw ani `pyogrio`,
  a plik `dzialki.gpkg` jednego miasta waży ~70 MB. Słownik tego samego
  miasta to ~215 wierszy tekstu, więc jedzie w paczce workspace'u.
- **operator poprawia ręcznie.** EGIB czasem nie ma nazwy obrębu albo ma ją
  niespójną; wpis `zrodlo=reczny` jest chroniony przed nadpisaniem przy
  kolejnym generowaniu z EGIB.

Format: CSV z separatorem `;` (Excel po polsku otwiera go bez czarów),
kolumny `teryt_gminy;numer_obrebu;oznaczenie;gmina;zrodlo`. Plik leży obok
bazy jako `<nazwa>.obreby.csv` -- sidecar, jak `.poi.sqlite` i `.notes.sqlite`,
tylko że ten JEST częścią deliverable'u (oznaczenia mają dojechać do odbiorcy).

Klucz jest parą, nie numerem: numer obrębu powtarza się między jednostkami
ewidencyjnymi (w Łodzi „0024" to cztery obręby), a nazwa między gminami
(w powiecie piotrkowskim „JANÓW" to dwa obręby).
"""
from __future__ import annotations

import csv
import gzip
import io
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SUFIKS = ".obreby.csv"
# Słownik krajowy wbudowany w aplikację: 377 powiatów, ~49,6 tys. obrębów,
# spakowany 419 KB (rozpakowany 2,7 MB). Dzięki niemu oznaczenia („B-42")
# działają OD RAZU po instalacji, także u kogoś, kto importuje własny GML
# i nie dostał od nikogo pliku słownika. Zrzut EGIB 2026-08-06.
ZASOB_KRAJOWY = Path(__file__).with_name("resources") / "obreby-polska.csv.gz"
ZRODLO_WBUDOWANY = "wbudowany"
KOLUMNY = ("teryt_gminy", "numer_obrebu", "oznaczenie", "gmina", "zrodlo")
ZRODLO_RECZNY = "reczny"

_IDENT_RE = re.compile(r"^([0-9]+_[0-9]+)\.([^.]+)\.")


@dataclass(frozen=True)
class WpisObrebu:
    teryt_gminy: str
    numer_obrebu: str
    oznaczenie: str
    gmina: str = ""
    zrodlo: str = ""

    @property
    def klucz(self) -> str:
        return f"{self.teryt_gminy}.{self.numer_obrebu}"

    @property
    def reczny(self) -> bool:
        return self.zrodlo == ZRODLO_RECZNY


def sciezka_slownika(folder: Path, nazwa_bazy: str) -> Path:
    """`<folder>/<nazwa bazy bez rozszerzenia>.obreby.csv`."""
    return folder / f"{Path(nazwa_bazy).stem}{SUFIKS}"


def znajdz_slownik(folder: Path) -> Path | None:
    """Pierwszy plik `*.obreby.csv` w folderze workspace'u (albo None)."""
    pliki = sorted(folder.glob(f"*{SUFIKS}"))
    return pliki[0] if pliki else None


def wczytaj(sciezka: Path) -> dict[str, WpisObrebu]:
    """Słownik `{teryt}.{numer}` -> wpis. Brak pliku = pusty słownik.

    Wiersze bez oznaczenia są pomijane: puste oznaczenie nie ma czego wnieść,
    a wpuszczone dalej wyczyściłoby nazwę już zapisaną w bazie.
    """
    if not sciezka.exists():
        return {}
    wynik: dict[str, WpisObrebu] = {}
    with sciezka.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            teryt = (row.get("teryt_gminy") or "").strip()
            numer = (row.get("numer_obrebu") or "").strip()
            oznaczenie = (row.get("oznaczenie") or "").strip()
            if not teryt or not numer or not oznaczenie:
                continue
            wpis = WpisObrebu(
                teryt_gminy=teryt,
                numer_obrebu=numer,
                oznaczenie=oznaczenie,
                gmina=(row.get("gmina") or "").strip(),
                zrodlo=(row.get("zrodlo") or "").strip(),
            )
            wynik[wpis.klucz] = wpis
    return wynik


_KRAJOWY_CACHE: dict[str, WpisObrebu] | None = None


def wczytaj_krajowy() -> dict[str, WpisObrebu]:
    """Wbudowany słownik krajowy (cache na proces).

    Brak pliku albo uszkodzony zasób NIE jest błędem krytycznym: aplikacja ma
    wtedy działać jak dotąd, pokazując numery obrębów zamiast oznaczeń.
    """
    global _KRAJOWY_CACHE
    if _KRAJOWY_CACHE is not None:
        return _KRAJOWY_CACHE
    wynik: dict[str, WpisObrebu] = {}
    try:
        with gzip.open(ZASOB_KRAJOWY, "rb") as f:
            tekst = io.TextIOWrapper(f, encoding="utf-8-sig", newline="")
            for row in csv.DictReader(tekst, delimiter=";"):
                teryt = (row.get("teryt_gminy") or "").strip()
                numer = (row.get("numer_obrebu") or "").strip()
                oznaczenie = (row.get("oznaczenie") or "").strip()
                if not teryt or not numer or not oznaczenie:
                    continue
                wpis = WpisObrebu(teryt, numer, oznaczenie,
                                  (row.get("gmina") or "").strip(), ZRODLO_WBUDOWANY)
                wynik[wpis.klucz] = wpis
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        log.warning("Nie udało się wczytać wbudowanego słownika obrębów: %s", exc)
        wynik = {}
    _KRAJOWY_CACHE = wynik
    return wynik


def oznaczenie_wbudowane(teryt_gminy: str | None, numer: str | None) -> str | None:
    """Oznaczenie z wbudowanego słownika albo None."""
    if not teryt_gminy or not numer:
        return None
    wpis = wczytaj_krajowy().get(f"{teryt_gminy}.{numer}")
    return wpis.oznaczenie if wpis else None


def zapisz(sciezka: Path, wpisy: Iterable[WpisObrebu]) -> int:
    """Zapis atomowy (tmp + replace) -- plik bywa edytowany z UI podczas pracy."""
    lista = sorted(wpisy, key=lambda w: (w.teryt_gminy, w.numer_obrebu))
    tmp = sciezka.with_suffix(sciezka.suffix + ".tmp")
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(KOLUMNY)
        for wpis in lista:
            w.writerow([wpis.teryt_gminy, wpis.numer_obrebu, wpis.oznaczenie,
                        wpis.gmina, wpis.zrodlo])
    tmp.replace(sciezka)
    return len(lista)


def scal(stare: dict[str, WpisObrebu], nowe: Iterable[WpisObrebu]) -> dict[str, WpisObrebu]:
    """Dołóż wpisy do słownika, NIE ruszając poprawek ręcznych.

    Kolejne generowanie z nowszego zrzutu EGIB ma uzupełniać braki i odświeżać
    to, co samo wcześniej wpisało -- ale nie ma prawa cofnąć decyzji operatora.
    """
    wynik = dict(stare)
    for wpis in nowe:
        obecny = wynik.get(wpis.klucz)
        if obecny is not None and obecny.reczny:
            continue
        wynik[wpis.klucz] = wpis
    return wynik


def oznaczenie_wnosi_informacje(oznaczenie: str, teryt: str, numer: str) -> bool:
    """Czy oznaczenie z EGIB mówi cokolwiek więcej niż sam numer obrębu.

    Zrzuty EGIB bywają wypełnione bez sensu i podstawienie takiego „oznaczenia"
    pogorszyłoby dane (sprawdzone na zrzucie 2026-08-06):
    - powiat sieradzki ma w `NAZWA_OBREBU` identyfikator: „(101401_1.0001)",
    - powiat bełchatowski numer bez zer: „01" dla obrębu „0001",
    - powiat drawski dokładnie ten numer: „0001".

    Przechodzą prawdziwe oznaczenia: „B-1", „6-06-15", „RASZEW PIASKI",
    „OBRĘB 2", „Czarna Woda".
    """
    czysty = re.sub(r"[^0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]", "", oznaczenie)
    if not czysty:
        return False
    if teryt and re.sub(r"[^0-9A-Za-z]", "", teryt) in re.sub(r"[^0-9A-Za-z]", "", oznaczenie):
        return False
    # Sam numer, także zapisany bez zer wiodących („01" dla obrębu „0001").
    return czysty != numer and czysty.lstrip("0") != numer.lstrip("0")


def rozbij_identyfikator(ident: str | None) -> tuple[str, str] | None:
    """`106102_9.0042.44/1` -> („106102_9", „0042"). None, gdy identyfikator
    jest niepełny -- w danych źródłowych trafiają się same numery działek
    („133/12"), rzędu 0,1% rekordów."""
    m = _IDENT_RE.match(ident or "")
    return (m.group(1), m.group(2)) if m else None


def zastosuj_do_bazy(conn, slownik: dict[str, WpisObrebu], *, wykonaj: bool = True) -> dict:
    """Przepisz oznaczenia ze słownika do kolumny `obreb` w plots, buildings
    i locals (te ostatnie od schema v11 -- identyfikator lokalu niesie obręb
    w tym samym segmencie, więc pominięcie ich zostawiałoby transakcje
    lokalowe z samym numerem).

    Numer NIE ginie -- zostaje w `obreb_numer` (schema v9), więc po zastosowaniu
    wyszukiwanie działa i po „B-24", i po „0042".

    Zwraca statystyki; z `wykonaj=False` niczego nie zapisuje (podgląd dla UI).
    """
    stat = {"zmienione": 0, "bez_dopasowania": 0, "tabele": {}}
    for tabela, kol in (("plots", "identyfikator_dzialki"),
                        ("buildings", "identyfikator_budynku"),
                        ("locals", "identyfikator_lokalu")):
        do_zmiany: list[tuple[str, int]] = []
        bez = 0
        for pk, ident, obecny in conn.execute(
            f"SELECT id, {kol}, obreb FROM {tabela} WHERE {kol} IS NOT NULL"
        ).fetchall():
            para = rozbij_identyfikator(ident)
            if para is None:
                continue
            wpis = slownik.get(f"{para[0]}.{para[1]}")
            if wpis is None:
                bez += 1
                continue
            if wpis.oznaczenie != obecny:
                do_zmiany.append((wpis.oznaczenie, pk))
        if wykonaj and do_zmiany:
            conn.executemany(f"UPDATE {tabela} SET obreb = ? WHERE id = ?", do_zmiany)
        stat["tabele"][tabela] = {"zmienione": len(do_zmiany), "bez_dopasowania": bez}
        stat["zmienione"] += len(do_zmiany)
        stat["bez_dopasowania"] += bez

    if wykonaj and stat["zmienione"]:
        # tx_cache trzyma oznaczenie reprezentanta transakcji i musi pójść za
        # zmianą, inaczej tabela i filtry pokazują starą wartość. Aktualizujemy
        # WYŁĄCZNIE kolumnę `obreb`, skorelowanym podzapytaniem po
        # `idx_plots_id_rcn` / `idx_buildings_id_rcn` / `idx_locals_id_rcn`.
        # Lokale są ostatnie w COALESCE: mają dołożyć obręb transakcjom, które
        # nie mają działki ani budynku, a nie odbierać reprezentanta tym, które mają.
        #
        # ⚠️ NIE wołać tu `refresh_tx_cache(conn, None)`: przelicza cały cache
        # (adresy, powierzchnie, liczniki, centroidy) i na Warszawie -- 600 tys.
        # transakcji -- idzie w kwadranse, a to endpoint wołany z UI.
        conn.execute(
            """
            UPDATE tx_cache SET obreb = COALESCE(
                (SELECT MIN(p.obreb) FROM plots p
                  WHERE p.id_rcn = tx_cache.id_rcn AND p.obreb IS NOT NULL),
                (SELECT MIN(b.obreb) FROM buildings b
                  WHERE b.id_rcn = tx_cache.id_rcn AND b.obreb IS NOT NULL),
                (SELECT MIN(l.obreb) FROM locals l
                  WHERE l.id_rcn = tx_cache.id_rcn AND l.obreb IS NOT NULL),
                obreb)
            """
        )
        conn.commit()
    return stat


def obreby_z_bazy(conn) -> list[tuple[str, str, str | None]]:
    """Pary (teryt, numer, obecne oznaczenie) faktycznie występujące w bazie.

    To lista, którą UI pokazuje operatorowi: dokładnie te obręby, dla których
    warto mieć oznaczenie -- nie cały powiat z EGIB.
    """
    sql = """
        SELECT DISTINCT teryt_gminy, obreb_numer, obreb FROM (
            SELECT teryt_gminy, obreb_numer, obreb FROM plots
             WHERE teryt_gminy IS NOT NULL AND obreb_numer IS NOT NULL
            UNION
            SELECT teryt_gminy, obreb_numer, obreb FROM buildings
             WHERE teryt_gminy IS NOT NULL AND obreb_numer IS NOT NULL
            UNION
            SELECT teryt_gminy, obreb_numer, obreb FROM locals
             WHERE teryt_gminy IS NOT NULL AND obreb_numer IS NOT NULL
        ) ORDER BY teryt_gminy, obreb_numer
    """
    return [(r[0], r[1], r[2]) for r in conn.execute(sql).fetchall()]
