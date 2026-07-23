# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec dla RCN Workshop (launcher okienkowy desktop.py).

Build (przez uv, bez instalacji globalnej):
    uv run --python 3.12 --extra desktop --extra build pyinstaller --noconfirm --clean RCN-Workshop.spec

Wynik: dist/RCN-Workshop/RCN-Workshop.exe (one-folder). Po stabilizacji można
przełączyć na one-file.
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Pliki danych aplikacji (Jinja2 + static + słowniki RCN).
datas = [
    ("templates", "templates"),
    ("static", "static"),
    ("rcn_core/resources", "rcn_core/resources"),
    (".env.example", "."),
]
binaries = []

# app.main:app jest importowane przez uvicorn jako STRING -> PyInstaller tego nie
# wykryje statycznie. Wciągamy całe app/ i rcn_core/ + dynamiczne submoduły uvicorna.
#
# WTYCZKI (data/plugins/*.py, exec w runtime):
# PyInstaller pakuje tylko statycznie wykryte moduły, więc wtyczki mogą używać
# wyłącznie stdlib już obecnego w buildzie (pewniaki: statistics, math,
# collections, datetime, json, itertools, re). Nowy moduł stdlib dla wtyczek
# -> dopisać do hiddenimports poniżej. Katalog repo plugins/ celowo NIE jest
# pakowany (dystrybucja osobno, instalacja przez upload w UI).
hiddenimports = ["app.main"]
hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("rcn_core")
hiddenimports += collect_submodules("uvicorn")

# Pakiety z natywnymi DLL/danymi (GDAL, PROJ db, GEOS, WebView2/pythonnet).
for pkg in ("pyproj", "pyogrio", "shapely", "webview", "clr_loader", "pythonnet"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # rcn_producer WYKLUCZONY -> build konsumenta nie zawiera kodu wzbogacania
    # (Model A; rcn_core.producer.HAS_PRODUCER=False -> brak UI/endpointów enrich).
    # tools/ też producent (nie jest entry-pointem, więc i tak nie wejdzie).
    excludes=["rcn_producer", "fiona", "tkinter", "matplotlib", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RCN-Workshop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=False -> windowed (bez czarnej konsoli). W tym trybie PyInstaller daje
    # sys.stdout/err = None; desktop.py::_run_server przepina je na rcn-server.log PRZED
    # startem uvicorna (inaczej formatter logowania pada na sys.stdout.isatty()). Do
    # diagnozy ciężkich błędów startu użyj launchera konsolowego start-windows-uv.bat.
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="RCN-Workshop",
)
