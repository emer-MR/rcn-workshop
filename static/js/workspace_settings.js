/* Settings page per workspace -- Alpine.js component.
   Endpoints:
     GET  /api/me
     GET  /api/workspaces/{id}
     PATCH /api/workspaces/{id}
     GET  /api/workspaces/{id}/custom-layers
     POST /api/workspaces/{id}/layers/add
     DELETE /api/workspaces/{id}/layers/{slug}
     GET  /api/workspaces/{id}/imports
     DELETE /api/workspaces/{id}/imports/{importId}
     GET  /api/workspaces/{id}/enhancements/status
     POST /api/workspaces/{id}/enhancements/{op}
     DELETE /api/workspaces/{id}
*/
function workspaceSettings() {
    const wsId = window.__WORKSPACE_ID__;
    const wsName = window.__WORKSPACE_NAME__;

    return {
        loading: true,
        isAdmin: false,
        info: { name: wsName, slug: '', transaction_count: 0 },
        meta: { name: '', slug: '', notes: '' },
        _metaOriginal: { name: '', slug: '', notes: '' },
        metaSaving: false,
        get metaDirty() {
            return JSON.stringify(this.meta) !== JSON.stringify(this._metaOriginal);
        },

        customLayers: [],
        newLayer: { name: '', file: null, fileName: '', uploading: false },

        // Upload GML state -- file + tryb (snapshot|delta) + uploading flag.
        // Po POST nowy import startuje subprocess, busy=true → lock overlay
        // w workspace.html. Settings page sam nie blokuje, ale tracker
        // imports się odświeża żeby user widział "processing → success".
        newGml: { file: null, fileName: '', tryb: 'snapshot', uploading: false },

        imports: [],

        // Oznaczenia obrębów (słownik <nazwa>.obreby.csv obok bazy).
        // `obreby` trzyma listę PAR (jednostka ewidencyjna, numer) z bazy --
        // numer sam nie identyfikuje obrębu, patrz rcn_core/slownik_obrebow.py.
        obreby: {
            zaladowane: false, zajety: false, zmienione: false,
            plik: null, wpisow_w_slowniku: 0, obrebow_w_bazie: 0,
            pokrytych: 0, zastosowanych: 0,
            obreby: [], plikDoWgrania: null,
            // Powiat ziemski ma kilkaset obrębów; renderujemy porcjami, żeby
            // strona ustawień nie budowała tysiąca inputów naraz.
            limitWidocznych: 200,
        },

        enhancements: [
            {
                op: 'enrich-egib', dbPhase: 'enrich_egib', producerOnly: true,
                label: 'Wzbogać geometrię z EGIB',
                description: 'Lookup geometrii działek/budynków z lokalnych GPKG (EGIB) + inheritance lokali z budynków. Pełny refresh tx_cache po wzbogaceniu.',
                running: false, lastRun: null, lastStatus: null,
                statusText: 'Nigdy nie uruchamiane', disabled: false,
                history: [], historyOpen: false,
            },
            {
                op: 'compute-flags', dbPhase: 'compute_flags',
                label: 'Oblicz flagi jakości',
                description: '6 reguł jakości: no_objects, multi_object_act, extreme_price_per_m2, zero_area, total_price_split_suspect, zero_or_null_price. Idempotent — można uruchomić ponownie po zmianie thresholds.',
                running: false, lastRun: null, lastStatus: null,
                statusText: 'Nigdy nie uruchamiane', disabled: false,
                history: [], historyOpen: false,
            },
            {
                op: 'geocoding', dbPhase: 'geocoding', producerOnly: true,
                label: 'Geocoding (Nominatim)',
                description: 'Uzupełnienie geometrii dla transakcji z samym adresem tekstowym (bez ID EGIB). W przygotowaniu — Priorytet #2 z roadmapy.',
                running: false, lastRun: null, lastStatus: null,
                statusText: 'Nieaktywne (w przygotowaniu)', disabled: true,
                history: [], historyOpen: false,
            },
        ],

        deleteOpen: false,
        deleteConfirmText: '',
        deleting: false,

        toast: { visible: false, text: '', kind: 'ok' },
        _enhTimer: null,
        _bc: null,

        showToast(text, kind = 'ok') {
            this.toast = { visible: true, text, kind };
            setTimeout(() => { this.toast.visible = false; }, kind === 'error' ? 6000 : 3000);
        },

        // BroadcastChannel "rcn-workspace" -- powiadom otwartą zakładkę
        // workspace.html (mapę/tabelę) o zmianie warstw lub zakończeniu
        // ulepszenia. Workspace.js w boot subskrybuje i robi reload po
        // odebraniu eventu dla swojego wsId. Bez tego user musi Ctrl+F5.
        _broadcast(type, extra) {
            try {
                if (!this._bc && 'BroadcastChannel' in window) {
                    this._bc = new BroadcastChannel('rcn-workspace');
                }
                this._bc?.postMessage({ type, wsId, ...(extra || {}) });
            } catch (e) { /* ignore -- BroadcastChannel niedostępny */ }
        },

        async boot() {
            // Model A: build konsumenta (bez rcn_producer) -> bez ulepszeń producenta
            // (enrich-egib/geocoding). Zostaje compute-flags (liczone z GML).
            this.hasProducer = window.__HAS_PRODUCER__ === true;
            if (!this.hasProducer) {
                this.enhancements = this.enhancements.filter(e => !e.producerOnly);
            }
            try {
                const me = await fetch('/api/me').then(r => r.ok ? r.json() : null);
                this.isAdmin = me?.role === 'admin';
            } catch (e) { /* ignore */ }
            await Promise.all([this.loadInfo(), this.loadLayers(), this.loadImports(),
                               this.loadEnhancements(), this.wczytajObreby()]);
            this.loading = false;
        },

        async loadInfo() {
            const r = await fetch(`/api/workspaces/${wsId}`);
            if (!r.ok) { this.showToast('Nie udało się pobrać workspace', 'error'); return; }
            const data = await r.json();
            this.info = data;
            this.meta = { name: data.name || '', slug: data.slug || '', notes: data.notes || '' };
            this._metaOriginal = JSON.parse(JSON.stringify(this.meta));
        },

        metaReset() {
            this.meta = JSON.parse(JSON.stringify(this._metaOriginal));
        },

        async metaSave() {
            this.metaSaving = true;
            try {
                const body = { name: this.meta.name, slug: this.meta.slug, notes: this.meta.notes };
                const r = await fetch(`/api/workspaces/${wsId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    this.showToast(err.detail || `Błąd HTTP ${r.status}`, 'error');
                    return;
                }
                const data = await r.json();
                this.info = data;
                this._metaOriginal = JSON.parse(JSON.stringify(this.meta));
                this.showToast('Zapisano metadane');
            } finally {
                this.metaSaving = false;
            }
        },

        async loadLayers() {
            const r = await fetch(`/api/workspaces/${wsId}/custom-layers`);
            if (r.ok) {
                // Endpoint zwraca {workspace_id, layers: [...]}, NIE bare array.
                // Bez tej dezopakowania customLayers.length jest undefined,
                // x-show "length > 0" i "length === 0" oba false → user nie
                // widzi ani warstw, ani komunikatu "brak warstw".
                const data = await r.json();
                this.customLayers = Array.isArray(data?.layers) ? data.layers : [];
            }
        },

        async addLayer() {
            if (!this.newLayer.name || !this.newLayer.file) return;
            this.newLayer.uploading = true;
            try {
                const fd = new FormData();
                fd.append('name', this.newLayer.name);
                fd.append('file', this.newLayer.file);
                const r = await fetch(`/api/workspaces/${wsId}/layers/add`, { method: 'POST', body: fd });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    this.showToast(err.detail || `Błąd HTTP ${r.status}`, 'error');
                    return;
                }
                this.newLayer = { name: '', file: null, fileName: '', uploading: false };
                await this.loadLayers();
                this.showToast('Warstwa dodana');
                this._broadcast('layer-added');
            } finally {
                this.newLayer.uploading = false;
            }
        },

        async deleteLayer(layer) {
            if (!confirm(`Usunąć warstwę „${layer.name}"?`)) return;
            const r = await fetch(`/api/workspaces/${wsId}/layers/${layer.slug}`, { method: 'DELETE' });
            if (!r.ok) { this.showToast(`Błąd HTTP ${r.status}`, 'error'); return; }
            await this.loadLayers();
            this.showToast('Warstwa usunięta');
            this._broadcast('layer-removed', { slug: layer.slug });
        },

        async uploadGml() {
            if (!this.newGml.file) return;
            this.newGml.uploading = true;
            try {
                const fd = new FormData();
                fd.append('file', this.newGml.file);
                fd.append('tryb', this.newGml.tryb);
                const r = await fetch(`/api/workspaces/${wsId}/upload`, { method: 'POST', body: fd });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    this.showToast(err.detail || `Błąd HTTP ${r.status}`, 'error');
                    return;
                }
                const data = await r.json();
                this.newGml = { file: null, fileName: '', tryb: this.newGml.tryb, uploading: false };
                const ile = (data.import_ids && data.import_ids.length) || 1;
                this.showToast(data.uwaga || (data.zrodlo_archiwum
                    ? `Paczka rozpakowana — ${ile} plik(ów) w kolejce`
                    : `Wgrywanie startuje (import_id=${data.import_id})`));
                // Reset input file (Alpine x-model nie czyści <input type=file>)
                document.querySelectorAll('.ws-card input[type=file][accept*=".gml"]').forEach(el => el.value = '');
                // Odśwież natychmiast historię + zacznij polling status
                await this.loadImports();
            } finally {
                this.newGml.uploading = false;
            }
        },

        async loadImports() {
            const r = await fetch(`/api/workspaces/${wsId}/imports`);
            if (r.ok) {
                this.imports = await r.json();
                // Jeśli któryś import jest w trakcie, odśwież co 3s
                const anyProcessing = this.imports.some(i => i.status === 'processing' || i.status === 'queued');
                if (anyProcessing) {
                    setTimeout(() => this.loadImports(), 3000);
                }
            }
        },

        async deleteImport(imp) {
            if (!confirm(`Usunąć import „${imp.original_filename}"? Skasuje też wszystkie powiązane transakcje.`)) return;
            const r = await fetch(`/api/workspaces/${wsId}/imports/${imp.id}`, { method: 'DELETE' });
            if (!r.ok) { this.showToast(`Błąd HTTP ${r.status}`, 'error'); return; }
            await Promise.all([this.loadImports(), this.loadInfo()]);
            this.showToast('Import usunięty');
        },

        async loadEnhancements() {
            const r = await fetch(`/api/workspaces/${wsId}/enhancements/status?limit=20`);
            if (!r.ok) return;
            const runs = await r.json();
            // Group runs by phase
            const byPhase = {};
            for (const run of runs) {
                if (!byPhase[run.phase]) byPhase[run.phase] = [];
                byPhase[run.phase].push(run);
            }
            let anyRunning = false;
            for (const enh of this.enhancements) {
                const wasRunning = enh.running === true;
                const prevRunId = enh.lastRun?.id ?? null;
                const history = byPhase[enh.dbPhase] || [];
                enh.history = history;
                enh.lastRun = history[0] || null;
                enh.lastStatus = enh.lastRun?.status || null;
                enh.running = enh.lastStatus === 'running';
                if (enh.running) anyRunning = true;
                if (!enh.lastRun) {
                    enh.statusText = enh.disabled ? 'Nieaktywne (w przygotowaniu)' : 'Nigdy nie uruchamiane';
                } else {
                    const map = {
                        running: 'W trakcie...',
                        success: 'Sukces',
                        failed: 'Błąd',
                    };
                    enh.statusText = map[enh.lastStatus] || enh.lastStatus;
                }
                // Wykryj transition running → success/failed (ten sam run.id
                // przechodzi na status terminal). Toast + broadcast tylko raz.
                if (wasRunning && !enh.running && enh.lastRun && enh.lastRun.id === prevRunId) {
                    if (enh.lastStatus === 'success') {
                        const summary = this._formatPhaseSummary(enh);
                        this.showToast(`${enh.label}: ${summary}`);
                        this._broadcast('phase-finished', { phase: enh.dbPhase });
                    } else if (enh.lastStatus === 'failed') {
                        const errMsg = enh.lastRun?.error_msg || 'błąd';
                        this.showToast(`${enh.label}: ${errMsg}`, 'error');
                    }
                }
            }
            // Polluj co 2s gdy coś trwa
            if (anyRunning) {
                if (this._enhTimer) return;
                this._enhTimer = setTimeout(() => {
                    this._enhTimer = null;
                    this.loadEnhancements();
                }, 2000);
            }
        },

        _formatPhaseSummary(enh) {
            // Zwięzły opis "co się stało" do wyświetlenia w toast po success.
            // diagnostics_json to JSON-string (stats z enrich_workspace /
            // compute_flags); parsujemy lazy.
            let s = {};
            try {
                if (enh.lastRun?.diagnostics_json) {
                    s = JSON.parse(enh.lastRun.diagnostics_json) || {};
                }
            } catch { /* zostaw {} -- toast pokaże tylko duration */ }
            const dur = typeof enh.lastRun?.duration_s === 'number'
                ? ` (${enh.lastRun.duration_s.toFixed(1)}s)`
                : '';
            if (enh.dbPhase === 'enrich_egib') {
                const p = s.plots_matched ?? 0;
                const b = s.buildings_matched ?? 0;
                const li = (s.locals_inherited_b ?? 0) + (s.locals_inherited_p ?? 0);
                return `${p} działek + ${b} budynków + ${li} lokali zmatchowanych${dur}`;
            }
            if (enh.dbPhase === 'compute_flags') {
                const f = s.flagged_count ?? s.total_flagged ?? 0;
                return `${f} transakcji oflagowanych${dur}`;
            }
            return `sukces${dur}`;
        },

        async runEnhancement(op) {
            const enh = this.enhancements.find(e => e.op === op);
            if (!enh || enh.running || enh.disabled) return;
            const r = await fetch(`/api/workspaces/${wsId}/enhancements/${op}`, { method: 'POST' });
            if (r.status === 409) {
                this.showToast('Ulepszenie już trwa', 'error');
                return;
            }
            if (!r.ok) {
                const err = await r.json().catch(() => ({}));
                this.showToast(err.detail || `Błąd HTTP ${r.status}`, 'error');
                return;
            }
            this.showToast(`Uruchomiono: ${enh.label}`);
            // Natychmiast odśwież status (subprocess już wstawił row z 'running')
            setTimeout(() => this.loadEnhancements(), 500);
        },

        async confirmDelete() {
            if (this.deleteConfirmText !== this.info.name) return;
            this.deleting = true;
            try {
                const r = await fetch(`/api/workspaces/${wsId}`, { method: 'DELETE' });
                if (!r.ok) {
                    this.showToast(`Błąd HTTP ${r.status}`, 'error');
                    this.deleting = false;
                    return;
                }
                window.location.href = '/workspaces';
            } catch (e) {
                this.showToast('Błąd usuwania: ' + e.message, 'error');
                this.deleting = false;
            }
        },

        get obrebyWidoczne() {
            return this.obreby.obreby.slice(0, this.obreby.limitWidocznych);
        },

        async wczytajObreby() {
            try {
                const r = await fetch(`/api/workspaces/${wsId}/obreby`);
                if (!r.ok) return;
                const d = await r.json();
                Object.assign(this.obreby, {
                    zaladowane: true, zmienione: false,
                    plik: d.plik, wpisow_w_slowniku: d.wpisow_w_slowniku,
                    obrebow_w_bazie: d.obrebow_w_bazie, pokrytych: d.pokrytych,
                    zastosowanych: d.zastosowanych, obreby: d.obreby || [],
                });
            } catch (e) { /* sekcja zostaje pusta -- nie blokuje strony */ }
        },

        async zapiszSlownikObrebow() {
            // Wysyłamy tylko wiersze z oznaczeniem ALBO takie, które je miały
            // (puste oznaczenie = usunięcie wpisu ze słownika po stronie API).
            const wpisy = this.obreby.obreby.map(o => ({
                teryt_gminy: o.teryt_gminy,
                numer_obrebu: o.numer_obrebu,
                oznaczenie: (o.oznaczenie || '').trim(),
                gmina: o.gmina || '',
            }));
            this.obreby.zajety = true;
            try {
                const r = await fetch(`/api/workspaces/${wsId}/obreby`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ wpisy }),
                });
                if (!r.ok) {
                    const err = await r.json().catch(() => ({}));
                    this.showToast(err.detail || `Błąd HTTP ${r.status}`, 'error');
                    return;
                }
                const d = await r.json();
                this.showToast(`Zapisano słownik: ${d.wpisow} wpisów`);
                await this.wczytajObreby();
            } catch (e) {
                this.showToast('Błąd zapisu słownika: ' + e.message, 'error');
            } finally {
                this.obreby.zajety = false;
            }
        },

        async wgrajSlownikObrebow() {
            if (!this.obreby.plikDoWgrania) return;
            const fd = new FormData();
            fd.append('file', this.obreby.plikDoWgrania);
            this.obreby.zajety = true;
            try {
                const r = await fetch(`/api/workspaces/${wsId}/obreby/plik`, { method: 'POST', body: fd });
                const d = await r.json().catch(() => ({}));
                if (!r.ok) {
                    this.showToast(d.detail || `Błąd HTTP ${r.status}`, 'error');
                    return;
                }
                this.showToast(`Wgrano słownik: ${d.wpisow} wpisów`);
                this.obreby.plikDoWgrania = null;
                await this.wczytajObreby();
            } catch (e) {
                this.showToast('Błąd wgrywania: ' + e.message, 'error');
            } finally {
                this.obreby.zajety = false;
            }
        },

        async zastosujSlownikObrebow() {
            this.obreby.zajety = true;
            try {
                const r = await fetch(`/api/workspaces/${wsId}/obreby/zastosuj`, { method: 'POST' });
                const d = await r.json().catch(() => ({}));
                if (!r.ok) {
                    this.showToast(d.detail || `Błąd HTTP ${r.status}`, 'error');
                    return;
                }
                this.showToast(`Zastosowano oznaczenia: ${d.zmienione} wierszy`);
                await this.wczytajObreby();
            } catch (e) {
                this.showToast('Błąd stosowania: ' + e.message, 'error');
            } finally {
                this.obreby.zajety = false;
            }
        },

        formatBytes(b) {
            if (!b) return '—';
            const mb = b / 1024 / 1024;
            if (mb < 1) return (b / 1024).toFixed(1) + ' KB';
            return mb.toFixed(1) + ' MB';
        },
        formatDate(ts) {
            if (!ts) return '—';
            // imports.upload_timestamp jest INTEGER (sekundy)
            // phase_runs.started_at jest REAL (sekundy z ułamkami)
            const d = new Date(ts * 1000);
            return d.toLocaleString('pl-PL', { dateStyle: 'short', timeStyle: 'short' });
        },
    };
}
