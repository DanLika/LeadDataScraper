# Discovery engine invariants (`src/scrapers/discovery_engine.py`)

Sourced from CLAUDE.md 2026-05-29 slim.

- `find_leads(query, location)` — Google-Maps. Host hardcoded `google.com`, `query` `quote_plus`-encoded (no host-SSRF). Playwright route guard re-runs `assert_safe_url` on subresources + redirects (closes TOCTOU + redirect-chain hops).
- `unique_key` from `!1s<id>!` segment of place URL (stable); fallback 16-char MD5 of `name` (`usedforsecurity=False`).
- `_extract_lead_data` returns `{name, unique_key, website, phone, rating, audit_status, lead_source: 'google_maps', address}`. Address via `_extract_address`: `button[data-item-id='address']` → `button[aria-label^='Address:']` → `[data-tooltip='Copy address']`. Opens side-panel if closed. Normalised via `re.sub(r'\s+', ' ', ...)` + `re.search(r'[\w].*')`. Returns `None` on miss.
