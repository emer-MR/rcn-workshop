# Historia zmian

Format: [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/),
wersjonowanie [SemVer](https://semver.org/lang/pl/) z sufiksem przedpremierowym.

## [0.2.0-beta.4] - 2026-09-14

### Dodane

- **Obręby rozróżniane jednoznacznie.** Numer obrębu jest unikalny tylko w ramach
  jednostki ewidencyjnej, więc w mieście podzielonym na dzielnice jedna pozycja
  katalogu („0024") reprezentowała kilka różnych obrębów, a filtr po niej zwracał
  transakcje z kilku części miasta. Filtr używa teraz pary (jednostka, numer).
- **Oznaczenia urzędowe obrębów** („B-24") obok numerów („0024"), wczytywane
  z pliku `<nazwa>.obreby.csv` leżącego obok bazy. Wyszukiwarka rozumie obie
  formy, także zapis bez separatora („b24"). Plik jedzie w paczce workspace'u,
  waży kilkanaście kilobajtów i da się go poprawić ręcznie (arkusz albo edytor
  w ustawieniach workspace'u).
- **Lokale też mają obręb.** Identyfikator lokalu niesie go w tym samym miejscu
  co identyfikator działki. Wcześniej transakcja obejmująca wyłącznie lokal nie
  miała obrębu nigdzie i wypadała z filtrów oraz z katalogu.
- **Import spakowanych GML-i.** Tam, gdzie wcześniej wchodził tylko `.gml`/`.xml`,
  można wgrać archiwum `.zip` - także takie, w którym pliki nie mają rozszerzenia
  (rozpoznawane po zawartości). Każdy plik z paczki to osobny import z własnym
  paskiem postępu.
- **Tryb publicznego odczytu** (`RCN_PUBLIC_READONLY=1`): gość bez logowania
  ogląda dane, a administrator loguje się na `/login`. Uwaga: w tym trybie
  strona pomocy jest dostępna publicznie.

### Zmienione

- **Import domyślnie w trybie `delta`** także przy zakładaniu workspace'u
  i przy wgrywaniu archiwum. Tryb `snapshot` wycofuje z bazy wszystko, czego nie
  ma w importowanym pliku (w zakresie jego dat), więc przy kilku plikach każdy
  kolejny kasował dorobek poprzedniego. Archiwum z więcej niż jednym plikiem
  idzie `deltą` zawsze, z komunikatem.
- **Sortowanie kolumny „Obręb"** po jednostce i numerze, nie po tekście
  oznaczenia: „B-2" wypada teraz przed „B-10", a nie po nim.
- **Wyszukiwanie po obrębie przegląda wszystkie obiekty transakcji.** Transakcja
  z działkami w kilku obrębach była wcześniej znajdowana tylko pod jednym z nich.

### Naprawione

- **Import fragmentu zbioru nie ukryje reszty bazy.** Wgranie pliku obejmującego
  wycinek okresu w trybie `snapshot` potrafiło oznaczyć większość bazy jako
  wycofaną z portalu - dane pozostawały na dysku, ale znikały z tabeli i z mapy.
  Import odmawia teraz wycofania nieproporcjonalnego do zawartości pliku, zakres
  snapshotu liczy z percentyli (jedna błędna data nie rozciąga go na stulecia),
  a każde wycofanie zapisuje, który import je spowodował.
- **Nieudany upload sprząta po sobie** - nie zostaje pusty workspace blokujący
  nazwę przy kolejnej próbie.

### Migracja danych

Wersje schematu v9, v10 i v11 wykonują się **automatycznie przy pierwszym
uruchomieniu** i nie wymagają działania użytkownika. Przy bardzo dużych zbiorach
(setki workspace'ów albo baza metropolii) pierwsze otwarcie listy potrwa dłużej
niż zwykle - to jednorazowy koszt uzupełnienia numerów obrębów.

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
