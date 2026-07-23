"""Launcher okienkowy RCN Workshop -- natywne okno zamiast czarnej konsoli.

Uruchamia serwer uvicorn jako *subprocess* na loopbacku i otwiera natywne okno
(na Windows: Edge WebView2 / Chromium) wskazujące na lokalny adres. Zamknięcie
okna ubija serwer.

Subprocess (a nie wątek) bo: (1) uvicorn instaluje signal handlery tylko w
głównym wątku, (2) izolacja procesu istotna przy kolizji pyproj/fiona
(rcn_core/enrich.py uruchamia phase_runner osobno -- bez zmian względem dziś).

Uruchomienie (dev): `uv run --python 3.12 python desktop.py`
Wymaga grupy zależności `desktop` (pywebview): `uv sync --extra desktop`.

UWAGA: ustawia RCN_DISABLE_AUTH=1 -- bez logowania, wszystko jako admin. To jest
bezpieczne TYLKO na loopbacku (jeden komputer, jeden user). Nie używać sieciowo.
"""
from __future__ import annotations

import base64
import os
import socket
import subprocess
import sys
import time
import urllib.request

# UWAGA: `webview` (pywebview/WebView2/pythonnet) importujemy LENIWIE -- tylko w
# trybie okna. Podprocesy --serve / --phase-runner nie ładują WebView2.

HOST = "127.0.0.1"

# Mutex rozpoznawany przez instalator (AppMutex w RCN-Workshop.iss): dzięki niemu
# setup.exe wie, że aplikacja działa, i prosi o jej zamknięcie PRZED nadpisaniem
# plików -- inaczej upgrade z otwartym oknem kończy się zablokowanymi plikami.
APP_MUTEX = "RCNWorkshopAppMutex"


