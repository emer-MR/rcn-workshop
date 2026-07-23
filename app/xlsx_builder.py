"""XLSX export -- własny layout RCN Workshop, wyraźnie różny od wzorca
pluginu QGIS (`rcn_core/exporters.py` pozostaje nietknięty jako "surowy"
eksport w stylu pluginu).

Różnice vs plugin QGIS:
  - 5 arkuszy w zamian za "Raport zbiorczy" z mieszaniem typów rekordów:
    "Podsumowanie" (nowy dashboard z metadatą), "Transakcje", "Działki",
    "Budynki", "Lokale" -- każdy jako płaska, skondensowana tabela.
  - Skrócone, czytelne nazwy kolumn ("ID transakcji", "Data", "Sygnatura",
    "Notariusz", "Cena [PLN]", "Pow. [m²]") zamiast dosłownych kopii GML-owych.
  - Branding RCN Workshop: granatowe nagłówki (#1f4e79), akcent pomarańczowy
    (#f4b183) dla sum/podsumowań, zebra striping (#f8fafc), emoji w nazwach
    arkuszy.
  - Dashboard "Podsumowanie" pokazuje liczniki, zakres dat, statystyki cen
    i filtry użyte przy eksporcie -- tego QGIS nie ma.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet


# Kolory brand RCN Workshop
PRIMARY = "1F4E79"       # granat
PRIMARY_SOFT = "D6E4F5"  # jasno-granatowy tint (działki)
ACCENT = "F4B183"        # akcent pomarańczowy (dla sum/totals)
ACCENT_SOFT = "FCE7D0"   # jasno-pomarańczowy tint
ZEBRA = "F8FAFC"         # bardzo jasno-szary (zebra striping)
MUTED = "64748B"         # szary dla metadata
DANGER = "C0392B"        # czerwony dla błędnych danych

# Kolory dla grupowanego "Raportu zbiorczego" -- wyraźnie inne niż QGIS
# (QGIS: niebieski/pomarańczowy/zielony jako tło). Tu użyty jest branding
# RCN + pastelowe warianty dla obiektów.
BUILDING_BG = "FEF3C7"   # jasnożółty dla budynków
LOCAL_BG = "D1FAE5"      # jasnozielony (mięta) dla lokali
SEPARATOR_BG = "E2E8F0"  # jasnoszary dla pustych separatorów

WHITE = "FFFFFF"
BLACK = "0F172A"


@dataclass
class ExportMeta:
    """Metadane widoczne w arkuszu 'Podsumowanie'."""
    workspace_name: str
    workspace_id: str
    generated_at: datetime
    total_count: int
    filters_summary: str  # krótki opis aktywnych filtrów ("wszystkie" albo "rynek wtórny, cena 100k-500k")
    export_comment: str = ""  # PR6 fix: jednorazowy komentarz uzytkownika "do tego eksportu"


# ---------- Styl helpers ----------

def _header_style(cell):
    cell.fill = PatternFill("solid", fgColor=PRIMARY)
    cell.font = Font(bold=True, color=WHITE, size=11)
    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    cell.border = Border(bottom=Side(style="medium", color=PRIMARY))


def _meta_label(cell):
    cell.font = Font(bold=True, color=MUTED, size=10)
    cell.alignment = Alignment(horizontal="right", vertical="center")


def _meta_value(cell):
    cell.font = Font(color=BLACK, size=11)
    cell.alignment = Alignment(horizontal="left", vertical="center")


def _title(cell):
    cell.font = Font(bold=True, color=PRIMARY, size=18)
    cell.alignment = Alignment(horizontal="left", vertical="center")


def _apply_zebra(ws: Worksheet, start_row: int, end_row: int, start_col: int, end_col: int):
    """Zebra striping -- co drugi wiersz jasno-szary."""
    fill = PatternFill("solid", fgColor=ZEBRA)
    for row in range(start_row, end_row + 1):
        if (row - start_row) % 2 == 1:
            for col in range(start_col, end_col + 1):
                ws.cell(row=row, column=col).fill = fill


def _freeze_header(ws: Worksheet, row: int):
    """Zamrozi wiersz nagłówka."""
    ws.freeze_panes = f"A{row + 1}"


def _autosize(ws: Worksheet, widths: dict[int, float]):
    """widths: {col_index (1-based): width_in_chars}"""
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def _fmt_money(v):
    """Liczba z separatorem tysięcy, 2 miejsca po przecinku, bez 'zł'."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_int(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ---------- Arkusz 1: Podsumowanie (meta dashboard) ----------

