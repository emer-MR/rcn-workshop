# -*- coding: utf-8 -*-
"""RCN GML parser — adapted from an internal QGIS viewer tool.

Behaviour must remain observably identical to the original:
- same ParsedData shape (summary_rows / plot_rows / building_rows / local_rows),
- same column names (Polish, matching the original),
- same EPSG detection (2176-2180),
- same XY swap heuristic,
- same WKT emission for POLYGON / MULTIPOLYGON / POINT,
- same enum dictionary (loaded from resources/slowniki.json).
"""
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

try:
    from lxml import etree as ET  # ~2-3x faster, lower memory
    _HAS_LXML = True
except ImportError:  # pragma: no cover
    import xml.etree.ElementTree as ET
    _HAS_LXML = False


NS = {
    'gml': 'http://www.opengis.net/gml/3.2',
    'rcn': 'urn:gugik:specyfikacje:gmlas:rejestrcennieruchomosci:1.0',
    'xlink': 'http://www.w3.org/1999/xlink',
}


@dataclass
class ParsedData:
    source_path: str = ''
    epsg: str = 'EPSG:2177'
    summary_rows: list = field(default_factory=list)
    plot_rows: list = field(default_factory=list)
    building_rows: list = field(default_factory=list)
    local_rows: list = field(default_factory=list)
    transaction_count: int = 0
    object_count: int = 0
    diagnostics: dict = field(default_factory=dict)


