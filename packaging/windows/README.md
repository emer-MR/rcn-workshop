# packaging/windows

Pliki do dystrybucji wersji **Windows** (lokalnej). Aplikacja jest ta sama co web (VPS/Docker) —
to tylko inny sposób pakowania/uruchamiania. Instrukcja dla użytkownika końcowego:
[`../../docs/QUICKSTART_Windows.md`](../../docs/QUICKSTART_Windows.md).

## Zawartość
- **`start-windows-uv.bat`** — launcher przez [uv](https://docs.astral.sh/uv/) (zalecany). uv sam pobiera
  Python 3.12, jedna linijka. Dołącz `uv.exe` obok pliku → user nie instaluje niczego.
- **`start-windows.bat`** — launcher klasyczny (venv + pip). Wymaga Pythona 3.12 z python.org.
- **`start-windows-desktop.bat`** — natywne okno (pywebview/WebView2) zamiast przeglądarki.
- **`RCN-Workshop.iss`** — skrypt **Inno Setup**: pakuje build PyInstallera (`dist/RCN-Workshop/`)
  w `RCN-Workshop-Setup.exe` (skróty, uninstaller, warunkowy bootstrapper WebView2,
  instalacja do `C:\RCN Workshop`). Patrz nagłówek pliku.

Oba baty robią `cd` do roota repo (dwa poziomy wyżej), więc działają z tego podfolderu.
Loopback-only (`127.0.0.1`).

## Build setup.exe (skrót)
1. `uv run --python 3.12 --extra desktop --extra build pyinstaller --noconfirm RCN-Workshop.spec` → `dist/RCN-Workshop/`
2. (opcjonalnie) wrzuć `MicrosoftEdgeWebview2Setup.exe` obok `RCN-Workshop.iss`
3. `& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\windows\RCN-Workshop.iss` → `packaging\windows\Output\RCN-Workshop-Setup.exe`

> Instrukcja dla użytkownika końcowego (laika): `docs/QUICKSTART_Windows.md`.