def _build_summary_sheet(wb: Workbook, meta: ExportMeta, summary_rows: list, plot_rows: list,
                        building_rows: list, local_rows: list) -> None:
    """Dashboard z metadata -- element nieobecny w pluginie QGIS."""
    ws = wb.active
    ws.title = "Podsumowanie"
    ws.sheet_view.showGridLines = False

    # Tytuł
    ws.cell(row=2, column=2, value="RCN Workshop — eksport wybranych transakcji")
    _title(ws["B2"])
    ws.merge_cells("B2:F2")
    ws.row_dimensions[2].height = 32

    # Metadata
    rows = [
        ("Workspace:",       meta.workspace_name),
        ("ID workspace:",    meta.workspace_id),
        ("Wygenerowano:",    meta.generated_at.strftime("%Y-%m-%d %H:%M:%S")),
        ("Filtry:",          meta.filters_summary or "wszystkie transakcje"),
    ]
    if meta.export_comment:
        rows.append(("Komentarz:", meta.export_comment))
    for i, (label, value) in enumerate(rows):
        r = 4 + i
        ws.cell(row=r, column=2, value=label)
        _meta_label(ws.cell(row=r, column=2))
        c = ws.cell(row=r, column=3, value=value)
        _meta_value(c)
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=6)
        # Komentarz może być wieloliniowy — pozwól na wrap text
        if label == "Komentarz:":
            c.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            ws.row_dimensions[r].height = max(40, len(value.split("\n")) * 16)

    # Statystyki liczbowe -- start dynamicznie: zwykle 10, ale jesli komentarz
    # jest wpisany i jest wieloliniowy, przesun w dol zeby nie zachodzil.
    base_stats_start = 4 + len(rows) + 2
    extra_lines = max(0, len(meta.export_comment.split("\n")) - 1) if meta.export_comment else 0
    stats_start = base_stats_start + extra_lines
    ws.cell(row=stats_start, column=2, value="Liczba rekordów")
    ws.cell(row=stats_start, column=2).font = Font(bold=True, color=PRIMARY, size=13)

    items = [
        ("Transakcje",    len(summary_rows)),
        ("Działki",       len(plot_rows)),
        ("Budynki",       len(building_rows)),
        ("Lokale",        len(local_rows)),
    ]
    for i, (label, count) in enumerate(items):
        r = stats_start + 1 + i
        c = ws.cell(row=r, column=2, value=label)
        c.font = Font(color=BLACK, size=11)
        c.alignment = Alignment(horizontal="right")
        c = ws.cell(row=r, column=3, value=count)
        c.font = Font(bold=True, color=PRIMARY, size=12)
        c.alignment = Alignment(horizontal="left")

    # Statystyki cen (mini)
    prices = [
        _fmt_money(row.get("cena transakcji brutto")) for row in summary_rows
        if row.get("cena transakcji brutto")
    ]
    prices = [p for p in prices if p is not None and p > 0]
    if prices:
        stats2 = stats_start + 1 + len(items) + 2
        ws.cell(row=stats2, column=2, value="Statystyki cen (brutto)")
        ws.cell(row=stats2, column=2).font = Font(bold=True, color=PRIMARY, size=13)
        price_items = [
            ("Minimum:",  f"{min(prices):,.0f} zł"),
            ("Mediana:",  f"{sorted(prices)[len(prices) // 2]:,.0f} zł"),
            ("Maksimum:", f"{max(prices):,.0f} zł"),
            ("Suma:",     f"{sum(prices):,.0f} zł"),
        ]
        for i, (label, val) in enumerate(price_items):
            r = stats2 + 1 + i
            c = ws.cell(row=r, column=2, value=label)
            c.font = Font(color=BLACK, size=11)
            c.alignment = Alignment(horizontal="right")
            c = ws.cell(row=r, column=3, value=val.replace(",", " "))
            c.font = Font(bold=True, color=BLACK, size=11)
            c.alignment = Alignment(horizontal="left")

    # Stopka
    footer_row = 28
    ws.cell(row=footer_row, column=2,
            value=f"Wygenerowano przez RCN Workshop · {meta.generated_at.strftime('%Y-%m-%d')} · "
                  "plik należy chronić -- zawiera dane osobowe z aktów notarialnych")
    ws.cell(row=footer_row, column=2).font = Font(italic=True, color=MUTED, size=9)
    ws.merge_cells(start_row=footer_row, start_column=2, end_row=footer_row, end_column=7)

    _autosize(ws, {1: 2, 2: 22, 3: 28, 4: 18, 5: 18, 6: 18, 7: 18})


