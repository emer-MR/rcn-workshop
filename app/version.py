"""Jedno źródło wersji aplikacji.

Zmieniasz TUTAJ i tylko tutaj. Miejsca, które czytają tę wartość:
- `app/main.py` -- FastAPI(version=...), /healthz, globals szablonów
  (`app_version`, `is_beta` -> badge BETA w topbarach, modal ostrzegawczy),
- `desktop.py` -- tytuł okna w becie.

Miejsca wymagające RĘCZNEJ synchronizacji przy podbiciu (nie importują Pythona):
- `pyproject.toml` -> `version` (PEP 440, np. "0.2.0b1" dla "0.2.0-beta.1"),
- `packaging/windows/RCN-Workshop.iss` -> `#define AppVersion`.

Konwencja: SemVer + sufiks przedpremierowy. Obecność "beta" (lub "rc"/"alpha")
w stringu włącza `IS_BETA` -- badge w UI i domyślne ostrzeżenie w desktopie.
Wydanie stabilne = usunięcie sufiksu, IS_BETA gaśnie samo.
"""

__version__ = "0.2.0-beta.2"

IS_BETA = any(tag in __version__ for tag in ("alpha", "beta", "rc"))
