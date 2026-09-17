"""Dane osobowe notariusza poza zasięgiem użytkowników bez uprawnień admina.

Akt notarialny w RCN niesie `tworcaDokumentu` -- imię i nazwisko notariusza
(w GML zwykle „NOTARIUSZ ANNA KOWALSKA"). Na instancji dostępnej publicznie
(`RCN_PUBLIC_READONLY`) albo z kontem readonly nie ma powodu, żeby nazwisko
wychodziło do przeglądającego: **numer repertorium zostaje**, bo identyfikuje
akt, a osoby nie wskazuje.

Zasada: maskujemy po stronie SERWERA, we wszystkich odpowiedziach, nie stylami
w przeglądarce. Ukrycie w CSS albo `x-show` byłoby pozorne -- dane i tak
jechałyby w JSON-ie i w pliku eksportu.

Kto widzi: wyłącznie roli `admin` (właściciel instancji, tryb desktopowy na
loopbacku, zalogowany operator). Gość i konto readonly dostają puste pole,
a interfejs po prostu nie rysuje wtedy wiersza „Notariusz".

Drogi, którymi to pole wychodziło (stan 2026-09-17):
- `GET /transactions/details/...` -- kolumna `tworca_dokumentu` ORAZ kopia
  w `extra` (surowe atrybuty z GML, klucz „twórca dokumentu"),
- `export.csv` -- kolumna `tworca_dokumentu`,
- `export.xlsx` -- kolumna „Notariusz" budowana z atrybutów,
- `GET /transactions/note/...` -- **seed notatki** (markdown budowany z danych
  transakcji) miał wiersz „Twórca dokumentu"; zgłoszenie 2026-09-17.
Warstwy GeoJSON i kontekst wtyczek notariusza nie dostają (sprawdzone).
"""
from __future__ import annotations

import re
from typing import Any

# Nazwa kolumny w tabeli `transakcje` i klucz w `attributes_json` -- to samo
# pole w dwóch postaciach, obie trzeba wyczyścić.
KOLUMNA_NOTARIUSZ = "tworca_dokumentu"
ATRYBUT_NOTARIUSZ = "twórca dokumentu"
# Zapis bez polskich znaków trafia się w starszych plikach; kosztuje jedno
# sprawdzenie, a chroni przed cichym przeciekiem.
ATRYBUTY_NOTARIUSZ = (ATRYBUT_NOTARIUSZ, "tworca dokumentu", "tworcaDokumentu")


def ukrywac_notariusza(ctx: Any) -> bool:
    """Czy temu żądaniu należy ukryć nazwisko notariusza.

    Bierze rolę z `AuthContext`. Gdy handler dostał zwykły string (starsze
    sygnatury `_: str = Depends(require_auth)`), zakładamy BRAK uprawnień --
    bezpieczniejsza strona pomyłki przy instancji publicznej.
    """
    return getattr(ctx, "role", None) != "admin"


def bez_notariusza(dane: dict) -> dict:
    """Kopia słownika z wyczyszczonym nazwiskiem notariusza.

    Kolumna zostaje (interfejs i nagłówki plików mają stały kształt), ale jest
    pusta -- `x-show` w tabeli sam ukryje wtedy wiersz „Notariusz".
    Klucze z surowych atrybutów GML są usuwane w całości.
    """
    wynik = dict(dane)
    if KOLUMNA_NOTARIUSZ in wynik:
        wynik[KOLUMNA_NOTARIUSZ] = None
    for klucz in ATRYBUTY_NOTARIUSZ:
        wynik.pop(klucz, None)
    return wynik


def bez_notariusza_w_wierszach(wiersze: list[dict]) -> list[dict]:
    return [bez_notariusza(w) for w in wiersze]


# Wiersz seeda notatki: „- **Twórca dokumentu:** NOTARIUSZ ...". Wzorzec bierze
# obie pisownie (z polskimi znakami i bez), bo seed jest budowany z tego samego
# pola, które w starszych plikach bywa zapisane różnie.
_WIERSZ_NOTARIUSZA = re.compile(
    r"^[ \t]*[-*][ \t]*\*\*[ \t]*(?:Twórca|Tworca)[ \t]+dokumentu[ \t]*:?\*\*.*$",
    re.IGNORECASE | re.MULTILINE,
)


def bez_notariusza_w_notatce(tekst: str) -> str:
    """Treść notatki bez wiersza „Twórca dokumentu" z seeda.

    Notatka to markdown pisany przez operatora, ale jej wersja domyślna (seed)
    jest generowana z danych transakcji i do 2026-09-17 niosła nazwisko. Rzecz
    dotyczy dwóch przypadków naraz: seeda renderowanego w locie ORAZ notatki,
    którą admin zapisał „tak jak była" -- ta druga siedzi już w bazie, więc
    samo poprawienie generatora by nie wystarczyło.

    Ograniczenie, świadome: filtrujemy WIERSZ ZE ZNANEGO SZABLONU, nie dowolny
    tekst. Nazwisko wpisane przez operatora w zdaniu („rozmawiałem z mec. X")
    zostaje -- to treść własna notatki, a zgadywanie nazwisk w wolnym tekście
    dawałoby złudzenie ochrony i psuło notatki.
    """
    if not tekst:
        return tekst
    return _WIERSZ_NOTARIUSZA.sub("", tekst)