def _acquire_app_mutex() -> None:
    """Windows: utwórz nazwany mutex na czas życia procesu okna (nie zwalniamy
    ręcznie -- system sprząta przy wyjściu). Na innych OS i przy błędzie: no-op."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX)
    except Exception:
        pass


def _load_rcn_env(data_dir: str | None) -> None:
    """Wczytaj opcjonalny plik konfiguracyjny ``rcn.env`` z katalogu danych
    (np. ``C:\\RCN Workshop\\rcn.env``). Format: ``KLUCZ=WARTOŚĆ`` po linii,
    ``#`` zaczyna komentarz. Wartości NIE nadpisują już ustawionych zmiennych
    środowiskowych (setdefault) -- env systemowy ma pierwszeństwo.

    Po co: tester nie będzie ustawiał zmiennych systemowych. Instalator może
    dostarczyć ten plik (np. z RCN_SURVEY_URL -- linkiem do ankiety zgłoszeń),
    a repo pozostaje czyste od prywatnych adresów (plik NIE jest commitowany;
    dokłada się go do dystrybucji jak bootstrapper WebView2)."""
    if not data_dir:
        return
    path = os.path.join(data_dir, "rcn.env")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key:
                    os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass
    except OSError:
        pass  # nieczytelny plik konfiguracyjny nie może blokować startu aplikacji


def _save_dialog_const(webview):
    """pywebview 6.x: SAVE_DIALOG przestarzałe na rzecz FileDialog.SAVE."""
    const = getattr(getattr(webview, "FileDialog", None), "SAVE", None)
    return const if const is not None else webview.SAVE_DIALOG


def _frozen_data_dir() -> str | None:
    """W zamrożonym .exe: PROSTA, WIDOCZNA i jednakowa u wszystkich ścieżka danych
    -- domyślnie ``C:\\RCN Workshop`` (workspace'y w ``C:\\RCN Workshop\\workspaces``),
    zamiast ukrytego %LOCALAPPDATA%. Łatwa do wytłumaczenia laikowi i do wsparcia.
    Fallback do %LOCALAPPDATA%\\RCN-Workshop\\data, gdy ``C:\\`` nie jest zapisywalny
    (np. blokady korporacyjne). W dev (z kodu) zwraca None -> ./data w repo.
    Zawsze można nadpisać własnym RCN_DATA_DIR."""
    if not getattr(sys, "frozen", False):
        return None
    fallback = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "RCN-Workshop", "data"
    )
    for base in (r"C:\RCN Workshop", fallback):
        try:
            os.makedirs(os.path.join(base, "workspaces"), exist_ok=True)
            return base
        except Exception:
            continue
    return None


class Api:
    """Most JS->Python wystawiany do strony jako window.pywebview.api.

    WebView2 po cichu gubi pobranie bloba (blob: + a.download), więc eksport w
    trybie desktopowym trafia tutaj: front przekazuje plik jako base64, a my
    pokazujemy natywne 'Zapisz jako' i zapisujemy na dysk. Patrz workspace.js
    doExport.
    """

    def __init__(self) -> None:
        self._window: webview.Window | None = None

    def set_window(self, window: webview.Window) -> None:
        self._window = window

    def save_export(self, filename: str, b64: str) -> dict:
        if self._window is None:
            return {"ok": False, "error": "Brak okna."}
        try:
            data = base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Błędne dane: {exc}"}

        import webview
        result = self._window.create_file_dialog(
            _save_dialog_const(webview), save_filename=filename
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        try:
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": path}


def _free_port() -> int:
    """Wolny port na loopbacku (efemeryczny). Mała szansa wyścigu między close
    a bind uvicorna -- pomijalna na maszynie lokalnej jednego użytkownika."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_ready(url: str, timeout_s: int = 60) -> bool:
    """Odpytuje /healthz aż serwer odpowie 200 (start metropolii bywa wolny)."""
    deadline = time.monotonic() + timeout_s
    health = url + "/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


_SPLASH = """
<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{height:100%;margin:0}
  body{display:flex;flex-direction:column;align-items:center;justify-content:center;
       font-family:Segoe UI,system-ui,sans-serif;background:#0f172a;color:#e2e8f0}
  .sp{width:42px;height:42px;border:4px solid #334155;border-top-color:#38bdf8;
      border-radius:50%;animation:spin 0.9s linear infinite;margin-bottom:18px}
  @keyframes spin{to{transform:rotate(360deg)}}
  h1{font-size:1.1rem;font-weight:600;margin:0}
  p{color:#94a3b8;font-size:0.85rem;margin:6px 0 0}
</style></head><body>
  <div class="sp"></div>
  <h1>RCN Workshop</h1>
  <p>Uruchamianie serwera lokalnego...</p>
</body></html>
"""


def _server_command(port: int) -> list[str]:
    """Komenda startująca serwer jako podproces -- frozen-safe.
    - dev: `python desktop.py --serve --port N`
    - frozen (.exe): `RCN-Workshop.exe --serve --port N` (sys.executable = exe)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--serve", "--port", str(port)]
    return [sys.executable, os.path.abspath(__file__), "--serve", "--port", str(port)]


def _run_server(port: int) -> int:
    """Uruchom uvicorn w TYM procesie (wołane przez --serve w podprocesie okna)."""
    import uvicorn

    os.environ.setdefault("RCN_BIND", HOST)
    os.environ.setdefault("RCN_DISABLE_AUTH", "1")
    dd = _frozen_data_dir()
    if dd:
        os.environ.setdefault("RCN_DATA_DIR", dd)
    os.environ["RCN_PORT"] = str(port)

    # Konfiguracja testera (rcn.env) + domyślne ostrzeżenie beta w zamrożonym
    # buildzie. Kolejność ma znaczenie: rcn.env może świadomie WYŁĄCZYĆ modal
    # (RCN_TEST_WARNING=0), więc czytamy plik przed setdefault.
    _load_rcn_env(os.environ.get("RCN_DATA_DIR"))
    if getattr(sys, "frozen", False):
        from app.version import IS_BETA
        if IS_BETA:
            os.environ.setdefault("RCN_TEST_WARNING", "1")

    # Frozen windowed (.exe, console=False): PyInstaller ustawia sys.stdout/err = None
    # (brak konsoli). uvicorn konfiguruje formatter logowania, który woła
    # sys.stdout.isatty() -> AttributeError i serwer NIE wstaje. Przepinamy std* na
    # realny plik logu PRZED uvicorn.run -- to naprawia isatty() (plik -> False) i daje
    # log do wsparcia (rcn-server.log obok danych). W dev std* są realne -> pomijamy.
    if sys.stdout is None or sys.stderr is None:
        log_dir = os.environ.get("RCN_DATA_DIR") or os.path.expanduser("~")
        try:
            os.makedirs(log_dir, exist_ok=True)
            fh = open(os.path.join(log_dir, "rcn-server.log"), "a", buffering=1, encoding="utf-8")
        except Exception:
            fh = open(os.devnull, "w")
        sys.stdout = fh
        sys.stderr = fh

    uvicorn.run("app.main:app", host=HOST, port=port, log_level="warning")
    return 0


def _run_window() -> int:
    import webview

    _acquire_app_mutex()  # sygnał dla instalatora, że aplikacja działa

    port = _free_port()
    url = f"http://{HOST}:{port}"

    env = os.environ.copy()
    env["RCN_BIND"] = HOST
    env["RCN_PORT"] = str(port)
    env["RCN_DISABLE_AUTH"] = "1"  # loopback desktop: bez okienka Basic Auth
    dd = _frozen_data_dir()
    if dd:
        env.setdefault("RCN_DATA_DIR", dd)  # .exe: dane w %LOCALAPPDATA%, nie w paczce

    # Frozen windowed (.exe): proces okna nie ma konsoli -> dziecko odziedziczyłoby
    # nieprawidłowe uchwyty std (WinError 6). Dajemy DEVNULL; serwer sam loguje do
    # pliku (patrz _run_server -> rcn-server.log). W dev (z konsolą) zostawiamy
    # dziedziczenie, żeby logi serwera były widoczne w terminalu.
    redirect = subprocess.DEVNULL if getattr(sys, "frozen", False) else None
    srv = subprocess.Popen(
        _server_command(port),
        env=env,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdin=subprocess.DEVNULL,
        stdout=redirect,
        stderr=redirect,
    )

    # Tytuł okna z wersją w wydaniach przedpremierowych -- tester widzi od razu,
    # którą betę zgłasza; w wydaniu stabilnym czysty tytuł bez numerków.
    from app.version import IS_BETA, __version__
    title = f"RCN Workshop — wersja testowa {__version__}" if IS_BETA else "RCN Workshop"

    api = Api()
    try:
        window = webview.create_window(
            title,
            html=_SPLASH,
            width=1280,
            height=860,
            confirm_close=True,  # ochrona przed ubiciem serwera w trakcie importu
            js_api=api,  # window.pywebview.api.save_export (natywny zapis eksportu)
        )
        api.set_window(window)

        def boot() -> None:
            if _wait_ready(url):
                window.load_url(url)
            else:
                window.load_html(
                    "<body style='font-family:sans-serif;padding:2rem'>"
                    "<h2>Serwer nie wstał</h2><p>Zamknij okno i uruchom ponownie. "
                    "Jeśli problem się powtarza -- uruchom przez konsolę "
                    "(start-windows-uv.bat) i wyślij administratorowi treść błędu.</p></body>"
                )

        webview.start(boot)
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
    return 0


def main() -> int:
    """Punkt wejścia (też dla zamrożonego .exe). Dyspozytor podprocesów:
    - `--phase-runner ...` -> rcn_core.phase_runner.main (ingest / ulepszenia)
    - `--serve --port N`   -> uvicorn w tym procesie (serwer okna)
    - bez argumentów        -> natywne okno aplikacji."""
    argv = sys.argv[1:]
    if argv and argv[0] == "--phase-runner":
        from rcn_core.phase_runner import main as phase_main
        return phase_main(argv[1:])
    if argv and argv[0] == "--serve":
        port = 8000
        if "--port" in argv:
            try:
                port = int(argv[argv.index("--port") + 1])
            except (ValueError, IndexError):
                pass
        return _run_server(port)
    return _run_window()


if __name__ == "__main__":
    raise SystemExit(main())
