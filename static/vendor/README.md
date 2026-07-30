# Biblioteki frontendu (vendored)

Wszystkie zależności frontendu serwujemy lokalnie — aplikacja (zwłaszcza
desktop) musi działać bez dostępu do internetu, a firewalle/antywirusy
potrafią blokować zewnętrzne CDN-y. Jedyny ruch zewnętrzny w UI to kafelki
mapy OpenStreetMap (dane, nie kod — bez sieci mapa jest pusta, reszta działa).

| Plik / katalog | Biblioteka | Wersja | Źródło | Licencja |
|---|---|---|---|---|
| `tailwind-play-3.4.17.min.js` | Tailwind CSS Play (JIT w przeglądarce) | 3.4.17 | cdn.tailwindcss.com/3.4.17 | MIT |
| `alpine-3.14.3.min.js` | Alpine.js | 3.14.3 | unpkg.com/alpinejs | MIT |
| `chart.umd.min.js` | Chart.js | 4.4.4 | jsDelivr | MIT |
| `marked-14.1.2.min.js` | Marked | 14.1.2 | unpkg.com/marked | MIT |
| `purify-3.1.7.min.js` | DOMPurify | 3.1.7 | unpkg.com/dompurify | Apache-2.0 OR MPL-2.0 |
| `leaflet/` | Leaflet (js+css+images) | 1.9.4 | unpkg.com/leaflet | BSD-2-Clause |
| `leaflet-draw/` | Leaflet.draw (js+css+images) | 1.0.4 | unpkg.com/leaflet-draw | MIT |
| `markercluster/` | Leaflet.markercluster (js+css) | 1.5.3 | unpkg.com/leaflet.markercluster | MIT |
| `fonts/` | Geist + Geist Mono (woff2 + `geist.css`) | GF api | fonts.googleapis.com (css2) | OFL-1.1 |

Aktualizacja: pobrać nową wersję z tego samego źródła, podmienić plik
i wpis w tabeli, zaktualizować ścieżkę w `templates/base.html`
(lub `templates/workspace.html` dla JS Leafleta). `geist.css` ma URL-e
przepisane na lokalne nazwy plików woff2 (subsety, m.in. latin-ext
dla polskich znaków).

Uwaga: Tailwind działa w trybie Play (kompilacja klas w przeglądarce).
Docelowy cleanup do statycznego builda Tailwinda — patrz plany redesignu
(`static/css/reset.css`, nagłówek).
