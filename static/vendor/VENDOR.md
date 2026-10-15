# Vendored front-end libraries

Served from our own static files (no CDN at runtime). To upgrade, download the new
files from the same URLs with the new version, update this table, and re-check the UI.

| Library | Version | Source | SHA-256 |
|---|---|---|---|
| Bootstrap CSS | 5.3.8 | https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css | da3c8de9d9ba178e949942cd1e61acb79a7f5d47d6a8e175165dc7105b479589 |
| Bootstrap JS bundle | 5.3.8 | https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/js/bootstrap.bundle.min.js | 541c57949cee316ccf4647b5bdfcbe5852fea5c3765d7f432473f58c08c422ea |
| HTMX | 2.0.7 | https://cdn.jsdelivr.net/npm/htmx.org@2.0.7/dist/htmx.min.js | 60231ae6ba9db3825eb15a261122d5f55921c4d53b66bf637dc18b4ee27c79f9 |

Both libraries are MIT licensed; their licenses sit next to the files.
The source-map reference comments are removed from the minified Bootstrap CSS/JS because their
maps are not shipped; this keeps Django's production manifest build self-contained.
