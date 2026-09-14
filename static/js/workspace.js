/* RCN Workshop — workspace view */

function workspaceView(workspaceId) {
    return {
        workspaceId,
        info: {},
        me: { username: '', role: 'readonly', anonymous: false },
        imports: [],
        // Workspace lock state (busy = trwa import lub ulepszenie). Gdy active,
        // overlay zakrywa mapę/tabelę; reszta boot()u jest pomijana. Polling
        // co 3s w _scheduleBusyPoll(); po unlock automatyczny reload strony
        // żeby fresh-data trafiło do UI.
        busy: { active: false, kind: '', label: '', duration_s: 0, since: 0 },
        _busyTimer: null,
        lookups: { rodzaj_rynku: [], rodzaj_transakcji: [], rodzaj_nieruchomosci: [], miejscowosc: [], teryt_gminy: [], obreb: [], obreby: [] },
        filters: {
            rodzaj_rynku: [],
            rodzaj_transakcji: [],
            rodzaj_nieruchomosci: [],
            miejscowosc: [],
            teryt_gminy: [],
            obreb: [],
            obreb_key: [],
            obreb_search: null,
            adres: '',
            cena_min: null,
            cena_max: null,
            cena_m2_min: null,
            cena_m2_max: null,
            // Faza 4: filtry per-kolumna w nagłówku tabeli (Pow., Nr działki).
            area_min: null,
            area_max: null,
            plot_ident_search: null,
            data_od: null,
            data_do: null,
            has_note_choice: '',
            notes_search: '',
            // Wiki-linki (filtr wg obiektu) -- jeden z tych naraz.
            // Ustawiane przez wbFilterByPlot/Building/Local; usuwane przez chip ×.
            plot_ident: null,
            building_ident: null,
            local_ident: null,
            // Pokaż wycofane z portalu (status != 'aktywna'). Domyślnie false.
            include_withdrawn: false,
            // v6 (2026-04-29): tylko transakcje bez flag jakości danych
            // (multi_object_act, no_objects, extreme_price_per_m2, etc.).
            only_verified: false,
        },
        // Etykieta dla chipa wiki-linku -- żeby pokazać np. "Działka 106103_9.0029.85/3 (Bałuty)"
        // zamiast surowego ID. Wypełniana przy kliku wiki-linku.
        wikiLabel: null,

        // Moduł analityczny -- state dla modala Σ Analiza.
        analysisOpen: false,
        analysisLoading: false,
        analysisData: null,
        analysisTab: 'stats',
        analysisError: '',
        _wbChartInstances: { quarterly: null, scatter: null },

        // Wtyczki (pliki .py w data/plugins/) -- dropdown w koszyku + modal wyniku.
        pluginsList: [],
        pluginsLoaded: false,
        pluginMenuOpen: false,
        pluginOpen: false,
        pluginLoading: false,
        pluginError: '',
        pluginResult: null,
        pluginName: '',
        pluginCurrent: null,      // wtyczka otwarta w modalu (do re-run z parametrami)
        pluginParams: null,       // deklaracja PLUGIN['params'] -> formularz przed startem
        pluginParamValues: {},    // wartości formularza (name -> string)
        pluginCapped: '',         // notka o przycięciu koszyka (nagłówek X-RCN-Capped)
        _wbPluginCharts: [],

        // PR1 redesign: usunięto visibleColumns + colsOpen (toggle kolumn nigdy nie był wpięty w UI).

        expandedIds: new Set(),
        detailsCache: {},
        detailsLoading: {},

        // PR4 redesign: sidebar zadokowany.
        // - filtersCollapsed: stan zwinięty (48 px pasek ikony) vs rozwinięty (380 px),
        //   persystowany w localStorage per workspace.
        // - filtersOpen: używane TYLKO w mobile (≤ 900 px) gdzie sidebar jest overlayem.
        filtersCollapsed: false,
        filtersOpen: false,
        cartOpen: false,
        // Faza 4: viewMode -- 'map' lub 'table'. Toggle w topbarze.
        // Priorytet inicjalizacji: ?view=table z URL > localStorage > default 'table'.
        // Oba widoki renderują się w DOM (x-show), więc Alpine state przeżywa toggle.
        viewMode: 'table',
        // Faza 4 commit 6: persystencja scroll position w tabeli między toggle Mapa<->Tabela.
        // In-memory (Alpine state), nie localStorage -- scroll to sesyjna kontekstualność,
        // nie domena trwała.
        _scrollPositions: { map: 0, table: 0 },
        // Faza 4 commit 3: modal "Pokaż na mapie" -- pojedyncza transakcja
        // + bbox-aware transakcje w widoku + warstwy GPKG/EGiB. Otwierany z 🎯.
        // Popupy markerów mają full funkcjonalność: + Do kolekcji, W tabeli,
        // Street View, Geoportal (jak na głównej mapie).
        // filterScope: 'all' (cała baza, default 4c) | 'filtered' (respektuje filtry tabeli).
        mapModal: {
            open: false,
            item: null,
            neighborsCount: 0,
            loading: false,
            layerFlags: {},
            filterScope: 'all',
        },
        // Modal "Warstwy GPKG" -- lista custom layers + add/delete (admin only).
        layersAdminOpen: false,
        layerAddName: '',
        layerAddFile: null,
        layerActionStatus: '',
        layerActionBusy: false,
        // Drawer header toolbar -- collapsible (przycisk ▴/▾ w lewym rogu drawera).
        drawerHeaderCollapsed: false,
        // Tymczasowy filtr "pokaż tę jedną transakcję" -- ustawiany przez
        // wbShowInTable, gdy item jest poza aktualną stroną. Widoczny jako
        // chip "Pokazany: …" który user może zdjąć przez ×.
        focusIdRcn: null,
        // Krok "Sąsiedzi w X m" (Sesja 9, po SpatiaLite). Filter "transakcje
        // porównawcze w 50/100/250/500 m od wycenianej". Backend
        // /api/.../transactions/{id}/neighbors zwraca id_rcn -- frontend
        // wstawia jako filters.id_rcn_in (reuse istniejacego mechanizmu).
        neighborhoodIds: null,        // Set<string> | null
        neighborhoodFocus: null,      // { id_rcn, radius_m, label, count } | null
        // PR6 fix: usunieto cartNotes (ad-hoc draft notatki w koszyku) -- Wariant A:
        // notatka per transakcja jest jedna w DB, koszyk pokazuje podglad/edycje
        // tej samej notatki przez istniejacy modal "note". Nie ma juz osobnego
        // pola "notatka tylko do tego eksportu" -- jego zastapuje cartExportComment.
        cartExportComment: '',  // jednorazowy komentarz "do tego eksportu", trafia do XLSX Podsumowanie
        // PR8 fix4: cache item-ow dodanych do koszyka. Bez tego item dodany
        // z popupu markera (gdy nie ma go w queryResult.items, np. inna strona
        // paginacji) pokazuje sie w koszyku jako hash "97CF55FA…" zamiast adresu.
        // Klucz: id_rcn, wartosc: {adres, miejscowosc, rodzaj_nieruchomosci, cena_*, area_m2, data_transakcji}
        cartItemCache: {},
        // PR9: stack toastow w prawym dolnym rogu (success/info/error). Auto-hide 4 s.
        toasts: [],
        _toastNextId: 1,
        // PR8 fix2: plansza overlay "Przybliz mape..." widoczna gdy zoom <14
        _showZoomHint: true,
        drawerState: 'default',
        drawerHeight: null,
        _drawerDrag: null,

        notePopover: {
            open: false, id_rcn: null, has_note: false,
            loading: false, body: '', is_seed: false,
            top: 0, left: 0, maxHeight: 400, placement: 'below',
        },
        noteContentCache: {},  // id_rcn -> { body, isSeed }
        _noteShowTimer: null,
        _noteHideTimer: null,
        sort: { column: 'data_transakcji', order: 'desc' },
        page: 1,
        pageSize: 100,
        // Lekki filtr tekstowy w headerze tabeli (obok chipów z slide-overa).
        // Dla obrębu multi-select (filters.obreb) działa jako dodatkowy OR -- wpisany tekst szuka w nazwie lub kodzie.
        colFilterObreb: '',
        queryResult: { total: 0, items: [] },
        geojson: { features: [], capped: false, limit: 10000 },
        loadingQuery: false,
        selected: null,

        selectedIds: new Set(),
        // PR1 redesign: usunięto showOnlySelected + loadingAllIds (UI nigdy nie wystawiało
        // checkboxa "tylko zaznaczone" ani przycisku "zaznacz wszystkie wyniki").
        exporting: false,

        note: { open: false, id_rcn: null, body: '', isSeed: false, updated_at: null, loading: false },

        spatial: { active: false, kind: null, bbox: null, polygon: null },

        openUpload: false,
        // Delta domyślnie -- snapshot wycofuje brakujące i pomyłka na tym polu
        // kosztowała już widoczność 96% bazy Łodzi (awaria 2026-09-12).
        uploadTryb: 'delta',
        file: null,
        uploading: false,
        uploadStatus: '',
        uploadProgress: { active: false, stage: '', pct: 0, importId: null, finalMsg: '' },

        map: null,
        markerCluster: null,
        markerIndex: {},
        drawnLayer: null,
        drawControl: null,

        overlays: {
            // Działki i budynki RCN domyślnie ON -- to główny kontekst rzeczoznawcy
            // (widzi obrysy obiektów transakcyjnych na tle OSM).
            rcnParcels:  { layer: null, enabled: true,  minZoom: 12, loading: false, endpoint: 'parcels' },
            rcnBuilds:   { layer: null, enabled: true,  minZoom: 14, loading: false, endpoint: 'buildings' },
            egibParcels: { layer: null, enabled: false, minZoom: 14, loading: false, egib: 'dzialki' },
            egibBuilds:  { layer: null, enabled: false, minZoom: 15, loading: false, egib: 'budynki' },
            wmsEgib:     { layer: null, enabled: false, minZoom: 12, loading: false, wms: 'dzialki' },
        },
        // Per-workspace custom GPKG layers -- ładowane z /api/workspaces/{id}/custom-layers
        // i doklejane do overlays w bootie, po setupie mapy.
        customLayers: [],
        overlayMeta: { capped: {}, sources: {} },
        moveTimer: null,

        // Miarka: klik punkt A (np. POI z warstwy) -> klik punkt B (np. marker
        // transakcji) -> linia z odległością. Pomiary zostają po wyjściu z trybu
        // (czyści „Wyczyść"). Decyzja 2026-06-11: miarka zamiast automatycznych
        // linii koszyk->POI (te robiły pajęczynę).
        measureActive: false,
        measurePending: null,        // {latlng, label} pierwszego kliknięcia
        measureLayer: null,          // L.featureGroup z pomiarami
        measureHasResult: false,
        _measureLastLayerClick: 0,   // dedup: klik w marker NIE ma też liczyć się jako klik w mapę

        async boot() {
            // PR4: odtwórz stan zwinięcia sidebara z localStorage
            try {
                const stored = localStorage.getItem(`rcn.filtersCollapsed.${this.workspaceId}`);
                if (stored === 'true') this.filtersCollapsed = true;
            } catch {}
            // Faza 4: viewMode -- query param > localStorage > default 'table'.
            const urlView = new URLSearchParams(window.location.search).get('view');
            if (urlView === 'table' || urlView === 'map') {
                this.viewMode = urlView;
            } else {
                try {
                    const stored = localStorage.getItem(`rcn.viewMode.${this.workspaceId}`);
                    if (stored === 'table' || stored === 'map') this.viewMode = stored;
                } catch {}
            }
            // Faza 4 commit 6: restore koszyka (selectedIds + cartItemCache) z localStorage.
            // Klucz per-workspace, JSON {ids:[...], cache:{id:{...}}}. Cache snapshotów
            // pozwala pokazać metadane w modalu koszyka nawet gdy item poza queryResult.
            try {
                const cartRaw = localStorage.getItem(`rcn.cart.${this.workspaceId}`);
                if (cartRaw) {
                    const data = JSON.parse(cartRaw);
                    if (Array.isArray(data.ids)) this.selectedIds = new Set(data.ids);
                    if (data.cache && typeof data.cache === 'object') this.cartItemCache = data.cache;
                }
            } catch (e) { /* ignore -- corrupt LS */ }
            await this.loadMe();
            // BroadcastChannel listener -- Settings page (workspace_settings.js)
            // wysyła event po addLayer/deleteLayer/runEnhancement. Odbieramy w
            // tym samym przeglądarce, filtrujemy po wsId i robimy reload żeby
            // fresh customLayers/overlays/tx_cache się załadowały. Bez tego user
            // musi Ctrl+F5 po edycji w Settings.
            this._bindBroadcastChannel();
            // Najpierw sprawdź lock -- jeśli workspace busy (import / enrich /
            // compute_flags w trakcie), nie wywołuj reszty bootu (read endpoints
            // zwrócą 423). Wystarczy poll co 1-3s + auto-reload po unlock.
            await this._checkBusy();
            if (this.busy.active) {
                this._scheduleBusyPoll();
                // Załaduj minimum: info (lekkie, niezguardowane), żeby header
                // pokazał nazwę workspace.
                try { await this.loadInfo(); } catch {}
                return;
            }
            await this.loadInfo();
            await this.loadLookups();
            await this.loadCustomLayers();
            this.wbBindDrawerDrag();
            this.setupMap();
            await this.runQuery();
            await this.loadImports();
        },

        async _checkBusy() {
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/busy`);
                if (r.ok) {
                    this.busy = await r.json();
                }
            } catch (e) { /* ignore */ }
        },

        _scheduleBusyPoll() {
            if (this._busyTimer) return;
            // 1s przez pierwsze 30s (szybka reakcja UI po starcie procesu),
            // potem 3s żeby nie obciążać. _busyPollStart ustawiany przy
            // pierwszym scheduling (wykrywamy przez null check).
            if (this._busyPollStart == null) this._busyPollStart = Date.now();
            const elapsed = Date.now() - this._busyPollStart;
            const interval = elapsed < 30000 ? 1000 : 3000;
            this._busyTimer = setTimeout(async () => {
                this._busyTimer = null;
                const wasBusy = this.busy.active;
                await this._checkBusy();
                if (this.busy.active) {
                    this._scheduleBusyPoll();
                } else if (wasBusy) {
                    // Unlock -- reload, żeby fresh tx_cache + custom layers
                    // załadowały się czysto (a.runQuery, setupMap, etc.).
                    window.location.reload();
                }
            }, interval);
        },

        _bindBroadcastChannel() {
            if (!('BroadcastChannel' in window)) return;
            try {
                this._bc = new BroadcastChannel('rcn-workspace');
                this._bc.onmessage = (ev) => {
                    const msg = ev.data || {};
                    if (msg.wsId !== this.workspaceId) return;
                    // Każdy event powodujący zmianę warstw / geometrii / tx_cache
                    // → najprostszy fix: reload. Tańszy niż reaktywne dorzucanie
                    // do Leaflet controla, który buduje się raz w setupMap.
                    if (msg.type === 'layer-added' || msg.type === 'layer-removed' || msg.type === 'phase-finished') {
                        window.location.reload();
                    }
                };
            } catch (e) { /* ignore -- BroadcastChannel niedostępny */ }
        },

        formatBusyDuration(s) {
            if (!s) return '0s';
            if (s < 60) return Math.round(s) + 's';
            const m = Math.floor(s / 60);
            const sec = Math.round(s - m * 60);
            return `${m} min ${sec}s`;
        },

        async loadMe() {
            try {
                const r = await fetch('/api/me');
                if (r.ok) this.me = await r.json();
            } catch (e) { /* ignore -- default role=readonly */ }
        },

        get isAdmin() { return this.me.role === 'admin'; },

        // Faza 4: przełącznik widoków [Mapa | Tabela]. Persystuje w localStorage
        // i URL (?view=table). Po powrocie do mapy wymuszamy invalidateSize na
        // Leaflet -- mapa była hidden (x-show display:none), więc wewnętrzny
        // size cache jest nieaktualny i bez tego markery i tile'e nie układają się.
        setViewMode(mode) {
            if (mode !== 'map' && mode !== 'table') return;
            if (this.viewMode === mode) return;
            // Faza 4 commit 6: zapisz scrollTop obecnego widoku przed zmianą.
            const currentEl = this.viewMode === 'table'
                ? document.querySelector('.wb-table-scroll')
                : document.querySelector('.wb-drawer-scroll');
            if (currentEl) this._scrollPositions[this.viewMode] = currentEl.scrollTop;
            this.viewMode = mode;
            try { localStorage.setItem(`rcn.viewMode.${this.workspaceId}`, mode); } catch {}
            const url = new URL(window.location.href);
            if (mode === 'map') url.searchParams.delete('view');
            else url.searchParams.set('view', mode);
            window.history.replaceState(null, '', url.toString());
            if (mode === 'map' && this._map) {
                queueMicrotask(() => this._map && this._map.invalidateSize());
            }
            // Restore scroll dla nowego widoku po DOM update (x-show display:block).
            queueMicrotask(() => {
                const nextEl = mode === 'table'
                    ? document.querySelector('.wb-table-scroll')
                    : document.querySelector('.wb-drawer-scroll');
                if (nextEl) nextEl.scrollTop = this._scrollPositions[mode] || 0;
            });
        },

        // Faza 4 commit 6: persist koszyk (ids + cache) do localStorage.
        // Wywoływane z każdej metody mutującej selectedIds.
        _persistCart() {
            try {
                const data = {
                    ids: Array.from(this.selectedIds),
                    cache: this.cartItemCache,
                };
                localStorage.setItem(`rcn.cart.${this.workspaceId}`, JSON.stringify(data));
            } catch (e) { /* quota exceeded / private mode -- ignore */ }
        },

        // === Faza 4 commit 3: modal mapy "Pokaż na mapie" ====================
        // Modal pojedynczej transakcji + bbox-aware sąsiedzi. Mapa Leaflet
        // montowana lazy przy otwarciu, demontowana przy zamknięciu.
        async wbOpenMapModal(item) {
            if (!item || item.centroid_lat == null || item.centroid_lon == null) {
                alert('Ta transakcja nie ma centroidu na mapie.');
                return;
            }
            this._initModalLayerFlags();
            this.mapModal.open = true;
            this.mapModal.item = item;
            this.mapModal.loading = true;
            this.mapModal.neighborsCount = 0;
            this.mapModal.filterScope = 'all';
            await this.$nextTick();
            setTimeout(() => this._mountModalMap(), 60);
        },

        _initModalLayerFlags() {
            const flags = {
                neighbors: true,
                parcels: true,
                buildings: true,
                egibParcels: (this.customLayers || []).length === 0,
                egibBuilds: (this.customLayers || []).length === 0,
            };
            for (const cl of (this.customLayers || [])) {
                flags[`custom_${cl.slug}`] = true;
            }
            this.mapModal.layerFlags = flags;
        },

        wbCloseMapModal() {
            this.mapModal.open = false;
            this.mapModal.item = null;
            if (this._modalFetchTimer) {
                clearTimeout(this._modalFetchTimer);
                this._modalFetchTimer = null;
            }
            if (this._modalMap) {
                try { this._modalMap.remove(); } catch {}
                this._modalMap = null;
            }
            this._modalMarkerCluster = null;
            this._modalFocusMarker = null;
            this._modalFocusHalo = null;
            this._modalOverlays = {};
        },

        _mountModalMap() {
            const el = document.getElementById('wb-modal-map');
            if (!el) { this.mapModal.loading = false; return; }
            if (this._modalMap) {
                try { this._modalMap.remove(); } catch {}
                this._modalMap = null;
            }
            const map = L.map(el, {
                zoomControl: true,
                attributionControl: true,
            });
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '© OpenStreetMap contributors',
            }).addTo(map);
            // Dedicated pane dla markerów transakcji: circleMarker to vector,
            // domyślnie trafia do overlayPane (z-index 400) razem z polygonami
            // działek RCN/EGIB -- przy wyższym zoomie polygony są dodawane
            // później i przejmują kliki. Własny pane z z-index 650 (powyżej
            // markerPane=600) gwarantuje że markery łapią klik nawet pod warstwą.
            if (!map.getPane('wb-modal-tx-pane')) {
                map.createPane('wb-modal-tx-pane');
                map.getPane('wb-modal-tx-pane').style.zIndex = 650;
                map.getPane('wb-modal-tx-pane').style.pointerEvents = 'auto';
            }
            // Zoom 16 -- decyzja Fazy 4: sweet spot 200 m promień, kontekst sąsiedztwa
            // bez chaosu pinów.
            const item = this.mapModal.item;
            map.setView([item.centroid_lat, item.centroid_lon], 16);
            this._modalMap = map;
            this._modalOverlays = {};
            this._modalMarkerCluster = L.markerClusterGroup({
                chunkedLoading: true,
                maxClusterRadius: 40,
                spiderfyOnMaxZoom: true,
                showCoverageOnHover: false,
                // Fix: spider znikał za szybko -- removeOutsideVisibleBounds:false
                // żeby markery poza viewport pozostawały w DOM (popup nie traci targeta).
                removeOutsideVisibleBounds: false,
                spiderfyDistanceMultiplier: 1.6,
                // BRAK disableClusteringAtZoom -- musi byc cluster do maxZoom, zeby
                // spiderfyOnMaxZoom rozlozyl 70+ transakcji o identycznym centroidzie
                // (jeden budynek = jeden pixel). Wczesniej "disable=18" powodowalo
                // ze markery byly wstawiane indywidualnie -> nakladaly sie -> user
                // widzial tylko jedna kropke zamiast spider 70 markerow.
                clusterPane: 'wb-modal-tx-pane',
            }).addTo(map);
            this._renderModalFocus();
            const onMove = () => this._scheduleModalFetch();
            map.on('moveend', onMove);
            map.on('zoomend', onMove);
            this._modalFetchNeighbors();
            this._modalFetchOverlays();
            setTimeout(() => map.invalidateSize(), 220);
            this.mapModal.loading = false;
        },

        _renderModalFocus() {
            if (!this._modalMap) return;
            if (this._modalFocusHalo) { try { this._modalMap.removeLayer(this._modalFocusHalo); } catch {} }
            if (this._modalFocusMarker) { try { this._modalMap.removeLayer(this._modalFocusMarker); } catch {} }
            const item = this.mapModal.item;
            if (!item) return;
            const lat = item.centroid_lat, lon = item.centroid_lon;
            // Aureola amber (animowana, niefokusowalna) -- focus highlight.
            const haloIcon = L.divIcon({
                className: 'wb-focus-halo',
                html: '<div class="wb-focus-halo-ring"></div>',
                iconSize: [60, 60], iconAnchor: [30, 30],
            });
            this._modalFocusHalo = L.marker([lat, lon], {
                icon: haloIcon, interactive: false, zIndexOffset: 999,
                pane: 'wb-modal-tx-pane',
            }).addTo(this._modalMap);
            // Focused circleMarker amber + popup z full content (jak w renderMarkers głównej mapy).
            const tx = L.circleMarker([lat, lon], {
                radius: 11, color: '#F59E0B', weight: 3, fillColor: '#FBBF24', fillOpacity: 0.95,
                pane: 'wb-modal-tx-pane',
            });
            const popupHtml = this._modalBuildPopupHtml(item, 'FOCUSED', { focused: true });
            tx.bindPopup(popupHtml, { maxWidth: 380, minWidth: 320, autoClose: false, closeOnClick: false });
            tx.on('popupopen', (ev) => this._modalWirePopupActions(ev, item));
            tx.addTo(this._modalMap);
            tx.openPopup();
            this._modalFocusMarker = tx;
        },

        // Faza 4: wspólny builder popup HTML dla modala -- zachowuje pełną
        // funkcjonalność popup z głównej mapy: + Do kolekcji, W tabeli, Street View, Geoportal.
        // opts.focused=true -> dorzuca SVG star przed labelem (focused marker amber).
        _modalBuildPopupHtml(item, badgeLabel, opts = {}) {
            const addr = item.adres || (item.miejscowosc ? item.miejscowosc : '') || '';
            const cenaPLN = (item.cena_transakcji_brutto != null)
                ? Math.round(item.cena_transakcji_brutto).toLocaleString('pl-PL') + ' zł' : '—';
            const cenaM2 = (item.cena_na_m2 != null)
                ? Math.round(item.cena_na_m2).toLocaleString('pl-PL') + ' zł' : (
                    (item.cena_transakcji_brutto && item.area_m2 > 0)
                        ? Math.round(item.cena_transakcji_brutto / item.area_m2).toLocaleString('pl-PL') + ' zł'
                        : '—');
            const typeKeyVal = this.typeKey(item.rodzaj_nieruchomosci);
            const typeLabelVal = this.typeLabel(item.rodzaj_nieruchomosci);
            const lat = item.centroid_lat, lon = item.centroid_lon;
            // Universal Google Maps URL: api=1 wymusza Street View web (zamiast
            // przekierowania do natywnej aplikacji Maps), fov=120 daje szeroki
            // kąt -- domyslnie czarna sciana zniknie, user widzi otoczenie od razu.
            const svUrl = (lat != null && lon != null)
                ? `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat.toFixed(6)},${lon.toFixed(6)}&fov=120` : '#';
            const idents = Array.isArray(item.plot_idents) ? item.plot_idents : [];
            const geoUrl = idents.length > 0
                ? `https://mapy.geoportal.gov.pl/imapnext/imap/?identifyParcel=${encodeURIComponent(idents[0])}`
                : `https://mapy.geoportal.gov.pl/imapnext/imap/?gpmap=gp0&zoom=19`;
            const geoLink = this._buildExternalLinkBtn(geoUrl, 'geoportal', 'Geoportal');
            const svLink = (lat != null && lon != null)
                ? this._buildExternalLinkBtn(svUrl, 'streetview', 'Street View')
                : '';
            return `<div class="marker-tip">
                <div class="tip-head">
                    <span class="tip-badge${opts.focused ? ' tip-badge-focused' : ''}">${opts.focused ? '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none" class="tip-badge-icon" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>' : ''}${escapeHtml(badgeLabel)}</span>
                    <span class="tip-date">${escapeHtml(item.data_transakcji || '')}</span>
                </div>
                <div class="tip-body">
                    <div class="tip-addr">${escapeHtml(addr || '—')}</div>
                    <div class="tip-meta">
                        ${item.obreb ? `<span class="mono">obręb ${escapeHtml(item.obreb)}</span>` : ''}
                        ${typeLabelVal ? `<span class="dot">·</span><span class="tip-type-badge t-${typeKeyVal}"><span class="dot-color"></span>${escapeHtml(typeLabelVal)}</span>` : ''}
                    </div>
                    <div class="tip-prices">
                        <div class="tip-price-block"><span class="label">Cena</span><span class="value">${cenaPLN}</span></div>
                        <div class="tip-price-block"><span class="label">Cena/m²</span><span class="value">${cenaM2}</span></div>
                    </div>
                    <div class="tip-actions">
                        <button type="button" class="tip-btn-primary" data-action="toggleCart">+ Do kolekcji</button>
                        <button type="button" class="tip-btn-secondary" data-action="showInTable">W tabeli</button>
                    </div>
                    ${this._buildNeighborsBlock()}
                    <div class="tip-links">
                        ${svLink}
                        ${geoLink}
                    </div>
                </div>
            </div>`;
        },

        // Wire CTA buttons w popupie po jego otwarciu -- jak w renderMarkers głównej mapy.
        _modalWirePopupActions(ev, item) {
            const node = ev.popup.getElement();
            if (!node) return;
            const cartBtn = node.querySelector('[data-action="toggleCart"]');
            const tableBtn = node.querySelector('[data-action="showInTable"]');
            const refreshCartLabel = () => {
                if (!cartBtn) return;
                const inCart = this.selectedIds.has(item.id_rcn);
                cartBtn.textContent = inCart ? '✓ W kolekcji' : '+ Do kolekcji';
                cartBtn.classList.toggle('in-cart', inCart);
            };
            refreshCartLabel();
            if (cartBtn) {
                cartBtn.onclick = (e) => {
                    e.preventDefault(); e.stopPropagation();
                    this.wbToggleItem(item);
                    refreshCartLabel();
                };
            }
            if (tableBtn) {
                tableBtn.onclick = (e) => {
                    e.preventDefault(); e.stopPropagation();
                    this.wbCloseMapModal();
                    this.wbShowInTable(item);
                };
            }
            // Wire "Sąsiedzi w X m" buttony -- po klik fetch + filter + zamknij modal
            // (żeby user zobaczył wynik w tabeli pod spodem).
            const neighBtns = node.querySelectorAll('[data-action="neighbors"]');
            neighBtns.forEach(btn => {
                btn.onclick = (e) => {
                    e.preventDefault(); e.stopPropagation();
                    const r = parseInt(btn.getAttribute('data-radius'), 10);
                    this.wbCloseMapModal();
                    this.wbShowNeighbors(item, r);
                };
            });
        },

        _scheduleModalFetch() {
            if (this._modalFetchTimer) clearTimeout(this._modalFetchTimer);
            this._modalFetchTimer = setTimeout(() => {
                this._modalFetchNeighbors();
                this._modalFetchOverlays();
            }, 350);
        },

        // Faza 4 commit 3 (fix): warstwy GPKG/EGiB w modalu mapy.
        // Reuse pattern z refreshOverlay -- bbox + endpoint /api/layers/...
        // Decyzja 3a: warstwy widoczne tylko w modalu mapy (nie w glownej tabeli).
        async _modalFetchOverlays() {
            if (!this._modalMap) return;
            const zoom = this._modalMap.getZoom();
            const b = this._modalMap.getBounds();
            const bbox = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
            const tasks = [];
            if (zoom >= 12) {
                tasks.push({
                    key: 'parcels',
                    url: `/api/layers/workspaces/${this.workspaceId}/parcels.geojson?bbox=${encodeURIComponent(bbox)}&limit=2000`,
                    style: { color: '#1f4e79', weight: 0.7, fillColor: '#3b82f6', fillOpacity: 0.10 },
                    label: 'działka RCN',
                });
            }
            if (zoom >= 14) {
                tasks.push({
                    key: 'buildings',
                    url: `/api/layers/workspaces/${this.workspaceId}/buildings.geojson?bbox=${encodeURIComponent(bbox)}&limit=2000`,
                    style: { color: '#9a3412', weight: 0.9, fillColor: '#fb923c', fillOpacity: 0.20 },
                    label: 'budynek RCN',
                });
            }
            for (const cl of (this.customLayers || [])) {
                if (zoom < 12) continue;
                tasks.push({
                    key: `custom_${cl.slug}`,
                    url: `/api/layers/workspaces/${this.workspaceId}/custom/${cl.slug}.geojson?bbox=${encodeURIComponent(bbox)}&limit=2000`,
                    style: { color: '#7c3aed', weight: 0.9, fillColor: '#c4b5fd', fillOpacity: 0.15, dashArray: '2,3' },
                    label: cl.name || 'custom',
                });
            }
            // Globalne EGIB tylko jesli workspace nie ma wlasnych GPKG (fallback).
            if ((this.customLayers || []).length === 0 && zoom >= 14) {
                tasks.push({
                    key: 'egibParcels',
                    url: `/api/layers/egib/dzialki.geojson?bbox=${encodeURIComponent(bbox)}&limit=2000`,
                    style: { color: '#475569', weight: 0.7, fillColor: '#94a3b8', fillOpacity: 0.10 },
                    label: 'EGIB działka',
                });
                if (zoom >= 15) {
                    tasks.push({
                        key: 'egibBuilds',
                        url: `/api/layers/egib/budynki.geojson?bbox=${encodeURIComponent(bbox)}&limit=2000`,
                        style: { color: '#475569', weight: 0.9, fillColor: '#94a3b8', fillOpacity: 0.20 },
                        label: 'EGIB budynek',
                    });
                }
            }
            for (const t of tasks) {
                // Toggle z panelu warstw -- jeśli OFF, clearLayers (zostaje pusty L.geoJSON).
                if (this.mapModal.layerFlags[t.key] === false) {
                    if (this._modalOverlays[t.key]) this._modalOverlays[t.key].clearLayers();
                    continue;
                }
                if (!this._modalOverlays[t.key]) {
                    const layer = L.geoJSON(null, {
                        style: t.style,
                        onEachFeature: (f, lyr) => {
                            this.bindOverlayTooltip(f, lyr, t.label);
                            this._bindModalOverlayPopup(f, lyr, t.label);
                        },
                    });
                    this._modalOverlays[t.key] = layer;
                    layer.addTo(this._modalMap);
                }
                try {
                    const r = await fetch(t.url);
                    if (!r.ok) continue;
                    const data = await r.json();
                    this._modalOverlays[t.key].clearLayers();
                    this._modalOverlays[t.key].addData(data);
                } catch (e) { /* ignore -- modal mógł być zamknięty */ }
            }
            // Focused marker + halo (direct na mapie) muszą być widoczne nad
            // poligonami. markerPane (z-index 600) jest powyżej overlayPane (400),
            // więc to "for free", ale wywołujemy bringToFront na pewność.
            // Cluster ma własny pane (markerPane) -- też z-index OK.
            try { this._modalFocusMarker?.bringToFront?.(); } catch {}
        },

        async _modalFetchNeighbors() {
            if (!this._modalMap) return;
            // Toggle "Transakcje w widoku" w panelu warstw -- jeśli OFF, czyszczamy markery.
            if (this.mapModal.layerFlags.neighbors === false) {
                this._renderModalNeighbors([]);
                return;
            }
            const b = this._modalMap.getBounds();
            const qs = new URLSearchParams();
            // Faza 4 commit 4: filterScope toggle -- 'all' (cała baza, default) ignoruje
            // filtry tabeli; 'filtered' respektuje aktualne filtry sidebara/headera.
            // Decyzja 4c: default cała baza -- najwięcej kontekstu sąsiedztwa.
            if (this.mapModal.filterScope === 'filtered') {
                const f = this.filters;
                (f.rodzaj_rynku || []).forEach(v => qs.append('rodzaj_rynku', v));
                (f.rodzaj_transakcji || []).forEach(v => qs.append('rodzaj_transakcji', v));
                (f.rodzaj_nieruchomosci || []).forEach(v => qs.append('rodzaj_nieruchomosci', v));
                (f.miejscowosc || []).forEach(v => qs.append('miejscowosc', v));
                (f.teryt_gminy || []).forEach(v => qs.append('teryt_gminy', v));
                (f.obreb || []).forEach(v => qs.append('obreb', v));
                (f.obreb_key || []).forEach(v => qs.append('obreb_key', v));
                if (f.obreb_search?.trim()) qs.set('obreb_search', f.obreb_search.trim());
                if (f.adres?.trim()) qs.set('adres', f.adres.trim());
                if (f.cena_min != null && f.cena_min !== '') qs.set('cena_min', f.cena_min);
                if (f.cena_max != null && f.cena_max !== '') qs.set('cena_max', f.cena_max);
                if (f.cena_m2_min != null && f.cena_m2_min !== '') qs.set('cena_m2_min', f.cena_m2_min);
                if (f.cena_m2_max != null && f.cena_m2_max !== '') qs.set('cena_m2_max', f.cena_m2_max);
                if (f.area_min != null && f.area_min !== '') qs.set('area_min', f.area_min);
                if (f.area_max != null && f.area_max !== '') qs.set('area_max', f.area_max);
                if (f.plot_ident_search?.trim()) qs.set('plot_ident_search', f.plot_ident_search.trim());
                if (f.data_od) qs.set('data_od', f.data_od);
                if (f.data_do) qs.set('data_do', f.data_do);
                if (f.include_withdrawn) qs.set('include_withdrawn', 'true');
            }
            qs.set('min_lon', b.getWest().toFixed(6));
            qs.set('min_lat', b.getSouth().toFixed(6));
            qs.set('max_lon', b.getEast().toFixed(6));
            qs.set('max_lat', b.getNorth().toFixed(6));
            // Limit 1500 -- kompromis: dużo żeby pokazać duży widok, mało żeby
            // canvas renderer dał płynny pan/zoom (zwłaszcza z Łodzi 161k transakcji).
            qs.set('limit', 1500);
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/geojson?${qs}`);
                if (!r.ok) return;
                const data = await r.json();
                this._renderModalNeighbors(data.features || []);
            } catch (e) { /* ignore -- user może zamknął modal w trakcie */ }
        },

        _renderModalNeighbors(features) {
            if (!this._modalMap || !this._modalMarkerCluster) return;
            this._modalMarkerCluster.clearLayers();
            const focusedId = this.mapModal.item?.id_rcn;
            const markers = [];
            for (const feat of features) {
                const p = feat.properties || {};
                if (p.id_rcn === focusedId) continue;  // skip focused (renderowany direct amber)
                const coords = feat.geometry?.coordinates;
                if (!coords || coords.length < 2) continue;
                const [lon, lat] = coords;
                const tk = this.typeKey(p.rodzaj_nieruchomosci);
                const colors = {
                    'grunt-niezab': '#1E40AF',
                    'grunt-zab':    '#0EA5E9',
                    'budynek':      '#EA580C',
                    'lokal':        '#16A34A',
                };
                const col = colors[tk] || '#64748B';
                const m = L.circleMarker([lat, lon], {
                    radius: 6,
                    color: col,
                    weight: 1.5,
                    fillColor: col,
                    fillOpacity: 0.7,
                    pane: 'wb-modal-tx-pane',
                });
                // Buduj fallback item -- p (geojson properties) NIE zawiera
                // centroid_lat/lon (są w geometry.coordinates osobno). Wzbogacamy
                // o lat/lon -- żeby _modalBuildPopupHtml wygenerowało Street View
                // link (warunek: item.centroid_lat != null).
                const itemForActions = { ...p, centroid_lat: lat, centroid_lon: lon };
                // Pełen popup (jak w renderMarkers głównej mapy): + Do kolekcji, W tabeli,
                // Street View, Geoportal, badge typu, prices.
                const popupHtml = this._modalBuildPopupHtml(itemForActions, 'TRANSAKCJA');
                // closeOnClick:false -- klik gdziekolwiek poza popup NIE zamyka go.
                // Wcześniej user zgłaszał "bardzo szybko znika możliwość kliknięcia"
                // -- popup zamykał się przy kliku w popupie (kliki na CTA wewnątrz).
                m.bindPopup(popupHtml, { maxWidth: 380, minWidth: 320, autoClose: true, closeOnClick: false });
                m.on('popupopen', (ev) => this._modalWirePopupActions(ev, itemForActions));
                markers.push(m);
            }
            // addLayers bulk -- szybsze niż pojedyncze addLayer w pętli.
            // markercluster sam zdecyduje czy klastrować na obecnym zoomie czy spider.
            this._modalMarkerCluster.addLayers(markers);
            this.mapModal.neighborsCount = markers.length;
        },

        // Toggle warstwy w panelu modala -- po zmianie checkboxa odpalamy odpowiedni fetch.
        // 'neighbors' -> _modalFetchNeighbors, pozostałe -> _modalFetchOverlays.
        toggleModalLayer(key) {
            if (key === 'neighbors') {
                this._modalFetchNeighbors();
            } else {
                this._modalFetchOverlays();
            }
        },

        // Faza 4 commit 4: toggle "Cała baza | W kontekście filtru" w panelu warstw modala.
        // Decyzja 4c -- default 'all' (cała baza, najwięcej kontekstu sąsiedztwa).
        setModalFilterScope(scope) {
            if (scope !== 'all' && scope !== 'filtered') return;
            if (this.mapModal.filterScope === scope) return;
            this.mapModal.filterScope = scope;
            this._modalFetchNeighbors();
        },

        // --- Plik POI (zarządzanie w modalu „Warstwy GPKG") ---
        poiInfo: { present: false },
        poiBusy: false,
        poiStatus: '',

        async loadPoiInfo() {
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/poi`);
                if (r.ok) this.poiInfo = await r.json();
            } catch (e) { /* ignore */ }
        },

        async uploadPoiFile(e) {
            const f = e.target.files && e.target.files[0];
            if (!f) return;
            this.poiBusy = true;
            this.poiStatus = '';
            try {
                const fd = new FormData();
                fd.append('file', f);
                const r = await fetch(`/api/workspaces/${this.workspaceId}/poi`, { method: 'POST', body: fd });
                const j = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
                this.poiStatus = `Wgrano ${j.file} (${(j.points || 0).toLocaleString('pl-PL')} punktów). Przeładowuję…`;
                // Warstwa POI w L.control.layers buduje się raz w setupMap -- reload
                // (ten sam wzorzec co addCustomLayer).
                setTimeout(() => window.location.reload(), 900);
            } catch (err) {
                this.poiStatus = 'Błąd: ' + (err.message || err);
                this.poiBusy = false;
            } finally {
                e.target.value = '';
            }
        },

        async deletePoi() {
            if (!confirm('Usunąć plik POI z tego workspace?\n\nZniknie warstwa POI na mapie, '
                + 'a wtyczki korzystające z POI przestaną działać.')) return;
            this.poiBusy = true;
            this.poiStatus = '';
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/poi`, { method: 'DELETE' });
                if (!r.ok) {
                    const j = await r.json().catch(() => ({}));
                    throw new Error(j.detail || `HTTP ${r.status}`);
                }
                this.poiStatus = 'Usunięto. Przeładowuję…';
                setTimeout(() => window.location.reload(), 900);
            } catch (err) {
                this.poiStatus = 'Błąd: ' + (err.message || err);
                this.poiBusy = false;
            }
        },

        async loadCustomLayers() {
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/custom-layers`);
                if (!r.ok) return;
                const data = await r.json();
                this.customLayers = Array.isArray(data.layers) ? data.layers : [];
                // Per-workspace GPKG -- domyślnie ON, bo user wgrał je celowo dla
                // tego workspace (tu jest jego główny kontekst geograficzny).
                for (const cl of this.customLayers) {
                    const key = `custom_${cl.slug}`;
                    this.overlays[key] = {
                        layer: null,
                        enabled: true,
                        minZoom: 12,
                        loading: false,
                        custom: cl.slug,
                        displayName: cl.name,
                    };
                }
                // Jeżeli workspace ma własne GPKG, NIE pokazuj globalnych EGIB
                // z data/layers/ -- te są fallback dla workspace'ów bez własnych
                // warstw. Ukrywamy je z control.layers żeby uniknąć pomyłki
                // (np. EGIB Łodzi w workspace Bełchatowa).
                if (this.customLayers.length > 0) {
                    delete this.overlays.egibParcels;
                    delete this.overlays.egibBuilds;
                }
                // Warstwa POI (OSM) -- tylko gdy workspace ma plik <nazwa>.poi.sqlite.
                // Dane statyczne i nieliczne: ładowane RAZ przy pierwszym włączeniu
                // (specjalna ścieżka w refreshOverlay), bez odświeżania po bbox.
                if (data.has_poi) {
                    this.overlays.poi = { layer: null, enabled: false, minZoom: 0,
                                          loading: false, poi: true, _loaded: false };
                }
            } catch (e) {
                console.warn('loadCustomLayers failed:', e);
            }
        },

        async addCustomLayer() {
            // Wgranie nowego GPKG do istniejącego workspace (modal "Warstwy GPKG").
            // Po sukcesie odświeżamy info + listę i robimy reload strony, bo overlays
            // i L.control.layers buduje się raz w setupMap -- prościej odświeżyć niż
            // dodać nową warstwę w runtime do już istniejącego controlu.
            if (!this.layerAddName.trim() || !this.layerAddFile) return;
            this.layerActionBusy = true;
            this.layerActionStatus = '';
            const fd = new FormData();
            fd.append('name', this.layerAddName.trim());
            fd.append('file', this.layerAddFile);
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/layers/add`, {
                    method: 'POST',
                    body: fd,
                });
                const txt = await r.text();
                if (!r.ok) throw new Error(`HTTP ${r.status}: ${txt}`);
                this.layerActionStatus = 'OK -- warstwa dodana. Odświeżam stronę…';
                setTimeout(() => window.location.reload(), 600);
            } catch (err) {
                this.layerActionStatus = 'BŁĄD: ' + (err.message || err);
            } finally {
                this.layerActionBusy = false;
            }
        },

        async deleteCustomLayer(slug, name) {
            if (!confirm(`Na pewno usunąć warstwę "${name}"?\n(plik GPKG zostanie skasowany z dysku)`)) return;
            this.layerActionBusy = true;
            this.layerActionStatus = '';
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/layers/${encodeURIComponent(slug)}`, {
                    method: 'DELETE',
                });
                if (!r.ok) {
                    const txt = await r.text();
                    throw new Error(`HTTP ${r.status}: ${txt}`);
                }
                this.layerActionStatus = `OK -- usunięto "${name}". Odświeżam…`;
                setTimeout(() => window.location.reload(), 600);
            } catch (err) {
                this.layerActionStatus = 'BŁĄD: ' + (err.message || err);
            } finally {
                this.layerActionBusy = false;
            }
        },

        // PR1 redesign: usunięto loadVisibleColumns / saveVisibleColumns / colsVisibleCount
        // (kolumny toggle nigdy nie zostały zaimplementowane w UI).

        async toggleExpand(idRcn) {
            if (this.expandedIds.has(idRcn)) {
                this.expandedIds.delete(idRcn);
                this.expandedIds = new Set(this.expandedIds);
                return;
            }
            this.expandedIds.add(idRcn);
            this.expandedIds = new Set(this.expandedIds);
            if (this.detailsCache[idRcn]) return;
            this.detailsLoading[idRcn] = true;
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/transactions/details/${encodeURIComponent(idRcn)}`);
                if (!r.ok) {
                    alert(`Nie udało się wczytać szczegółów (${r.status})`);
                    this.expandedIds.delete(idRcn);
                    this.expandedIds = new Set(this.expandedIds);
                    return;
                }
                this.detailsCache[idRcn] = await r.json();
            } catch (e) {
                alert('Błąd sieci: ' + (e.message || e));
                this.expandedIds.delete(idRcn);
                this.expandedIds = new Set(this.expandedIds);
            } finally {
                delete this.detailsLoading[idRcn];
            }
        },

        async loadInfo() {
            const r = await fetch(`/api/workspaces/${this.workspaceId}`);
            if (r.ok) this.info = await r.json();
        },

        async loadLookups() {
            const r = await fetch(`/api/workspaces/${this.workspaceId}/lookups`);
            if (r.ok) this.lookups = await r.json();
        },

        async loadImports() {
            const r = await fetch(`/api/workspaces/${this.workspaceId}/imports`);
            if (r.ok) {
                this.imports = await r.json();
                if (this.imports.some(i => i.status === 'processing' || i.status === 'queued')) {
                    this._scheduleImportsRefresh();
                }
            }
        },

        _scheduleImportsRefresh() {
            if (this._importsTimer) return;
            this._importsTimer = setTimeout(async () => {
                this._importsTimer = null;
                await this.loadImports();
            }, 2000);
        },

        buildFiltersPayload(includeSelection = true) {
            const f = {};
            const copy = ['rodzaj_rynku','rodzaj_transakcji','rodzaj_nieruchomosci','miejscowosc','teryt_gminy','obreb','obreb_key'];
            copy.forEach(k => { if (this.filters[k]?.length) f[k] = this.filters[k]; });
            if (this.filters.cena_min != null && this.filters.cena_min !== '') f.cena_min = Number(this.filters.cena_min);
            if (this.filters.cena_max != null && this.filters.cena_max !== '') f.cena_max = Number(this.filters.cena_max);
            if (this.filters.cena_m2_min != null && this.filters.cena_m2_min !== '') f.cena_m2_min = Number(this.filters.cena_m2_min);
            if (this.filters.cena_m2_max != null && this.filters.cena_m2_max !== '') f.cena_m2_max = Number(this.filters.cena_m2_max);
            if (this.filters.area_min != null && this.filters.area_min !== '') f.area_min = Number(this.filters.area_min);
            if (this.filters.area_max != null && this.filters.area_max !== '') f.area_max = Number(this.filters.area_max);
            if (this.filters.plot_ident_search && String(this.filters.plot_ident_search).trim()) {
                f.plot_ident_search = String(this.filters.plot_ident_search).trim();
            }
            if (this.filters.data_od) f.data_od = this.filters.data_od;
            if (this.filters.data_do) f.data_do = this.filters.data_do;
            if (this.spatial.active) {
                if (this.spatial.kind === 'bbox') {
                    f.geometry = { type: 'bbox', bbox: this.spatial.bbox };
                } else if (this.spatial.kind === 'polygon' && this.spatial.polygon) {
                    f.geometry = { type: 'polygon', coordinates: this.spatial.polygon };
                }
            }
            if (this.focusIdRcn) {
                // "Pokaż tę jedną" -- używamy istniejącego filtra id_rcn_in
                // (backend wspiera) zamiast dokładać nową kolumnę.
                f.id_rcn_in = [this.focusIdRcn];
            } else if (this.neighborhoodIds && this.neighborhoodIds.size > 0) {
                // Sąsiedzi w X m od focused. Backend zwrócił neighbor_ids,
                // dorzucony id_rcn focused -- razem id_rcn_in wraca tylko
                // transakcje w promieniu.
                f.id_rcn_in = Array.from(this.neighborhoodIds);
            }
            if (this.filters.has_note_choice === 'true') f.has_note = true;
            else if (this.filters.has_note_choice === 'false') f.has_note = false;
            if (this.filters.notes_search && this.filters.notes_search.trim()) {
                f.notes_search = this.filters.notes_search.trim();
            }
            if (this.filters.adres && this.filters.adres.trim()) {
                f.adres = this.filters.adres.trim();
            }
            if (this.filters.obreb_search && this.filters.obreb_search.trim()) {
                f.obreb_search = this.filters.obreb_search.trim();
            }
            if (this.filters.plot_ident) f.plot_ident = this.filters.plot_ident;
            if (this.filters.building_ident) f.building_ident = this.filters.building_ident;
            if (this.filters.local_ident) f.local_ident = this.filters.local_ident;
            if (this.filters.include_withdrawn) f.include_withdrawn = true;
            if (this.filters.only_verified) f.only_verified = true;
            return f;
        },

        // Wiki-linki -- klik w identyfikator obiektu w rozwinięciu wiersza
        // (działka/budynek/lokal) filtruje tabelę do wszystkich transakcji
        // w których ten sam obiekt występuje. Jeden typ na raz:
        // wywołanie dowolnej z tych metod czyści pozostałe dwa filtry.
        wbFilterByPlot(ident, label) {
            if (!ident) return;
            this.filters.plot_ident = ident;
            this.filters.building_ident = null;
            this.filters.local_ident = null;
            this.wikiLabel = label || `Działka ${ident}`;
            this.focusIdRcn = null;
            this.page = 1;
            this.runQuery();
        },
        wbFilterByBuilding(ident, label) {
            if (!ident) return;
            this.filters.plot_ident = null;
            this.filters.building_ident = ident;
            this.filters.local_ident = null;
            this.wikiLabel = label || `Budynek ${ident}`;
            this.focusIdRcn = null;
            this.page = 1;
            this.runQuery();
        },
        wbFilterByLocal(ident, label) {
            if (!ident) return;
            this.filters.plot_ident = null;
            this.filters.building_ident = null;
            this.filters.local_ident = ident;
            this.wikiLabel = label || `Lokal ${ident}`;
            this.focusIdRcn = null;
            this.page = 1;
            this.runQuery();
        },
        wbClearWikiFilter() {
            this.filters.plot_ident = null;
            this.filters.building_ident = null;
            this.filters.local_ident = null;
            this.wikiLabel = null;
            this.page = 1;
            this.runQuery();
        },

        // Moduł analityczny -- fetch + render wykresów po kliku Σ Analiza w koszyku.
        async wbOpenAnalysis() {
            if (this.selectedIds.size < 2) return;
            this.analysisOpen = true;
            this.analysisLoading = true;
            this.analysisError = '';
            this.analysisData = null;
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/analysis`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id_rcn_in: Array.from(this.selectedIds) }),
                });
                if (!r.ok) {
                    const txt = await r.text();
                    throw new Error(`HTTP ${r.status}: ${txt}`);
                }
                this.analysisData = await r.json();
                this.analysisTab = 'stats';
                // Wykresy renderujemy lazy przy przełączeniu na tab Trend (wymaga canvas w DOM).
            } catch (err) {
                this.analysisError = err.message || String(err);
            } finally {
                this.analysisLoading = false;
            }
        },

        wbRenderCharts() {
            if (!this.analysisData || typeof Chart === 'undefined') return;
            const kindColors = {
                grunt_niezab: '#1f4e79',
                grunt_zab:    '#6366f1',
                budynek:      '#b45309',
                lokal:        '#16a34a',
                inne:         '#64748b',
            };
            // --- Quarterly bar chart (mediana cena/m² per typ per kwartał) ---
            const quartersSet = new Set(this.analysisData.quarterly.map(q => q.q));
            const kindsSet = new Set(this.analysisData.quarterly.map(q => q.kind));
            const quarters = Array.from(quartersSet).sort();
            const datasets = Array.from(kindsSet).map(kind => {
                const dataByQ = Object.fromEntries(
                    this.analysisData.quarterly.filter(x => x.kind === kind).map(x => [x.q, x.median_cena_m2])
                );
                return {
                    label: this._wbKindLabel(kind),
                    data: quarters.map(q => dataByQ[q] ?? null),
                    backgroundColor: kindColors[kind] || '#64748b',
                    borderColor: kindColors[kind] || '#64748b',
                    borderWidth: 1,
                };
            });
            const qCanvas = document.getElementById('wbChartQuarterly');
            if (qCanvas) {
                if (this._wbChartInstances.quarterly) this._wbChartInstances.quarterly.destroy();
                this._wbChartInstances.quarterly = new Chart(qCanvas, {
                    type: 'bar',
                    data: { labels: quarters, datasets },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        plugins: {
                            title: { display: true, text: 'Mediana cena/m² per kwartał' },
                            legend: { position: 'bottom' },
                        },
                        scales: { y: { beginAtZero: true, title: { display: true, text: 'cena/m² [zł]' } } },
                    },
                });
            }
            // --- Scatter (cena/m² vs data) ---
            const byKind = {};
            for (const p of this.analysisData.scatter) {
                if (!p.data || p.cena_m2 == null) continue;
                (byKind[p.kind] = byKind[p.kind] || []).push({
                    x: new Date(p.data).getTime(),
                    y: p.cena_m2,
                    id: p.id_rcn,
                    adres: p.adres,
                });
            }
            const scatterDatasets = Object.entries(byKind).map(([kind, pts]) => ({
                label: this._wbKindLabel(kind),
                data: pts,
                backgroundColor: kindColors[kind] || '#64748b',
                pointRadius: 3,
                pointHoverRadius: 6,
            }));
            const sCanvas = document.getElementById('wbChartScatter');
            if (sCanvas) {
                if (this._wbChartInstances.scatter) this._wbChartInstances.scatter.destroy();
                this._wbChartInstances.scatter = new Chart(sCanvas, {
                    type: 'scatter',
                    data: { datasets: scatterDatasets },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        plugins: {
                            title: { display: true, text: 'cena/m² vs data transakcji' },
                            legend: { position: 'bottom' },
                            tooltip: {
                                callbacks: {
                                    label: (ctx) => {
                                        const raw = ctx.raw || {};
                                        const d = new Date(raw.x).toISOString().slice(0, 10);
                                        return `${d} · ${Math.round(raw.y).toLocaleString('pl-PL')} zł/m² · ${raw.adres || raw.id}`;
                                    },
                                },
                            },
                        },
                        scales: {
                            x: {
                                type: 'linear',
                                title: { display: true, text: 'data' },
                                ticks: {
                                    callback: (val) => new Date(val).toISOString().slice(0, 7),
                                },
                            },
                            y: { title: { display: true, text: 'cena/m² [zł]' } },
                        },
                    },
                });
            }
        },

        _wbKindLabel(kind) {
            return ({
                grunt_niezab: 'Grunt niezabud.',
                grunt_zab:    'Grunt zabud.',
                budynek:      'Budynek',
                lokal:        'Lokal',
                inne:         'Inne',
            })[kind] || kind;
        },

        // --- Wtyczki: lista (dropdown w koszyku), uruchomienie, generyczny renderer ---
        async wbLoadPlugins() {
            if (this.pluginsLoaded) return;
            try {
                const r = await fetch('/api/plugins');
                if (r.ok) {
                    this.pluginsList = (await r.json())
                        .filter(p => p.kind === 'analysis' && p.enabled && !p.error);
                    this.pluginsLoaded = true;
                }
            } catch (e) { /* offline / błąd -> pusta lista, dropdown pokaże komunikat */ }
        },

        // Klik w pozycję dropdownu: wtyczka z deklaracją params dostaje najpierw
        // formularz w modalu; bez deklaracji -- start od razu (jak dotychczas).
        wbOpenPlugin(p) {
            if (this.selectedIds.size === 0) return;
            this.pluginMenuOpen = false;
            this.pluginCurrent = p;
            this.pluginName = p.name;
            this.pluginError = '';
            this.pluginResult = null;
            this.pluginCapped = '';
            if ((p.params || []).length) {
                const values = {};
                for (const spec of p.params) {
                    values[spec.name] = spec.default == null ? '' : String(spec.default);
                }
                this.pluginParamValues = values;
                this.pluginParams = p.params;
                this.pluginOpen = true;
            } else {
                this.pluginParams = null;
                this.wbRunPlugin(p);
            }
        },

        async wbRunPlugin(p, paramValues) {
            if (this.selectedIds.size === 0) return;
            this.pluginMenuOpen = false;
            this.pluginOpen = true;
            this.pluginLoading = true;
            this.pluginError = '';
            this.pluginResult = null;
            this.pluginCapped = '';
            this.pluginName = p.name;
            // Puste pola pomijamy -- wtyczka użyje swoich domyślnych wartości.
            const params = {};
            for (const [k, v] of Object.entries(paramValues || {})) {
                if (v !== '' && v != null) params[k] = v;
            }
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/plugins/${p.id}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id_rcn_in: Array.from(this.selectedIds), params }),
                });
                if (!r.ok) {
                    let msg = `HTTP ${r.status}`;
                    try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) {}
                    throw new Error(msg);
                }
                const capped = r.headers.get('X-RCN-Capped');
                if (capped) {
                    const [used, requested] = capped.split('/');
                    this.pluginCapped = `Uwaga: analiza objęła ${used} z ${requested} zaznaczonych transakcji (limit).`;
                }
                this.pluginResult = await r.json();
                this.$nextTick(() => this.wbRenderPluginCharts());
            } catch (err) {
                this.pluginError = err.message || String(err);
            } finally {
                this.pluginLoading = false;
            }
        },

        wbRunPluginWithParams() {
            if (!this.pluginCurrent) return;
            this.wbRunPlugin(this.pluginCurrent, this.pluginParamValues);
        },

        // Generyczny renderer wyniku wtyczki. Kontrakt:
        // error / table (dict k->v LUB {columns, rows}) / tables (lista {title, columns, rows})
        // / charts (lista speców Chart.js {type, title?, labels?, datasets, options?}; chart =
        // skrót na jeden) / svg (raw -- wtyczka to zaufany kod) / text (pre).
        wbPluginHtml() {
            const res = this.pluginResult;
            if (!res) return '';
            const esc = escapeHtml;
            const parts = [];
            if (res.error) {
                return `<div class="wb-plugin-error">${esc(String(res.error))}</div>`;
            }
            const renderTable = (t, title) => {
                let html = title ? `<h3 class="wb-plugin-table-title">${esc(String(title))}</h3>` : '';
                if (t && Array.isArray(t.columns) && Array.isArray(t.rows)) {
                    html += '<table class="wb-plugin-table"><thead><tr>'
                        + t.columns.map(c => `<th>${esc(String(c))}</th>`).join('')
                        + '</tr></thead><tbody>'
                        + t.rows.map(row => '<tr>'
                            + row.map(c => `<td>${esc(c == null ? '—' : String(c))}</td>`).join('')
                            + '</tr>').join('')
                        + '</tbody></table>';
                } else if (t && typeof t === 'object') {
                    html += '<table class="wb-plugin-table"><tbody>'
                        + Object.entries(t)
                            // Klucz 'title' wyrenderowany już jako nagłówek -- nie dublować wierszem.
                            .filter(([k]) => !(title && k === 'title'))
                            .map(([k, v]) =>
                                `<tr><th>${esc(String(k))}</th><td>${esc(v == null ? '—' : String(v))}</td></tr>`).join('')
                        + '</tbody></table>';
                }
                return html;
            };
            if (res.table) parts.push(renderTable(res.table, res.table.title));
            for (const t of (res.tables || [])) parts.push(renderTable(t, t.title));
            const charts = res.charts || (res.chart ? [res.chart] : []);
            charts.forEach((c, i) => {
                const title = c.title ? `<h3 class="wb-plugin-table-title">${esc(String(c.title))}</h3>` : '';
                parts.push(`${title}<div class="wb-plugin-chart"><canvas id="wbPluginChart${i}"></canvas></div>`);
            });
            if (res.svg) parts.push(`<div class="wb-plugin-svg">${res.svg}</div>`);
            if (res.text) parts.push(`<pre class="wb-plugin-text">${esc(String(res.text))}</pre>`);
            return parts.join('\n');
        },

        // Eksport wyniku DOWOLNEJ wtyczki do XLSX: tabele JSON, które już mamy
        // w pluginResult, konwertuje serwer (POST /api/plugins/result.xlsx,
        // arkusz per tabela + Interpretacja). Pobranie jak doExport (most
        // pywebview w desktopie / blob-download w przeglądarce).
        wbCanExportPlugin() {
            const res = this.pluginResult;
            return !!(res && !res.error && (res.table || (res.tables || []).length));
        },

        async wbExportPluginResult() {
            const res = this.pluginResult;
            if (!this.wbCanExportPlugin()) return;
            const tables = [];
            if (res.table) tables.push(res.table);
            for (const t of (res.tables || [])) tables.push(t);
            try {
                const r = await fetch('/api/plugins/result.xlsx', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename: this.pluginName || 'wynik-wtyczki',
                                           tables, text: res.text || null }),
                });
                if (!r.ok) {
                    let msg = `HTTP ${r.status}`;
                    try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) {}
                    throw new Error(msg);
                }
                const blob = await r.blob();
                const base = (this.pluginName || 'wynik-wtyczki')
                    .replace(/[^a-zA-Z0-9_.-]/g, '-').slice(0, 60);
                const filename = `${base}_${new Date().toISOString().slice(0, 10)}.xlsx`;
                if (window.pywebview && window.pywebview.api && window.pywebview.api.save_export) {
                    const b64 = await blobToBase64(blob);
                    const out = await window.pywebview.api.save_export(filename, b64);
                    if (out && out.ok) this.showToast('success', 'Zapisano XLSX', out.path);
                    else if (!(out && out.cancelled)) {
                        this.showToast('error', 'Eksport nie powiódł się', (out && out.error) || 'Błąd zapisu pliku.');
                    }
                    return;
                }
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
            } catch (err) {
                this.pluginError = 'Eksport XLSX nieudany: ' + (err.message || err);
            }
        },

        wbRenderPluginCharts() {
            if (!this.pluginResult || typeof Chart === 'undefined') return;
            for (const inst of this._wbPluginCharts) inst.destroy();
            this._wbPluginCharts = [];
            const charts = this.pluginResult.charts || (this.pluginResult.chart ? [this.pluginResult.chart] : []);
            charts.forEach((spec, i) => {
                const canvas = document.getElementById(`wbPluginChart${i}`);
                if (!canvas) return;
                const options = Object.assign(
                    { responsive: true, maintainAspectRatio: false },
                    spec.options || {},
                );
                this._wbPluginCharts.push(new Chart(canvas, {
                    type: spec.type || 'bar',
                    data: { labels: spec.labels || [], datasets: spec.datasets || [] },
                    options,
                }));
            });
        },

        // Klik na adres/obręb w głównym wierszu tabeli -- ustawia filtr
        // (adres: LIKE przez filters.adres, obręb: exact w filters.obreb).
        wbSetAdresFilter(adres) {
            if (!adres) return;
            this.filters.adres = adres;
            this.page = 1;
            this.runQuery();
        },
        wbSetObrebFilter(obreb, terytGminy) {
            if (!obreb) return;
            // Zamień, nie dokładaj: jeden klik = filtr tylko po tym obrębie.
            // Filtrujemy po kluczu z jednostką ewidencyjną, bo sam numer trafia
            // też w obręby o tym samym numerze w innych dzielnicach.
            this.filters.obreb = [];
            this.filters.obreb_key = [`${terytGminy || ''}|${obreb}`];
            this.page = 1;
            this.runQuery();
        },

        // Filtry tekstowe z headera tabeli (per-kolumna). Ustawiają pola w this.filters
        // żeby współdzielić pipeline z filtrami w slide-overze, i resetują paginację.
        applyColFilters() {
            this.filters.obreb_search = (this.colFilterObreb || '').trim() || null;
            this.page = 1;
            this.runQuery();
        },

        toggleRow(idRcn, checked) {
            if (checked) {
                if (!this._canAddToCart(1)) return;
                this.selectedIds.add(idRcn);
            } else {
                this.selectedIds.delete(idRcn);
            }
            this.selectedIds = new Set(this.selectedIds);
            this._persistCart();  // Faza 4 commit 6
            this.renderMarkers();
        },

        pageFullySelected() {
            const items = this.queryResult.items || [];
            return items.length > 0 && items.every(it => this.selectedIds.has(it.id_rcn));
        },
        pageMixedSelected() {
            const items = this.queryResult.items || [];
            if (items.length === 0) return false;
            const n = items.filter(it => this.selectedIds.has(it.id_rcn)).length;
            return n > 0 && n < items.length;
        },
        togglePageSelection(checked) {
            const items = this.queryResult.items || [];
            if (checked) {
                // Liczymy ile NEW items zostanie dodane (nie te już w koszyku).
                const newOnes = items.filter(it => !this.selectedIds.has(it.id_rcn));
                if (!this._canAddToCart(newOnes.length)) return;
                items.forEach(it => this.selectedIds.add(it.id_rcn));
            } else {
                items.forEach(it => this.selectedIds.delete(it.id_rcn));
            }
            this.selectedIds = new Set(this.selectedIds);
            this._persistCart();  // Faza 4 commit 6
            this.renderMarkers();
        },

        // PR1 redesign: usunięto selectAllResults + onToggleOnlySelected (UI nigdy nie wystawiało
        // tych akcji; backend endpoint /query/ids zostaje dla ewentualnych przyszłych użyć).

        clearSelection() {
            if (this.selectedIds.size === 0) return;
            this.selectedIds = new Set();
            this.cartItemCache = {};
            this.cartExportComment = '';
            this._persistCart();  // Faza 4 commit 6
        },

        async runQuery(opts = {}) {
            // opts.skipGeojson = true -- przeładuj tylko tabelę (pomija
            // loadGeoJson). Używane przez wbShowInTable, żeby reload
            // nie wywołał fitBounds na mapie (user byłby "oddalony" z
            // zoomu po 🎯 lub kliku markera).
            this.loadingQuery = true;
            const payload = {
                filters: this.buildFiltersPayload(),
                sort: this.sort,
                page: this.page,
                pageSize: this.pageSize,
            };
            const r = await fetch(`/api/workspaces/${this.workspaceId}/query`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            if (r.ok) {
                this.queryResult = await r.json();
            } else {
                this.queryResult = { total: 0, items: [] };
                alert('Błąd zapytania: ' + r.status);
            }
            if (!opts.skipGeojson) {
                await this.loadGeoJson({ fitBounds: opts.fitBounds });
            }
            this.loadingQuery = false;
        },

        async loadGeoJson(opts = {}) {
            // PR8: zoom-gated rendering. Zoom <14 -> agregaty per obreb (~150 kropek
            // dla calej Lodzi z liczba transakcji + srednia cena). Zoom >=14 ->
            // indywidualne markery z bbox (typowo <2000 w widoku, bez cap).
            // Cap warning chip mozna usunac z UI -- nie ma juz pojecia "ucielismy".
            const useBbox = opts.useBbox !== false;
            const shouldFit = opts.fitBounds !== false;

            const qs = new URLSearchParams();
            const f = this.filters;
            (f.rodzaj_rynku || []).forEach(v => qs.append('rodzaj_rynku', v));
            (f.rodzaj_transakcji || []).forEach(v => qs.append('rodzaj_transakcji', v));
            (f.rodzaj_nieruchomosci || []).forEach(v => qs.append('rodzaj_nieruchomosci', v));
            (f.miejscowosc || []).forEach(v => qs.append('miejscowosc', v));
            (f.teryt_gminy || []).forEach(v => qs.append('teryt_gminy', v));
            (f.obreb || []).forEach(v => qs.append('obreb', v));
            (f.obreb_key || []).forEach(v => qs.append('obreb_key', v));
            if (f.obreb_search && f.obreb_search.trim()) qs.set('obreb_search', f.obreb_search.trim());
            if (f.plot_ident) qs.set('plot_ident', f.plot_ident);
            if (f.building_ident) qs.set('building_ident', f.building_ident);
            if (f.local_ident) qs.set('local_ident', f.local_ident);
            if (f.include_withdrawn) qs.set('include_withdrawn', 'true');
            if (f.cena_min != null && f.cena_min !== '') qs.set('cena_min', f.cena_min);
            if (f.cena_max != null && f.cena_max !== '') qs.set('cena_max', f.cena_max);
            if (f.cena_m2_min != null && f.cena_m2_min !== '') qs.set('cena_m2_min', f.cena_m2_min);
            if (f.cena_m2_max != null && f.cena_m2_max !== '') qs.set('cena_m2_max', f.cena_m2_max);
            if (f.area_min != null && f.area_min !== '') qs.set('area_min', f.area_min);
            if (f.area_max != null && f.area_max !== '') qs.set('area_max', f.area_max);
            if (f.plot_ident_search && String(f.plot_ident_search).trim()) qs.set('plot_ident_search', String(f.plot_ident_search).trim());
            if (f.data_od) qs.set('data_od', f.data_od);
            if (f.data_do) qs.set('data_do', f.data_do);
            if (f.adres && f.adres.trim()) qs.set('adres', f.adres.trim());

            const zoom = this.map ? this.map.getZoom() : 13;
            // PR8 fix2: usuniete agregaty (perf + zombie markers przy panowaniu).
            // Czyste 2 progi:
            //   <14  -> NIC na mapie + plansza "Przybliz mape..." overlay
            //   >=14 -> indywidualne markery z bbox (limit 20k)
            // Faza 5 (po PR10): "klik w obiekt RCN -> modal transakcji" zastapi
            // potrzebe agregatow przy widoku globalnym.
            const showNothing = zoom < 14;

            // Zawsze najpierw wyczysc -- zapobiega "zombie" markerom po pan/zoom
            if (this.markerCluster) this.markerCluster.clearLayers();
            this.markerIndex = {};
            this._showZoomHint = showNothing;

            if (showNothing) {
                this.geojson = { features: [], capped: false, limit: 0 };
                return;
            }

            if (useBbox && this.map) {
                const b = this.map.getBounds();
                qs.set('min_lon', b.getWest().toFixed(6));
                qs.set('min_lat', b.getSouth().toFixed(6));
                qs.set('max_lon', b.getEast().toFixed(6));
                qs.set('max_lat', b.getNorth().toFixed(6));
            }

            qs.set('limit', 20000);
            const r = await fetch(`/api/workspaces/${this.workspaceId}/geojson?${qs.toString()}`);
            if (!r.ok) return;
            this.geojson = await r.json();
            this.renderMarkers(shouldFit);
        },

        // Debounced reload geojson przy pan/zoom. Wywoluje nasz zoom-gated loader.
        _scheduleGeojsonReload() {
            clearTimeout(this._geojsonReloadTimer);
            this._geojsonReloadTimer = setTimeout(() => {
                this.loadGeoJson({ fitBounds: false });
            }, 400);
        },

        // PR8 fix2: usunieto renderAggregates() -- agregaty zastapione
        // plansza overlay "Przybliz mape..." (zoom <14). Faza 5 (po PR10)
        // wprowadzi alternatywe: klik w obiekt RCN -> modal transakcji.

        // PR8 fix3: button "Przybliz mape" w plansze ma zoomowac do MIEJSCA
        // GDZIE SA TRANSAKCJE w aktualnym filtrze, nie do losowego centrum mapy.
        // Strategia: pierwszy item z queryResult.items (paginacja, ma centroid_lat/lon
        // z /query). Jesli brak (np. filter dal 0 wynikow) -- po prostu zoomIn(2).
        wbZoomToFirstResult() {
            if (!this.map) return;
            const items = this.queryResult.items || [];
            const first = items.find(it => it.centroid_lat != null && it.centroid_lon != null);
            if (first) {
                // Zoom 14 = prog gdzie pojawiaja sie indywidualne piny
                this.map.setView([first.centroid_lat, first.centroid_lon], 14);
            } else {
                // Brak transakcji w filtrze albo brak centroidu -- zwykly zoomIn
                this.map.zoomIn(2);
            }
        },

        // PR9: system powiadomien toast.
        // showToast(type, title, desc?, ttl?) -- type: 'success' | 'info' | 'error'
        // Auto-hide po ttl ms (default 4000). Klik X -> dismissToast(id).
        showToast(type, title, desc = '', ttl = 4000) {
            const id = this._toastNextId++;
            this.toasts.push({ id, type, title, desc });
            if (ttl > 0) {
                setTimeout(() => this.dismissToast(id), ttl);
            }
            return id;
        },
        dismissToast(id) {
            const i = this.toasts.findIndex(t => t.id === id);
            if (i >= 0) this.toasts.splice(i, 1);
        },

        setupMap() {
            // PR6 fix: attribution control w prawym GORNYM rogu mapy.
            // Bottomright zachodzil pod sidebar 380 px (niewidoczny).
            // Bottomleft byl pod drawerem 1100 z-index (niewidoczny).
            // Topright = prawy gorny rog mapy = na lewo od sidebara, nad drawerem,
            // ZAWSZE WIDOCZNY. Wymagane przez Leaflet license.
            this.map = L.map('map', {
                attributionControl: false,
            }).setView([52.1, 19.2], 6);
            L.control.attribution({ position: 'topright', prefix: 'Leaflet' }).addTo(this.map);
            // Dedicated pane above overlayPane (400) and markerPane (600), so
            // transaction circle markers sit on top of polygon overlays (RCN plots,
            // EGIB, WMS) and still receive clicks/tooltips.
            this.map.createPane('txMarkers');
            this.map.getPane('txMarkers').style.zIndex = 650;

            // Przy pan/zoom mapy przeładowujemy geojson z nowym bbox --
            // żeby zawsze widzieć wszystkie transakcje w aktualnym widoku
            // (a nie tylko pierwsze 5000 globalnie). Flaga _skipNextMoveend
            // zapobiega pętli gdy moveend pochodzi z naszego fitBounds.
            this.map.on('moveend', () => {
                if (this._skipNextMoveend) {
                    this._skipNextMoveend = false;
                    return;
                }
                this._scheduleGeojsonReload();
            });

            // Leaflet nie odświeża się sam, gdy kontener zmienia rozmiar
            // (Tailwind CDN init, drag drawera). ResizeObserver wymusza invalidate,
            // ale przy drag drawera firuje setki razy na sekundę -- dlatego
            // debouncujemy przez requestAnimationFrame (max raz na klatkę).
            const mapEl = document.getElementById('map');
            if (mapEl && window.ResizeObserver) {
                let rafPending = false;
                const ro = new ResizeObserver(() => {
                    if (rafPending) return;
                    rafPending = true;
                    requestAnimationFrame(() => {
                        rafPending = false;
                        if (this.map) this.map.invalidateSize({ animate: false });
                    });
                });
                ro.observe(mapEl);
                this._mapResizeObserver = ro;
            }
            const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '&copy; OpenStreetMap',
            }).addTo(this.map);

            const orto = L.tileLayer.wms('https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/HighResolution', {
                layers: 'Raster',
                format: 'image/jpeg',
                transparent: false,
                maxZoom: 21,
                attribution: '&copy; GUGiK Ortofotomapa',
            });
            const ortoStandard = L.tileLayer.wms('https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution', {
                layers: 'Raster',
                format: 'image/jpeg',
                transparent: false,
                maxZoom: 19,
                attribution: '&copy; GUGiK Ortofotomapa',
            });

            this.markerCluster = L.markerClusterGroup({ chunkedLoading: true });
            this.map.addLayer(this.markerCluster);
            // Miarka: klik w marker transakcji = pomiar do dokładnej pozycji
            // markera (nie kursora); popup w trybie pomiaru nie zostaje otwarty.
            this.markerCluster.on('click', (e) => {
                if (!this.measureActive || !e.layer || !e.layer.getLatLng) return;
                this._measureClick(e.layer.getLatLng(), null, true);
                queueMicrotask(() => { if (e.layer.closePopup) e.layer.closePopup(); });
            });

            this.drawnLayer = new L.FeatureGroup();
            this.map.addLayer(this.drawnLayer);

            for (const key of Object.keys(this.overlays)) {
                if (key === 'wmsEgib') {
                    this.overlays[key].layer = L.tileLayer.wms(
                        'https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow',
                        {
                            layers: 'dzialki,numery_dzialek,budynki',
                            format: 'image/png',
                            transparent: true,
                            version: '1.3.0',
                            maxZoom: 22,
                            attribution: '&copy; GUGiK EGIB',
                        }
                    );
                } else if (this.overlays[key].custom) {
                    // Custom GPKG -- neutralny styl (user może sam kolorować w przyszłości).
                    this.overlays[key].layer = L.geoJSON(null, {
                        style: { color: '#7c3aed', weight: 0.9, fillColor: '#c4b5fd', fillOpacity: 0.15, dashArray: '2,3' },
                        onEachFeature: (f, lyr) => this.bindOverlayTooltip(f, lyr, this.overlays[key].displayName || 'custom'),
                    });
                } else if (this.overlays[key].poi) {
                    // POI z OSM (plik <nazwa>.poi.sqlite) -- małe kolorowe kropki
                    // z popupem kategoria/nazwa. Atrybucja ODbL wymagana licencyjnie.
                    this.overlays[key].layer = L.geoJSON(null, {
                        attribution: '© autorzy OpenStreetMap (ODbL)',
                        pointToLayer: (f, latlng) => L.circleMarker(latlng, {
                            radius: 5,
                            color: '#ffffff',
                            weight: 1,
                            fillColor: this.poiColor((f.properties || {}).kind),
                            fillOpacity: 0.9,
                        }),
                        onEachFeature: (f, lyr) => {
                            const p = f.properties || {};
                            const label = this.poiKindLabel(p.kind);
                            lyr.bindPopup(
                                `<strong>${escapeHtml(p.name || label)}</strong><br>`
                                + `<span class="muted">${escapeHtml(label)} · dane © autorzy OpenStreetMap</span>`
                            );
                        },
                    });
                    // Miarka: klik w POI = pomiar do dokładnej pozycji punktu,
                    // z nazwą POI w etykiecie pomiaru.
                    this.overlays[key].layer.on('click', (e) => {
                        if (!this.measureActive || !e.layer) return;
                        const p = (e.layer.feature || {}).properties || {};
                        this._measureClick(e.layer.getLatLng(),
                                           p.name || this.poiKindLabel(p.kind), true);
                        queueMicrotask(() => { if (e.layer.closePopup) e.layer.closePopup(); });
                    });
                } else {
                    this.overlays[key].layer = L.geoJSON(null, this.styleForOverlay(key));
                }
                // Warstwy z enabled:true dodajemy od razu, żeby L.control.layers
                // zaznaczył checkbox, a refreshOverlay (po moveend) dociągnął dane.
                if (this.overlays[key].enabled) {
                    this.map.addLayer(this.overlays[key].layer);
                }
            }

            const overlayLabels = {
                rcnParcels:  'Działki z RCN (objęte transakcjami)',
                rcnBuilds:   'Budynki z RCN (objęte transakcjami)',
                egibParcels: 'Podkład EGIB — działki (lokalny GPKG)',
                egibBuilds:  'Podkład EGIB — budynki (lokalny GPKG)',
                wmsEgib:     'EGIB online (WMS GUGiK — działki + budynki)',
                poi:         'POI — szkoły, sklepy, przystanki… (OSM)',
            };
            // Per-workspace custom GPKG warstwy -- etykiety z nazw dodanych przy tworzeniu.
            for (const cl of this.customLayers) {
                overlayLabels[`custom_${cl.slug}`] = `📍 ${cl.name}`;
            }
            const overlaysForControl = {};
            for (const [key, meta] of Object.entries(this.overlays)) {
                overlaysForControl[overlayLabels[key]] = meta.layer;
            }
            L.control.layers(
                {
                    'OpenStreetMap': osm,
                    'Ortofoto GUGiK (HD)': orto,
                    'Ortofoto GUGiK (standard)': ortoStandard,
                },
                overlaysForControl,
                { collapsed: true, position: 'topright', hideSingleBase: false },
            ).addTo(this.map);

            this.map.on('overlayadd', (e) => this.onOverlayToggled(e.name, overlayLabels, true));
            this.map.on('overlayremove', (e) => this.onOverlayToggled(e.name, overlayLabels, false));
            this.map.on('moveend', () => this.scheduleOverlayRefresh());
            this.map.on('click', (e) => this._measureClick(e.latlng, null));

            this.drawControl = new L.Control.Draw({
                draw: {
                    polygon: true,
                    rectangle: true,
                    marker: false,
                    polyline: false,
                    circle: false,
                    circlemarker: false,
                },
                edit: { featureGroup: this.drawnLayer, edit: false, remove: true },
            });
            this.map.addControl(this.drawControl);

            this.map.on(L.Draw.Event.CREATED, (e) => {
                this.drawnLayer.clearLayers();
                this.drawnLayer.addLayer(e.layer);
                if (e.layerType === 'rectangle') {
                    const b = e.layer.getBounds();
                    this.spatial = {
                        active: true, kind: 'bbox',
                        bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()],
                        polygon: null,
                    };
                } else if (e.layerType === 'polygon') {
                    const ring = e.layer.getLatLngs()[0].map(p => [p.lng, p.lat]);
                    if (ring.length > 0 && (ring[0][0] !== ring[ring.length-1][0] || ring[0][1] !== ring[ring.length-1][1])) {
                        ring.push(ring[0]);
                    }
                    this.spatial = { active: true, kind: 'polygon', bbox: null, polygon: [ring] };
                }
                this.page = 1;
                // BBox/polygon narysowany w aktualnym viewport -- NIE fitujemy mapy,
                // bo renderMarkers(fitBounds=true, maxZoom=14) z 700+ markerow
                // odjechalby do zoom 14 (z 16-17 ktory user mial przy draw).
                this.runQuery({ fitBounds: false });
            });
            this.map.on(L.Draw.Event.DELETED, () => {
                this.clearSpatial();
            });
        },

        // --- Miarka (pomiar odległości na mapie) ---
        wbToggleMeasure() {
            this.measureActive = !this.measureActive;
            const el = document.getElementById('map');
            if (el) el.classList.toggle('wb-measuring', this.measureActive);
            if (this.measureActive) {
                if (!this.measureLayer) this.measureLayer = L.featureGroup().addTo(this.map);
            } else {
                this._measureDropPending();
            }
        },

        wbClearMeasure() {
            this._measureDropPending();
            if (this.measureLayer) this.measureLayer.clearLayers();
            this.measureHasResult = false;
        },

        _measureDropPending() {
            if (this.measurePending && this.measurePending.marker) {
                this.measureLayer.removeLayer(this.measurePending.marker);
            }
            this.measurePending = null;
        },

        _measureDot(latlng) {
            return L.circleMarker(latlng, {
                radius: 5, color: '#9a3412', weight: 2,
                fillColor: '#ffffff', fillOpacity: 1, pane: 'txMarkers',
            });
        },

        // Wspólna obsługa kliknięcia w trybie miarki. `label` = opis punktu
        // (np. nazwa POI), `fromLayer` = klik delegowany z markera (dedup).
        _measureClick(latlng, label, fromLayer = false) {
            if (!this.measureActive || !latlng) return false;
            if (fromLayer) {
                this._measureLastLayerClick = Date.now();
            } else if (Date.now() - this._measureLastLayerClick < 150) {
                return true;  // ten sam klik dotarł już z markera
            }
            if (!this.measurePending) {
                const marker = this._measureDot(latlng).addTo(this.measureLayer);
                this.measurePending = { latlng, label, marker };
                return true;
            }
            const a = this.measurePending;
            const d = this.map.distance(a.latlng, latlng);
            const dist = d >= 1000
                ? (d / 1000).toFixed(2).replace('.', ',') + ' km'
                : Math.round(d) + ' m';
            const parts = [];
            if (a.label) parts.push(escapeHtml(a.label));
            parts.push(`<strong>${dist}</strong>`);
            if (label) parts.push(escapeHtml(label));
            const line = L.polyline([a.latlng, latlng], {
                color: '#9a3412', weight: 2.5, dashArray: '6,4', opacity: 0.9,
            });
            line.bindTooltip(parts.join(' · '), {
                permanent: true, direction: 'center', className: 'wb-measure-tip',
            });
            this.measureLayer.addLayer(line);
            this.measureLayer.addLayer(this._measureDot(latlng));
            this.measurePending = null;  // start dot zostaje jako koniec pomiaru
            this.measureHasResult = true;
            return true;
        },

        // Kategorie POI (spójne z kategoriami generatora plików POI).
        poiKindLabel(kind) {
            return ({
                szkola: 'Szkoła', przedszkole: 'Przedszkole', sklep: 'Sklep spożywczy',
                przystanek: 'Przystanek', apteka: 'Apteka', restauracja: 'Restauracja',
                park: 'Park', szpital: 'Szpital', biblioteka: 'Biblioteka', poczta: 'Poczta',
            })[kind] || (kind || 'POI');
        },

        poiColor(kind) {
            return ({
                szkola: '#2563eb', przedszkole: '#60a5fa', sklep: '#16a34a',
                przystanek: '#9333ea', apteka: '#dc2626', restauracja: '#ea580c',
                park: '#15803d', szpital: '#be123c', biblioteka: '#0e7490', poczta: '#a16207',
            })[kind] || '#475569';
        },

        // Paleta kolorów per rodzaj nieruchomości -- rzeczoznawca czyta mapę
        // bez legendy: grunty niezabud. = ciemny granat, grunty zabud. = indigo,
        // budynki = burnt orange, lokale = zieleń. Patrz: decyzja z sesji 2026-04-24.
        rcnPaletteFor(feature) {
            const kind = String((feature.properties || {}).rodzaj || '').toLowerCase();
            if (kind.includes('niezab'))        return { color: '#1f4e79', fillColor: '#3b82f6' };  // grunt niezabud.
            if (kind.includes('grunt'))         return { color: '#1e3a8a', fillColor: '#6366f1' };  // grunt zabud.
            if (kind.includes('budynk'))        return { color: '#9a3412', fillColor: '#fb923c' };  // budynek
            if (kind.includes('lokal'))         return { color: '#166534', fillColor: '#4ade80' };  // lokal
            return { color: '#475569', fillColor: '#94a3b8' };                                       // inne / brak
        },

        styleForOverlay(key) {
            const self = this;
            const styles = {
                rcnParcels: {
                    style: (f) => ({
                        ...self.rcnPaletteFor(f),
                        weight: 1.5,
                        fillOpacity: 0.28,
                    }),
                    onEachFeature: (f, lyr) => this.bindOverlayTooltip(f, lyr, 'działka RCN'),
                },
                rcnBuilds: {
                    style: (f) => ({
                        ...self.rcnPaletteFor(f),
                        weight: 1.3,
                        fillOpacity: 0.45,
                    }),
                    onEachFeature: (f, lyr) => this.bindOverlayTooltip(f, lyr, 'budynek RCN'),
                },
                egibParcels: {
                    style: { color: '#475569', weight: 0.7, fillColor: '#cbd5e1', fillOpacity: 0.08 },
                    onEachFeature: (f, lyr) => this.bindOverlayTooltip(f, lyr, 'EGIB działka'),
                },
                egibBuilds: {
                    style: { color: '#52525b', weight: 0.7, fillColor: '#9ca3af', fillOpacity: 0.25 },
                    onEachFeature: (f, lyr) => this.bindOverlayTooltip(f, lyr, 'EGIB budynek'),
                },
                wfsParcels: {
                    style: { color: '#6b21a8', weight: 0.9, fillColor: '#c4b5fd', fillOpacity: 0.1, dashArray: '3,2' },
                    onEachFeature: (f, lyr) => this.bindOverlayTooltip(f, lyr, 'WFS działka'),
                },
            };
            return styles[key] || {};
        },

        // Buduje HTML opisu działki/budynku z properties GeoJSON.
        // Reuse: bindOverlayTooltip (hover) + _bindModalOverlayPopup (click w modalu).
        // opts.includeGeoportal=true dodaje link Geoportal pod opisem (popup w modalu).
        _buildOverlayInfoHtml(feature, label, opts = {}) {
            const rawProps = feature.properties || {};
            const p = {};
            for (const [k, v] of Object.entries(rawProps)) {
                p[k.toLowerCase()] = v;
            }
            const isBuilding = label.toLowerCase().includes('budynek');
            const parts = [];
            const fullId = p.id_dzialki || p.id_budynku || p.ident || p.identyfikator
                || p.identyfikator_ewidencyjny || p.nr_dzialki || '';
            let shortNum = p.numer_dzialki || p.numer || '';
            if (!shortNum && isBuilding) {
                // Budynek: etykieta (zwarty label z EGIB, np. "5m2") albo ostatni
                // komponent id_budynku ("306401_1.0002.AR_12.78.5_BUD" -> "78.5").
                if (p.etykieta) shortNum = String(p.etykieta);
                else if (fullId) {
                    const stripped = String(fullId).replace(/_BUD$/i, '');
                    const tail = stripped.split('.').slice(-2).join('.');
                    if (tail) shortNum = tail;
                }
            }
            if (!shortNum && fullId && fullId.includes('.')) {
                shortNum = fullId.split('.').pop();
            }
            const mainLabel = shortNum || fullId;
            if (mainLabel) parts.push(`<strong>${escapeHtml(mainLabel)}</strong>`);
            const obrebName = p.nazwa_obrebu || p.obreb || p.numer_obrebu;
            if (obrebName && obrebName !== mainLabel) parts.push(`obręb <strong>${escapeHtml(obrebName)}</strong>`);
            const addr = [p.miejscowosc, p.adres, p.nazwa_gminy].filter(Boolean).join(' · ');
            if (addr) parts.push(escapeHtml(addr));
            const area = p.powierzchnia ?? p.pole_ewidencyjne ?? p.pow ?? p.pow_ew ?? p.pole;
            if (area != null && area !== '') {
                const numeric = Number(area);
                let areaStr;
                if (Number.isFinite(numeric)) {
                    // EGIB Downloader zapisuje powierzchnię w hektarach (np. 0.0787 ha).
                    // Konwertujemy do m² gdy wartość jest < 10 (z pewnością ha).
                    const m2 = numeric < 10 ? numeric * 10000 : numeric;
                    areaStr = `${m2.toLocaleString('pl-PL', { maximumFractionDigits: 0 })} m²`;
                } else {
                    areaStr = `${escapeHtml(String(area))} m²`;
                }
                parts.push(`pow. <strong>${areaStr}</strong>`);
            }
            if (isBuilding) {
                // EGIB kody jedno-/dwuznakowe (m=mieszkalny, g=gospodarczy itd.)
                // Niektorzy operatorzy zwracaja pelne nazwy ("budynki mieszkalne"),
                // wtedy wyswietlamy as-is.
                const RODZAJ_MAP = {
                    m: 'mieszkalny', g: 'gospodarczy', p: 'produkcyjny',
                    h: 'handlowo-usługowy', u: 'użyteczność publiczna',
                    t: 'transportu', b: 'biurowy', l: 'łączności',
                    n: 'niemieszkalny inny', i: 'inny',
                };
                let rodzaj = p.funkcja_budynku || p.rodzaj_budynku || p.rodzaj;
                if (rodzaj && String(rodzaj).length <= 2) {
                    const mapped = RODZAJ_MAP[String(rodzaj).toLowerCase()];
                    if (mapped) rodzaj = mapped;
                }
                if (rodzaj) parts.push(`rodzaj: ${escapeHtml(String(rodzaj))}`);
                const kondNad = p.kondygnacje_nadziemne ?? p.liczba_kondygnacji;
                const kondPod = p.kondygnacje_podziemne;
                if (kondNad != null && kondNad !== '') {
                    let kondStr = `${kondNad} nadz.`;
                    if (kondPod != null && kondPod !== '' && Number(kondPod) > 0) kondStr += ` / ${kondPod} podz.`;
                    parts.push(`kondygnacje: ${escapeHtml(kondStr)}`);
                }
            } else {
                const uzytek = p.sposob_uzytkowania || p.uzytkowanie || p.klasouzytki_egib;
                if (uzytek) parts.push(`użytek: ${escapeHtml(uzytek)}`);
            }
            if (p.cena_brutto != null) parts.push(`cena: <strong>${formatMoney(p.cena_brutto)}</strong>`);
            if (fullId && fullId !== mainLabel) {
                parts.push(`<span class="tip-meta">ID: ${escapeHtml(fullId)}</span>`);
            }
            if (opts.includeGeoportal && fullId) {
                const geoUrl = `https://mapy.geoportal.gov.pl/imapnext/imap/?identifyParcel=${encodeURIComponent(fullId)}`;
                parts.push(`<a href="${geoUrl}" target="_blank" rel="noopener">Geoportal ↗</a>`);
            }
            const pillKind = label.includes('budynek') ? 'building'
                : label.startsWith('EGIB') || label.startsWith('WFS') || label.includes('RCN') ? 'plot'
                : 'local';
            const html = `<div class="marker-tip">
                <div class="tip-head"><span class="pill pill-${pillKind}">${escapeHtml(label)}</span></div>
                <div class="tip-body">${parts.join('<br>') || '—'}</div>
            </div>`;
            return { html, fullId };
        },

        bindOverlayTooltip(feature, layer, label) {
            const { html } = this._buildOverlayInfoHtml(feature, label);
            const tooltipOpts = { direction: 'top', sticky: true, opacity: 0.97, className: 'rcn-tooltip' };
            layer.bindTooltip(html, tooltipOpts);
            const isEgibOrWfs = label.startsWith('EGIB') || label.startsWith('WFS');
            if (isEgibOrWfs) {
                layer.on('tooltipopen', () => {
                    if (this.map.getZoom() < 16) layer.closeTooltip();
                });
            }
        },

        // Popup dla overlays w modalu mapy -- ten sam content co tooltip + link Geoportal.
        // Click-popup zamiast hover, bo w modalu tooltip czasem trudny do trafienia
        // (małe polygony pod markerami transakcji).
        _bindModalOverlayPopup(feature, layer, label) {
            const { html } = this._buildOverlayInfoHtml(feature, label, { includeGeoportal: true });
            layer.bindPopup(html, { maxWidth: 320, autoClose: true, closeOnClick: false });
        },

        // PR1 redesign: usunięto scrollToActiveRow (zastąpione przez wbShowInTable
        // które ma własny scroll + pulse). Stara metoda nigdy nie była wywoływana.

        onOverlayToggled(name, labelsMap, enabled) {
            const entry = Object.entries(labelsMap).find(([, v]) => v === name);
            if (!entry) return;
            const key = entry[0];
            this.overlays[key].enabled = enabled;
            if (enabled) this.refreshOverlay(key);
            // POI nie czyścimy -- dane statyczne, wyłączenie zdejmuje warstwę z mapy,
            // a ponowne włączenie ma być natychmiastowe (bez refetchu).
            else if (!this.overlays[key].poi && this.overlays[key].layer.clearLayers) this.overlays[key].layer.clearLayers();
        },

        scheduleOverlayRefresh() {
            clearTimeout(this.moveTimer);
            this.moveTimer = setTimeout(() => {
                for (const key of Object.keys(this.overlays)) {
                    if (this.overlays[key].enabled) this.refreshOverlay(key);
                }
            }, 250);
        },

        async refreshOverlay(key) {
            const meta = this.overlays[key];
            if (!meta || !meta.enabled || meta.loading) return;
            if (meta.wms) return;  // WMS tile layer refreshes itself, no bbox fetch needed
            if (meta.poi) {
                // POI: cały zbiór raz (statyczny, nieliczny) -- bez bbox i moveend.
                if (meta._loaded) return;
                meta.loading = true;
                try {
                    const r = await fetch(`/api/layers/workspaces/${this.workspaceId}/poi.geojson`);
                    if (!r.ok) return;
                    const geojson = await r.json();
                    meta.layer.clearLayers();
                    meta.layer.addData(geojson);
                    meta._loaded = true;
                    this.overlayMeta.sources[key] = geojson.source || 'poi';
                } catch (e) {
                    console.warn('Overlay poi error:', e);
                } finally {
                    meta.loading = false;
                }
                return;
            }
            if (this.map.getZoom() < meta.minZoom) {
                meta.layer.clearLayers();
                return;
            }
            const b = this.map.getBounds();
            const bbox = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
            const url = this.overlayUrl(key, bbox);
            if (!url) return;
            meta.loading = true;
            try {
                const r = await fetch(url);
                if (!r.ok) {
                    console.warn(`Overlay ${key} failed: ${r.status}`);
                    return;
                }
                const geojson = await r.json();
                meta.layer.clearLayers();
                meta.layer.addData(geojson);
                this.overlayMeta.capped[key] = !!geojson.capped;
                this.overlayMeta.sources[key] = geojson.source || '';
            } catch (e) {
                console.warn(`Overlay ${key} error:`, e);
            } finally {
                meta.loading = false;
            }
        },

        overlayUrl(key, bbox) {
            const meta = this.overlays[key];
            const q = `bbox=${encodeURIComponent(bbox)}&limit=2000`;
            if (meta.endpoint === 'parcels') return `/api/layers/workspaces/${this.workspaceId}/parcels.geojson?${q}`;
            if (meta.endpoint === 'buildings') return `/api/layers/workspaces/${this.workspaceId}/buildings.geojson?${q}`;
            if (meta.egib) return `/api/layers/egib/${meta.egib}.geojson?${q}`;
            if (meta.wfs) return `/api/layers/wfs/${meta.wfs}.geojson?bbox=${encodeURIComponent(bbox)}`;
            if (meta.custom) return `/api/layers/workspaces/${this.workspaceId}/custom/${meta.custom}.geojson?${q}`;
            return null;
        },


        renderMarkers(fitBounds = false) {
            if (!this.markerCluster) return;
            this.markerCluster.clearLayers();
            this.markerIndex = {};
            const markers = [];
            for (const f of this.geojson.features || []) {
                const [lon, lat] = f.geometry.coordinates;
                if (lon == null || lat == null) continue;
                const p = f.properties;
                const rodzaj = p.rodzaj_nieruchomosci || '';
                const colorBase =
                    /Lokalowa/i.test(rodzaj) ? '#5a9c61'
                    : /Gruntowa/i.test(rodzaj) ? '#1f4e79'
                    : /Budynkowa/i.test(rodzaj) ? '#d27a3a'
                    : '#475569';

                const m = L.circleMarker([lat, lon], {
                    pane: 'txMarkers',
                    radius: 6,
                    color: colorBase,
                    fillColor: colorBase,
                    weight: 1.5,
                    fillOpacity: 0.85,
                });

                const rodzajShort = rodzaj.replace(/^nieruchomosc\s+/i, '');
                const addr = [p.miejscowosc, p.adres].filter(Boolean).join(' · ');
                const cena = p.cena_transakcji_brutto != null ? formatMoney(p.cena_transakcji_brutto) : '';
                const objCounts = [];
                if (p.plot_count)     objCounts.push(`${p.plot_count} dz.`);
                if (p.building_count) objCounts.push(`${p.building_count} bud.`);
                if (p.local_count)    objCounts.push(`${p.local_count} lok.`);

                const tooltipHtml = `
                    <div class="marker-tip">
                      <div class="tip-head">
                        <span class="pill pill-tx">${escapeHtml(rodzajShort || 'transakcja')}</span>
                        <strong>${escapeHtml(p.data_transakcji || '')}</strong>
                      </div>
                      <div class="tip-addr">${escapeHtml(addr || '—')}${p.obreb ? ' · obręb <strong>' + escapeHtml(p.obreb) + '</strong>' : ''}</div>
                      <div class="tip-price">${escapeHtml(cena)}</div>
                      <div class="tip-meta">${escapeHtml(p.rodzaj_transakcji || '')}${p.rodzaj_rynku ? ' · ' + escapeHtml(p.rodzaj_rynku) : ''}</div>
                      ${objCounts.length ? `<div class="tip-meta">obiekty: ${objCounts.join(' · ')}</div>` : ''}
                    </div>`;
                m.bindTooltip(tooltipHtml, {
                    direction: 'top',
                    offset: [0, -4],
                    sticky: true,
                    opacity: 0.97,
                    className: 'rcn-tooltip',
                });

                const svUrl = `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat.toFixed(6)},${lon.toFixed(6)}&fov=120`;
                const idents = Array.isArray(p.plot_idents) ? p.plot_idents : [];
                const geoportalLinksHtml = idents.length > 0
                    ? idents.map(id => {
                        const url = `https://mapy.geoportal.gov.pl/imapnext/imap/?identifyParcel=${encodeURIComponent(id)}`;
                        return `<a href="${url}" target="_blank" rel="noopener">🗺 ${escapeHtml(id)}</a>`;
                      }).join('')
                    : `<a href="https://mapy.geoportal.gov.pl/imapnext/imap/?gpmap=gp0&zoom=19" target="_blank" rel="noopener">🗺 Geoportal (brak ID)</a>`;
                // PR8: popup z mocks/redesign-v2-components.html sekcja 4
                // Header z badge "TRANSAKCJA" + data, adres bold, meta z badge typu,
                // group cen w jasnym tle, 2 primary CTA, linki secondary footer.
                const typeKey = this.typeKey(p.rodzaj_nieruchomosci);
                const typeLabel = this.typeLabel(p.rodzaj_nieruchomosci);
                const cenaM2 = (p.cena_transakcji_brutto && p.area_m2 > 0)
                    ? Math.round(p.cena_transakcji_brutto / p.area_m2).toLocaleString('pl-PL') + ' zł'
                    : '—';
                const cenaPLN = (p.cena_transakcji_brutto != null)
                    ? Math.round(p.cena_transakcji_brutto).toLocaleString('pl-PL') + ' zł'
                    : '—';
                const objCountsHtml = objCounts.length
                    ? `<span class="dot">·</span><span>${objCounts.join(' · ')}</span>`
                    : '';
                m.bindPopup(`
                    <div class="marker-tip">
                      <div class="tip-head">
                        <span class="tip-badge">TRANSAKCJA</span>
                        <span class="tip-date">${escapeHtml(p.data_transakcji || '')}</span>
                      </div>
                      <div class="tip-body">
                        <div class="tip-addr">${escapeHtml(addr || '—')}</div>
                        <div class="tip-meta">
                          ${p.obreb ? `<span class="mono">obręb ${escapeHtml(p.obreb)}</span>` : ''}
                          ${objCountsHtml}
                          ${typeLabel ? `<span class="dot">·</span><span class="tip-type-badge t-${typeKey}"><span class="dot-color"></span>${escapeHtml(typeLabel)}</span>` : ''}
                        </div>
                        <div class="tip-prices">
                          <div class="tip-price-block">
                            <span class="label">Cena</span>
                            <span class="value">${cenaPLN}</span>
                          </div>
                          <div class="tip-price-block">
                            <span class="label">Cena/m²</span>
                            <span class="value">${cenaM2}</span>
                          </div>
                        </div>
                        <div class="tip-actions">
                          <button type="button" class="tip-btn-primary" data-action="toggleCart">+ Do kolekcji</button>
                          <button type="button" class="tip-btn-secondary" data-action="showInTable">W tabeli</button>
                        </div>
                        ${this._buildNeighborsBlock()}
                        <div class="tip-links">
                          ${this._buildExternalLinkBtn(svUrl, 'streetview', 'Street View')}
                          ${idents.length > 0
                            ? this._buildExternalLinkBtn(`https://mapy.geoportal.gov.pl/imapnext/imap/?identifyParcel=${encodeURIComponent(idents[0])}`, 'geoportal', 'Geoportal')
                            : this._buildExternalLinkBtn('https://mapy.geoportal.gov.pl/imapnext/imap/?gpmap=gp0&zoom=19', 'geoportal', 'Geoportal')}
                        </div>
                      </div>
                    </div>
                `, {
                    // Klik w mapę poza popupem NIE zamyka -- user musi kliknąć ×.
                    // Przy szybkim przemieszczaniu kursora/kliku w popup nie trzaskaj sobie w twarz.
                    closeOnClick: false,
                    autoClose: false,
                    keepInView: true,
                    maxWidth: 380,
                    minWidth: 320,
                });
                m.on('click', () => {
                    // Klik markera = zaznacz logicznie (active wiersz w tabeli),
                    // ale NIE dodawaj automatycznie do koszyka -- to jest teraz
                    // przyciskiem w popupie.
                    this.selected = p.id_rcn;
                });
                m.on('popupopen', (ev) => {
                    const node = ev.popup.getElement();
                    if (!node) return;
                    // PR8 fix4: fallback item z geojson properties (p) zawiera
                    // wszystkie pola potrzebne dla cartItemCache -- tak zeby
                    // koszyk pokazywal pelne dane (adres/cena/typ) zamiast hash.
                    const item = this.queryResult.items.find(it => it.id_rcn === p.id_rcn) || {
                        id_rcn: p.id_rcn,
                        adres: p.adres,
                        miejscowosc: p.miejscowosc,
                        obreb: p.obreb,
                        rodzaj_nieruchomosci: p.rodzaj_nieruchomosci,
                        cena_transakcji_brutto: p.cena_transakcji_brutto,
                        // /geojson nie zwraca area_m2 ani cena_na_m2 --
                        // brak ich w popupie nie jest blokujacy (po prostu
                        // koszyk pokaze "—" w price-sub).
                        data_transakcji: p.data_transakcji,
                    };
                    const cartBtn = node.querySelector('[data-action="toggleCart"]');
                    const tableBtn = node.querySelector('[data-action="showInTable"]');
                    const refreshCartLabel = () => {
                        if (!cartBtn) return;
                        const inCart = this.selectedIds.has(p.id_rcn);
                        cartBtn.textContent = inCart ? '✓ W kolekcji' : '+ Do kolekcji';
                        cartBtn.classList.toggle('in-cart', inCart);
                    };
                    refreshCartLabel();
                    if (cartBtn) {
                        cartBtn.onclick = (e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            this.wbToggleItem(item);
                            refreshCartLabel();
                        };
                    }
                    if (tableBtn) {
                        tableBtn.onclick = (e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            this.wbShowInTable(item);
                            m.closePopup();
                        };
                    }
                    // Wire "Sąsiedzi w X m" buttony -- enrich item z lat/lon
                    // (popup w main map dostaje p z geojson, centroid_lat/lon nie
                    // są tam standardowo, ale `lat`/`lon` lokalne są).
                    const itemForNeighbors = { ...item, centroid_lat: lat, centroid_lon: lon };
                    this._wireNeighborsActions(node, itemForNeighbors);
                });
                markers.push(m);
                this.markerIndex[p.id_rcn] = [m];
            }
            this.markerCluster.addLayers(markers);
            if (fitBounds && markers.length > 0) {
                const group = L.featureGroup(markers);
                try {
                    // Flaga zapobiega pętli: nasz fitBounds trigguje moveend,
                    // który normalnie by wywołał kolejny loadGeoJson.
                    this._skipNextMoveend = true;
                    this.map.fitBounds(group.getBounds().pad(0.1), { maxZoom: 14 });
                } catch (e) { /* ignore */ this._skipNextMoveend = false; }
            }
        },

        sortBy(column) {
            if (this.sort.column === column) {
                this.sort.order = this.sort.order === 'asc' ? 'desc' : 'asc';
            } else {
                this.sort.column = column;
                this.sort.order = 'asc';
            }
            this.runQuery();
        },

        // PR3 redesign: zamiast sortArrow tekstowego ↕/▲/▼ używamy
        // sortClass() do zaaplikowania CSS na <th>, który steruje kolorami
        // dwóch SVG chevronów w nagłówku. ariaSortFor() generuje aria-sort.
        sortArrow(column) {
            // Zachowane dla wstecznej kompatybilności (gdyby gdzieś tkwiło).
            if (this.sort.column !== column) return '';
            return this.sort.order === 'asc' ? '▲' : '▼';
        },
        sortClass(column) {
            if (this.sort.column !== column) return '';
            return this.sort.order === 'asc' ? 'sorted-asc' : 'sorted-desc';
        },
        ariaSortFor(column) {
            if (this.sort.column !== column) return 'none';
            return this.sort.order === 'asc' ? 'ascending' : 'descending';
        },

        // PR3 redesign: helpery do badge'a typu nieruchomości w komórce "Typ".
        // Mapują pełną nazwę z GML (np. "Lokalowa Mieszkalna") na krótki klucz
        // CSS (lokal/budynek/grunt-zab/grunt-niezab/inne) i krótki label.
        typeKey(rodzaj) {
            const s = (rodzaj || '').toLowerCase();
            if (s.includes('niezab')) return 'grunt-niezab';
            if (s.includes('grunt')) return 'grunt-zab';
            if (s.includes('budynk')) return 'budynek';
            if (s.includes('lokal')) return 'lokal';
            return 'inne';
        },
        typeLabel(rodzaj) {
            const k = this.typeKey(rodzaj);
            return ({
                'lokal': 'Lokal',
                'budynek': 'Budynek',
                'grunt-zab': 'Grunt zab.',
                'grunt-niezab': 'Grunt niezab.',
                'inne': rodzaj || 'Inne',
            })[k];
        },

        // PR4 redesign: stan zwinięcia sidebara filtrów (zadokowany 380 px / 48 px).
        toggleFiltersCollapsed() {
            this.filtersCollapsed = !this.filtersCollapsed;
            try {
                localStorage.setItem(`rcn.filtersCollapsed.${this.workspaceId}`, String(this.filtersCollapsed));
            } catch {}
            // Mapa NIE ma CSS transition na `right` (PR4 fix5), więc skacze instant
            // do nowego rozmiaru. Wywołujemy invalidateSize() w kolejnej klatce żeby
            // Leaflet pobrał świeże tile dla nowych wymiarów.
            if (this.map) {
                requestAnimationFrame(() => this.map.invalidateSize());
            }
        },

        // PR4: kliknięcie "⚙ Filtruj" w drawer toolbar.
        // - Desktop (>900 px): jeśli sidebar zwinięty, rozwiń; jeśli już rozwinięty, scrolluj
        //   panel do góry (focus na pierwszym filtrze).
        // - Mobile (≤900 px): toggle filtersOpen (overlay).
        openFiltersPanel() {
            if (window.matchMedia('(max-width: 900px)').matches) {
                this.filtersOpen = !this.filtersOpen;
                return;
            }
            if (this.filtersCollapsed) {
                this.toggleFiltersCollapsed();
            } else {
                // Już otwarty -- scroll do góry sekcji filtrów
                const body = document.querySelector('.wb-sidebar-body');
                if (body) body.scrollTop = 0;
            }
        },

        // Krok 1 (request usera): button "Filtruj" w prawym rogu toolbara jako
        // prawdziwy toggle sidebara filtrów (chowa/rozwija). Mobile -- overlay
        // open/close. Desktop -- collapse 380<->48 px (toggleFiltersCollapsed).
        wbToggleFilters() {
            if (window.matchMedia('(max-width: 900px)').matches) {
                this.filtersOpen = !this.filtersOpen;
            } else {
                this.toggleFiltersCollapsed();
            }
        },

        // Data quality flags (v6, 2026-04-29) -- mapowanie kodu flagi na
        // czytelny opis dla rzeczoznawcy. Tooltip widoczny przy hover/click
        // na badge ⚠ obok ceny w tabeli. Polityka: NIE blokujemy transakcji,
        // tylko ostrzegamy żeby rzeczoznawca zweryfikował przed wyceną.
        wbQualityFlagDescriptions: {
            no_objects: 'Transakcja bez obiektów (działek/budynków/lokali) — niemożliwa wycena porównawcza',
            multi_object_act: 'Cena prawdopodobnie z aktu wielolokalowego (ten sam dokument + ta sama cena dla wielu transakcji) — operator wpisał total aktu zamiast cen per-obiekt',
            extreme_price_per_m2: 'Cena/m² poza realistycznym zakresem (lokal: <300 lub >30k zł/m²; budynek: <500 lub >50k; grunt: <5 lub >5k) — możliwy bug skali (×100/÷100) albo cena symboliczna (darowizna)',
            zero_area: 'Brak powierzchni (area_m2 = 0/NULL) przy niezerowej cenie',
            total_price_split_suspect: 'Suma cen child rows (działki/budynki/lokale) jest >2× mniejsza od cena_transakcji_brutto — prawdopodobny multi-akt z totalną ceną',
            zero_or_null_price: 'Cena = 0 zł lub brak ceny — prawdopodobnie darowizna / zniesienie współwłasności / bug operatora. Niemożliwa wycena porównawcza.',
            suspicious_date: 'Data transakcji poza realnym zakresem (przed 1990 lub po 2030) — typo operatora w dataSporzadzeniaDokumentu, np. "3007-04-05" zamiast "2007-04-05".',
        },
        wbQualityTooltip(flags) {
            if (!flags || !flags.length) return '';
            const lines = flags.map(f => '• ' + (this.wbQualityFlagDescriptions[f] || f));
            return 'Uwagi co do jakości danych GML:\n\n' + lines.join('\n\n') +
                '\n\nUżyj z ostrożnością do wyceny porównawczej.';
        },
        // Liczba transakcji z flagami w koszyku -- badge ostrzeżenia w modal koszyka.
        // Korzysta z cartItemCache (zbudowany przy wbToggleItem) który zawiera kopie
        // metadanych w tym data_quality_flags z queryResult.items. Items dodane przed
        // v6 (lub spoza tabeli) mogą nie mieć flag w cache -- traktujemy jako 0.
        wbCartFlaggedCount() {
            let n = 0;
            for (const id of this.selectedIds) {
                const cached = this.cartItemCache[id];
                if (cached && cached.data_quality_flags && cached.data_quality_flags.length > 0) {
                    n++;
                }
            }
            return n;
        },

        // Helper -- pill button do popup linków zewnętrznych (Street View /
        // Geoportal). Ikona SVG inline, label, target _blank.
        _buildExternalLinkBtn(href, kind, label) {
            const icons = {
                streetview: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>',
                geoportal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>',
            };
            return `<a href="${href}" target="_blank" rel="noopener" class="tip-link-btn tip-link-${kind}">${icons[kind] || ''}<span>${escapeHtml(label)}</span></a>`;
        },

        // === Configurable quality thresholds (Admin -> ⚙ Ustawienia jakości) ===
        qualityThresholdsOpen: false,
        qualityThresholdsLoading: false,
        qualityThresholds: {
            lokal_high: 30000, lokal_low_ratio: 100,
            budynek_high: 50000, budynek_low_ratio: 100,
            grunt_high: 5000, grunt_low_ratio: 1000,
            split_suspect_ratio: 2.0,
        },
        qualityThresholdsDefaults: null,
        // Komunikat po Auto-detect: "Sugerowane na podstawie X tx (P5/P95): grunt 1-150 zł/m²..."
        qualityThresholdsAutoMsg: '',

        async wbOpenQualityThresholds() {
            this.qualityThresholdsLoading = true;
            this.qualityThresholdsOpen = true;
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/quality-thresholds`);
                if (r.ok) {
                    const data = await r.json();
                    this.qualityThresholds = { ...this.qualityThresholds, ...data.current };
                    this.qualityThresholdsDefaults = data.defaults;
                }
            } catch (e) { /* ignore -- fallback do current state */ }
            this.qualityThresholdsLoading = false;
        },

        wbResetQualityThresholdsToDefaults() {
            if (this.qualityThresholdsDefaults) {
                this.qualityThresholds = { ...this.qualityThresholdsDefaults };
                this.qualityThresholdsAutoMsg = '';
            }
        },

        async wbAutoDetectQualityThresholds() {
            // Wylicz progi z faktycznych danych workspace (percentyle P5/P95).
            // Backend zwraca {auto: {...}, current, defaults}. Wypełniamy inputy
            // z auto + pokazujemy komunikat z liczbą próbek per typ. User może
            // edytować i zatwierdzić "Zapisz + recompute".
            this.qualityThresholdsLoading = true;
            this.qualityThresholdsAutoMsg = '';
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/quality-thresholds/auto`, { method: 'POST' });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    alert('Auto-detect błąd: ' + (err.detail || r.status));
                    return;
                }
                const data = await r.json();
                const auto = data.auto || {};
                const diag = auto._diagnostics || {};
                // Wypełnij inputy (pomiń klucze zaczynające się od '_')
                const fresh = {};
                for (const [k, v] of Object.entries(auto)) {
                    if (!k.startsWith('_')) fresh[k] = v;
                }
                this.qualityThresholds = { ...this.qualityThresholds, ...fresh };

                // Komunikat: krótki opis per typ
                const parts = [];
                for (const typ of ['lokal', 'budynek', 'grunt']) {
                    const d = diag[typ];
                    if (!d) continue;
                    if (d.skipped) {
                        parts.push(`${typ}: za mało próbek (${d.samples})`);
                    } else {
                        const floor = d.low_floor_zlm2;
                        parts.push(`${typ}: ${floor}-${d.high_rounded} zł/m² (${d.samples} tx)`);
                    }
                }
                const pl = auto._percentile_low || 5;
                const ph = auto._percentile_high || 95;
                this.qualityThresholdsAutoMsg = `📊 Auto-detect (P${pl}/P${ph}): ${parts.join(' · ')}. Edytuj wyżej jeśli potrzebujesz, potem "Zapisz + recompute".`;
            } catch (e) {
                alert('Auto-detect błąd: ' + e);
            }
            this.qualityThresholdsLoading = false;
        },

        async wbSaveQualityThresholds() {
            this.qualityThresholdsLoading = true;
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/quality-thresholds`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.qualityThresholds),
                });
                if (!r.ok) {
                    alert('Błąd zapisu: ' + r.status);
                    return;
                }
                const data = await r.json();
                const stats = data.stats || {};
                const total = Object.values(stats).reduce((s, v) => s + v, 0);
                const flagged = total - (stats.no_flags || 0);
                alert(`Zapisane. Recompute: ${total} transakcji, ${flagged} z flagami (${(flagged*100/total).toFixed(0)}%).`);
                this.qualityThresholdsOpen = false;
                // Odśwież tabelę żeby zobaczyć nowe flagi.
                this.runQuery();
            } catch (e) {
                alert('Błąd: ' + e);
            }
            this.qualityThresholdsLoading = false;
        },

        // Limit koszyka 100 dla użytkownika testowego (readonly). Admin bez limitu.
        // Walidator do wywołania PRZED dodaniem do selectedIds. Pokazuje toast
        // i blokuje akcję gdy przekroczyłby limit.
        get cartLimit() { return this.isAdmin ? Infinity : 100; },
        _canAddToCart(addCount = 1) {
            if (this.isAdmin) return true;
            const after = this.selectedIds.size + addCount;
            if (after > this.cartLimit) {
                const remaining = Math.max(0, this.cartLimit - this.selectedIds.size);
                this.wbShowToast(
                    `Limit koszyka dla użytkownika testowego: ${this.cartLimit} transakcji.\n` +
                    `W koszyku: ${this.selectedIds.size} • próba dodania: ${addCount} • dostępne: ${remaining}.\n` +
                    `Admin może dodać więcej.`,
                    'warn'
                );
                return false;
            }
            return true;
        },
        wbShowToast(msg, kind = 'info') {
            // Prosty toast -- alert() jest blokujący ale wystarczająco wyraźny dla
            // sytuacji "tej akcji nie da się wykonać". Lepszy custom toast TODO.
            alert(msg);
        },

        // Sąsiedzi w X m -- fetch do /neighbors + filter. Klikalne z popupów
        // markerów (główna mapa + modal mapy). Reset poprzedniego sąsiedztwa
        // przed zastosowaniem nowego.
        async wbShowNeighbors(item, radius) {
            if (!item || item.centroid_lat == null || item.centroid_lon == null) {
                alert('Ta transakcja nie ma centroidu na mapie -- nie można wyznaczyć sąsiadów.');
                return;
            }
            // Reset focusIdRcn (kolizja: focus = jedna; neighbors = wiele).
            this.focusIdRcn = null;
            const r = await fetch(
                `/api/workspaces/${this.workspaceId}/transactions/neighbors/${encodeURIComponent(item.id_rcn)}?radius_m=${radius}`
            );
            if (!r.ok) {
                alert('Błąd pobierania sąsiadów: ' + r.status);
                return;
            }
            const data = await r.json();
            const ids = new Set([item.id_rcn, ...(data.neighbor_ids || [])]);
            this.neighborhoodIds = ids;
            this.neighborhoodFocus = {
                id_rcn: item.id_rcn,
                radius_m: data.radius_m,
                label: item.adres || item.id_rcn.slice(0, 12),
                count: (data.neighbor_ids || []).length,
            };
            this.page = 1;
            await this.runQuery({ fitBounds: false });
        },

        wbClearNeighborhood() {
            this.neighborhoodIds = null;
            this.neighborhoodFocus = null;
            this.page = 1;
            this.runQuery();
        },

        // HTML block "Sąsiedzi w: [50] [100] [250] [500] m" w popupach markerów.
        // Wspólny dla głównej mapy i modala. Buttony mają data-action="neighbors"
        // + data-radius -- handler popupopen wire-uje onclick.
        _buildNeighborsBlock() {
            const radii = [50, 100, 250, 500];
            const buttons = radii.map(r =>
                `<button type="button" class="tip-neighbor-btn" data-action="neighbors" data-radius="${r}">${r}m</button>`
            ).join('');
            return `<div class="tip-neighbors">
                <span class="tip-neighbors-label">Sąsiedzi w:</span>
                ${buttons}
            </div>`;
        },

        _wireNeighborsActions(node, item) {
            const btns = node.querySelectorAll('[data-action="neighbors"]');
            btns.forEach(btn => {
                btn.onclick = (e) => {
                    e.preventDefault(); e.stopPropagation();
                    const r = parseInt(btn.getAttribute('data-radius'), 10);
                    this.wbShowNeighbors(item, r);
                };
            });
        },

        // PR4: liczba aktywnych filtrów -- wyświetlana jako badge w headerze sidebara
        // i jako mała kropka gdy sidebar zwinięty (48 px).
        activeFilterCount() {
            let n = 0;
            const f = this.filters;
            if (f.rodzaj_rynku?.length) n++;
            if (f.rodzaj_transakcji?.length) n++;
            if (f.rodzaj_nieruchomosci?.length) n++;
            if (f.obreb?.length) n++;
            if (f.obreb_key?.length) n++;
            if (f.miejscowosc?.length) n++;
            if (f.teryt_gminy?.length) n++;
            if (f.adres?.trim()) n++;
            if (f.cena_min != null && f.cena_min !== '') n++;
            if (f.cena_max != null && f.cena_max !== '') n++;
            if (f.cena_m2_min != null && f.cena_m2_min !== '') n++;
            if (f.cena_m2_max != null && f.cena_m2_max !== '') n++;
            if (f.data_od) n++;
            if (f.data_do) n++;
            if (f.has_note_choice) n++;
            if (f.notes_search?.trim()) n++;
            if (f.include_withdrawn) n++;
            if (this.spatial?.active) n++;
            return n;
        },

        goPage(n) {
            n = Math.max(1, n);
            const max = Math.max(1, Math.ceil(this.queryResult.total / this.pageSize));
            this.page = Math.min(max, n);
            this.runQuery();
        },

        resetFilters() {
            this.filters = {
                rodzaj_rynku: [],
                rodzaj_transakcji: [],
                rodzaj_nieruchomosci: [],
                miejscowosc: [],
                teryt_gminy: [],
                obreb: [],
                obreb_key: [],
                obreb_search: null,
                adres: '',
                cena_min: null, cena_max: null,
                cena_m2_min: null, cena_m2_max: null,
                area_min: null, area_max: null,
                plot_ident_search: null,
                data_od: null, data_do: null,
                has_note_choice: '', notes_search: '',
                plot_ident: null, building_ident: null, local_ident: null,
                include_withdrawn: false,
                only_verified: false,
            };
            this.colFilterObreb = '';
            this.wikiLabel = null;
            this.page = 1;
            this.focusIdRcn = null;
            this.clearSpatial();
            this.runQuery();
        },

        async openNote(idRcn) {
            this.note.open = true;
            this.note.id_rcn = idRcn;
            this.note.body = '';
            this.note.loading = true;
            this.note.isSeed = false;
            this.note.updated_at = null;
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/transactions/note/${encodeURIComponent(idRcn)}`);
                if (!r.ok) { alert('Nie udało się wczytać notatki: ' + r.status); this.note.open = false; return; }
                const data = await r.json();
                this.note.body = data.body || '';
                this.note.isSeed = data.is_seed || false;
                this.note.updated_at = data.updated_at || null;
            } finally {
                this.note.loading = false;
            }
        },

        closeNote() {
            this.note.open = false;
            this.note.id_rcn = null;
            this.note.body = '';
        },

        async saveNote() {
            if (!this.note.id_rcn) return;
            this.note.loading = true;
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/transactions/note/${encodeURIComponent(this.note.id_rcn)}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ body: this.note.body }),
                });
                if (!r.ok) {
                    this.showToast('error', 'Nie udało się zapisać notatki', `HTTP ${r.status}`);
                    return;
                }
                const data = await r.json();
                this.note.isSeed = false;
                this.note.updated_at = data.updated_at;
                const item = this.queryResult.items.find(it => it.id_rcn === this.note.id_rcn);
                if (item) item.has_note = true;
                this.wbNoteInvalidateCache(this.note.id_rcn);
                // PR9: toast success z adresem (jeśli znany)
                const addr = item?.adres || this.cartItemCache[this.note.id_rcn]?.adres || '';
                this.showToast('success', 'Notatka zapisana', addr);
            } finally {
                this.note.loading = false;
            }
        },

        async deleteNote() {
            if (!this.note.id_rcn) return;
            if (!confirm('Usunąć notatkę dla tej transakcji?')) return;
            const idRcn = this.note.id_rcn;
            this.note.loading = true;
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/transactions/note/${encodeURIComponent(idRcn)}`, { method: 'DELETE' });
                if (!r.ok) {
                    this.showToast('error', 'Nie udało się usunąć notatki', `HTTP ${r.status}`);
                    return;
                }
                const item = this.queryResult.items.find(it => it.id_rcn === idRcn);
                if (item) item.has_note = false;
                this.wbNoteInvalidateCache(idRcn);
                this.closeNote();
                // PR9: toast info po usunieciu notatki
                this.showToast('info', 'Notatka usunięta', '');
            } finally {
                this.note.loading = false;
            }
        },

        renderMarkdown(text) {
            // PR7: marked.parse + DOMPurify.sanitize -- bezpieczny rendering
            // Markdown z notatek do x-html. Zostaja tylko bezpieczne tagi/atrybuty,
            // wycinane sa <script>, on*= handlers, javascript: URLs etc.
            // Notatki sa user-input -- single-user dziś, ale gdyby kiedykolwiek
            // multi-user / shared workspace, ta sanityzacja jest wymagana.
            if (!text) return '';
            if (typeof marked === 'undefined') return escapeHtml(text);
            try {
                const raw = marked.parse(text, { breaks: true, gfm: true });
                if (typeof DOMPurify !== 'undefined') {
                    return DOMPurify.sanitize(raw);
                }
                return raw;
            } catch (e) {
                return escapeHtml(text);
            }
        },

        formatDate(ts) {
            if (!ts) return '';
            return new Date(ts * 1000).toLocaleString('pl-PL');
        },

        clearSpatial() {
            this.spatial = { active: false, kind: null, bbox: null, polygon: null };
            if (this.drawnLayer) this.drawnLayer.clearLayers();
            this.page = 1;
            this.runQuery();
        },

        async doUpload() {
            if (!this.file) return;
            this.uploading = true;
            this.uploadStatus = 'Przesyłanie pliku…';
            this.uploadProgress = { active: true, stage: 'upload', pct: 0, importId: null, finalMsg: '' };
            const fd = new FormData();
            fd.append('file', this.file);
            fd.append('tryb', this.uploadTryb);
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/upload`, {
                    method: 'POST', body: fd,
                });
                const body = await r.json();
                if (!r.ok) {
                    this.uploadStatus = `Błąd: ${body.detail || r.status}`;
                    this.uploadProgress.active = false;
                    return;
                }
                const importy = (body.import_ids && body.import_ids.length)
                    ? body.import_ids : [body.import_id];
                this.uploadProgress.importId = importy[0];
                this.uploadStatus = body.zrodlo_archiwum
                    ? `Paczka rozpakowana — ${importy.length} plik(ów) w kolejce`
                    : `Plik przesłany — parsing w tle (import #${importy[0]})`;
                // Serwer mógł zmienić tryb importu (paczka z kilkoma plikami nie
                // może iść snapshotem) — operator musi to zobaczyć.
                if (body.uwaga) this.uploadStatus += ` — ${body.uwaga}`;
                await this.loadImports();
                // Sekwencyjnie: zadania w tle też idą jedno po drugim, więc
                // pasek pokazuje ten plik, który faktycznie jest przetwarzany.
                for (let i = 0; i < importy.length; i++) {
                    this.uploadProgress.importId = importy[i];
                    if (importy.length > 1) {
                        this.uploadStatus = `Plik ${i + 1} z ${importy.length} (import #${importy[i]})`;
                    }
                    await this.pollImportStatus(importy[i]);
                }
            } catch (e) {
                this.uploadStatus = `Błąd sieci: ${e.message || e}`;
                this.uploadProgress.active = false;
            } finally {
                this.uploading = false;
            }
        },

        async pollImportStatus(importId) {
            const url = `/api/workspaces/${this.workspaceId}/imports/${importId}`;
            const start = Date.now();
            const maxWaitMs = 60 * 60 * 1000;  // 1h safety cap
            while (Date.now() - start < maxWaitMs) {
                await new Promise(resolve => setTimeout(resolve, 1500));
                let info;
                try {
                    const r = await fetch(url);
                    if (!r.ok) continue;
                    info = await r.json();
                } catch (e) {
                    continue;
                }
                this.uploadProgress.stage = info.stage || info.status;
                this.uploadProgress.pct = info.progress_pct || 0;
                if (info.status === 'success') {
                    this.uploadProgress.pct = 100;
                    this.uploadProgress.finalMsg =
                        `OK — ${info.transaction_count} transakcji` +
                        ` · wstawione ${info.inserted_count}` +
                        ` · zaktualizowane ${info.updated_count}` +
                        ` · pominięte ${info.skipped_count}` +
                        ` · ${info.duration_s}s · EPSG ${info.parser_epsg || '?'}`;
                    this.uploadStatus = this.uploadProgress.finalMsg;
                    this.file = null;
                    await this.loadInfo();
                    await this.loadLookups();
                    await this.loadImports();
                    await this.runQuery();
                    this.uploadProgress.active = false;
                    return;
                }
                if (info.status === 'failed') {
                    this.uploadProgress.finalMsg = `Błąd: ${info.error_msg || 'nieznany'}`;
                    this.uploadStatus = this.uploadProgress.finalMsg;
                    await this.loadImports();
                    this.uploadProgress.active = false;
                    return;
                }
            }
            this.uploadStatus = 'Timeout oczekiwania na zakończenie (>1h). Sprawdź listę importów.';
            this.uploadProgress.active = false;
        },

        async removeImport(imp) {
            if (!confirm(`Usunąć import "${imp.original_filename}" (#${imp.id})? Usuwa wszystkie rekordy z tego pliku.`)) return;
            const r = await fetch(`/api/workspaces/${this.workspaceId}/imports/${imp.id}`, { method: 'DELETE' });
            if (r.ok) {
                await this.loadInfo();
                await this.loadImports();
                await this.runQuery();
            } else {
                alert('Nie udało się usunąć importu.');
            }
        },

        async doExport(fmt) {
            this.exporting = true;
            try {
                // PR6 fix: nie ma juz cartPersistNotes() bo notatki zarzadza modal
                // "note" osobno (Wariant A). Tutaj tylko export_comment z koszyka.
                const filters = this.buildFiltersPayload();
                // Eksport dotyczy wyłącznie zaznaczonych wierszy (koszyka), o ile są.
                if (this.selectedIds.size > 0) {
                    filters.id_rcn_in = Array.from(this.selectedIds);
                }
                const body = { filters };
                // Komentarz "do tego eksportu" (XLSX i CSV go ignoruje, tylko XLSX
                // pokazuje w arkuszu Podsumowanie) -- przekaz tylko gdy niepusty.
                const comment = (this.cartExportComment || '').trim();
                if (comment) body.export_comment = comment;
                const r = await fetch(`/api/workspaces/${this.workspaceId}/export.${fmt}`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body),
                });
                if (!r.ok) {
                    let detail = `HTTP ${r.status}`;
                    try { detail = (await r.json()).detail || detail; } catch {}
                    this.showToast('error', `Eksport ${fmt.toUpperCase()} nie powiódł się`, detail);
                    return;
                }
                const blob = await r.blob();
                const base = (this.info.name || 'rcn-export')
                    .replace(/[^a-zA-Z0-9_.-]/g, '-').slice(0, 60);
                const filename = [base, new Date().toISOString().slice(0,10)]
                    .join('_') + '.' + fmt;
                const n = this.selectedIds.size;
                const txWord = n === 1 ? 'transakcja' : (n > 1 && n < 5 ? 'transakcje' : 'transakcji');

                // Tryb desktopowy (pywebview/WebView2): pobranie bloba bywa po cichu
                // gubione przez WebView2, więc zapisujemy plik natywnie mostem
                // JS->Python (desktop.py: Api.save_export + create_file_dialog).
                // W przeglądarce: klasyczny blob-download.
                if (window.pywebview && window.pywebview.api && window.pywebview.api.save_export) {
                    const b64 = await blobToBase64(blob);
                    const res = await window.pywebview.api.save_export(filename, b64);
                    if (res && res.ok) {
                        this.showToast('success', `Zapisano ${fmt.toUpperCase()}`, `${n} ${txWord} · ${res.path}`);
                    } else if (res && res.cancelled) {
                        this.showToast('info', 'Eksport anulowany', 'Nie wybrano lokalizacji zapisu.');
                    } else {
                        this.showToast('error', `Eksport ${fmt.toUpperCase()} nie powiódł się`,
                            (res && res.error) || 'Błąd zapisu pliku.');
                    }
                    return;
                }

                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
                // PR9: toast success z liczba transakcji + nazwa pliku
                this.showToast(
                    'success',
                    `Wyeksportowano ${fmt.toUpperCase()}`,
                    `${n} ${txWord} · ${filename}`
                );
            } finally {
                this.exporting = false;
            }
        },

        formatMoney(v) { return formatMoney(v); },

        shortParcelNumber(fullIdent) {
            if (!fullIdent) return '';
            return fullIdent.includes('.') ? fullIdent.split('.').pop() : fullIdent;
        },

        formatParcelNumbers(idents) {
            if (!idents || idents.length === 0) return '—';
            const first = this.shortParcelNumber(idents[0]);
            if (idents.length === 1) return first;
            return `${first} +${idents.length - 1}`;
        },

        formatParcelNumbersTitle(idents) {
            if (!idents || idents.length === 0) return '';
            return idents.join(', ');
        },

        // ================================================================
        // Variant B (map-first) — metody dostępne tylko w ?layout=b
        // ================================================================

        // PR6 fix: usunieto wbEnsureCartEntry (nie potrzebne bez cartNotes).
        // Helper wbCartHasNote(id) -- sprawdza w cache czy transakcja ma notatke.
        // Zwraca true/false/null (null = nie wiemy, item poza queryResult/cache).
        wbCartHasNote(idRcn) {
            // 1. Cache notatki (po openNote/popoverze) -- pewne info
            const cached = this.noteContentCache && this.noteContentCache[idRcn];
            if (cached) return !cached.isSeed && !!cached.body;
            // 2. Aktualna strona wynikow ma flage has_note
            const item = (this.queryResult.items || []).find(it => it.id_rcn === idRcn);
            if (item) return !!item.has_note;
            // 3. Item poza strona/cache -- nie wiemy
            return null;
        },

        wbToggleItem(item) {
            const id = item.id_rcn;
            if (this.selectedIds.has(id)) {
                this.selectedIds.delete(id);
                delete this.cartItemCache[id];
            } else {
                if (!this._canAddToCart(1)) return;
                this.selectedIds.add(id);
                // PR8 fix4: zachowaj snapshot itemu w cache (adres/cena/typ/etc).
                // Dzieki temu w koszyku item pokazuje sie z metadanymi nawet
                // gdy nie ma go w queryResult.items (paginacja, popup markera).
                this.cartItemCache[id] = {
                    id_rcn: id,
                    adres: item.adres,
                    miejscowosc: item.miejscowosc,
                    obreb: item.obreb,
                    rodzaj_nieruchomosci: item.rodzaj_nieruchomosci,
                    cena_transakcji_brutto: item.cena_transakcji_brutto,
                    cena_na_m2: item.cena_na_m2,
                    area_m2: item.area_m2,
                    data_transakcji: item.data_transakcji,
                    has_note: item.has_note,
                    data_quality_flags: item.data_quality_flags || [],
                };
                // Nie otwieramy koszyka automatycznie (toggle w topbarze ma
                // licznik i wyróżnienie .has-items -- to wystarcza jako
                // feedback). Auto-open byłby wymagał liczenia pozycji z
                // querySelector i przeszkadzał przy checkboxie w tabeli.
            }
            this.selectedIds = new Set(this.selectedIds);
            this._persistCart();  // Faza 4 commit 6: zapis do localStorage
            // NIE wywołujemy renderMarkers() -- kolor markerów nie zależy od
            // selekcji, więc re-render jest bezkosztowo bezcelowy. 5000 markerów
            // usuniętych i stworzonych od nowa = wstrząs mapy przy każdym klik.
        },

        wbRowClick(item) {
            // Klik wiersza rozwija/zwija szczegóły transakcji (działki, budynki, lokale)
            // z lazy-loadem (endpoint /transactions/{id}/details). Drugi klik = zwija,
            // kolejne otwarcia korzystają z cache (0 requestów).
            // Focus na mapie realizowany osobnym przyciskiem 🎯 w pierwszej kolumnie.
            this.selected = item.id_rcn;
            this.toggleExpand(item.id_rcn);
        },

        wbFocusOnMap(item) {
            // Przycisk celu w pierwszej kolumnie: zoom+pan do centroidu transakcji
            // PR8: dodatkowo aureola amber (pulsujace kolo) przez 2s po setView,
            // zeby user wiedzial gdzie patrzec (przy 10k pinkow latwo zgubic).
            this.selected = item.id_rcn;
            if (item.centroid_lat == null || item.centroid_lon == null) return;
            const targetZoom = Math.max(this.map.getZoom(), 18);
            this.map.setView(
                [item.centroid_lat, item.centroid_lon],
                targetZoom,
                { animate: true },
            );
            // Aureola amber po zakonczeniu zoom-animation
            setTimeout(() => {
                this._showFocusHalo(item.centroid_lat, item.centroid_lon);
            }, 350);
        },

        // PR8: pulsujaca aureola amber wokol fokusowanego punktu na mapie.
        _showFocusHalo(lat, lon) {
            if (!this.map) return;
            // Usun poprzedni jesli jest
            if (this._focusHalo) {
                try { this.map.removeLayer(this._focusHalo); } catch {}
                this._focusHalo = null;
            }
            const icon = L.divIcon({
                className: 'wb-focus-halo',
                html: '<div class="wb-focus-halo-ring"></div>',
                iconSize: [60, 60],
                iconAnchor: [30, 30],
            });
            this._focusHalo = L.marker([lat, lon], {
                icon: icon,
                interactive: false,
                keyboard: false,
                zIndexOffset: 1000,
            }).addTo(this.map);
            // Auto-remove po 2s (animacja CSS pulses 2x)
            setTimeout(() => {
                if (this._focusHalo) {
                    try { this.map.removeLayer(this._focusHalo); } catch {}
                    this._focusHalo = null;
                }
            }, 2000);
        },

        async wbShowInTable(item) {
            // Z popupa markera -- pokaż wiersz w tabeli. Jeśli jest już na
            // aktualnej stronie, tylko scroll + expand + pulse. Jeśli nie
            // (inna strona paginacji albo poza aktualnym filtrem), ustaw
            // focusIdRcn (tymczasowy filtr id_rcn_in=[id]) i przeładuj
            // tabelę -- user zobaczy jeden wiersz i chip "Pokazany" do zdjęcia.
            this.selected = item.id_rcn;
            if (this.drawerState === 'collapsed') this.wbSetDrawer('default');
            const inCurrentPage = this.queryResult.items?.some(it => it.id_rcn === item.id_rcn);
            if (!inCurrentPage) {
                this.focusIdRcn = item.id_rcn;
                this.page = 1;
                // skipGeojson -- mapa ma zostać tam gdzie użytkownik ją
                // ustawił (po 🎯 lub kliku markera). Bez tego loadGeoJson
                // + renderMarkers(true) zrobiłby fitBounds i "oddalił".
                await this.runQuery({ skipGeojson: true });
            }
            if (!this.expandedIds.has(item.id_rcn)) {
                await this.toggleExpand(item.id_rcn);
            }
            await this.$nextTick();
            const row = document.querySelector(`tr[data-id-rcn="${CSS.escape(item.id_rcn)}"]`);
            if (!row) return;
            try {
                row.scrollIntoView({ block: 'center', behavior: 'smooth' });
            } catch (e) { /* older browsers */ }
            row.classList.remove('pulse');
            void row.offsetWidth;
            row.classList.add('pulse');
            setTimeout(() => row.classList.remove('pulse'), 1800);
        },

        wbRemoveFromCart(idRcn) {
            this.selectedIds.delete(idRcn);
            delete this.cartItemCache[idRcn];
            this.selectedIds = new Set(this.selectedIds);
            this._persistCart();  // Faza 4 commit 6
        },

        // ---------- Notatka jako hover-popover ----------
        wbNoteShow(item, ev) {
            clearTimeout(this._noteHideTimer);
            const rect = ev.currentTarget.getBoundingClientRect();
            this._noteShowTimer = setTimeout(async () => {
                this.notePopover.id_rcn = item.id_rcn;
                this.notePopover.has_note = !!item.has_note;
                this.notePopover.body = '';
                this.notePopover.is_seed = false;

                // Inteligentne pozycjonowanie: jeśli pod wskaźnikiem nie ma
                // sensownej ilości miejsca (drawer/dolny pasek zasłania), otwieramy
                // nad wskaźnikiem. max-height dynamicznie liczony z dostępnej
                // przestrzeni, żeby popover zawsze mieścił się w widoku.
                const popoverWidth = 300;
                const margin = 10;
                const absCap = 400;            // górna granica wysokości
                const minUseful = 160;         // minimalna akceptowalna wysokość poniżej
                const spaceBelow = window.innerHeight - rect.bottom - margin;
                const spaceAbove = rect.top - margin;
                let top, maxH, placement;
                if (spaceBelow >= minUseful || spaceBelow >= spaceAbove) {
                    placement = 'below';
                    maxH = Math.min(absCap, spaceBelow - 6);
                    top = rect.bottom + 6;
                } else {
                    placement = 'above';
                    maxH = Math.min(absCap, spaceAbove - 6);
                    top = Math.max(margin, rect.top - 6 - maxH);
                }
                this.notePopover.placement = placement;
                this.notePopover.maxHeight = Math.max(100, maxH);
                this.notePopover.top = top;
                this.notePopover.left = Math.max(
                    margin,
                    Math.min(window.innerWidth - popoverWidth - margin, rect.left)
                );
                this.notePopover.open = true;
                // Zawsze pokazujemy body: albo realna notatka usera, albo seed
                // (szablon z danych transakcji wygenerowany przez backend).
                if (this.noteContentCache[item.id_rcn] !== undefined) {
                    const c = this.noteContentCache[item.id_rcn];
                    this.notePopover.body = c.body;
                    this.notePopover.is_seed = c.isSeed;
                    this.notePopover.loading = false;
                } else {
                    this.notePopover.loading = true;
                    await this.wbLoadNoteContent(item.id_rcn);
                    // Tylko aktualizuj popover jeśli user nadal hoveruje tę samą transakcję
                    if (this.notePopover.id_rcn === item.id_rcn) {
                        const c = this.noteContentCache[item.id_rcn] || { body: '', isSeed: true };
                        this.notePopover.body = c.body;
                        this.notePopover.is_seed = c.isSeed;
                        this.notePopover.loading = false;
                    }
                }
            }, 300);
        },

        wbNoteHide() {
            clearTimeout(this._noteShowTimer);
            this._noteHideTimer = setTimeout(() => {
                this.notePopover.open = false;
            }, 220);
        },

        wbNoteKeep() {
            clearTimeout(this._noteHideTimer);
        },

        async wbLoadNoteContent(idRcn) {
            try {
                const r = await fetch(`/api/workspaces/${this.workspaceId}/transactions/note/${encodeURIComponent(idRcn)}`);
                if (!r.ok) { this.noteContentCache[idRcn] = { body: '', isSeed: true }; return; }
                const data = await r.json();
                this.noteContentCache[idRcn] = {
                    body: data.body || '',
                    isSeed: !!data.is_seed,
                };
            } catch (e) {
                this.noteContentCache[idRcn] = { body: '', isSeed: true };
            }
        },

        wbNoteInvalidateCache(idRcn) {
            // Wywoływane po zapisaniu/usunięciu notatki, żeby hover pobrał aktualną wersję.
            delete this.noteContentCache[idRcn];
        },

        // PR8 fix4: helper -- najpierw cache (item dodany z popupu/innej strony),
        // potem queryResult.items (item na aktualnej stronie). Dzieki temu
        // koszyk pokazuje pelne dane niezaleznie od stanu paginacji.
        _wbCartLookup(idRcn) {
            return this.cartItemCache[idRcn]
                || (this.queryResult.items || []).find(it => it.id_rcn === idRcn);
        },

        wbCartAddr(idRcn) {
            const item = this._wbCartLookup(idRcn);
            if (item) {
                return item.adres
                    || [item.miejscowosc, item.obreb].filter(Boolean).join(' · ')
                    || idRcn.slice(0, 8) + '…';
            }
            return idRcn.slice(0, 8) + '…';
        },

        wbCartMeta(idRcn) {
            // PR6 redesign: meta NIE zawiera ceny (jest w osobnej kolumnie .wb-cart-price).
            // Pokazuje typ + data + powierzchnia.
            const item = this._wbCartLookup(idRcn);
            if (!item) return '(brak danych)';
            const parts = [];
            if (item.data_transakcji) parts.push(item.data_transakcji);
            if (item.rodzaj_nieruchomosci) parts.push(this.typeLabel(item.rodzaj_nieruchomosci));
            if (item.area_m2 != null && item.area_m2 > 0) {
                parts.push(`${Math.round(item.area_m2).toLocaleString('pl-PL')} m²`);
            }
            return parts.join(' · ');
        },

        // PR6: cena per item w koszyku -- osobna kolumna w siatce 3-kolumnowej.
        wbCartPrice(idRcn) {
            const item = this._wbCartLookup(idRcn);
            if (!item || item.cena_transakcji_brutto == null) return '—';
            return Math.round(item.cena_transakcji_brutto).toLocaleString('pl-PL') + ' zł';
        },
        wbCartPriceM2(idRcn) {
            const item = this._wbCartLookup(idRcn);
            if (!item || item.cena_na_m2 == null || item.area_m2 === 0) return '';
            return Math.round(item.cena_na_m2).toLocaleString('pl-PL') + ' zł/m²';
        },

        // PR6 fix: usunieto cartPersistNotes() -- bylo do flushowania ad-hoc cart
        // notatek przed eksportem (Wariant B). W Wariancie A notatki sa juz
        // w DB (zapisane przez modal "note" lub popover edycji).

        wbSetDrawer(state) {
            this.drawerState = state;
            this.drawerHeight = null;
            if (this.map) this.$nextTick(() => setTimeout(() => this.map.invalidateSize(), 220));
        },

        wbDrawerClass() {
            if (this.drawerHeight != null) return '';
            if (this.drawerState === 'collapsed') return 'collapsed';
            if (this.drawerState === 'expanded') return 'expanded';
            return '';
        },

        wbDrawerStyle() {
            // Gdy user ustawił custom wysokość (przez drag), wyłączamy CSS
            // transition -- inaczej setki mousemove'ów walczy z animacją 180ms
            // i wygląda to jak "kiwanie" mapy.
            if (this.drawerHeight == null) return '';
            return `height: ${this.drawerHeight}px; transition: none;`;
        },

        wbDragStart(ev) {
            this._drawerDrag = { startY: ev.clientY, startH: null };
            const drawer = ev.currentTarget.parentElement;
            this._drawerDrag.startH = drawer ? drawer.offsetHeight : 0;
            ev.preventDefault();
            document.body.style.userSelect = 'none';
        },

        wbBindDrawerDrag() {
            const onMove = (e) => {
                if (!this._drawerDrag) return;
                const delta = this._drawerDrag.startY - e.clientY;
                const h = Math.max(60, Math.min(window.innerHeight - 140, this._drawerDrag.startH + delta));
                this.drawerHeight = h;
                this.drawerState = 'custom';
            };
            const onUp = () => {
                if (!this._drawerDrag) return;
                this._drawerDrag = null;
                document.body.style.userSelect = '';
                if (this.map) this.map.invalidateSize();
            };
            window.addEventListener('mousemove', onMove);
            window.addEventListener('mouseup', onUp);
        },

        wbActiveChips() {
            const chips = [];
            const f = this.filters;
            const push = (key, label, value, kind) => chips.push({ key, label, value, kind: kind || 'attr' });
            if (f.rodzaj_rynku?.length) push('rodzaj_rynku', 'Rynek', f.rodzaj_rynku.join(', '));
            if (f.rodzaj_transakcji?.length) push('rodzaj_transakcji', 'Transakcja', f.rodzaj_transakcji.join(', '));
            if (f.rodzaj_nieruchomosci?.length) push('rodzaj_nieruchomosci', 'Typ', f.rodzaj_nieruchomosci.join(', '));
            if (f.obreb?.length) {
                const v = f.obreb.slice(0, 2).join(', ') + (f.obreb.length > 2 ? ` +${f.obreb.length - 2}` : '');
                push('obreb', 'Obręb', v);
            }
            if (f.obreb_key?.length) {
                // Na chipie samo oznaczenie -- klucz z jednostką ewidencyjną
                // jest wartością techniczną, nie treścią dla użytkownika.
                const etykiety = f.obreb_key.map(k => {
                    const opcja = (this.lookups.obreby || []).find(o => o.key === k);
                    return opcja ? opcja.label : k.split('|').pop();
                });
                const v = etykiety.slice(0, 2).join(', ') + (etykiety.length > 2 ? ` +${etykiety.length - 2}` : '');
                push('obreb_key', 'Obręb', v);
            }
            if (f.miejscowosc?.length) push('miejscowosc', 'Miejscowość', f.miejscowosc.join(', '));
            if (f.teryt_gminy?.length) push('teryt_gminy', 'TERYT', f.teryt_gminy.join(', '));
            if (f.adres && f.adres.trim()) push('adres', 'Adres', `"${f.adres.trim()}"`);
            if (f.cena_min != null || f.cena_max != null) {
                push('cena', 'Cena', `${f.cena_min ?? '0'}–${f.cena_max ?? '∞'}`);
            }
            if (f.cena_m2_min != null || f.cena_m2_max != null) {
                push('cena_m2', 'Cena/m²', `${f.cena_m2_min ?? '0'}–${f.cena_m2_max ?? '∞'}`);
            }
            if (f.data_od || f.data_do) push('data', 'Data', `${f.data_od || '…'} → ${f.data_do || '…'}`);
            if (f.has_note_choice === 'true') push('has_note', 'Notatka', 'tylko z');
            else if (f.has_note_choice === 'false') push('has_note', 'Notatka', 'tylko bez');
            if (this.spatial?.active) {
                push('spatial', this.spatial.kind === 'bbox' ? 'BBOX' : 'Polygon', 'aktywny', 'spatial');
            }
            if (this.focusIdRcn) {
                push('focus', 'Pokazany', this.focusIdRcn.slice(0, 8) + '…', 'spatial');
            }
            if (this.neighborhoodFocus) {
                const f = this.neighborhoodFocus;
                push('neighborhood', 'Sąsiedzi', `${f.radius_m} m od ${(f.label || '').slice(0, 30)}`, 'spatial');
            }
            if (f.plot_ident || f.building_ident || f.local_ident) {
                const lbl = this.wikiLabel || (f.plot_ident ? `Działka ${f.plot_ident}`
                    : f.building_ident ? `Budynek ${f.building_ident}`
                    : `Lokal ${f.local_ident}`);
                push('wiki', '🔗 Obiekt', lbl, 'spatial');
            }
            if (f.include_withdrawn) push('include_withdrawn', 'Wycofane', 'widoczne');
            return chips;
        },

        wbRemoveChip(key) {
            const f = this.filters;
            switch (key) {
                case 'cena': f.cena_min = null; f.cena_max = null; break;
                case 'cena_m2': f.cena_m2_min = null; f.cena_m2_max = null; break;
                case 'data': f.data_od = null; f.data_do = null; break;
                case 'has_note': f.has_note_choice = ''; break;
                case 'spatial': this.clearSpatial(); return;
                case 'focus': this.focusIdRcn = null; break;
                case 'neighborhood':
                    this.neighborhoodIds = null;
                    this.neighborhoodFocus = null;
                    break;
                case 'wiki': this.wbClearWikiFilter(); return;
                case 'include_withdrawn': f.include_withdrawn = false; break;
                default:
                    if (Array.isArray(f[key])) f[key] = [];
                    else f[key] = null;
            }
            this.page = 1;
            this.runQuery();
        },
    };
}

function formatMoney(v) {
    if (v == null) return '';
    try {
        return new Intl.NumberFormat('pl-PL', { style: 'currency', currency: 'PLN', maximumFractionDigits: 0 }).format(v);
    } catch (e) {
        return String(v) + ' zł';
    }
}

function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
        .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// Blob -> czysty base64 (bez prefiksu data:). Używane przy zapisie eksportu
// mostem JS->Python w trybie desktopowym (pywebview). FileReader radzi sobie
// z dużymi plikami bez przepełnienia stosu (inaczej niż btoa na long string).
function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => {
            const s = String(reader.result || '');
            resolve(s.slice(s.indexOf(',') + 1));
        };
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(blob);
    });
}
