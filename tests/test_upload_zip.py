"""GML spakowany w `.zip` ma wchodzić tą samą drogą co GML luzem.

Źródła RCN rozsyłają powiaty spakowane -- często kilka GML-i w jednym archiwum
(po gminach albo po okresach). Do tej pory formularz przyjmował wyłącznie
`.gml`/`.xml`, więc każde wgranie zaczynało się od ręcznego rozpakowania.

Testy pilnują trzech rzeczy: co jest uznawane za GML (także plik BEZ
rozszerzenia, bo takie archiwa realnie przyszły z powiatów otwockiego
i przemyskiego), co archiwum robi z limitem uploadu, i czy wgranie paczki
kończy się kompletem importów.
"""
import time
import zipfile

import pytest

from rcn_core.archiwum import (
    BlednaPaczka,
    PaczkaZaDuza,
    wyglada_na_gml,
    wypakuj_gml,
)

XML = b'<?xml version="1.0" encoding="UTF-8"?>\n<wfs:FeatureCollection>tresc</wfs:FeatureCollection>'


def _nazwa(original: str) -> str:
    return f"zapisany_{original}"


def _archiwum(path, wpisy: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as zf:
        for nazwa, tresc in wpisy.items():
            zf.writestr(nazwa, tresc)
    return path


def test_rozpoznanie_pliku_gml():
    assert wyglada_na_gml("dane.gml", b"")
    assert wyglada_na_gml("dane.XML", b"")
    assert not wyglada_na_gml("readme.txt", XML)
    assert not wyglada_na_gml("skan.pdf", b"%PDF-1.7")
    # Plik bez rozszerzenia rozstrzyga się po zawartości: tak przyszły paczki
    # powiatu otwockiego („Wszystkie gminy") i przemyskiego („RCN_1813").
    assert wyglada_na_gml("Wszystkie gminy", XML)
    assert not wyglada_na_gml("Wszystkie gminy", b"losowe bajty")


def test_wypakowuje_gmle_i_pomija_reszte(tmp_path):
    zip_path = _archiwum(tmp_path / "powiat.zip", {
        "gmina_a.gml": XML,
        "podkatalog/gmina_b.gml": XML,
        "RCN_1813": XML,                    # GML bez rozszerzenia
        "readme.txt": b"opis paczki",
        "__MACOSX/._gmina_a.gml": b"\x00\x05\x16\x07",
    })
    cel = tmp_path / "uploads"

    pliki = wypakuj_gml(zip_path, cel, nazwa_docelowa=_nazwa)

    assert sorted(p.nazwa_oryginalna for p in pliki) == ["RCN_1813", "gmina_a.gml", "gmina_b.gml"]
    assert all(p.sciezka.parent == cel and p.sciezka.exists() for p in pliki)
    assert all(p.rozmiar == len(XML) for p in pliki)
    # Ścieżka z archiwum nie przenosi się na dysk -- zostaje sama nazwa pliku.
    assert not (cel / "podkatalog").exists()


def test_archiwum_bez_gmli_jest_odrzucane(tmp_path):
    zip_path = _archiwum(tmp_path / "puste.zip", {"readme.txt": b"nic tu nie ma"})
    with pytest.raises(BlednaPaczka):
        wypakuj_gml(zip_path, tmp_path / "uploads", nazwa_docelowa=_nazwa)


def test_uszkodzone_archiwum_daje_czytelny_blad(tmp_path):
    zepsute = tmp_path / "zepsute.zip"
    zepsute.write_bytes(b"to nie jest zip")
    with pytest.raises(BlednaPaczka):
        wypakuj_gml(zepsute, tmp_path / "uploads", nazwa_docelowa=_nazwa)


def test_limit_uploadu_dotyczy_rozpakowanej_zawartosci(tmp_path):
    zip_path = _archiwum(tmp_path / "duza.zip", {"a.gml": XML * 100, "b.gml": XML * 100})
    cel = tmp_path / "uploads"

    with pytest.raises(PaczkaZaDuza):
        wypakuj_gml(zip_path, cel, nazwa_docelowa=_nazwa, limit_bajtow=len(XML))

    # Odrzucona paczka nie zostawia połówek plików w katalogu uploadów.
    assert list(cel.glob("*")) == []


def test_brak_limitu_przepuszcza_wszystko(tmp_path):
    """`RCN_MAX_UPLOAD_MB=0` (desktop) to `limit_bajtow=None`, nie limit zerowy."""
    zip_path = _archiwum(tmp_path / "desktop.zip", {"a.gml": XML * 1000})
    pliki = wypakuj_gml(zip_path, tmp_path / "uploads", nazwa_docelowa=_nazwa, limit_bajtow=None)
    assert len(pliki) == 1


def _czekaj_na_import(client, auth, wid, import_id, timeout_s=60):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        info = client.get(f"/api/workspaces/{wid}/imports/{import_id}", auth=auth).json()
        if info["status"] != "processing":
            return info
        time.sleep(0.5)
    pytest.fail(f"import {import_id} nie skończył się w {timeout_s} s")


def test_aktualizacja_workspace_z_archiwum(client, auth, delta_gml, tmp_path):
    """Ścieżka, o którą chodzi w praktyce: wgranie spakowanego GML-a do bazy."""
    wid = client.post("/api/workspaces", auth=auth, json={"name": "pytest-zip"}).json()["id"]
    zip_path = tmp_path / "powiat.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(delta_gml, arcname="od14do27marca2026.gml")

    with open(zip_path, "rb") as f:
        r = client.post(
            f"/api/workspaces/{wid}/upload",
            auth=auth,
            files={"file": ("powiat.zip", f, "application/zip")},
            data={"tryb": "delta"},
        )

    assert r.status_code == 202, r.text[:300]
    body = r.json()
    assert body["import_ids"] == [body["import_id"]]
    assert body["zrodlo_archiwum"] == "powiat.zip"
    # W historii importów widać nazwę pliku Z ARCHIWUM, nie nazwę paczki.
    assert body["original_filename"] == "od14do27marca2026.gml"

    info = _czekaj_na_import(client, auth, wid, body["import_id"])
    assert info["status"] == "success", info.get("error_msg")
    assert info["transaction_count"] == 674

    total = client.post(
        f"/api/workspaces/{wid}/query", auth=auth, json={"page": 1, "pageSize": 1}
    ).json()["total"]
    assert total == 674


def test_zly_format_pliku_odrzucony(client, auth, tmp_path):
    wid = client.post("/api/workspaces", auth=auth, json={"name": "pytest-zly"}).json()["id"]
    plik = tmp_path / "dane.csv"
    plik.write_text("a;b;c", encoding="utf-8")
    with open(plik, "rb") as f:
        r = client.post(
            f"/api/workspaces/{wid}/upload",
            auth=auth,
            files={"file": ("dane.csv", f, "text/csv")},
        )
    assert r.status_code == 400
    assert ".zip" in r.json()["detail"]


def test_paczka_z_kilkoma_gmlami_daje_komplet_importow(client, auth, delta_gml, tmp_path):
    """Powiat bywa dzielony po gminach -- każdy plik z paczki ma swój import.

    Ten sam GML wgrany dwa razy w trybie `delta`: drugi przebieg ma
    zaktualizować te same transakcje, a nie dołożyć drugiego kompletu.
    """
    wid = client.post("/api/workspaces", auth=auth, json={"name": "pytest-zip-2"}).json()["id"]
    zip_path = tmp_path / "powiat_po_gminach.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(delta_gml, arcname="gmina_a.gml")
        zf.write(delta_gml, arcname="gmina_b.gml")

    with open(zip_path, "rb") as f:
        r = client.post(
            f"/api/workspaces/{wid}/upload",
            auth=auth,
            files={"file": ("powiat_po_gminach.zip", f, "application/zip")},
            data={"tryb": "delta"},
        )

    assert r.status_code == 202, r.text[:300]
    importy = r.json()["import_ids"]
    assert len(importy) == 2
    assert r.json()["import_id"] == importy[0]

    for import_id in importy:
        info = _czekaj_na_import(client, auth, wid, import_id, timeout_s=120)
        assert info["status"] == "success", info.get("error_msg")

    historia = client.get(f"/api/workspaces/{wid}/imports", auth=auth).json()
    assert {i["original_filename"] for i in historia} == {"gmina_a.gml", "gmina_b.gml"}

    total = client.post(
        f"/api/workspaces/{wid}/query", auth=auth, json={"page": 1, "pageSize": 1}
    ).json()["total"]
    assert total == 674


def test_paczka_nie_idzie_snapshotem(client, auth, delta_gml, tmp_path):
    """Snapshot + kilka plików = awaria 2026-09-12 od nowa, więc tryb jest wymuszany.

    Pliki z jednej paczki są FRAGMENTAMI zbioru. W trybie `snapshot` drugi
    wycofałby wszystko, co wniósł pierwszy (snapshot kasuje z bazy to, czego
    nie ma w pliku, w zakresie jego dat).
    """
    wid = client.post("/api/workspaces", auth=auth, json={"name": "pytest-zip-3"}).json()["id"]
    zip_path = tmp_path / "roczniki.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(delta_gml, arcname="rok_2025.gml")
        zf.write(delta_gml, arcname="rok_2026.gml")

    with open(zip_path, "rb") as f:
        r = client.post(
            f"/api/workspaces/{wid}/upload",
            auth=auth,
            files={"file": ("roczniki.zip", f, "application/zip")},
            data={"tryb": "snapshot"},          # operator wybrał świadomie -- i tak zmieniamy
        )

    assert r.status_code == 202, r.text[:300]
    body = r.json()
    assert body["tryb"] == "delta"
    # Zmiana trybu nie może być cicha.
    assert body["uwaga"] and "delta" in body["uwaga"]

    for import_id in body["import_ids"]:
        info = _czekaj_na_import(client, auth, wid, import_id, timeout_s=120)
        assert info["status"] == "success", info.get("error_msg")
        assert info["tryb"] == "delta"

    # Nic nie zostało wycofane: drugi plik zaktualizował te same transakcje.
    total = client.post(
        f"/api/workspaces/{wid}/query", auth=auth, json={"page": 1, "pageSize": 1}
    ).json()["total"]
    assert total == 674


def test_pojedynczy_gml_w_paczce_zachowuje_wybrany_tryb(client, auth, delta_gml, tmp_path):
    """Jeden plik w archiwum = zwykłe wgranie GML-a, więc wybór operatora zostaje."""
    wid = client.post("/api/workspaces", auth=auth, json={"name": "pytest-zip-4"}).json()["id"]
    zip_path = tmp_path / "jeden.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(delta_gml, arcname="caly_powiat.gml")

    with open(zip_path, "rb") as f:
        r = client.post(
            f"/api/workspaces/{wid}/upload",
            auth=auth,
            files={"file": ("jeden.zip", f, "application/zip")},
            data={"tryb": "snapshot"},
        )

    assert r.json()["tryb"] == "snapshot"
    assert r.json()["uwaga"] is None


def test_zakladanie_workspace_idzie_delta(client, auth, delta_gml, tmp_path):
    """Nowy workspace bierze zwykle kilka plików -- snapshot kasowałby dorobek poprzednich."""
    zip_path = tmp_path / "start.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(delta_gml, arcname="czesc_1.gml")
        zf.write(delta_gml, arcname="czesc_2.gml")

    with open(zip_path, "rb") as f:
        r = client.post(
            "/api/workspaces/new",
            auth=auth,
            data={"name": "pytest-new-zip"},
            files={"gml_files": ("start.zip", f, "application/zip")},
        )

    assert r.status_code == 201, r.text[:300]
    wid = r.json()["id"]

    deadline = time.time() + 120
    while time.time() < deadline:
        historia = client.get(f"/api/workspaces/{wid}/imports", auth=auth).json()
        if historia and all(i["status"] != "processing" for i in historia):
            break
        time.sleep(0.5)

    assert {i["original_filename"] for i in historia} == {"czesc_1.gml", "czesc_2.gml"}
    assert {i["tryb"] for i in historia} == {"delta"}
    assert all(i["status"] == "success" for i in historia), historia

    total = client.post(
        f"/api/workspaces/{wid}/query", auth=auth, json={"page": 1, "pageSize": 1}
    ).json()["total"]
    assert total == 674
