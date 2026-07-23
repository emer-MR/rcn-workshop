# -*- coding: utf-8 -*-
import csv
import datetime as _dt
import math
import zipfile
from xml.sax.saxutils import escape


def export_rows_to_csv(rows, path, columns=None):
    columns = columns or _extract_columns(rows)
    with open(path, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})


def export_rows_to_xlsx(rows, path, sheet_name='Dane', columns=None):
    """Stable XLSX export for QGIS/Windows without relying on openpyxl.

    Some QGIS Python builds on Windows crash inside openpyxl while creating
    a new workbook. This writer creates a minimal valid .xlsx package directly.
    """
    export_workbook_to_xlsx([{'name': sheet_name, 'rows': rows, 'columns': columns or _extract_columns(rows)}], path)


def export_workbook_to_xlsx(sheets, path):
    normalized = []
    for idx, sheet in enumerate(sheets, start=1):
        rows = sheet.get('rows') or []
        columns = sheet.get('columns') or _extract_columns(rows)
        name = _sanitize_sheet_name(sheet.get('name') or f'Dane {idx}')
        normalized.append({'name': name, 'rows': rows, 'columns': columns})

    workbook_xml = _workbook_xml_multi([s['name'] for s in normalized])
    workbook_rels_xml = _workbook_rels_xml_multi(len(normalized))

    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', _content_types_xml_multi(len(normalized)))
        zf.writestr('_rels/.rels', _root_rels_xml())
        zf.writestr('docProps/app.xml', _app_xml())
        zf.writestr('docProps/core.xml', _core_xml())
        zf.writestr('xl/workbook.xml', workbook_xml)
        zf.writestr('xl/_rels/workbook.xml.rels', workbook_rels_xml)
        zf.writestr('xl/styles.xml', _styles_xml())
        for idx, sheet in enumerate(normalized, start=1):
            zf.writestr(f'xl/worksheets/sheet{idx}.xml', _build_worksheet_xml(sheet['rows'], sheet['columns'], sheet['name']))




def export_parsed_data_to_txt(parsed_data, path):
    transactions = _group_transactions_for_txt(parsed_data)
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        handle.write(_render_transactions_txt(transactions))


def _group_transactions_for_txt(parsed_data):
    grouped = {}

    def ensure(tx_id):
        tx_id = str(tx_id or '(brak id_RCN)')
        if tx_id not in grouped:
            grouped[tx_id] = {
                'id': tx_id,
                'summary': [],
                'plots': [],
                'buildings': [],
                'locals': [],
            }
        return grouped[tx_id]

    for row in parsed_data.summary_rows or []:
        ensure(row.get('id_RCN')).get('summary').append(row)
    for row in parsed_data.plot_rows or []:
        ensure(row.get('id_RCN')).get('plots').append(row)
    for row in parsed_data.building_rows or []:
        ensure(row.get('id_RCN')).get('buildings').append(row)
    for row in parsed_data.local_rows or []:
        ensure(row.get('id_RCN')).get('locals').append(row)

    def sort_key(item):
        header = item[1]['summary'][:1] or item[1]['plots'][:1] or item[1]['buildings'][:1] or item[1]['locals'][:1]
        row = header[0] if header else {}
        return (str(row.get('data transakcji') or ''), str(item[0]))

    return [bucket for _, bucket in sorted(grouped.items(), key=sort_key)]