# ---------- Arkusze z danymi ----------

@dataclass
class _Col:
    header: str
    src: str              # key w dict row-a
    transform: callable = None  # opcjonalna transformacja wartości
    width: float = 14
    number_format: str = None


def _write_sheet(wb: Workbook, title: str, rows: list[dict], cols: list[_Col]) -> None:
    """Helper: tworzy arkusz z nagłówkiem + danymi + zebra striping."""
    ws = wb.create_sheet(title)
    ws.sheet_view.showGridLines = False

    # Nagłówki
    for i, col in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=i, value=col.header)
        _header_style(cell)
    ws.row_dimensions[1].height = 28

    # Dane
    for row_idx, row in enumerate(rows, start=2):
        for col_idx, col in enumerate(cols, start=1):
            value = row.get(col.src)
            if col.transform:
                value = col.transform(value, row)
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            if col.number_format and value is not None:
                cell.number_format = col.number_format
            cell.font = Font(color=BLACK, size=10)
            cell.alignment = Alignment(vertical="center", wrap_text=False)

    if rows:
        _apply_zebra(ws, start_row=2, end_row=len(rows) + 1, start_col=1, end_col=len(cols))
    _freeze_header(ws, 1)

    widths = {i: col.width for i, col in enumerate(cols, start=1)}
    _autosize(ws, widths)


