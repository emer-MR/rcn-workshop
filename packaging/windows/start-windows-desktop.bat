@echo off
REM ============================================================
REM  RCN Workshop - launcher OKIENKOWY (pywebview / Edge WebView2)
REM  Otwiera aplikacje w natywnym oknie zamiast czarnej konsoli.
REM  Wymaga uv (https://docs.astral.sh/uv/) - mozesz dolaczyc uv.exe
REM  obok tego pliku. uv sam pobierze Python 3.12 i zaleznosci.
REM  Loopback + bez logowania (RCN_DISABLE_AUTH ustawia desktop.py).
REM  Plik lezy w packaging/windows/ -> cd do roota repo (dwa poziomy wyzej).
REM ============================================================
setlocal
cd /d "%~dp0..\.."

REM Dane w tej samej, prostej lokalizacji co .exe (spojnosc zrodlo <-> .exe).
set "RCN_DATA_DIR=C:\RCN Workshop"

set "UV=uv"
if exist "%~dp0uv.exe" set "UV=%~dp0uv.exe"

where %UV% >nul 2>nul
if errorlevel 1 (
  echo [RCN] Brak uv. Zainstaluj: https://docs.astral.sh/uv/  albo dolacz uv.exe obok tego pliku.
  pause
  exit /b 1
)

if not exist ".env" copy ".env.example" ".env" >nul

echo [RCN] Uruchamianie okna aplikacji (uv pobierze Python 3.12 + zaleznosci przy pierwszym razie)...

REM --extra desktop dociaga pywebview; desktop.py startuje serwer i otwiera okno.
"%UV%" run --python 3.12 --extra desktop python desktop.py

endlocal
