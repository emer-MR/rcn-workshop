"""GML-e spakowane w `.zip` -- rozpakowanie do katalogu uploadów.

Źródła RCN rozsyłają dane zarówno luzem, jak i w archiwach: powiat przychodzi
zwykle jako jeden `.zip` z kilkoma GML-ami (po gminie albo po okresie). Do tej
pory trzeba było rozpakować je ręcznie, bo formularz przyjmował wyłącznie
`.gml`/`.xml`.

Dwie właściwości realnych archiwów, na które ten moduł jest przygotowany:

- **plik GML bywa BEZ rozszerzenia.** W zbiorze krajowym trafiły się takie
  paczki co najmniej dwa razy (powiat otwocki: wpis `Wszystkie gminy`,
  przemyski: `RCN_1813`). Nazwa nic wtedy nie mówi, więc rozstrzyga początek
  zawartości -- deklaracja XML albo korzeń `FeatureCollection`.
- **archiwum niesie też inne pliki** (PDF-y, `readme.txt`, katalogi): biorą się
  z nich tylko kandydaci wyglądający na GML, reszta jest pomijana bez błędu.

Bezpieczeństwo: nazwy wpisów są sprowadzane do samej nazwy pliku (żadnych
ścieżek i `..`), a rozmiar jest pilnowany podwójnie -- najpierw po nagłówkach
archiwum, potem po faktycznie zapisanych bajtach, bo nagłówek potrafi kłamać.
"""
from __future__ import annotations

import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROZSZERZENIA_GML = (".gml", ".xml")
# Ile bajtów początku pliku oglądamy, szukając śladu XML-a.
PROBKA_BAJTOW = 4096
_SLADY_XML = (b"<?xml", b"<gml:", b"<FeatureCollection", b"<wfs:", b"<rcn:")


class BlednaPaczka(Exception):
    """Archiwum nie nadaje się do importu (uszkodzone albo bez GML-i)."""


class PaczkaZaDuza(Exception):
    """Zawartość archiwum przekracza limit uploadu."""


@dataclass(frozen=True)
class WypakowanyGml:
    nazwa_oryginalna: str   # nazwa wpisu w archiwum, do pokazania w historii importów
    sciezka: Path           # plik zapisany w katalogu uploadów
    rozmiar: int


def wyglada_na_gml(nazwa: str, poczatek: bytes) -> bool:
    """Czy wpis archiwum jest kandydatem na GML.

    Rozszerzenie rozstrzyga, gdy jest. Gdy go nie ma -- decyduje zawartość,
    bo część starostw pakuje pliki bez rozszerzenia w ogóle.
    """
    sufiks = Path(nazwa).suffix.lower()
    if sufiks in ROZSZERZENIA_GML:
        return True
    if sufiks:
        return False
    return any(slad in poczatek for slad in _SLADY_XML)


def _wpisy_do_rozwazenia(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    wynik = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        nazwa = Path(info.filename).name
        # Śmieci macOS-a i pliki ukryte: „__MACOSX/._plik.gml" wygląda jak GML,
        # a jest widełkami zasobów -- 4 KB binariów, które parser odrzuci.
        if not nazwa or nazwa.startswith("._") or nazwa.startswith("."):
            continue
        if info.filename.startswith("__MACOSX/"):
            continue
        wynik.append(info)
    return wynik


def wypakuj_gml(
    zip_path: Path,
    katalog: Path,
    *,
    nazwa_docelowa: Callable[[str], str],
    limit_bajtow: int | None = None,
) -> list[WypakowanyGml]:
    """Wypakuj z archiwum wszystkie pliki wyglądające na GML.

    `nazwa_docelowa` dostaje nazwę wpisu i zwraca nazwę pliku na dysku -- dzięki
    temu wołający narzuca własną konwencję (znacznik czasu + uuid), a ten moduł
    nie musi znać reguł nazewnictwa uploadów.

    `limit_bajtow=None` znaczy brak limitu (tryb desktop, `RCN_MAX_UPLOAD_MB=0`).
    """
    katalog.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise BlednaPaczka("Uszkodzone archiwum .zip") from exc

    wypakowane: list[WypakowanyGml] = []
    try:
        kandydaci: list[zipfile.ZipInfo] = []
        for info in _wpisy_do_rozwazenia(zf):
            with zf.open(info) as strumien:
                poczatek = strumien.read(PROBKA_BAJTOW)
            if wyglada_na_gml(info.filename, poczatek):
                kandydaci.append(info)

        if not kandydaci:
            raise BlednaPaczka(
                "Archiwum nie zawiera plików GML (szukane: .gml, .xml albo plik XML "
                "bez rozszerzenia)"
            )

        # Pierwsze sito: deklarowane rozmiary. Tanie, odrzuca paczkę zanim
        # cokolwiek wyląduje na dysku.
        deklarowane = sum(i.file_size for i in kandydaci)
        if limit_bajtow is not None and deklarowane > limit_bajtow:
            raise PaczkaZaDuza(
                f"Rozpakowana zawartość to {deklarowane / (1024 * 1024):.0f} MB "
                f"i przekracza limit"
            )

        zapisane_razem = 0
        for info in sorted(kandydaci, key=lambda i: i.filename):
            nazwa_wpisu = Path(info.filename).name
            cel = katalog / nazwa_docelowa(nazwa_wpisu)
            rozmiar = 0
            with zf.open(info) as zrodlo, open(cel, "wb") as plik:
                while True:
                    kawalek = zrodlo.read(1024 * 1024)
                    if not kawalek:
                        break
                    rozmiar += len(kawalek)
                    zapisane_razem += len(kawalek)
                    # Drugie sito: nagłówek ZIP-a potrafi deklarować mniej, niż
                    # wpis faktycznie rozpakowuje (klasyczna „zip bomb").
                    if limit_bajtow is not None and zapisane_razem > limit_bajtow:
                        plik.close()
                        cel.unlink(missing_ok=True)
                        raise PaczkaZaDuza(
                            "Rozpakowana zawartość przekracza limit "
                            "(archiwum deklarowało mniejszy rozmiar)"
                        )
                    plik.write(kawalek)
            wypakowane.append(WypakowanyGml(nazwa_wpisu, cel, rozmiar))
        return wypakowane
    except Exception:
        # Nieudane rozpakowanie nie zostawia połowy plików w uploads/.
        for plik_do_sprzatniecia in wypakowane:
            plik_do_sprzatniecia.sciezka.unlink(missing_ok=True)
        raise
    finally:
        zf.close()