def _build_raport_zbiorczy_sheet(wb: Workbook, summary_rows: list[dict], plot_rows: list[dict],
                                  building_rows: list[dict], local_rows: list[dict]) -> None:
    """Widok grupowany: jedna transakcja = 1 wiersz-nagłówek + N wierszy
    działek + M budynków + K lokali + separator. Kolorowanie typu rekordu
    (nie jak w QGIS -- inne palety, inny układ kolumn).

    Kolumny:
      Blok | Nr obiektu / id_RCN | Data / adres | Miejscowość | Pow. [m²] |
      Cena [PLN] | Uwagi

    Transakcja: granatowy nagłówek, kolumny rozmyte ("Data + Sygnatura" w jednej,
    "Typ rynku + Typ transakcji" w drugiej).
    Działka: jasny indigo.
    Budynek: jasnożółty.
    Lokal: jasnozielony.
    Separator: jasnoszary wiersz między transakcjami.
    """
    ws = wb.create_sheet("Raport zbiorczy")
    ws.sheet_view.showGridLines = False

    HEADERS = ["Blok", "Nr obiektu / ID transakcji", "Data / Sygnatura", "Miejscowość",
               "Adres", "Pow. [m²]", "Cena [PLN]", "Uwagi"]
    COLS = len(HEADERS)

    # Nagłówek
    for i, h in enumerate(HEADERS, start=1):
        _header_style(ws.cell(row=1, column=i, value=h))
    ws.row_dimensions[1].height = 28

    # Grupowanie po id_RCN
    plots_by_id: dict[str, list[dict]] = {}
    for p in plot_rows:
        plots_by_id.setdefault(p.get("id_RCN"), []).append(p)
    buildings_by_id: dict[str, list[dict]] = {}
    for b in building_rows:
        buildings_by_id.setdefault(b.get("id_RCN"), []).append(b)
    locals_by_id: dict[str, list[dict]] = {}
    for l in local_rows:
        locals_by_id.setdefault(l.get("id_RCN"), []).append(l)

    def _fill_row(row_num: int, color: str, bold: bool = False, white_text: bool = False):
        fill = PatternFill("solid", fgColor=color)
        for c in range(1, COLS + 1):
            cell = ws.cell(row=row_num, column=c)
            cell.fill = fill
            cell.font = Font(
                bold=bold,
                color=WHITE if white_text else BLACK,
                size=10,
            )
            cell.alignment = Alignment(vertical="center", wrap_text=True)

    current = 2
    for tx in summary_rows:
        id_rcn = tx.get("id_RCN", "")
        # --- Wiersz transakcji (granat, bold, białe napisy)
        ws.cell(row=current, column=1, value="🔷 TRANSAKCJA")
        ws.cell(row=current, column=2, value=id_rcn)
        data = tx.get("data transakcji") or ""
        sygn = tx.get("dokument") or ""
        ws.cell(row=current, column=3, value=f"{data} · {sygn}" if sygn else data)
        # kolumna Miejscowość -- zostawimy pustą, bo na poziomie transakcji nie ma;
        # bierzemy z pierwszej działki/budynku/lokalu jako "reprezentanta"
        rep_miejscowosc = ""
        rep_adres = ""
        for src_key, src_rows in [
            ("dz. - miejscowość", plots_by_id.get(id_rcn, [])),
            ("bud. - miejscowość", buildings_by_id.get(id_rcn, [])),
            ("lok. - miejscowość", locals_by_id.get(id_rcn, [])),
        ]:
            if src_rows and src_rows[0].get(src_key):
                rep_miejscowosc = src_rows[0].get(src_key, "")
                break
        for src_key, src_rows in [
            ("dz. - adres", plots_by_id.get(id_rcn, [])),
            ("bud. - adres", buildings_by_id.get(id_rcn, [])),
            ("lok. - adres", locals_by_id.get(id_rcn, [])),
        ]:
            if src_rows and src_rows[0].get(src_key):
                rep_adres = src_rows[0].get(src_key, "")
                break
        ws.cell(row=current, column=4, value=rep_miejscowosc)
        ws.cell(row=current, column=5, value=rep_adres)
        # Pow. i Cena na poziomie transakcji
        ws.cell(row=current, column=6, value="")  # pow. wyświetla się per-obiekt niżej
        cena_tx = _fmt_money(tx.get("cena transakcji brutto"))
        c = ws.cell(row=current, column=7, value=cena_tx)
        if cena_tx is not None:
            c.number_format = '#,##0.00'
        rynek = tx.get("rodzaj rynku") or ""
        typ_tx = tx.get("rodzaj transakcji") or ""
        rodzaj = tx.get("rodzaj nier.") or ""
        notariusz = tx.get("twórca dokumentu") or ""
        # TODO(notatki-transakcji): notatka uzytkownika doklejana do uwag.
        # Wylaczone do pozniejszej analizy/wdrozenia -- na razie tylko
        # "Komentarz do eksportu" trafia do XLSX (arkusz "Podsumowanie").
        # user_note = tx.get("__user_note") or ""
        # parts = [rodzaj, rynek, typ_tx, notariusz]
        # if user_note:
        #     parts.append(f"📝 {user_note}")
        # uwagi_tx = " · ".join(filter(None, parts))
        uwagi_tx = " · ".join(filter(None, [rodzaj, rynek, typ_tx, notariusz]))
        ws.cell(row=current, column=8, value=uwagi_tx)
        _fill_row(current, PRIMARY, bold=True, white_text=True)
        ws.row_dimensions[current].height = 30
        current += 1

        # --- Działki
        for p in plots_by_id.get(id_rcn, []):
            ws.cell(row=current, column=1, value="🟦 działka")
            ws.cell(row=current, column=2, value=p.get("identyfikator działki") or "")
            ws.cell(row=current, column=3, value="")
            ws.cell(row=current, column=4, value=p.get("dz. - miejscowość") or "")
            ws.cell(row=current, column=5, value=p.get("dz. - adres") or "")
            pow_m2 = _fmt_money(p.get("dz. - pole pow. ewid."))
            c = ws.cell(row=current, column=6, value=pow_m2)
            if pow_m2 is not None:
                c.number_format = '#,##0'
            cena_p = _fmt_money(p.get("dz. - cena brutto"))
            c = ws.cell(row=current, column=7, value=cena_p)
            if cena_p is not None:
                c.number_format = '#,##0.00'
            uwagi = " · ".join(filter(None, [
                p.get("dz. - przeznaczenie w mpzp") or "",
                p.get("dz. - sposób użytkowania") or "",
                p.get("dz. - dodatkowe informacje") or "",
            ]))
            ws.cell(row=current, column=8, value=uwagi)
            _fill_row(current, PRIMARY_SOFT)
            current += 1

        # --- Budynki
        for b in buildings_by_id.get(id_rcn, []):
            ws.cell(row=current, column=1, value="🟨 budynek")
            ws.cell(row=current, column=2, value=b.get("identyfikator budynku") or "")
            ws.cell(row=current, column=3, value="")
            ws.cell(row=current, column=4, value=b.get("bud. - miejscowość") or "")
            ws.cell(row=current, column=5, value=b.get("bud. - adres") or "")
            pow_b = _fmt_money(b.get("bud. - pow. uż."))
            c = ws.cell(row=current, column=6, value=pow_b)
            if pow_b is not None:
                c.number_format = '#,##0.00'
            cena_b = _fmt_money(b.get("bud. - cena brutto"))
            c = ws.cell(row=current, column=7, value=cena_b)
            if cena_b is not None:
                c.number_format = '#,##0.00'
            uwagi = " · ".join(filter(None, [
                b.get("bud. - rodzaj bud.") or "",
                b.get("bud. - dodatkowe informacje") or "",
            ]))
            ws.cell(row=current, column=8, value=uwagi)
            _fill_row(current, BUILDING_BG)
            current += 1

        # --- Lokale
        for l in locals_by_id.get(id_rcn, []):
            ws.cell(row=current, column=1, value="🟩 lokal")
            ws.cell(row=current, column=2, value=l.get("identyfikator lokalu") or "")
            ws.cell(row=current, column=3, value="")
            ws.cell(row=current, column=4, value=l.get("lok. - miejscowość") or "")
            ws.cell(row=current, column=5, value=l.get("lok. - adres") or "")
            pow_l = _fmt_money(l.get("lok. - pow. uż."))
            c = ws.cell(row=current, column=6, value=pow_l)
            if pow_l is not None:
                c.number_format = '#,##0.00'
            cena_l = _fmt_money(l.get("lok. - cena brutto"))
            c = ws.cell(row=current, column=7, value=cena_l)
            if cena_l is not None:
                c.number_format = '#,##0.00'
            piętro = _fmt_int(l.get("lok. - kondygnacja"))
            izb = _fmt_int(l.get("lok. - l. izb"))
            uwagi = " · ".join(filter(None, [
                l.get("lok. - funkcja") or "",
                f"{izb} izb" if izb is not None else "",
                f"piętro {piętro}" if piętro is not None else "",
                l.get("lok. - dodatkowe informacje") or "",
            ]))
            ws.cell(row=current, column=8, value=uwagi)
            _fill_row(current, LOCAL_BG)
            current += 1

        # --- Separator (jeśli to nie ostatnia transakcja)
        if tx != summary_rows[-1]:
            _fill_row(current, SEPARATOR_BG)
            ws.row_dimensions[current].height = 6
            current += 1

    _freeze_header(ws, 1)
    _autosize(ws, {
        1: 14,   # Blok
        2: 34,   # ID / Nr obiektu
        3: 28,   # Data / Sygnatura
        4: 14,   # Miejscowość
        5: 28,   # Adres
        6: 12,   # Pow.
        7: 14,   # Cena
        8: 50,   # Uwagi
    })