def _render_transactions_txt(transactions):
    blocks = []
    for bucket in transactions:
        rows_all = bucket['summary'] + bucket['plots'] + bucket['buildings'] + bucket['locals']
        lines = []
        _append_kv(lines, 'Dokument', _first_nonempty(rows_all, 'dokument'))
        _append_kv(lines, 'IRCW', _first_nonempty(rows_all, 'id_RCN'))
        _append_kv(lines, 'Rodzaj transakcji', _first_nonempty(rows_all, 'rodzaj transakcji'))
        _append_kv(lines, 'Forma obrotu', _first_nonempty(rows_all, 'rodzaj rynku'))
        _append_kv(lines, 'Rodzaj prawa', _first_nonempty(rows_all, 'rodzaj prawa do nieruchomości'))
        _append_kv(lines, 'Udział', _format_share(_first_nonempty(rows_all, 'udzial w prawie - licznik'), _first_nonempty(rows_all, 'udzial w prawie - mianownik')))
        _append_kv(lines, 'Strona sprzedająca', _first_nonempty(rows_all, 'Strona sprzedająca'))
        _append_kv(lines, 'Strona kupująca', _first_nonempty(rows_all, 'Strona kupująca'))
        _append_kv(lines, 'Cena', _format_currency(_first_nonempty(rows_all, 'cena transakcji brutto')))
        lines.append('')

        prop_rows = bucket['summary'] or bucket['plots'] or bucket['buildings'] or bucket['locals'] or [{}]
        seen = set()
        unique_props = []
        for row in prop_rows:
            key = (row.get('rodzaj nier.'), row.get('pole pow. nier. gruntowej'), row.get('cena nieruchomości brutto'))
            if key in seen:
                continue
            seen.add(key)
            unique_props.append(row)
        for idx, row in enumerate(unique_props or [{}], start=1):
            title = 'Nieruchomość' if len(unique_props) <= 1 else f'Nieruchomość {idx}'
            _append_kv(lines, title, _first_nonempty([row], 'rodzaj nier.'))
            _append_remaining_fields(lines, row, [
                ('Pole powierzchni nieruchomości', 'pole pow. nier. gruntowej'),
                ('Cena nieruchomości', 'cena nieruchomości brutto'),
                ('VAT nieruchomości', 'kwota podatku VAT (nier.)'),
                ('Rodzaj prawa', 'rodzaj prawa do nieruchomości'),
                ('Udział - licznik', 'udzial w prawie - licznik'),
                ('Udział - mianownik', 'udzial w prawie - mianownik'),
            ], indent=5, skip_keys=_txt_common_skip_keys('nieruchomosc'))

        _append_object_group(lines, 'Działki', bucket['plots'], [
            ('Numer', 'identyfikator działki'),
            ('Miejscowość', 'dz. - miejscowość'),
            ('Adres', 'dz. - adres'),
            ('Przeznaczenie w MPZP', 'dz. - przeznaczenie w mpzp'),
            ('Sposób użytkowania', 'dz. - sposób użytkowania'),
            ('Powierzchnia', 'dz. - pole pow. ewid.'),
            ('Cena', 'dz. - cena brutto'),
            ('VAT', 'dz. - kwota vat'),
            ('Dodatkowe informacje', 'dz. - dodatkowe informacje'),
        ], indent=5, skip_keys=_txt_common_skip_keys('dzialka'))

        _append_object_group(lines, 'Budynki', bucket['buildings'], [
            ('Identyfikator', 'identyfikator budynku'),
            ('Miejscowość', 'bud. - miejscowość'),
            ('Adres', 'bud. - adres'),
            ('Rodzaj', 'bud. - rodzaj bud.'),
            ('Pow. użytkowa', 'bud. - pow. uż.'),
            ('Cena', 'bud. - cena brutto'),
            ('VAT', 'bud. - kwota vat'),
            ('Dodatkowe informacje', 'bud. - dodatkowe informacje'),
        ], indent=5, skip_keys=_txt_common_skip_keys('budynek'))

        _append_object_group(lines, 'Lokale', bucket['locals'], [
            ('Identyfikator', 'identyfikator lokalu'),
            ('Miejscowość', 'lok. - miejscowość'),
            ('Adres', 'lok. - adres'),
            ('Funkcja', 'lok. - funkcja'),
            ('Pow. użytkowa', 'lok. - pow. uż.'),
            ('Pow. pom. przyn.', 'lok. - pow. pom. przyn.'),
            ('Kondygnacja', 'lok. - kondygnacja'),
            ('Liczba izb', 'lok. - l. izb'),
            ('Cena', 'lok. - cena brutto'),
            ('VAT', 'lok. - kwota vat'),
            ('Dodatkowe informacje', 'lok. - dodatkowe informacje'),
        ], indent=5, skip_keys=_txt_common_skip_keys('lokal'))

        while lines and not lines[-1].strip():
            lines.pop()
        blocks.append('\n'.join(lines))

    separator = '\n' + ('=' * 70) + '\n'
    return separator.join(blocks) + ('\n' if blocks else '')


def _append_object_group(lines, title, rows, ordered_fields, indent=5, skip_keys=None):
    if not rows:
        return
    _append_section(lines, title, indent=indent)
    for row in rows:
        _append_remaining_fields(lines, row, ordered_fields, indent=indent + 4, skip_keys=skip_keys)