class RcnGmlParser:
    def __init__(self, dictionary_path=None):
        self.dictionary_path = dictionary_path or os.path.join(
            os.path.dirname(__file__), 'resources', 'slowniki.json'
        )
        self.enum_map = self._load_dictionary(self.dictionary_path)
        self.objects = {}
        self.current_srs_name = 'urn:ogc:def:crs:EPSG::2177'

    # Tagi obiektów RCN ekstraktowanych do dict-ów podczas streamingu iterparse.
    # Każdy taki obiekt ma `gml:id` — używany jako klucz w `self.objects`.
    _OBJECT_TAGS = frozenset({
        'RCN_Transakcja', 'RCN_Dokument', 'RCN_Nieruchomosc',
        'RCN_Dzialka', 'RCN_Budynek', 'RCN_Lokal', 'RCN_Adres',
    })
    _GML_ID_ATTR = '{http://www.opengis.net/gml/3.2}id'

    def parse(self, gml_path, progress_callback=None):
        """Streaming parser: lxml iterparse + ekstrakcja do lekkich dictów.

        Peak RAM ~stała niezależnie od rozmiaru pliku (vs `ET.parse()` które
        budowało pełne drzewo w pamięci, ~3-5× rozmiar pliku). Dla 3 GB GML:
        było ~12-15 GB, jest ~400-600 MB.

        Cross-references (xlink:href) zachowane: po pełnym przejściu pliku
        `self.objects` zawiera wszystkie obiekty jako dicty, pass 2 robi
        resolve nieruchomosci/dzialki/budynki/lokale po `gml_id`.
        """
        def report(stage, current, total, message):
            if progress_callback:
                progress_callback(stage, current, total, message)

        report('start', 0, 100, 'Otwieranie pliku GML (streaming)...')
        self.objects = {}
        epsg = 'EPSG:2177'
        transaction_ids = []
        # Pamiętaj kolejność per-typ żeby diagnostics 'object_local_names_seen' był stabilny
        seen_local_tags = set()

        # Single-pass streaming. lxml `iterparse` emituje 'end' gdy element się
        # zamyka; możemy go wtedy ekstrakcją zżuć i `clear()`-ować, zwalniając
        # pamięć poprzedników. xml.etree fallback nie ma `getprevious()` ale
        # sam `elem.clear()` też pomaga (drzewo nie rośnie nieskończenie).
        #
        # SECURITY (2026-04-29 review): bez `resolve_entities=False` lxml
        # rozwija external entities (np. `<!ENTITY xxe SYSTEM "file:///...">`),
        # co dawałoby XXE -- treść pliku trafiałaby do `attributes_json` i była
        # czytelna dla readonly usera przez /transactions/{id}/details.
        # `no_network=True` blokuje SSRF, `load_dtd=False` blokuje DTD entity
        # processing całkowicie.
        iterparse_kwargs = {"events": ('end',)}
        try:
            iterparse_kwargs.update({
                "resolve_entities": False,
                "no_network": True,
                "load_dtd": False,
                "huge_tree": False,
            })
            context = ET.iterparse(gml_path, **iterparse_kwargs)
        except TypeError:
            # Fallback dla xml.etree (stdlib) który nie wspiera tych kwargów --
            # tam XXE jest mniej groźne (xml.etree nie resolve external entities
            # w defragmencie 3.7.1+, plus stdlib expat wymaga explicit DTD).
            context = ET.iterparse(gml_path, events=('end',))
        scan_count = 0
        for _event, elem in context:
            local_tag = self._local_name(elem.tag)
            if local_tag not in self._OBJECT_TAGS:
                # featureMember/inne wrappery — pomijamy ekstrakcję, ale pozwalamy
                # iterparse przejść dalej. lxml usunie je przy clear() poprzedników
                # po przetworzeniu kolejnego obiektu RCN_*.
                continue

            gml_id = elem.attrib.get(self._GML_ID_ATTR)

            # EPSG detection raz, na pierwszym obiekcie z geometrią
            if epsg == 'EPSG:2177':
                geom = elem.find('.//*[@srsName]')
                if geom is not None:
                    self.current_srs_name = geom.attrib.get('srsName') or self.current_srs_name
                    epsg = self._extract_epsg(self.current_srs_name) or epsg

            obj_dict = self._extract_object_dict(local_tag, elem)
            if obj_dict and gml_id:
                self.objects[gml_id] = obj_dict
                if local_tag == 'RCN_Transakcja':
                    transaction_ids.append(gml_id)
            seen_local_tags.add(local_tag)
            scan_count += 1

            # Zwolnij subtree elementu — usuwamy go z drzewa lxml. Bez tego
            # iterparse trzyma rosnące drzewo w pamięci (defeats the purpose).
            elem.clear()
            if _HAS_LXML:
                # Usuń poprzedników rodzeństwa (już przetworzonych) z parenta.
                # Idiom z https://lxml.de/parsing.html#modifying-the-tree
                parent = elem.getparent()
                if parent is not None:
                    while parent[0] is not elem:
                        del parent[0]

            if scan_count % 5000 == 0:
                report('scan', scan_count, max(scan_count, 1),
                       f'Streaming: {scan_count} obiektów, {len(transaction_ids)} transakcji...')

        # Cleanup contextu (lxml zwalnia bufor parsera)
        del context

        report('scan', scan_count, max(scan_count, 1),
               f'Zakończono streaming: {scan_count} obiektów, {len(transaction_ids)} transakcji.')

        object_count = len(self.objects)
        parsed = ParsedData(source_path=gml_path, epsg=epsg, object_count=object_count)
        parsed.diagnostics = {
            'source_epsg': epsg,
            'target_epsg': 'EPSG:2180',
            'object_count': object_count,
            'transaction_count': 0,
            'summary_count': 0,
            'plot_count': 0,
            'building_count': 0,
            'local_count': 0,
            'plot_geom_count': 0,
            'building_geom_count': 0,
            'local_geom_count': 0,
            'missing_geometry': {'dzialki': [], 'budynki': [], 'lokale': []},
            'log_lines': [],
        }

        if not transaction_ids:
            # Diagnostyka cichego skipu — pomaga rozpoznać niestandardowy dialekt RCN
            # (inny namespace, inna nazwa głównego elementu, plik bez featureMember itp.).
            local_names = sorted(seen_local_tags)
            parsed.diagnostics['empty_parse_reason'] = {
                'objects_count': object_count,
                'object_local_names_seen': local_names[:30],
            }
            try:
                import logging as _logging
                _logging.getLogger('rcn_core.parser').warning(
                    'Parser found 0 RCN_Transakcja in %s. Diagnostics: objects=%d, object_local_names=%s',
                    gml_path, object_count, local_names[:30],
                )
            except Exception:
                pass

        total_transactions = max(len(transaction_ids), 1)
        report('transactions', 0, total_transactions, 'Przetwarzanie transakcji...')

        for tx_index, tx_id in enumerate(transaction_ids, start=1):
            obj = self.objects[tx_id]
            transaction = self._build_transaction_row(obj)
            parsed.transaction_count += 1
            parsed.diagnostics['transaction_count'] = parsed.transaction_count
            summary_candidates = []

            for nier_id in self._hrefs(obj, 'nieruchomosc'):
                nier = self.objects.get(nier_id)
                if nier is None:
                    continue
                common = self._merge_transaction_with_nieruchomosc(transaction, nier)

                local_plot_rows = []
                for plot_id in self._hrefs(nier, 'dzialka'):
                    plot = self.objects.get(plot_id)
                    if plot is None:
                        continue
                    row = self._build_plot_row(common, plot)
                    parsed.plot_rows.append(row)
                    local_plot_rows.append(row)
                    summary_candidates.append(row)
                    parsed.diagnostics['plot_count'] += 1
                    if row.get('_wkt'):
                        parsed.diagnostics['plot_geom_count'] += 1
                    else:
                        parsed.diagnostics['missing_geometry']['dzialki'].append(row.get('identyfikator działki') or row.get('id_RCN') or '(brak id)')

                local_building_rows = []
                for building_id in self._hrefs(nier, 'budynek'):
                    building = self.objects.get(building_id)
                    if building is None:
                        continue
                    row = self._build_building_row(common, building)
                    parsed.building_rows.append(row)
                    local_building_rows.append(row)
                    parsed.diagnostics['building_count'] += 1
                    if row.get('_wkt'):
                        parsed.diagnostics['building_geom_count'] += 1
                    else:
                        parsed.diagnostics['missing_geometry']['budynki'].append(row.get('identyfikator budynku') or row.get('id_RCN') or '(brak id)')
                    if not local_plot_rows:
                        summary_candidates.append(row)

                local_local_rows = []
                for local_id in self._hrefs(nier, 'lokal'):
                    local_obj = self.objects.get(local_id)
                    if local_obj is None:
                        continue
                    row = self._build_local_row(common, local_obj)
                    parsed.local_rows.append(row)
                    local_local_rows.append(row)
                    parsed.diagnostics['local_count'] += 1
                    if row.get('_wkt'):
                        parsed.diagnostics['local_geom_count'] += 1
                    else:
                        parsed.diagnostics['missing_geometry']['lokale'].append(row.get('identyfikator lokalu') or row.get('id_RCN') or '(brak id)')
                    if not local_plot_rows and not local_building_rows:
                        summary_candidates.append(row)

                if not (local_plot_rows or local_building_rows or local_local_rows):
                    summary_candidates.append(common.copy())

            if not summary_candidates:
                summary_candidates.append(transaction.copy())
            parsed.summary_rows.extend(summary_candidates)
            if tx_index == 1 or tx_index == total_transactions or tx_index % 5 == 0:
                report('transactions', tx_index, total_transactions, f'Przetwarzanie transakcji... {tx_index}/{total_transactions}')

        parsed.diagnostics['summary_count'] = len(parsed.summary_rows)
        parsed.diagnostics['log_lines'] = self._build_diagnostic_log(parsed)
        report('done', total_transactions, total_transactions, 'Zakończono wczytywanie danych.')
        return parsed

    def _build_transaction_row(self, transaction_obj):
        document = self.objects.get(self._href(transaction_obj, 'podstawaPrawna'))
        document_number = self._text(document, 'oznaczenieDokumentu') if document is not None else None
        document_date = self._text(document, 'dataSporzadzeniaDokumentu') if document is not None else None

        if document_number and document_date:
            document_label = f'{document_number} z dnia {document_date}'
        else:
            document_label = document_number or document_date

        # Preferuj lokalnyId z RCN_IdentyfikatorIIP (standard GUGiK, stabilny UUID
        # per transakcja). Fallback na oznaczenieTransakcji dla operatorow ktorzy
        # umieszczaja tam UUID (Lodz/Belchatow). Niektorzy operatorzy (Poznan)
        # wpisuja w oznaczenieTransakcji kolejny numer pozycji (1,2,3...),
        # co powoduje masowe kolizje id_rcn.
        local_id = self._text(transaction_obj, 'lokalnyId')
        oznaczenie = self._text(transaction_obj, 'oznaczenieTransakcji')
        id_rcn = local_id.replace('-', '') if local_id else oznaczenie

        return {
            'id_RCN': id_rcn,
            'data transakcji': document_date,
            'twórca dokumentu': self._text(document, 'tworcaDokumentu') if document is not None else None,
            'dokument': document_label,
            'liczba nier. w ramach transakcji': len(self._hrefs(transaction_obj, 'nieruchomosc')),
            'rodzaj transakcji': self._decode('RCN_RodzajTransakcjiType', self._text(transaction_obj, 'rodzajTransakcji')),
            'rodzaj rynku': self._decode('RCN_RodzajRynkuType', self._text(transaction_obj, 'rodzajRynku')),
            'Strona sprzedająca': self._decode(
                'RCN_StronaSprzedajacaKupujacaType', self._text(transaction_obj, 'stronaSprzedajaca')
            ),
            'Strona kupująca': self._decode(
                'RCN_StronaSprzedajacaKupujacaType', self._text(transaction_obj, 'stronaKupujaca')
            ),
            'cena transakcji brutto': self._to_number(self._text(transaction_obj, 'cenaTransakcjiBrutto')),
            'kwota podatku VAT': self._to_number(self._text(transaction_obj, 'kwotaPodatkuVAT')),
        }

    def _merge_transaction_with_nieruchomosc(self, transaction_row, nier_obj):
        licznik, mianownik = self._split_share(self._text(nier_obj, 'udzialWPrawieDoNieruchomosci'))
        row = dict(transaction_row)
        row.update({
            'rodzaj nier.': self._decode('RCN_RodzajNieruchomosciType', self._text(nier_obj, 'rodzajNieruchomosci')),
            'cena nieruchomości brutto': self._to_number(self._text(nier_obj, 'cenaNieruchomosciBrutto')),
            'kwota podatku VAT (nier.)': self._to_number(self._text(nier_obj, 'kwotaPodatkuVAT')),
            'rodzaj prawa do nieruchomości': self._decode(
                'RCN_RodzajPrawaType', self._text(nier_obj, 'rodzajPrawaDoNieruchomosci')
            ),
            'udzial w prawie - licznik': licznik,
            'udzial w prawie - mianownik': mianownik,
            'pole pow. nier. gruntowej': self._to_number(self._text(nier_obj, 'polePowierzchniNieruchomosciGruntowej')),
        })
        return row

    def _build_plot_row(self, common_row, plot_obj):
        address = self._address(plot_obj, 'adresDzialki')
        row = dict(common_row)
        row.update({
            'identyfikator działki': self._text(plot_obj, 'idDzialki'),
            'dz. - miejscowość': address['miejscowosc'],
            'dz. - adres': address['adres'],
            'dz. - przeznaczenie w mpzp': self._decode(
                'RCN_PrzeznaczenieWMPZPType', self._text(plot_obj, 'przeznaczenieWMPZP')
            ),
            'dz. - sposób użytkowania': self._decode(
                'RCN_SposobUzytkowaniaType', self._text(plot_obj, 'sposobUzytkowania')
            ),
            'dz. - pole pow. ewid.': self._to_number(self._text(plot_obj, 'polePowierzchniEwidencyjnej')),
            'dz. - cena brutto': self._to_number(self._text(plot_obj, 'cenaDzialkiEwidencyjnejBrutto')),
            'dz. - kwota vat': self._to_number(self._text(plot_obj, 'kwotaPodatkuVAT')),
            'dz. - dodatkowe informacje': self._text(plot_obj, 'dodatkoweInformacje'),
            '_wkt': plot_obj.get('wkt') if plot_obj else None,
        })
        return row

    def _build_building_row(self, common_row, building_obj):
        address = self._address(building_obj, 'adresBudynku')
        row = dict(common_row)
        row.update({
            'identyfikator budynku': self._text(building_obj, 'idBudynku'),
            'bud. - miejscowość': address['miejscowosc'],
            'bud. - adres': address['adres'],
            'bud. - rodzaj bud.': self._decode('RCN_RodzajBudynkuType', self._text(building_obj, 'rodzajBudynku')),
            'bud. - pow. uż.': self._to_number(self._text(building_obj, 'powierzchniaUzytkowaBudynku')),
            'bud. - cena brutto': self._to_number(self._text(building_obj, 'cenaBudynkuBrutto')),
            'bud. - kwota vat': self._to_number(self._text(building_obj, 'kwotaPodatkuVAT')),
            'bud. - dodatkowe informacje': self._text(building_obj, 'dodatkoweInformacje'),
            '_wkt': building_obj.get('wkt') if building_obj else None,
        })
        return row

    def _build_local_row(self, common_row, local_obj):
        address = self._address(local_obj, 'adresBudynkuZLokalem')
        row = dict(common_row)
        row.update({
            'identyfikator lokalu': self._text(local_obj, 'idLokalu'),
            'lok. - miejscowość': address['miejscowosc'],
            'lok. - adres': address['adres'],
            'lok. - funkcja': self._decode('RCN_FunkcjaLokaluType', self._text(local_obj, 'funkcjaLokalu')),
            'lok. - pow. uż.': self._to_number(self._text(local_obj, 'powUzytkowaLokalu')),
            'lok. - pow. pom. przyn.': self._to_number(self._text(local_obj, 'powUzytkowaPomieszczenPrzynal')),
            'lok. - kondygnacja': self._to_int(self._text(local_obj, 'nrKondygnacji')),
            'lok. - l. izb': self._to_int(self._text(local_obj, 'liczbaIzb')),
            'lok. - dodatkowe informacje': self._text(local_obj, 'dodatkoweInformacje'),
            'lok. - cena brutto': self._to_number(self._text(local_obj, 'cenaLokaluBrutto')),
            'lok. - kwota vat': self._to_number(self._text(local_obj, 'kwotaPodatkuVAT')),
            '_wkt': local_obj.get('wkt') if local_obj else None,
        })
        return row

    def _address(self, source_object, relation_name):
        address_ref = self._href(source_object, relation_name)
        if not address_ref:
            return {'miejscowosc': None, 'adres': None}
        address_object = self.objects.get(address_ref)
        if address_object is None:
            return {'miejscowosc': None, 'adres': None}
        miejscowosc = self._text(address_object, 'miejscowosc')
        ulica = self._text(address_object, 'ulica')
        numer = self._text(address_object, 'numerPorzadkowy')
        address_parts = [part for part in [ulica, numer] if part]
        return {'miejscowosc': miejscowosc, 'adres': ' '.join(address_parts) if address_parts else None}

    # ---- Object extraction (Element → dict) -- używane TYLKO w iterparse loop ----

    def _extract_object_dict(self, local_tag, elem):
        """Konwertuje element lxml/ET do lekkiego dicta z polami potrzebnymi
        w pass-2 (`_build_*_row`). Zachowuje semantykę xlink:href (jako
        `<name>_href` lub `<name>_hrefs`). Geometria ekstraktowana eager
        (zapisana jako `wkt`) — drzewo elementu może być po tym `clear()`-ed."""
        if local_tag == 'RCN_Transakcja':
            return {
                'oznaczenieTransakcji': self._elem_text(elem, 'oznaczenieTransakcji'),
                'lokalnyId': self._elem_text_path(elem, 'rcn:IdRCN/rcn:RCN_IdentyfikatorIIP/rcn:lokalnyId'),
                'rodzajTransakcji': self._elem_text(elem, 'rodzajTransakcji'),
                'rodzajRynku': self._elem_text(elem, 'rodzajRynku'),
                'stronaSprzedajaca': self._elem_text(elem, 'stronaSprzedajaca'),
                'stronaKupujaca': self._elem_text(elem, 'stronaKupujaca'),
                'cenaTransakcjiBrutto': self._elem_text(elem, 'cenaTransakcjiBrutto'),
                'kwotaPodatkuVAT': self._elem_text(elem, 'kwotaPodatkuVAT'),
                'nieruchomosc_hrefs': self._elem_hrefs(elem, 'nieruchomosc'),
                'podstawaPrawna_href': self._elem_href(elem, 'podstawaPrawna'),
                '_local_tag': local_tag,
            }
        if local_tag == 'RCN_Dokument':
            return {
                'oznaczenieDokumentu': self._elem_text(elem, 'oznaczenieDokumentu'),
                'dataSporzadzeniaDokumentu': self._elem_text(elem, 'dataSporzadzeniaDokumentu'),
                'tworcaDokumentu': self._elem_text(elem, 'tworcaDokumentu'),
                '_local_tag': local_tag,
            }
        if local_tag == 'RCN_Nieruchomosc':
            return {
                'udzialWPrawieDoNieruchomosci': self._elem_text(elem, 'udzialWPrawieDoNieruchomosci'),
                'rodzajNieruchomosci': self._elem_text(elem, 'rodzajNieruchomosci'),
                'cenaNieruchomosciBrutto': self._elem_text(elem, 'cenaNieruchomosciBrutto'),
                'kwotaPodatkuVAT': self._elem_text(elem, 'kwotaPodatkuVAT'),
                'rodzajPrawaDoNieruchomosci': self._elem_text(elem, 'rodzajPrawaDoNieruchomosci'),
                'polePowierzchniNieruchomosciGruntowej': self._elem_text(
                    elem, 'polePowierzchniNieruchomosciGruntowej'
                ),
                'dzialka_hrefs': self._elem_hrefs(elem, 'dzialka'),
                'budynek_hrefs': self._elem_hrefs(elem, 'budynek'),
                'lokal_hrefs': self._elem_hrefs(elem, 'lokal'),
                '_local_tag': local_tag,
            }
        if local_tag == 'RCN_Dzialka':
            return {
                'idDzialki': self._elem_text(elem, 'idDzialki'),
                'przeznaczenieWMPZP': self._elem_text(elem, 'przeznaczenieWMPZP'),
                'sposobUzytkowania': self._elem_text(elem, 'sposobUzytkowania'),
                # Powierzchnia ZAWSZE w m² (parser respektuje atrybut `uom`).
                # Łódź/Bełchatów GML: uom="ha", Warszawa: uom="m2". Bez tego
                # samej liczbie nie można ufać — np. 456 = 456 m² (Warszawa)
                # albo 0.0456 ha = 456 m² (Łódź) → różnica × 10000.
                'polePowierzchniEwidencyjnej': self._elem_area_m2(elem, 'polePowierzchniEwidencyjnej'),
                'cenaDzialkiEwidencyjnejBrutto': self._elem_text(elem, 'cenaDzialkiEwidencyjnejBrutto'),
                'kwotaPodatkuVAT': self._elem_text(elem, 'kwotaPodatkuVAT'),
                'dodatkoweInformacje': self._elem_text(elem, 'dodatkoweInformacje'),
                'adresDzialki_href': self._elem_href(elem, 'adresDzialki'),
                'wkt': self._extract_geometry_wkt(elem),
                '_local_tag': local_tag,
            }
        if local_tag == 'RCN_Budynek':
            return {
                'idBudynku': self._elem_text(elem, 'idBudynku'),
                'rodzajBudynku': self._elem_text(elem, 'rodzajBudynku'),
                'powierzchniaUzytkowaBudynku': self._elem_text(elem, 'powierzchniaUzytkowaBudynku'),
                'cenaBudynkuBrutto': self._elem_text(elem, 'cenaBudynkuBrutto'),
                'kwotaPodatkuVAT': self._elem_text(elem, 'kwotaPodatkuVAT'),
                'dodatkoweInformacje': self._elem_text(elem, 'dodatkoweInformacje'),
                'adresBudynku_href': self._elem_href(elem, 'adresBudynku'),
                'wkt': self._extract_geometry_wkt(elem),
                '_local_tag': local_tag,
            }
        if local_tag == 'RCN_Lokal':
            return {
                'idLokalu': self._elem_text(elem, 'idLokalu'),
                'funkcjaLokalu': self._elem_text(elem, 'funkcjaLokalu'),
                'powUzytkowaLokalu': self._elem_text(elem, 'powUzytkowaLokalu'),
                'powUzytkowaPomieszczenPrzynal': self._elem_text(elem, 'powUzytkowaPomieszczenPrzynal'),
                'nrKondygnacji': self._elem_text(elem, 'nrKondygnacji'),
                'liczbaIzb': self._elem_text(elem, 'liczbaIzb'),
                'dodatkoweInformacje': self._elem_text(elem, 'dodatkoweInformacje'),
                'cenaLokaluBrutto': self._elem_text(elem, 'cenaLokaluBrutto'),
                'kwotaPodatkuVAT': self._elem_text(elem, 'kwotaPodatkuVAT'),
                'adresBudynkuZLokalem_href': self._elem_href(elem, 'adresBudynkuZLokalem'),
                'wkt': self._extract_geometry_wkt(elem),
                '_local_tag': local_tag,
            }
        if local_tag == 'RCN_Adres':
            return {
                'miejscowosc': self._elem_text(elem, 'miejscowosc'),
                'ulica': self._elem_text(elem, 'ulica'),
                'numerPorzadkowy': self._elem_text(elem, 'numerPorzadkowy'),
                '_local_tag': local_tag,
            }
        return None

    @staticmethod
    def _elem_text(parent, name):
        if parent is None:
            return None
        element = parent.find(f'rcn:{name}', NS)
        if element is None or element.text is None:
            return None
        value = element.text.strip()
        return value if value else None

    @staticmethod
    def _elem_text_path(parent, path):
        """Tekst z zagniezdzonego elementu (np. 'rcn:IdRCN/rcn:RCN_IdentyfikatorIIP/rcn:lokalnyId')."""
        if parent is None:
            return None
        element = parent.find(path, NS)
        if element is None or element.text is None:
            return None
        value = element.text.strip()
        return value if value else None

    @staticmethod
    def _elem_area_m2(parent, name):
        """Read area element, normalize to **m²** based on `uom` attribute.

        GUGiK RCN GML deklaruje `uom="ha"` (Łódź, Bełchatów) lub `uom="m2"`
        (Warszawa). Zwracamy zawsze m². Gdy atrybutu brak — heurystyka:
        wartości <1000 traktujemy jako ha (typowa działka 0.001-10 ha),
        wyższe jako m² (typowa działka 100-100000 m²).
        """
        if parent is None:
            return None
        element = parent.find(f'rcn:{name}', NS)
        if element is None or element.text is None:
            return None
        text = element.text.strip()
        if not text:
            return None
        try:
            val = float(text.replace(',', '.'))
        except (ValueError, TypeError):
            return None
        uom = (element.attrib.get('uom') or '').strip().lower()
        if uom == 'ha':
            return val * 10000
        if uom in ('m2', 'm²'):
            return val
        # Brak uom — heurystyka oparta na typowych zakresach.
        return val * 10000 if val < 1000 else val

    @staticmethod
    def _elem_hrefs(parent, name):
        if parent is None:
            return []
        values = []
        for element in parent.findall(f'rcn:{name}', NS):
            href = element.attrib.get('{http://www.w3.org/1999/xlink}href')
            if href:
                values.append(href)
        return values

    @classmethod
    def _elem_href(cls, parent, name):
        refs = cls._elem_hrefs(parent, name)
        return refs[0] if refs else None

    def _build_diagnostic_log(self, parsed):
        d = parsed.diagnostics or {}
        lines = [
            'Raport diagnostyczny RCN GML',
            f"Plik źródłowy: {parsed.source_path}",
            f"Wykryty EPSG źródłowy: {d.get('source_epsg', parsed.epsg)}",
            f"Docelowy EPSG warstw: {d.get('target_epsg', 'EPSG:2180')}",
            f"Liczba obiektów GML: {d.get('object_count', parsed.object_count)}",
            f"Liczba transakcji: {d.get('transaction_count', parsed.transaction_count)}",
            f"Liczba rekordów zestawienia: {d.get('summary_count', len(parsed.summary_rows))}",
            '',
            f"Działki: {d.get('plot_count', len(parsed.plot_rows))} | z geometrią: {d.get('plot_geom_count', 0)} | bez geometrii: {max(d.get('plot_count', len(parsed.plot_rows)) - d.get('plot_geom_count', 0), 0)}",
            f"Budynki: {d.get('building_count', len(parsed.building_rows))} | z geometrią: {d.get('building_geom_count', 0)} | bez geometrii: {max(d.get('building_count', len(parsed.building_rows)) - d.get('building_geom_count', 0), 0)}",
            f"Lokale: {d.get('local_count', len(parsed.local_rows))} | z geometrią: {d.get('local_geom_count', 0)} | bez geometrii: {max(d.get('local_count', len(parsed.local_rows)) - d.get('local_geom_count', 0), 0)}",
        ]
        missing = d.get('missing_geometry', {})
        for label in ('dzialki', 'budynki', 'lokale'):
            ids = missing.get(label, [])[:25]
            if ids:
                lines.append('')
                lines.append(f"Braki geometrii - {label}: {', '.join(map(str, ids))}")
        return lines

    def _load_dictionary(self, path):
        if not path or not os.path.exists(path):
            return {}
        if path.lower().endswith('.json'):
            with open(path, 'r', encoding='utf-8') as handle:
                return json.load(handle)
        if path.lower().endswith('.xlsx'):
            return self._load_dictionary_from_xlsx(path)
        return {}

    def _load_dictionary_from_xlsx(self, path):
        try:
            from openpyxl import load_workbook
        except Exception:
            return {}
        workbook = load_workbook(path, data_only=True)
        sheet = workbook[workbook.sheetnames[0]]
        enum_map = defaultdict(dict)
        for row in sheet.iter_rows(min_row=2, values_only=True):
            type_name, value, description = row[:3]
            if not type_name or value is None:
                continue
            enum_map[str(type_name).strip()][str(value).strip()] = str(description).strip() if description is not None else ''
        return dict(enum_map)

    def _decode(self, type_name, value):
        if value is None:
            return None
        text = self.enum_map.get(type_name, {}).get(str(value), str(value))
        return self._humanize_enum(text)

    @staticmethod
    def _humanize_enum(text):
        if text is None:
            return None
        text = str(text).strip()
        if not text:
            return None
        text = text.replace('_', ' ')
        text = re.sub(r'([a-ząćęłńóśźż])([A-ZĄĆĘŁŃÓŚŹŻ])', r'\1 \2', text)
        replacements = {
            'Wz': 'WZ',
            'Mpzp': 'MPZP',
            'Vat': 'VAT',
            'Rc N': 'RCN',
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text[:1].lower() + text[1:] if text else text

    @staticmethod
    def _extract_epsg(srs_name):
        if not srs_name:
            return None
        match = re.search(r'EPSG[:]{1,2}(\d+)', srs_name)
        if match:
            return f'EPSG:{match.group(1)}'
        return None

    @staticmethod
    def _local_name(tag):
        return tag.split('}', 1)[1] if '}' in tag else tag

    @staticmethod
    def _text(parent, name):
        """Pole tekstowe z dicta. (Element-based wariant: `_elem_text`.)"""
        if parent is None:
            return None
        return parent.get(name)

    @staticmethod
    def _hrefs(parent, name):
        """Lista xlink:href z dicta. (Element-based wariant: `_elem_hrefs`.)"""
        if parent is None:
            return []
        return parent.get(f'{name}_hrefs', [])

    @staticmethod
    def _href(parent, name):
        """Single xlink:href z dicta. (Element-based wariant: `_elem_href`.)"""
        if parent is None:
            return None
        return parent.get(f'{name}_href')

    @staticmethod
    def _split_share(value):
        if not value:
            return None, None
        if '/' in value:
            numerator, denominator = value.split('/', 1)
            return numerator, denominator
        return value, None

    @staticmethod
    def _to_number(value):
        if value in (None, ''):
            return None
        try:
            number = float(str(value).replace(',', '.'))
            return int(number) if number.is_integer() else number
        except Exception:
            return value

    @staticmethod
    def _to_int(value):
        if value in (None, ''):
            return None
        try:
            return int(float(str(value).replace(',', '.')))
        except Exception:
            return value

    def _extract_geometry_wkt(self, source_object):
        geometry_nodes = []
        for tag_name in ('geometria', 'georeferencja'):
            for node in source_object.findall(f'rcn:{tag_name}', NS):
                if node is not None:
                    geometry_nodes.append(node)

        if not geometry_nodes:
            return None

        for geometry_node in geometry_nodes:
            polygon = geometry_node.find('.//gml:Polygon', NS)
            if polygon is not None:
                return self._polygon_element_to_wkt(polygon)

            multi_surface = geometry_node.find('.//gml:MultiSurface', NS)
            if multi_surface is not None:
                return self._multisurface_to_wkt(multi_surface)

            point = geometry_node.find('.//gml:Point', NS)
            if point is not None:
                return self._point_element_to_wkt(point)

            pos_list = geometry_node.find('.//gml:posList', NS)
            if pos_list is not None:
                return self._polygon_poslist_to_wkt(pos_list, geometry_node)

            pos = geometry_node.find('.//gml:pos', NS)
            if pos is not None:
                return self._point_pos_to_wkt(pos, geometry_node)

        return None

    def _polygon_element_to_wkt(self, polygon_element):
        rings = []
        for ring_node in polygon_element.findall('gml:exterior/gml:LinearRing', NS):
            ring = self._linearring_to_ring_text(ring_node, polygon_element)
            if ring:
                rings.append(ring)
        for ring_node in polygon_element.findall('gml:interior/gml:LinearRing', NS):
            ring = self._linearring_to_ring_text(ring_node, polygon_element)
            if ring:
                rings.append(ring)
        if not rings:
            return None
        return f"POLYGON ({', '.join(rings)})"

    def _multisurface_to_wkt(self, multisurface_element):
        polygons = []
        for polygon in multisurface_element.findall('.//gml:surfaceMember/gml:Polygon', NS):
            polygon_wkt = self._polygon_element_to_wkt(polygon)
            if polygon_wkt and polygon_wkt.startswith('POLYGON '):
                polygons.append(polygon_wkt[len('POLYGON '):])
        if not polygons:
            for polygon in multisurface_element.findall('.//gml:polygonMember/gml:Polygon', NS):
                polygon_wkt = self._polygon_element_to_wkt(polygon)
                if polygon_wkt and polygon_wkt.startswith('POLYGON '):
                    polygons.append(polygon_wkt[len('POLYGON '):])
        if not polygons:
            return None
        if len(polygons) == 1:
            return f'POLYGON {polygons[0]}'
        return f"MULTIPOLYGON ({', '.join(polygons)})"

    def _point_element_to_wkt(self, point_element):
        pos = point_element.find('gml:pos', NS)
        if pos is not None:
            return self._point_pos_to_wkt(pos, point_element)
        coords = point_element.find('gml:coordinates', NS)
        if coords is not None and coords.text:
            parts = re.split(r'[ ,]+', coords.text.strip())
            numbers = [float(part) for part in parts if part]
            xy = self._normalize_xy(numbers, point_element)
            if xy is None:
                return None
            return f'POINT ({xy[0]} {xy[1]})'
        return None

    def _polygon_poslist_to_wkt(self, pos_list_node, context_element):
        ring = self._coords_to_ring_text(self._parse_coordinate_numbers(pos_list_node), context_element, pos_list_node)
        if not ring:
            return None
        return f'POLYGON ({ring})'

    def _point_pos_to_wkt(self, pos_node, context_element):
        numbers = self._parse_coordinate_numbers(pos_node)
        xy = self._normalize_xy(numbers, context_element, pos_node)
        if xy is None:
            return None
        return f'POINT ({xy[0]} {xy[1]})'

    def _linearring_to_ring_text(self, ring_node, context_element):
        pos_list = ring_node.find('gml:posList', NS)
        if pos_list is not None:
            return self._coords_to_ring_text(self._parse_coordinate_numbers(pos_list), context_element, pos_list)

        positions = []
        for pos_node in ring_node.findall('gml:pos', NS):
            xy = self._normalize_xy(self._parse_coordinate_numbers(pos_node), context_element, pos_node)
            if xy is not None:
                positions.append(f'{xy[0]} {xy[1]}')
        if positions:
            if positions[0] != positions[-1]:
                positions.append(positions[0])
            return f"({', '.join(positions)})"
        return None

    def _coords_to_ring_text(self, numbers, context_element, coord_element=None):
        dimension = self._coordinate_dimension(context_element, coord_element, numbers)
        if dimension < 2 or len(numbers) < dimension:
            return None
        positions = []
        for index in range(0, len(numbers), dimension):
            chunk = numbers[index:index + dimension]
            if len(chunk) < 2:
                continue
            xy = self._normalize_xy(chunk, context_element, coord_element)
            if xy is not None:
                positions.append(f'{xy[0]} {xy[1]}')
        if len(positions) < 3:
            return None
        if positions[0] != positions[-1]:
            positions.append(positions[0])
        return f"({', '.join(positions)})"

    @staticmethod
    def _parse_coordinate_numbers(node):
        if node is None or not node.text:
            return []
        return [float(part) for part in node.text.strip().replace(',', ' ').split() if part]

    def _coordinate_dimension(self, context_element, coord_element, numbers):
        for node in (coord_element, context_element):
            if node is None:
                continue
            value = node.attrib.get('srsDimension') or node.attrib.get('{http://www.opengis.net/gml/3.2}srsDimension')
            if value:
                try:
                    return max(int(value), 2)
                except Exception:
                    pass
        return 2 if len(numbers) % 2 == 0 else 3

    def _normalize_xy(self, coords, context_element, coord_element=None):
        if len(coords) < 2:
            return None
        srs_name = None
        for node in (coord_element, context_element):
            if node is not None and node.attrib.get('srsName'):
                srs_name = node.attrib.get('srsName')
                break
        epsg = self._extract_epsg(srs_name) or self._extract_epsg(getattr(self, 'current_srs_name', None)) or 'EPSG:2177'
        x, y = float(coords[0]), float(coords[1])
        if self._should_swap_xy(epsg, x, y):
            x, y = y, x
        return x, y

    @staticmethod
    def _should_swap_xy(epsg, x, y):
        if epsg in {'EPSG:2176', 'EPSG:2177', 'EPSG:2178', 'EPSG:2179', 'EPSG:2180'}:
            # Dane GML RCN często zapisują współrzędne jako północ/wschód (Y/X dla GIS).
            if abs(x) > 1000000 and abs(y) > 1000000 and x < y:
                return True
        return False
