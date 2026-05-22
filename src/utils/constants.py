"""Operational constants — one place for every numeric policy.

Anything an operator might want to tune lives here. Anything that is
intrinsic-to-the-code (HTTP status codes, schema column counts, regex
group indices, etc.) does NOT — those stay inline at their call site
because moving them would only add indirection.

Grouped by domain. When adding a new constant:
  - Pick the right section, or add a new section header if no fit
  - Name it for the policy, not the value (`MAX_UPLOAD_BYTES` not
    `FIFTY_MB`)
  - Comment the unit if it isn't obvious from the suffix
    (`_BYTES` / `_MS` / `_S` / `_PX` / `_COUNT` — prefer suffixes
    over inline comments)
  - Keep it as a module-level `Final` value — never re-bind at runtime

Cross-language parity: when a constant has a frontend mirror in
`frontend/app/lib/constants.ts`, keep both files in sync by hand. The
test suite does not currently enforce parity; flag it in PR review.
"""
from __future__ import annotations

from typing import Final


# === Pagination caps ============================================
# Read-path bounds. Tighter than what supabase-py would accept by
# default; bounds the response payload size on the dashboard's hot
# paths.

#: Default cap for `/leads` and the agentic router's lead-index helper.
#: Matches the frontend's initial fetch + the dashboard render window.
DASHBOARD_LEAD_CAP: Final[int] = 200

#: Frontend renders only the first N campaign messages on the campaign
#: detail screen — keep the server-side cap aligned with the UI window
#: so we don't push payloads the operator can't see anyway.
CAMPAIGN_MESSAGES_PREVIEW: Final[int] = 50

#: Sample size for the `/insights` "what's the segment mix" panel —
#: small because it feeds Gemini-driven analysis where larger samples
#: don't materially improve output quality.
INSIGHTS_SAMPLE_SIZE: Final[int] = 5

#: AI database-query sample size. Bounds the per-lead row count fed
#: into Gemini's UNTRUSTED_DATA fence on `/ask` natural-language queries.
#: Distinct from `CAMPAIGN_MESSAGES_PREVIEW` (also 50) because that's a
#: UI render window — this is a token-budget bound. Decoupled so they
#: can evolve independently.
AI_DATABASE_QUERY_SAMPLE: Final[int] = 50


# === Pydantic field-length caps =================================
# Bounds that flow into every `@app.post` request body. Reused enough
# to deserve names. Loosening these here loosens them everywhere.

#: Lead unique key — base64-ish derived from Maps place-ID or MD5(name).
#: 128 chars is generous; production keys hover around 30.
UNIQUE_KEY_MAX_LENGTH: Final[int] = 128

#: Campaign + lead name maxima. 200 chars is enough for the longest
#: realistic business name; longer values usually indicate a parsing
#: error in the source CSV.
NAME_MAX_LENGTH: Final[int] = 200

#: Filter / search-query strings the operator types into the dashboard.
SEARCH_QUERY_MAX_LENGTH: Final[int] = 500

#: AI prompt cap — anything longer would blow per-request Gemini cost
#: budgets without proportional quality return.
AI_INSTRUCTION_MAX_LENGTH: Final[int] = 4000

#: Short identifiers (task names, filter type strings, etc.)
SHORT_IDENTIFIER_MAX_LENGTH: Final[int] = 64

#: Per-request lead-ID list cap on bulk operations. Higher and the
#: serialisation alone starts pushing latency budgets.
BULK_LEAD_IDS_MAX_LENGTH: Final[int] = 10_000

#: Per-request task list cap on bulk plan operations.
BULK_TASKS_MAX_LENGTH: Final[int] = 64


# === Upload + payload caps ======================================

#: `/upload` request body hard cap. Mirrored frontend-side as
#: `MAX_PROXY_BODY_BYTES` in `frontend/app/lib/constants.ts` — keep in
#: sync when retuning.
MAX_UPLOAD_BYTES: Final[int] = 50 * 1024 * 1024  # 50 MiB


# === Network / browser / SMTP timeouts ==========================
# Aggregated tunables for the IO-heavy paths. Suffix indicates unit
# (`_MS` for Playwright / aiohttp which take milliseconds; `_S` for
# Python `socket` / `aiohttp` which take seconds).

#: Playwright Maps load timeout — Maps takes 10-40s on a cold IP.
PLAYWRIGHT_MAPS_LOAD_TIMEOUT_MS: Final[int] = 60_000

#: Playwright `wait_for_selector` for the Maps result list.
PLAYWRIGHT_MAPS_SELECTOR_TIMEOUT_MS: Final[int] = 10_000

#: Enrichment first-pass page load timeout.
ENRICHMENT_PAGE_LOAD_TIMEOUT_MS: Final[int] = 45_000

#: Enrichment retry timeout (intentionally lower than first-pass —
#: if the slow path didn't return, hammering it again is unlikely
#: to help).
ENRICHMENT_RETRY_TIMEOUT_MS: Final[int] = 20_000

#: `asyncio.wait_for` outer timeout wrapping the inner `page.goto`.
#: Strictly larger than `ENRICHMENT_PAGE_LOAD_TIMEOUT_MS` to give the
#: inner timeout room to fire cleanly.
ENRICHMENT_OUTER_TIMEOUT_S: Final[float] = 50.0

#: SMTP send timeout — generous to tolerate Gmail's variable rate.
SMTP_SEND_TIMEOUT_S: Final[int] = 30

#: aiohttp HEAD/GET timeout for SEO audit fetches.
SEO_AUDIT_HTTP_TIMEOUT_S: Final[int] = 12

#: aiohttp HEAD/GET timeout for Crawlbase-fronted scrapes.
CRAWLBASE_HTTP_TIMEOUT_S: Final[int] = 30


# === Logging ====================================================

#: Per-file log size before rotation.
LOG_FILE_MAX_BYTES: Final[int] = 5 * 1024 * 1024  # 5 MiB

#: Number of rotated log files to retain.
LOG_FILE_BACKUP_COUNT: Final[int] = 3