def _append_remaining_fields(lines, row, ordered_fields, indent=0, skip_keys=None):
    used = set(skip_keys or [])
    for label, key in ordered_fields:
        if key in used:
            continue
        used.add(key)
        value = row.get(key)
        if key.startswith('_') or value in (None, ''):
            continue
        if 'cena' in key.lower() or 'vat' in key.lower() or 'kwota' in key.lower():
            value = _format_currency(value)
        _append_kv(lines, label, value, indent=indent)

    skip_prefixes = tuple(k for k in used if isinstance(k, str) and k.endswith('*'))
    explicit_skip = {k for k in used if not (isinstance(k, str) and k.endswith('*'))}
    for key, value in row.items():
        if key in explicit_skip or key.startswith('_') or value in (None, ''):
            continue
        if any(str(key).startswith(prefix[:-1]) for prefix in skip_prefixes):
            continue
        label = _txt_label_from_key(key)
        if 'cena' in key.lower() or 'vat' in key.lower() or 'kwota' in key.lower():
            value = _format_currency(value)
        _append_kv(lines, label, value, indent=indent)


def _txt_label_from_key(key):
    text = str(key).replace(' - ', ' ').replace('-', ' ').replace('_', ' ')
    text = ' '.join(part for part in text.split() if part)
    return text[:1].upper() + text[1:] if text else str(key)


def _txt_common_skip_keys(kind):
    common = {
        'id_RCN', 'data transakcji', 'twórca dokumentu', 'dokument', 'liczba nier. w ramach transakcji',
        'rodzaj transakcji', 'rodzaj rynku', 'Strona sprzedająca', 'Strona kupująca',
        'cena transakcji brutto', 'kwota podatku VAT', 'rodzaj nier.', 'cena nieruchomości brutto',
        'kwota podatku VAT (nier.)', 'rodzaj prawa do nieruchomości', 'udzial w prawie - licznik',
        'udzial w prawie - mianownik', 'pole pow. nier. gruntowej'
    }
    prefixes = {
        'dzialka': {'bud. -*', 'lok. -*'},
        'budynek': {'dz. -*', 'lok. -*'},
        'lokal': {'dz. -*', 'bud. -*'},
        'nieruchomosc': {'dz. -*', 'bud. -*', 'lok. -*', 'identyfikator działki', 'identyfikator budynku', 'identyfikator lokalu'},
    }
    return set(common) | set(prefixes.get(kind, set()))


def _append_section(lines, label, indent=0):
    pad = ' ' * indent
    lines.append(f"{pad}{label}:")


def _append_kv(lines, label, value, indent=0, width=28):
    if value is None or value == '':
        return
    pad = ' ' * indent
    lines.append(f"{pad}{label:<{width}} : {_stringify_txt_value(value)}")


def _first_nonempty(rows, key):
    for row in rows:
        value = row.get(key)
        if value not in (None, ''):
            return value
    return None


def _stringify_txt_value(value):
    if value is None:
        return ''
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f'{value:.2f}'.rstrip('0').rstrip('.')
    return str(value)


def _format_currency(value):
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        return f'{value:,.2f} zł'.replace(',', ' ').replace('.', ',')
    return str(value)


def _format_share(num, den):
    if num in (None, '') and den in (None, ''):
        return None
    if den in (None, '', 0):
        return _stringify_txt_value(num)
    return f'{_stringify_txt_value(num)}/{_stringify_txt_value(den)}'
def _extract_columns(rows):
    columns = []
    for row in rows:
        for key in row.keys():
            if key.startswith('_'):
                continue
            if key not in columns:
                columns.append(key)
    return columns


def _sanitize_sheet_name(name):
    name = (name or 'Dane').strip()
    invalid = set('[]:*?/\\')
    cleaned = ''.join('_' if ch in invalid else ch for ch in name)
    cleaned = cleaned.strip("'") or 'Dane'
    return cleaned[:31]


def _excel_col_name(idx):
    result = ''
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        result = chr(65 + rem) + result
    return result


def _xml_text(value):
    return escape(str(value), {'"': '&quot;', "'": '&apos;'})


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _date_to_excel_serial(value):
    if isinstance(value, _dt.datetime):
        delta = value - _dt.datetime(1899, 12, 30)
        return delta.days + (delta.seconds + delta.microseconds / 1_000_000.0) / 86400.0
    if isinstance(value, _dt.date):
        delta = value - _dt.date(1899, 12, 30)
        return delta.days
    return None


