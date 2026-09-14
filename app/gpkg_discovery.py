"""Auto-wykrywanie warstw GPKG w korzeniu folderu workspace'u (Model A).

Konwencja „folder = komplet": obok bazy `*.sqlite` mogą leżeć pliki `*.gpkg`,
które stają się warstwami na mapie BEZ rejestracji w `workspace_meta` — wystarczy
wrzucić plik. Klasyfikacja warstwy:
  1. po ZAWARTOŚCI (pola GPKG): `ID_DZIALKI` -> działki, `ID_BUDYNKU` -> budynki
     (solidne — nazwa pliku może być dowolna),
  2. fallback po NAZWIE (znormalizowanej): `dzialk*` -> działki, `budynk*`/
     `budynek*`/`building*` -> budynki.
Nierozpoznane GPKG są pomijane (nie zaśmiecamy mapy losowymi plikami).

Moduł celowo nie importuje `app.workspaces`/`app.layers` (brak cykli) —
przyjmuje katalog jako `Path`.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

_PL = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def _norm(s: str) -> str:
    s = s.translate(_PL)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.lower()


def _classify(gpkg: Path) -> tuple[str, str]:
    """(kind, label). kind: 'dzialki' | 'budynki' | '' (nierozpoznane)."""
    fields: set[str] = set()
    try:
        import pyogrio
        fields = {f.upper() for f in (pyogrio.read_info(str(gpkg)).get("fields") or ())}
    except Exception:
        pass
    n = _norm(gpkg.stem)
    # `startswith`, nie równość: pobieraczka EGIB zapisuje też `ID_DZIALKI_`
    # (GDAL skraca zbyt długie nazwy kolumn) i wariant małymi literami.
    ma_dzialki = any(f.startswith("ID_DZIALKI") for f in fields)
    ma_budynki = any(f.startswith("ID_BUDYNKU") for f in fields)
    if ma_dzialki or "dzialk" in n:
        return "dzialki", "Działki"
    if ma_budynki or "budynk" in n or "budynek" in n or "building" in n:
        return "budynki", "Budynki"
    return "", ""


# --- Wykrywanie faktycznego CRS (odporne na błędne metadane EGIB) -----------
# Granice Polski (z zapasem) w EPSG:4326. Kandydaci: PUWG 1992 (2180) + strefy
# PUWG 2000 (2176-2179) + WGS84. Pierwszy układ, dla którego próbka współrzędnych
# po reprojekcji do 4326 wpada w te granice, jest uznany za faktyczny.
_PL_BBOX = (13.5, 48.7, 24.5, 55.2)  # lon_min, lat_min, lon_max, lat_max
_PL_EPSG_CANDIDATES = (2180, 2177, 2176, 2178, 2179, 4326)


def _in_poland(lon: float, lat: float) -> bool:
    return _PL_BBOX[0] <= lon <= _PL_BBOX[2] and _PL_BBOX[1] <= lat <= _PL_BBOX[3]


def detect_gpkg_epsg(gpkg_path: Path, declared_epsg: int | None = None) -> int | None:
    """Faktyczny EPSG warstwy: ten, dla którego próbka współrzędnych ląduje w
    granicach Polski po reprojekcji do 4326. Odporne na błędne/zafałszowane
    metadane CRS w GPKG (częste w EGIB). Zwraca `declared_epsg`, gdy nie da się
    ustalić (np. dane spoza Polski albo brak geometrii)."""
    try:
        from pyogrio.raw import read as _read
        import pyproj
        from shapely import wkb as shapely_wkb
    except Exception:
        return declared_epsg
    try:
        _m, _f, geoms, _d = _read(str(gpkg_path), max_features=25, read_geometry=True, return_fids=True)
    except Exception:
        return declared_epsg
    pt = None
    for wkb in geoms:
        if wkb is None:
            continue
        try:
            g = shapely_wkb.loads(bytes(wkb))
            if g.is_empty:
                continue
            p = g.representative_point()
            pt = (p.x, p.y)
            break
        except Exception:
            continue
    if pt is None:
        return declared_epsg
    x, y = pt
    candidates: list[int] = []
    if declared_epsg:
        candidates.append(int(declared_epsg))
    candidates += [c for c in _PL_EPSG_CANDIDATES if c not in candidates]
    for epsg in candidates:
        try:
            lon, lat = pyproj.Transformer.from_crs(epsg, 4326, always_xy=True).transform(x, y)
            if _in_poland(lon, lat):
                return epsg
        except Exception:
            continue
    return declared_epsg


def discover_gpkg_layers(wdir: Path) -> list[dict]:
    """Warstwy GPKG z korzenia folderu: [{slug, name, file, kind, path}].
    Pierwszy plik danego rodzaju wygrywa (deterministycznie, alfabetycznie)."""
    out: list[dict] = []
    seen: set[str] = set()
    if not wdir.is_dir():
        return out
    for gpkg in sorted(wdir.glob("*.gpkg")):
        kind, label = _classify(gpkg)
        if not kind or kind in seen:
            continue
        seen.add(kind)
        out.append(
            {"slug": kind, "name": label, "file": gpkg.name, "kind": kind, "path": str(gpkg)}
        )
    return out