def _build_transakcje_sheet(wb: Workbook, rows: list[dict]) -> None:
    """Jedna transakcja na wiersz, skondensowane kolumny (nazwy ≠ QGIS).

    TODO(notatki-transakcji): kolumna "Notatka" wylaczona do pozniejszej
    analizy. Notatki transakcji sa w DB (tabela `notes`), ale na razie nie
    eksportujemy ich automatycznie -- user moze sobie zrobic ad-hoc query.
    Komentarz do tego eksportu (jednorazowy) trafia do arkusza "Podsumowanie"
    przez ExportMeta.export_comment.
    """
    cols = [
        _Col("Data",             "data transakcji",               width=12),
        _Col("ID transakcji",    "id_RCN",                        width=34),
        _Col("Typ nieruchomości","rodzaj nier.",                  width=22),
        _Col("Rynek",            "rodzaj rynku",                  width=14),
        _Col("Typ transakcji",   "rodzaj transakcji",             width=14),
        _Col("Cena [PLN]",       "cena transakcji brutto",
             transform=lambda v, r: _fmt_money(v), width=15, number_format='#,##0.00'),
        _Col("VAT [PLN]",        "kwota podatku VAT",
             transform=lambda v, r: _fmt_money(v), width=12, number_format='#,##0.00'),
        _Col("Liczba nier.",     "liczba nier. w ramach transakcji",
             transform=lambda v, r: _fmt_int(v), width=12),
        _Col("Sprzedający",      "Strona sprzedająca",            width=32),
        _Col("Kupujący",         "Strona kupująca",               width=32),
        _Col("Sygnatura aktu",   "dokument",                      width=28),
        _Col("Notariusz",        "twórca dokumentu",              width=32),
        # TODO(notatki-transakcji): odkomentuj gdy notatki zostana wlaczone
        # _Col("Notatka",          "__user_note",                   width=50),
    ]
    _write_sheet(wb, "Transakcje", rows, cols)


