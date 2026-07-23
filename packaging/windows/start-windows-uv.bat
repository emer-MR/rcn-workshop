@echo off
REM ============================================================
REM  RCN Workshop - launcher dla Windows przez uv (zalecany)
REM  Nie wymaga recznej instalacji Pythona - uv sam pobierze 3.12.
REM  Wymaga tylko uv (https://docs.astral.sh/uv/) - mozesz dolaczyc
REM  uv.exe obok tego pliku, wtedy user nie instaluje niczego.
REM  Uruchamia lokalny serwer i otwiera aplikacje w przegladarce.
REM  Plik lezy w packaging/windows/ -> cd do roota repo (dwa poziomy wyzej).
REM ============================================================
setlocal
cd /d "%~dp0..\.."

REM Loopback-only: aplikacja widoczna tylko na tym komputerze.
set RCN_BIND=127.0.0.1
set RCN_PORT=8000
set RCN_ALLOW_INSECURE_DEFAULT=1
REM Loopback = jeden uzytkownik: bez logowania (pomija splash i Basic Auth).
set RCN_DISABLE_AUTH=1
REM Dane w tej samej, prostej lokalizacji co .exe.
set "RCN_DATA_DIR=C:\RCN Workshop"

REM Uzyj uv.exe dolaczonego obok pliku, jesli jest; inaczej z PATH.
set "UV=uv"
if exist "%~dp0uv.exe" set "UV=%~dp0uv.exe"

where %UV% >nul 2>nul
if errorlevel 1 (
  echo [RCN] Brak uv. Zainstaluj: https://docs.astral.sh/uv/  albo dolacz uv.exe obok tego pliku.
  pause
  exit /b 1
)

if not exist ".env" copy ".env.example" ".env" >nul

echo [RCN] Start serwera na http://127.0.0.1:%RCN_PORT%  (uv pobierze Python 3.12 przy pierwszym uruchomieniu)
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:%RCN_PORT%"

REM uv run tworzy/synchronizuje srodowisko w locie z pyproject.toml/uv.lock
"%UV%" run --python 3.12 uvicorn app.main:app --host 127.0.0.1 --port %RCN_PORT%

endlocal
