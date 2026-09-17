# Historia zmian

Format: [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/),
wersjonowanie [SemVer](https://semver.org/lang/pl/) z sufiksem przedpremierowym.

## [0.2.0-beta.8] - 2026-09-17

### Naprawione

- Po wejściu na obszar roboczy widać informację o ładowaniu zamiast komunikatu
  o braku wyników. Przy dużym zbiorze dane pojawiają się po kilkunastu sekundach.
- Ograniczenie danych notariusza z wersji beta.5 objęło też domyślną treść
  notatki. Dotyczy wyłącznie instancji sieciowej.
- Opis przy zakładaniu obszaru roboczego zapowiadał tryb importu zmieniony
  w poprzednim wydaniu.

### Dodane

- Pomiar odległości i warstwa punktów użyteczności (POI) w oknie mapy otwieranym
  z tabeli - dotąd były tylko na dużej mapie. Kliknięcie w punkt mierzy do jego
  pozycji i podpisuje pomiar nazwą punktu.

## [0.2.0-beta.7] - 2026-09-14

### Dodane

- Oznaczenia obrębów („B-42") działają od razu po instalacji - aplikacja ma
  wbudowany słownik dla całej Polski (377 powiatów, blisko 50 tysięcy obrębów).
  Wcześniej wymagało to osobnego pliku obok bazy.
- Bazy z wcześniejszych wersji dostają oznaczenia przy pierwszym uruchomieniu
  po aktualizacji. Numer zostaje obok, więc wyszukiwanie działa w obu formach.

### Zmienione

- Oznaczenie poprawione ręcznie w ustawieniach obszaru roboczego ma
  pierwszeństwo i nie zostanie nadpisane przy aktualizacji.

## [0.2.0-beta.6] - 2026-09-14

### Naprawione

- Ograniczenie danych notariusza z wersji beta.5 objęło też szczegóły obiektów
  transakcji. Dotyczy wyłącznie instancji sieciowej.

## [0.2.0-beta.5] - 2026-09-14

### Zmienione

- Na instancji wystawionej w sieci dane notariusza są pokazywane wyłącznie
  administratorowi - w tabeli, w eksportach i w instrukcji. Numer repertorium
  zostaje, bo identyfikuje akt, a nie osobę.
- Instalacja na własnym komputerze działa bez zmian: widać komplet danych.

## [0.2.0-beta.4] - 2026-09-14

### Dodane

- Obręby rozróżniane jednoznacznie. Numer obrębu powtarza się między jednostkami
  ewidencyjnymi, więc w mieście z dzielnicami jedna pozycja katalogu mieszała
  kilka różnych obrębów. Filtr używa teraz pary (jednostka, numer).
- Oznaczenia urzędowe obrębów („B-24") obok numerów („0024"), z pliku
  `<nazwa>.obreby.csv` obok bazy. Wyszukiwarka rozumie obie formy, także zapis
  bez separatora („b24"). Plik da się poprawić ręcznie.
- Lokale też mają obręb - wcześniej transakcja obejmująca wyłącznie lokal
  wypadała z filtrów i z katalogu.
- Import spakowanych GML-i (`.zip`, także z plikami bez rozszerzenia). Każdy
  plik z paczki to osobny import z własnym paskiem postępu.
- Tryb publicznego odczytu (`RCN_PUBLIC_READONLY=1`): gość bez logowania ogląda
  dane, administrator loguje się na `/login`. W tym trybie strona pomocy jest
  dostępna publicznie.

### Zmienione

- Import domyślnie w trybie `delta`, także przy zakładaniu obszaru roboczego
  i przy wgrywaniu archiwum. Archiwum z kilkoma plikami idzie `deltą` zawsze.
- Sortowanie kolumny „Obręb" po jednostce i numerze, nie po tekście oznaczenia:
  „B-2" wypada teraz przed „B-10".
- Wyszukiwanie po obrębie przegląda wszystkie obiekty transakcji, nie tylko
  pierwszy z nich.

### Naprawione

- Import fragmentu zbioru nie ukryje reszty bazy. Wgranie pliku obejmującego
  wycinek okresu w trybie `snapshot` potrafiło oznaczyć większość bazy jako
  wycofaną z portalu - dane zostawały na dysku, ale znikały z tabeli i z mapy.
  Import odmawia teraz wycofania nieproporcjonalnego do zawartości pliku.
- Nieudany upload sprząta po sobie i nie blokuje nazwy przy kolejnej próbie.

### Migracja danych

Nowe wersje schematu wykonują się automatycznie przy pierwszym uruchomieniu.
Przy bardzo dużych zbiorach pierwsze otwarcie listy potrwa dłużej niż zwykle -
to jednorazowy koszt.

## [0.2.0-beta.3] - 2026-08-06

### Dodane
- Limit rozmiaru uploadu sterowany `RCN_MAX_UPLOAD_MB`; `0` znosi limit
  (domyślnie w trybie desktopowym, gdzie nie ma czego chronić).

### Naprawione
- Warstwy GPKG zapisane w urzędowej kolejności osi rysują się we właściwym
  miejscu, a nie kilkaset kilometrów obok.

## [0.2.0-beta.2] - 2026-07-30

### Zmienione
- Cały frontend serwowany lokalnie (`static/vendor/`), bez zewnętrznych CDN-ów -
  aplikacja działa też tam, gdzie firewall lub antywirus blokuje pobieranie
  bibliotek z sieci.

### Dodane
- Strona `/diagnostyka`: sprawdza łączność i lokalny magazyn przeglądarki,
  generuje raport do wklejenia w zgłoszeniu.

## [0.2.0-beta.1] - 2026-07-22

- Pierwsze wydanie z numerem wersji widocznym w interfejsie, instalatorem dla
  Windows i stroną pobierania.
