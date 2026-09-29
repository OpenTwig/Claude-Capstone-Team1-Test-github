# Vendored front-end libraries

Served locally (no CDN) so the Content Security Policy can stay `'self'`. See design DD9.
To upgrade: download the new files from the same jsdelivr paths, update this table, and re-run the tests.

| File | Version | Source | SHA-256 |
|---|---|---|---|
| `bootstrap/bootstrap.bundle.min.js` | 5.3.8 | https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js | `e4fd49181388c48ec5040bd3fe66f57c29c8e67fcd8502b3354b96ec7ab47cc7` |
| `bootstrap/bootstrap.min.css` | 5.3.8 | https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css | `d85327d99c7a3ee1f9b5d0500d1370acea3ad2db39c163c2f51f232baedbdede` |
| `chartjs/chart.umd.min.js` | 4.5.1 | https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js | `48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a` |

Bootstrap Icons 1.13.1 (https://cdn.jsdelivr.net/npm/bootstrap-icons@1.13.1/icons/): only the `sun` and `moon-stars` icons are used, inlined in `templates/_theme_icons.html` rather than served as files.

Licenses: `bootstrap/LICENSE` (MIT), `chartjs/LICENSE.md` (MIT), `bootstrap-icons/LICENSE` (MIT).
