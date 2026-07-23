# Wymiana powiatów między znajomymi 🔁

Każdy powiat w RCN Workshop to **jeden folder z danymi** — samowystarczalny. Żeby podzielić się powiatem z koleżanką/kolegą, **wystarczy wysłać ten folder**. Po drugiej stronie wrzuca się go do swojej aplikacji i gotowe. Nie trzeba nic instalować od nowa.

---

## Gdzie są powiaty na dysku
Powiaty leżą w folderze **`data\workspaces`** Twojej aplikacji:
- jeśli instalowałeś **instalatorem** → użyj skrótu w menu Start **„RCN Workshop — folder z powiatami"** (albo: `%LOCALAPPDATA%\RCN-Workshop\data\workspaces`),
- jeśli rozpakowałeś **ZIP** → to po prostu `…\RCN-Workshop\data\workspaces`.

W środku każdy powiat to osobny podfolder o długiej nazwie-kodzie (np. `3b7e51a0-24df-…`) albo o zwykłej nazwie (np. `Kutno`). Nazwa powiatu (np. „Piotrków") siedzi **w środku** pliku — dlatego po przeniesieniu folder sam się podpisze poprawnie w aplikacji.

---

## Jak WYSŁAĆ powiat komuś
1. Otwórz folder `data\workspaces`.
2. **Masz tylko jeden powiat?** To ten jedyny podfolder — go wysyłasz.
   **Masz kilka?** Musisz rozpoznać właściwy po kodzie (patrz ramka niżej).
3. Kliknij podfolder prawym → **„Kompresuj do pliku ZIP"**.
4. Wyślij ZIP: mały powiat e-mailem/WeTransferem, większy (kilkaset MB) przez Dysk Google / Dropbox.

> 🔎 **Nie wiesz, który folder to który powiat?** Najprościej poproś osobę, która przygotowała dane (administrator), albo wyślij powiat, którego jesteś pewien (gdy masz tylko jeden — nie ma dylematu). *W planach aplikacji jest przycisk „Pobierz powiat (ZIP)", który to uprości.*

> 📸 *[screen: folder workspaces → prawy przycisk → Kompresuj do ZIP]*

## Jak WGRAĆ powiat od kogoś
1. Rozpakuj otrzymany ZIP — w środku jest podfolder o nazwie-kodzie.
2. Skopiuj **cały ten podfolder** do swojego `data\workspaces`.
3. W przeglądarce **odśwież stronę (F5)** — powiat pojawi się na liście, z poprawną nazwą.

Gotowe — masz u siebie pełną kopię: tabelę, mapę, filtry, analizę. Działa offline.

> 📸 *[screen: lista z nowym powiatem po odświeżeniu]*

---

## ⚠ Zasady bezpiecznej wymiany (przeczytaj!)
- **Notatki są w środku pliku powiatu.** Jeśli masz już powiat „Piotrków" z **własnymi notatkami** i wgrasz cudzy „Piotrków" — **nadpiszesz swoje notatki**. Wgrywaj tylko powiaty, których jeszcze nie masz (albo w których nie masz swoich notatek).
- **Nie pracujcie na tym samym powiecie „na żywo" we dwoje.** To pojedynczy plik — wymiana to przekazanie kopii, nie wspólna baza online.
- **Chmura służy do przesłania, nie do pracy.** Nie trzymaj folderu `data` w synchronizowanym OneDrive/Dysku Google z **otwartą** aplikacją — synchronizacja otwartego pliku może go uszkodzić. Pracuj na kopii lokalnej, chmury używaj tylko do wysłania/odebrania.
- **Aktualizacja powiatu = nadpisanie folderu** (cały powiat naraz). Umówcie się, kto „prowadzi" dany powiat i rozsyła nowsze wersje.

> 💡 W przyszłości aplikacja rozdzieli **dane powiatu** (od administratora, aktualizowane) od **Twoich notatek** (osobny lokalny plik) — wtedy aktualizacja powiatu **nie ruszy Twoich notatek**. Na razie obowiązuje zasada powyżej.

---

## Coś nie wyszło? 🛠
| Problem | Co zrobić |
|---|---|
| Wgrałem folder, a powiatu nie ma | Sprawdź, czy trafił do `data\workspaces` i czy ma w środku plik `workspace.sqlite`; odśwież stronę (F5) |
| Nie wiem, który folder wysłać | Wyślij, gdy masz tylko jeden powiat; przy kilku — zapytaj administratora |
| Plik za duży na e-mail | Użyj Dysku Google / Dropbox / WeTransfer |
| Zniknęły mi notatki po wgraniu | Cudzy plik nadpisał Twój — patrz „Zasady" wyżej (odzyskanie tylko z kopii) |

W razie kłopotu zrób zrzut ekranu (`Win + Shift + S`) i wyślij administratorowi.
