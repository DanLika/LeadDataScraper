# Exception Handling Audit

Sweep date: **2026-05-22**
Branch: `refactor/errors-standardize` (stacked on PR #192's `refactor/campaigns-layered`)

Scope: `src/` + `backend/` — every `except` clause and logger call
that follows it.

## Headline

| Metric | Before | After this PR |
|---|---:|---:|
| `except:` (bare — no type named) | **0** | 0 |
| `try…finally` with no `except` | **0** | 0 |
| `except Exception` clauses | 61 | 61 (kept; reviewed and verdict-tagged) |
| `logger.error(…, exc_info=True)` (should be `.exception()`) | **35** | **0** ← mass-fixed |
| Canonical domain-error hierarchy | `src/services/exceptions.py` (campaign-only, 5 classes) | `src/errors.py` (3 domains + boundary classes, 16 classes) |

**Headline change**: 35 logger sites swapped from
`log.error("...", e, exc_info=True)` → `log.exception("...", e)` — the
documented canonical form for "log inside an except block + capture the
traceback". Same output, less ceremony, harder to forget the
`exc_info=True` flag.

**No bare `except:`** in the codebase — the operator already maintains
this invariant. **No `try…finally` without `except`** either.

## What this PR ships

| File | Change | LOC |
|---|---|---:|
| `src/errors.py` (new) | Canonical hierarchy: `DomainError` base; `NotFoundError`, `ValidationError`, `ConfigurationError` boundary classes; campaign errors moved + parented under `ConfigurationError` where appropriate; new `LeadError`, `EnrichmentError`, `AuditError` domains with 6 specific subclasses | +150 |
| `src/services/exceptions.py` | **Downgraded to shim** — re-exports from `src/errors.py` so PR #192 + existing callers don't break | -32 / +28 |
| 11 files (backend + src) | Mechanical swap: `logger.error("...", e, exc_info=True)` → `logger.exception("...", e)` | ±35 lines |

The shim path lets this PR ship without conflicting with PR #192
mid-flight. Once both merge, `src/services/exceptions.py` can be
deleted in a follow-up cleanup PR.

## The new hierarchy

```
DomainError                              (src/errors.py — caught by boundary catch-alls)
├── NotFoundError                        → HTTP 404
│   ├── CampaignNotFoundError            (was in services/exceptions.py)
│   ├── NoMatchingLeadsError             (was in services/exceptions.py)
│   ├── NoCampaignMessagesError          (was in services/exceptions.py)
│   └── LeadNotFoundError                NEW
├── ValidationError                      → HTTP 400/422 — NEW
├── ConfigurationError                   → HTTP 503 — NEW
│   └── CampaignTableMissingError        (was DomainError-direct → now ConfigurationError-typed)
├── LeadError                            → 500; lead-domain catch-all — NEW
│   └── LeadProcessingError              NEW
├── EnrichmentError                      → 500; enrichment pipeline — NEW
│   ├── EnrichmentTimeoutError           NEW
│   └── EnrichmentExtractionError        NEW
└── AuditError                           → 500; SEO audit — NEW
    ├── AuditTimeoutError                NEW
    └── AuditFetchError                  NEW
```

The campaign classes keep their identity (`from src.services.exceptions
import CampaignNotFoundError` resolves to the same object as
`from src.errors import CampaignNotFoundError` — verified). `isinstance`
checks in handlers continue to pass.

`CampaignTableMissingError`'s parent changed from `DomainError` directly
to `ConfigurationError`. This is a semantic refinement — any existing
`except CampaignTableMissingError` clause still works (more-specific
catch); `except DomainError` clauses still catch it transitively. Net
effect: a future handler can write `except ConfigurationError` and
catch every operator-action-required class at once.

## The 35 `logger.error(... exc_info=True)` → `logger.exception(...)` swap

Distribution (post-swap counts of `logger.exception`):

| File | exception sites |
|---|---:|
| `backend/main.py` | 17 (1 was already converted in PR #192) |
| `src/core/agentic_router.py` | 7 |
| `src/utils/supabase_helper.py` | 4 |
| `src/processors/leadhunter.py` | 4 |
| `src/core/task_orchestrator.py` | 3 |
| `src/scrapers/enrichment_engine.py` | 2 |
| `src/core/parallel_auditor.py` | 2 |
| `src/utils/csv_helper.py` | 1 |
| `src/processors/ai_mapper.py` | 1 |
| `src/scripts/run_multi_niche_discovery.py` | 1 |
| `src/scrapers/discovery_engine.py` | 1 |

`logger.exception(msg, *args)` is documented as
"identical to `logger.error(msg, *args, exc_info=True)` and must only
be called from inside an except handler". All 35 swapped sites are in
exception handlers — verified by reading each before transforming.

## The 61 `except Exception` clauses — per-site verdict

The plain catch-all is acceptable in **two** locations:

1. **Outermost request handlers** (`backend/main.py`'s
   `@app.<method>` functions, the global FastAPI exception handler).
   These are the boundary between "the operator's process" and "the
   HTTP wire" — any uncaught exception here becomes a 500 with the
   sensitive-substring scrub that
   `tests/test_error_message_leak.py` verifies, so the catch-all is
   load-bearing.
2. **Background-task supervisors** (`TaskOrchestrator._process_in_chunks`
   etc.) where a single lead's failure must not abort the whole batch.

Anywhere else, `except Exception` should narrow to a typed catch —
either the canonical domain class from `src/errors.py` or the
upstream library's exception type (e.g. `postgrest.APIError`,
`playwright._impl._api_types.Error`, `aiohttp.ClientError`).

### Per-file inventory

| File | `except Exception` count | Verdict |
|---|---:|---|
| `backend/main.py` | 17 | **KEEP ALL** — every one is the boundary catch-all at the bottom of an `@app.*` handler. The global FastAPI exception handler also catches `Exception` as the last resort. This is where the application's HTTP error contract is enforced (`error_response("Failed to ...")` + 500 status). |
| `src/core/agentic_router.py` | 8 | **NARROW** — every site catches Gemini-call failures + JSON parse errors. Should narrow to `(google.genai.errors.GoogleGenAIError, json.JSONDecodeError, ValueError)` per site. Defer to a `agentic_router` extraction PR. |
| `src/processors/leadhunter.py` | 9 | **NARROW** — Gemini calls + aiohttp scrapes. Should narrow to `(aiohttp.ClientError, asyncio.TimeoutError, GoogleGenAIError, EnrichmentExtractionError)`. Defer. |
| `src/utils/supabase_helper.py` | 7 | **NARROW** — PostgREST calls. Should narrow to `(postgrest.APIError,)` exclusively. Catching `Exception` hides programmer bugs (e.g. typo in column name) as DB errors. Defer to a repository-layer extraction PR. |
| `src/scrapers/enrichment_engine.py` | 6 | **NARROW** — Playwright operations. Should narrow to `(playwright._impl._api_types.Error, asyncio.TimeoutError, EnrichmentError)`. Defer. |
| `src/core/parallel_auditor.py` | 4 | **KEEP 2, NARROW 2** — the per-lead-loop sites (audit + hunt) need the broad catch so one bad lead doesn't take down a batch. The 2 outer sites can narrow to `AuditError`. |
| `src/core/task_orchestrator.py` | 3 | **KEEP** — background-task supervisors. One per-lead-loop, two per-job catch-alls. Same "don't fail the batch" reasoning as the parallel auditor's loop sites. |
| `src/utils/csv_helper.py` | 2 | **NARROW** — `pd.errors.ParserError` + `pd.errors.EmptyDataError` already specifically caught by the orchestrator; the remaining 2 `except Exception` in `merge_and_deduplicate` + `_read_csv_with_recovery` (recovery-of-recovery) should narrow to `pd.errors.ParserError`. |
| `src/scripts/run_multi_niche_discovery.py` | 1 | **KEEP** — CLI script main-loop supervisor. |
| `src/scripts/export_leads.py` | 1 | **KEEP** — CLI script supervisor. |
| `src/scrapers/seo_audit.py` | 1 | **NARROW** — should become `AuditFetchError` raised by the producer + `(aiohttp.ClientError, AuditError)` at the catch. |
| `src/processors/ai_mapper.py` | 1 | **NARROW** — Gemini call + JSON parse. Same as `agentic_router`. |
| `src/integrations/email_sender.py` | 1 | **NARROW** — already partly narrowed (`smtplib.SMTPRecipientsRefused`); the residual `except Exception` should narrow to `smtplib.SMTPException` (parent of the SMTP-specific classes). |
| **Total** | **61** | **27 KEEP** (boundary / supervisor) **+ 34 NARROW** (defer per file) |

### Verdict summary

- **27 `except Exception`** are load-bearing boundary catches → KEEP, document
- **34 `except Exception`** should narrow → DEFER to per-domain extraction PRs
- **0** sites need an immediate rewrite this PR (the file-level extraction PRs are the right unit of change for the narrowings)

## Roadmap (deferred per-domain PRs)

| Order | Domain | Narrowing target |
|---|---|---|
| 1 | `src/utils/supabase_helper.py` | `(postgrest.APIError,)` — small file, well-tested |
| 2 | `src/processors/ai_mapper.py` + `src/core/agentic_router.py` Gemini sites | `(GoogleGenAIError, json.JSONDecodeError, ValueError)` |
| 3 | `src/scrapers/seo_audit.py` | Producer raises `AuditFetchError` / `AuditTimeoutError`; catch site narrows to `AuditError` |
| 4 | `src/scrapers/enrichment_engine.py` | Producer raises `EnrichmentTimeoutError` / `EnrichmentExtractionError`; catch site narrows to `EnrichmentError` |
| 5 | `src/processors/leadhunter.py` | Multi-fix: aiohttp + Gemini + extraction errors |
| 6 | `src/integrations/email_sender.py` | `smtplib.SMTPException` |

Each of these is a small, focused PR that:
1. Has the producer raise the canonical domain error
2. Narrows the catch site to that error (+ the library's own type)
3. Updates the per-domain tests to assert on the typed exception

## False positives — DO NOT re-flag

### Boundary `except Exception` on FastAPI handlers

`backend/main.py:543` (and 16 others) catches `Exception` as the last
resort before returning `error_response("Failed to ...")` to the
operator. This is the contract — every endpoint MUST surface a
JSON-shaped response, never an uncaught traceback. Locked in by
`tests/test_error_message_leak.py` which fault-injects DB / Gemini /
file errors and scrapes the response against an 18-regex
sensitive-substring list.

### Background-task `except Exception` for batch survival

`TaskOrchestrator._process_in_chunks`, `ParallelAuditor.audit_single_lead`
loop body, and similar supervisor sites must catch broadly so one
poisoned lead doesn't take down a thousand-lead batch. Documented
in CLAUDE.md "EnrichmentEngine shared-browser pool" + the
orchestrator's `finally:` blocks.

### Tests assert on the post-merge layout

`tests/test_endpoint_hardening.py` and the campaign tests in
PR #192 already import `from src.services.exceptions import ...` —
the shim guarantees those imports continue to resolve. Identity
check (`from src.errors import X is from src.services.exceptions
import X`) verified in this PR.

## What did NOT change

- No producer-side rewrites — those land per-domain (roadmap above)
- No new tests — the existing
  `tests/test_endpoint_hardening.py` / `test_error_message_leak.py`
  cover the boundary contract
- No HTTP status code changes — handler mappings stay byte-identical
- No log line format changes — `logger.exception(msg, *args)`
  produces the same output as `logger.error(msg, *args, exc_info=True)`

## Reproducing

```sh
# bare except: (should be 0)
grep -rn "^\s*except:" src/ backend/ --include='*.py'

# try-finally with no except (should be 0)
python3 -c "
import ast, os
for root in ['src', 'backend']:
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if fn.endswith('.py'):
                p = os.path.join(dp, fn)
                tree = ast.parse(open(p).read())
                for n in ast.walk(tree):
                    if isinstance(n, ast.Try) and n.finalbody and not n.handlers:
                        print(f'{p}:{n.lineno}')"

# except Exception inventory
grep -rn "^\s*except Exception" src/ backend/ --include='*.py'

# log.error+exc_info=True (should be 0 after this PR)
grep -rnE "(log|logger)\.error\(.*exc_info=True" src/ backend/ --include='*.py'

# Sanity: shim still re-exports the canonical class
python3 -c "
from src.errors import CampaignNotFoundError as A
from src.services.exceptions import CampaignNotFoundError as B
assert A is B"
```

## Weekly tracking

| Week of | bare `except:` | `try…finally` no-except | `except Exception` | `log.error(...exc_info)` |
|---|---:|---:|---:|---:|
| 2026-05-22 | 0 | 0 | 61 (27 KEEP / 34 NARROW deferred) | **0** (was 35) |