def _cell_xml(cell_ref, value, style_id=0):
    if value is None:
        return f'<c r="{cell_ref}" s="{style_id}"/>'

    date_serial = _date_to_excel_serial(value)
    if date_serial is not None:
        return f'<c r="{cell_ref}" s="2"><v>{date_serial}</v></c>'

    if _is_number(value):
        return f'<c r="{cell_ref}" s="{style_id}"><v>{value}</v></c>'

    if isinstance(value, bool):
        return f'<c r="{cell_ref}" s="{style_id}" t="b"><v>{1 if value else 0}</v></c>'

    text = _xml_text(value)
    return f'<c r="{cell_ref}" s="{style_id}" t="inlineStr"><is><t>{text}</t></is></c>'


def _build_worksheet_xml(rows, columns, sheet_name='Dane'):
    display_title = _sheet_display_title(sheet_name)

    widths = [4, max(18, min(len(display_title) + 4, 26))]
    for col in columns:
        max_len = len(str(col))
        for row in rows[:5000]:
            value = row.get(col)
            if value is not None:
                max_len = max(max_len, len(str(value)))
        widths.append(min(max_len + 2, 50))

    last_col_ref = _excel_col_name(len(columns) + 2)

    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        f'<dimension ref="A1:{last_col_ref}{max(len(rows) + 3, 3)}"/>',
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="3" xSplit="2" topLeftCell="C4" activePane="bottomRight" state="frozen"/></sheetView></sheetViews>',
        '<sheetFormatPr defaultRowHeight="18"/>',
        '<cols>'
    ]
    for idx, width in enumerate(widths, start=1):
        parts.append(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')
    parts.append('</cols>')
    parts.append('<sheetData>')

    parts.append('<row r="1" ht="6" customHeight="1">')
    for col_idx in range(1, len(columns) + 3):
        ref = f'{_excel_col_name(col_idx)}1'
        style_id = 3 if col_idx == 1 else 0
        parts.append(_cell_xml(ref, None, style_id=style_id))
    parts.append('</row>')

    parts.append('<row r="2" ht="20" customHeight="1">')
    parts.append(_cell_xml('A2', None, style_id=0))
    parts.append(_cell_xml('B2', 'Wygenerowano przez RCN Workshop', style_id=4))
    for col_idx in range(3, len(columns) + 3):
        ref = f'{_excel_col_name(col_idx)}2'
        parts.append(_cell_xml(ref, None, style_id=0))
    parts.append('</row>')

    parts.append('<row r="3" ht="22" customHeight="1">')
    parts.append(_cell_xml('A3', None, style_id=3))
    parts.append(_cell_xml('B3', display_title, style_id=5))
    for col_idx, column_name in enumerate(columns, start=3):
        ref = f'{_excel_col_name(col_idx)}3'
        parts.append(_cell_xml(ref, column_name, style_id=5))
    parts.append('</row>')

    for row_idx, row in enumerate(rows, start=4):
        parts.append(f'<row r="{row_idx}">')
        parts.append(_cell_xml(f'A{row_idx}', None, style_id=0))
        parts.append(_cell_xml(f'B{row_idx}', None, style_id=0))
        for col_idx, column_name in enumerate(columns, start=3):
            ref = f'{_excel_col_name(col_idx)}{row_idx}'
            value = row.get(column_name)
            style_id = _style_id_for_value(column_name, value, row.get('_row_style'))
            parts.append(_cell_xml(ref, value, style_id=style_id))
        parts.append('</row>')

    parts.append('</sheetData>')
    parts.append(f'<autoFilter ref="C3:{last_col_ref}{max(len(rows) + 3, 3)}"/>')
    parts.append('</worksheet>')
    return ''.join(parts)


def _sheet_display_title(sheet_name):
    mapping = {
        'Zestawienie': ' A. Transakcja',
        'Działki': ' A. Transakcja',
        'Budynki': ' A. Transakcja',
        'Lokale': ' A. Transakcja',
    }
    return mapping.get(sheet_name, ' A. Transakcja')


def _style_id_for_value(column_name, value, row_style=None):
    name = str(column_name or '').lower()
    if row_style in {'transakcja', 'dzialka', 'budynek', 'lokal', 'separator'}:
        mapping = {
            'transakcja': 7,
            'dzialka': 11,
            'budynek': 12,
            'lokal': 13,
            'separator': 14,
        }
        return mapping[row_style]
    if value is None:
        return 0
    if _date_to_excel_serial(value) is not None:
        return 2
    if _is_number(value):
        if 'cena' in name or 'vat' in name or 'kwota' in name:
            return 6
        return 0
    return 0

def build_colored_summary_sheet(summary_rows, plot_rows, building_rows, local_rows):
    columns = [
        'Typ rekordu', 'id_RCN', 'data transakcji', 'dokument', 'rodzaj transakcji', 'rodzaj rynku',
        'rodzaj nier.', 'Id obiektu', 'miejscowość', 'adres', 'powierzchnia', 'cena brutto', 'VAT', 'Szczegóły'
    ]

    buckets = {}
    order = []

    def ensure(tx_id):
        key = str(tx_id or '(brak id_RCN)')
        if key not in buckets:
            buckets[key] = {'summary': [], 'plots': [], 'buildings': [], 'locals': []}
            order.append(key)
        return buckets[key]

    for row in summary_rows or []:
        ensure(row.get('id_RCN'))['summary'].append(row)
    for row in plot_rows or []:
        ensure(row.get('id_RCN'))['plots'].append(row)
    for row in building_rows or []:
        ensure(row.get('id_RCN'))['buildings'].append(row)
    for row in local_rows or []:
        ensure(row.get('id_RCN'))['locals'].append(row)

    result = []
    first_bucket = True
    for tx_id in order:
        bucket = buckets[tx_id]
        base = (bucket['summary'] or bucket['plots'] or bucket['buildings'] or bucket['locals'] or [{}])[0]

        if not first_bucket:
            result.append({'_row_style': 'separator'})
        first_bucket = False

        transaction_rows = bucket['summary'] or [base]
        for s in transaction_rows:
            result.append({
                '_row_style': 'transakcja',
                'Typ rekordu': 'Transakcja',
                'id_RCN': tx_id,
                'data transakcji': s.get('data transakcji'),
                'dokument': s.get('dokument'),
                'rodzaj transakcji': s.get('rodzaj transakcji'),
                'rodzaj rynku': s.get('rodzaj rynku'),
                'rodzaj nier.': s.get('rodzaj nier.'),
                'Id obiektu': '',
                'miejscowość': '',
                'adres': '',
                'powierzchnia': s.get('pole pow. nier. gruntowej'),
                'cena brutto': s.get('cena transakcji brutto') or s.get('cena nieruchomości brutto'),
                'VAT': s.get('kwota VAT') or s.get('kwota vat'),
                'Szczegóły': _join_nonempty([s.get('twórca dokumentu'), s.get('rodzaj prawa do nieruchomości')], ' | '),
            })

        for p in bucket['plots']:
            result.append({
                '_row_style': 'dzialka',
                'Typ rekordu': '   Działka',
                'id_RCN': tx_id,
                'data transakcji': '',
                'dokument': '',
                'rodzaj transakcji': '',
                'rodzaj rynku': '',
                'rodzaj nier.': p.get('rodzaj nier.'),
                'Id obiektu': p.get('identyfikator działki'),
                'miejscowość': p.get('dz. - miejscowość'),
                'adres': p.get('dz. - adres'),
                'powierzchnia': p.get('dz. - pole pow. ewid.'),
                'cena brutto': p.get('dz. - cena brutto'),
                'VAT': p.get('dz. - kwota vat'),
                'Szczegóły': _join_nonempty([p.get('dz. - przeznaczenie w mpzp'), p.get('dz. - sposób użytkowania')], ' | '),
            })

        for b in bucket['buildings']:
            result.append({
                '_row_style': 'budynek',
                'Typ rekordu': '   Budynek',
                'id_RCN': tx_id,
                'data transakcji': '',
                'dokument': '',
                'rodzaj transakcji': '',
                'rodzaj rynku': '',
                'rodzaj nier.': b.get('rodzaj nier.'),
                'Id obiektu': b.get('identyfikator budynku'),
                'miejscowość': b.get('bud. - miejscowość'),
                'adres': b.get('bud. - adres'),
                'powierzchnia': b.get('bud. - pow. uż.'),
                'cena brutto': b.get('bud. - cena brutto'),
                'VAT': b.get('bud. - kwota vat'),
                'Szczegóły': _join_nonempty([b.get('bud. - rodzaj bud.'), b.get('bud. - stan techniczny')], ' | '),
            })

        for l in bucket['locals']:
            result.append({
                '_row_style': 'lokal',
                'Typ rekordu': '   Lokal',
                'id_RCN': tx_id,
                'data transakcji': '',
                'dokument': '',
                'rodzaj transakcji': '',
                'rodzaj rynku': '',
                'rodzaj nier.': l.get('rodzaj nier.'),
                'Id obiektu': l.get('identyfikator lokalu'),
                'miejscowość': l.get('lok. - miejscowość'),
                'adres': l.get('lok. - adres'),
                'powierzchnia': l.get('lok. - pow. uż.'),
                'cena brutto': l.get('lok. - cena brutto'),
                'VAT': l.get('lok. - kwota vat'),
                'Szczegóły': _join_nonempty([l.get('lok. - funkcja'), l.get('lok. - kondygnacja'), l.get('lok. - l. izb')], ' | '),
            })
    return {'name': 'Raport zbiorczy', 'rows': result, 'columns': columns}


def _join_nonempty(values, sep=' | '):
    return sep.join(str(v) for v in values if v not in (None, ''))

def _content_types_xml():
    return _content_types_xml_multi(1)


def _content_types_xml_multi(sheet_count):
    parts = ['''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>''']
    for idx in range(1, sheet_count + 1):
        parts.append(f'  <Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    parts.append('</Types>')
    return '\n'.join(parts)


def _root_rels_xml():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''


def _app_xml():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>RCN Workshop</Application>
</Properties>'''


def _core_xml():
    created = _dt.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dc="http://purl.org/dc/elements/1.1/"
                   xmlns:dcterms="http://purl.org/dc/terms/"
                   xmlns:dcmitype="http://purl.org/dc/dcmitype/"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>RCN Workshop</dc:creator>
  <cp:lastModifiedBy>RCN Workshop</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>
</cp:coreProperties>'''


def _workbook_xml(sheet_name):
    return _workbook_xml_multi([sheet_name])


def _workbook_xml_multi(sheet_names):
    lines = ['''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <workbookViews><workbookView xWindow="0" yWindow="0" windowWidth="24000" windowHeight="12000"/></workbookViews>
  <sheets>''']
    for idx, name in enumerate(sheet_names, start=1):
        lines.append(f'    <sheet name="{_xml_text(name)}" sheetId="{idx}" r:id="rId{idx}"/>')
    lines.append('  </sheets>')
    lines.append('</workbook>')
    return '\n'.join(lines)


def _workbook_rels_xml():
    return _workbook_rels_xml_multi(1)


def _workbook_rels_xml_multi(sheet_count):
    lines = ['''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">''']
    for idx in range(1, sheet_count + 1):
        lines.append(f'  <Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>')
    lines.append(f'  <Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    lines.append('</Relationships>')
    return '\n'.join(lines)


def _styles_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1">
    <numFmt numFmtId="164" formatCode="#,##0.00 &quot;zł&quot;"/>
  </numFmts>
  <fonts count="4">
    <font><sz val="11"/><name val="Calibri"/><family val="2"/><color theme="1"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/><family val="2"/><color rgb="FF1F1F1F"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/><family val="2"/><color rgb="FF1F4E79"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/><family val="2"/><color rgb="FF000000"/></font>
  </fonts>
  <fills count="9">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFDE9D9"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF7F7F7"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF4B183"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFC6E0B4"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFBDD7EE"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="4">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left style="thin"><color rgb="FFD9D9D9"/></left><right style="thin"><color rgb="FFD9D9D9"/></right><top style="thin"><color rgb="FFD9D9D9"/></top><bottom style="thin"><color rgb="FFD9D9D9"/></bottom><diagonal/></border>
    <border><left/><right/><top style="medium"><color rgb="FFBFBFBF"/></top><bottom style="medium"><color rgb="FFBFBFBF"/></bottom><diagonal/></border>
    <border><left style="thin"><color rgb="FFD9D9D9"/></left><right style="thin"><color rgb="FFD9D9D9"/></right><top style="medium"><color rgb="FFFFFFFF"/></top><bottom style="medium"><color rgb="FFFFFFFF"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="15">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
    <xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="0" fontId="0" fillId="2" borderId="0" xfId="0" applyFill="1"/>
    <xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>
    <xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="0" fontId="1" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
    <xf numFmtId="0" fontId="1" fillId="6" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" indent="1"/></xf>
    <xf numFmtId="0" fontId="1" fillId="7" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" indent="1"/></xf>
    <xf numFmtId="0" fontId="1" fillId="8" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" indent="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="6" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" indent="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="7" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" indent="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="8" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" indent="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="2" xfId="0" applyBorder="1" applyFill="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""
