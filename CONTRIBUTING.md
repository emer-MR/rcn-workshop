# Kontrybucje

Dzięki za zainteresowanie rozwojem RCN Workshop!

## Zasady ogólne

- Język projektu to **polski** (kod, commity, UI, dokumentacja). Angielski jest
  OK w nazwach technicznych i identyfikatorach.
- Konwencja commitów/PR-ów: `feat(scope): opis`, `fix(scope): opis`, `docs: opis`.
- Przed PR-em uruchom testy i lint:
  ```bash
  uv sync --extra dev
  uv run pytest
  uv run ruff check .   # nowe/zmieniane pliki nie powinny dodawać błędów
  ```
- Do zmian nietrywialnych załóż najpierw Issue z opisem problemu/propozycji.

## Zgłaszanie błędów

W Issue podaj: wersję (commit/tag), sposób uruchomienia (Windows lokalnie /
Docker), kroki reprodukcji i — jeśli to możliwe — zanonimizowany fragment
danych wejściowych. **Nie załączaj rzeczywistych danych RCN** (rejestr ma
ograniczony dostęp) ani danych osobowych.

## Licencja wkładu (CLA)

Projekt jest licencjonowany na **AGPL-3.0** z zastrzeżeniem możliwości
dual-licensingu (licencja komercyjna udzielana przez autora projektu).

Wysyłając pull request oświadczasz, że:

1. jesteś autorem wkładu (lub masz prawo go wnieść),
2. udzielasz autorowi projektu **wieczystej, nieodwołalnej, niewyłącznej,
   nieodpłatnej licencji** na korzystanie z Twojego wkładu, w tym na jego
   relicencjonowanie na innych warunkach (dual-licensing),
3. Twój wkład jest udostępniany społeczności na licencji AGPL-3.0.

Jeśli nie zgadzasz się z punktem 2, zaznacz to w opisie PR-a — poszukamy
innego rozwiązania (np. wydzielenia wkładu), ale domyślnie merge oznacza
akceptację powyższych warunków.

## Bezpieczeństwo

Podatności zgłaszaj przez prywatne zgłoszenie (GitHub → Security → Report a
vulnerability) zamiast publicznego Issue.
