# RCN Workshop — szybki start na Windows 🪟

Instrukcja dla użytkownika. Nie musisz znać się na informatyce — wystarczy klikać po kolei.

Aplikacja działa **u Ciebie na komputerze** (nic nie wysyła do internetu). Otwiera się w zwykłej przeglądarce, jak strona — tyle że „strona" jest na Twoim laptopie.

---

## Czego potrzebujesz
- Komputer z Windows 10 lub 11
- Plik **`RCN-Workshop.zip`**, który dostałeś
- Połączenie z internetem **tylko przy pierwszym uruchomieniu** (potem działa offline)

---

## Krok 1 — Rozpakuj
1. Kliknij ZIP prawym przyciskiem → **„Wyodrębnij wszystkie…"**.
2. Wypakuj do prostego miejsca, np. **`C:\RCN-Workshop`**.
   - ⚠ **Nie wypakowuj** do folderu OneDrive / „Dokumenty" synchronizowanych w chmurze — może to uszkodzić dane. Najlepiej prosto na dysk `C:`.

> 📸 *[screen: rozpakowany folder z plikiem `start-windows-uv.bat`]*

## Krok 2 — Uruchom (pierwszy raz potrwa kilka minut)
1. Wejdź do folderu i kliknij dwa razy **`start-windows-uv.bat`**.
2. Pojawi się niebieski ekran **„System Windows ochronił Twój komputer"**:
   - kliknij **„Więcej informacji"**,
   - potem **„Uruchom mimo to"**.
   - (To normalne — plik jest nasz, po prostu nie ma „pieczątki" producenta.)

> 📸 *[screen: SmartScreen → „Więcej informacji" → „Uruchom mimo to"]*

3. Otworzy się **czarne okno** — to działający program. Za pierwszym razem pobiera potrzebne pliki (kilka minut, lecą napisy). **Poczekaj i nie zamykaj tego okna.**

> 📸 *[screen: czarne okno konsoli z napisami]*

## Krok 3 — Zaloguj się
1. Po chwili **przeglądarka otworzy się sama** na adresie `http://127.0.0.1:8000`.
2. Wyskoczy okienko logowania — wpisz:
   - **Użytkownik:** `admin`
   - **Hasło:** `change-me` *(albo to, które podał Ci administrator)*
3. Gotowe — widzisz aplikację. 🎉

> 📸 *[screen: okienko logowania przeglądarki + ekran główny aplikacji]*

## Krok 4 — Wczytaj dane powiatu
Po zalogowaniu lista jest pusta. Masz dwie możliwości:

**A. Dostałeś gotowy powiat** (folder z danymi):
1. Skopiuj otrzymany folder do: `C:\RCN-Workshop\data\workspaces\`
2. Odśwież stronę w przeglądarce (F5) — powiat pojawi się na liście.

**B. Masz własny plik GML** (z Geoportalu/urzędu):
1. W aplikacji: **➕ Utwórz workspace** → nadaj nazwę → **wgraj plik GML** → poczekaj na import.

> 📸 *[screen: lista workspace'ów z wczytanym powiatem]*

---

## Codzienne używanie
- **Włączanie:** dwuklik w `start-windows-uv.bat` → przeglądarka otworzy się w kilka sekund (już bez internetu).
- **Wyłączanie:** zamknij **czarne okno** (albo wciśnij w nim `Ctrl + C`).
- 💡 **Dopóki czarne okno jest otwarte — aplikacja działa. Zamknięcie okna ją wyłącza.** Nie zamykaj go w trakcie pracy.

> 💡 Wskazówka: kliknij `start-windows-uv.bat` prawym → **„Utwórz skrót"** → przeciągnij skrót na pulpit. Wtedy włączasz aplikację jednym kliknięciem z pulpitu.

---

## Coś nie działa? 🛠
| Problem | Co zrobić |
|---|---|
| Ekran „System Windows ochronił…" | „Więcej informacji" → „Uruchom mimo to" (Krok 2) |
| Czarne okno mignęło i zniknęło | Uruchom ponownie; jeśli dalej znika — zrób screen napisu w oknie i wyślij administratorowi |
| Przeglądarka nie otworzyła się sama | Otwórz ją ręcznie i wpisz adres: `http://127.0.0.1:8000` |
| Strona pokazuje „nie można połączyć" | Poczekaj — przy pierwszym razie pobieranie trwa kilka minut. Czarne okno musi być otwarte |
| Zapomniałem hasła | Domyślnie `admin` / `change-me`; jeśli zmienione — zapytaj administratora |
| Lista powiatów pusta | Wczytaj dane — Krok 4 |

W razie kłopotu zrób **zrzut ekranu** (klawisz `PrtScn` lub `Win + Shift + S`) i wyślij administratorowi — najszybciej pomoże.

---

*Aplikacja działa w pełni lokalnie. Twoje dane i notatki zostają na Twoim komputerze.*
