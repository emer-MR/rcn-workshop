@echo off
REM ============================================================
REM  RCN Workshop - launcher dla Windows (lokalnie, loopback)
REM  Dwuklik uruchamia serwer i otwiera przegladarke.
REM  Pierwsze uruchomienie samo tworzy srodowisko i instaluje
REM  zaleznosci (potrwa kilka minut). Kolejne sa juz szybkie.
REM  Plik lezy w packaging/windows/ -> cd do roota repo (dwa poziomy wyzej).
REM ============================================================
setlocal
cd /d "%~dp0..\.."

REM Loopback-only: aplikacja widoczna tylko na tym komputerze.
REM Dla instalacji lokalnej haslo jest formalnoscia (nikt z sieci nie ma dostepu).
set RCN_BIND=127.0.0.1
set RCN_PORT=8000
set RCN_ALLOW_INSECURE_DEFAULT=1
REM Loopback = jeden uzytkownik: bez logowania (pomija splash i Basic Auth).
set RCN_DISABLE_AUTH=1
REM Dane w tej samej, prostej lokalizacji co .exe.
set "RCN_DATA_DIR=C:\RCN Workshop"

if not exist ".venv\Scripts\activate.bat" (
  echo [RCN] Pierwsze uruchomienie - tworze srodowisko Python...
  py -3.12 -m venv .venv
  if errorlevel 1 (
    echo [RCN] BLAD: brak Python 3.12. Zainstaluj z python.org ^(zaznacz "Add to PATH"^).
    pause
    exit /b 1
  )
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  pip install -e .
) else (
  call ".venv\Scripts\activate.bat"
)

if not exist ".env" copy ".env.example" ".env" >nul

echo [RCN] Start serwera na http://127.0.0.1:%RCN_PORT%
REM Otworz przegladarke po ~3s (gdy serwer juz wstanie)
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:%RCN_PORT%"

uvicorn app.main:app --host 127.0.0.1 --port %RCN_PORT%

endlocal