def _build_dzialki_sheet(wb: Workbook, rows: list[dict]) -> None:
    cols = [
        _Col("ID transakcji",      "id_RCN",                        width=34),
        _Col("Data",               "data transakcji",               width=12),
        _Col("Nr działki",         "identyfikator działki",         width=28),
        _Col("Miejscowość",        "dz. - miejscowość",             width=16),
        _Col("Adres",              "dz. - adres",                   width=28),
        _Col("Pow. [m²]",          "dz. - pole pow. ewid.",
             transform=lambda v, r: _fmt_money(v), width=12, number_format='#,##0'),
        _Col("Cena działki [PLN]", "dz. - cena brutto",
             transform=lambda v, r: _fmt_money(v), width=16, number_format='#,##0.00'),
        _Col("Plan zagosp.",       "dz. - przeznaczenie w mpzp",    width=20),
        _Col("Sposób użytk.",      "dz. - sposób użytkowania",      width=18),
        _Col("Uwagi",              "dz. - dodatkowe informacje",    width=40),
    ]
    _write_sheet(wb, "Działki", rows, cols)


def _build_budynki_sheet(wb: Workbook, rows: list[dict]) -> None:
    cols = [
        _Col("ID transakcji",      "id_RCN",                        width=34),
        _Col("Data",               "data transakcji",               width=12),
        _Col("Nr budynku",         "identyfikator budynku",         width=30),
        _Col("Miejscowość",        "bud. - miejscowość",            width=16),
        _Col("Adres",              "bud. - adres",                  width=28),
        _Col("Rodzaj budynku",     "bud. - rodzaj bud.",            width=22),
        _Col("Pow. użytk. [m²]",   "bud. - pow. uż.",
             transform=lambda v, r: _fmt_money(v), width=14, number_format='#,##0.00'),
        _Col("Cena bud. [PLN]",    "bud. - cena brutto",
             transform=lambda v, r: _fmt_money(v), width=16, number_format='#,##0.00'),
        _Col("Uwagi",              "bud. - dodatkowe informacje",   width=40),
    ]
    _write_sheet(wb, "Budynki", rows, cols)


def _build_lokale_sheet(wb: Workbook, rows: list[dict]) -> None:
    cols = [
        _Col("ID transakcji",      "id_RCN",                        width=34),
        _Col("Data",               "data transakcji",               width=12),
        _Col("Nr lokalu",          "identyfikator lokalu",          width=34),
        _Col("Miejscowość",        "lok. - miejscowość",            width=16),
        _Col("Adres",              "lok. - adres",                  width=30),
        _Col("Funkcja",            "lok. - funkcja",                width=16),
        _Col("Pow. [m²]",          "lok. - pow. uż.",
             transform=lambda v, r: _fmt_money(v), width=12, number_format='#,##0.00'),
        _Col("Pow. pomieszcz. przyn. [m²]", "lok. - pow. pom. przyn.",
             transform=lambda v, r: _fmt_money(v), width=15, number_format='#,##0.00'),
        _Col("Izb",                "lok. - l. izb",
             transform=lambda v, r: _fmt_int(v), width=8),
        _Col("Piętro",             "lok. - kondygnacja",
             transform=lambda v, r: _fmt_int(v), width=8),
        _Col("Cena lok. [PLN]",    "lok. - cena brutto",
             transform=lambda v, r: _fmt_money(v), width=16, number_format='#,##0.00'),
        _Col("Uwagi",              "lok. - dodatkowe informacje",   width=40),
    ]
    _write_sheet(wb, "Lokale", rows, cols)


# ---------- Main entrypoint ----------

def build_workshop_xlsx(
    summary_rows: list[dict],
    plot_rows: list[dict],
    building_rows: list[dict],
    local_rows: list[dict],
    *,
    meta: ExportMeta,
) -> bytes:
    """Zwraca bytes XLSX-a w formacie RCN Workshop (różny od pluginu QGIS)."""
    wb = Workbook()
    _build_summary_sheet(wb, meta, summary_rows, plot_rows, building_rows, local_rows)
    _build_raport_zbiorczy_sheet(wb, summary_rows, plot_rows, building_rows, local_rows)
    _build_transakcje_sheet(wb, summary_rows)
    _build_dzialki_sheet(wb, plot_rows)
    _build_budynki_sheet(wb, building_rows)
    _build_lokale_sheet(wb, local_rows)

    # Kolejność arkuszy (Podsumowanie na początku)
    wb.active = 0

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
