# Test Reorganization

Sweep date: **2026-05-22**
Branch: `chore/test-reorganize` (base `origin/main` @ `ee2fa0c`)

## Headline

| | Before | After |
|---|---|---|
| Test files at `tests/` top-level | **13** | 0 (all moved to subdirs) |
| Test directories | `tests/` only | **5 subdirs** (`unit/`, `integration/`, `e2e/`, `security/`, `quality/`) |
| `pytest.ini` markers | none | **5** (`slow`, `live`, `security`, `integration`, `e2e`) |
| CI default filter | none | `-m "not slow and not live"` |
| Tests passing | 134 / 134 | **134 / 134** (no regression) |

## Caveat — scope is `origin/main`

The operator's brief ("tests/ has grown to 50+ files") refers to the
**local** working-tree state with the 13 unreleased commits. This
PR moves the 13 files currently on `origin/main`. When the unreleased
batch lands, a follow-up PR can repeat the classification on the
incoming files.

## Layout

```
tests/
├── unit/          (8 files)  pure unit, fast, no I/O
├── integration/   (2 files)  hit real DB / Supabase / Gemini
├── e2e/           (empty)    placeholder for Playwright e2e
├── security/      (3 files)  auth bypass, injection, CSRF, validation
└── quality/       (empty)    placeholder for meta-tests (Pydantic / mypy)
```

Empty subdirs ship with `__init__.py` so pytest discovery sees them
and a follow-up PR can land tests there without a new directory commit.

## Per-file placement

| File | Bucket | Why |
|---|---|---|
| `test_basic.py` | `unit/` | Tiny smoke test |
| `test_cors.py` | `unit/` | CORS header config — no I/O |
| `test_csv_helper_health.py` | `unit/` | sanitize / merge / save helpers — pure functions |
| `test_logging_config.py` | `unit/` | `setup_logging` exercise — no I/O |
| `test_robustness.py` | `unit/` | Pydantic models + helper coverage |
| `test_scaling.py` | `unit/` | scoring math — pure functions |
| `test_supabase_helper.py` | `unit/` | mocked Supabase client — no live DB |
| `test_agentic_router.py` | `unit/` | mocked Gemini — no live AI calls |
| `test_validation_authz_gate.py` | `security/` | 422-schema-leak gate on the validation handler |
| `test_security_defenses.py` | `security/` | `fenced_json` corpus + Playwright route guard |
| `test_execute_plan_model.py` | `security/` | `/execute` Pydantic Literal allowlist hardening |
| `test_cherry_picks.py` | `integration/` | static-grep over `src/` files (reads file system) |
| `test_cherry_picks_live.py` | `integration/` | live Gemini path |

## pytest.ini

```ini
testpaths = tests

addopts =
    -m "not slow and not live"

markers =
    slow: takes >5s — full file scans, hypothesis fuzz with high example counts
    live: requires real external services (Supabase / Gemini / SMTP / Playwright)
    security: security-invariant tests
    integration: needs a running DB or live Supabase project
    e2e: end-to-end via Playwright + running backend + live Supabase
```

The default `addopts` filter strips slow + live so the merge gate
stays fast. Override for the full sweep: `pytest -m ""`.

## Path-relativity gotcha (fixed)

`tests/test_cherry_picks.py` had 19 `os.path.join(os.path.dirname(__file__), '..', 'src', ...)`
sites — static-grep helpers that read source files. The `..` resolves
to the project root when the file is at `tests/test_X.py`, but moves
to `tests/` when the file moves to `tests/integration/test_X.py`,
breaking every read.

Fix: bump every site to `'..'` × 2 (one extra `..` per level deeper).
Plus the `sys.path.insert(...)` line at top of file uses
`os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` —
needed a third `os.path.dirname` wrap.

**Lesson for future test additions**: prefer
`Path(__file__).resolve().parents[N] / 'src' / ...` over manual
`'..'` chains — depth-independent, fail loud if the file moves
without the test author noticing.

## Markers — current state + adoption plan

This PR **adds the marker vocabulary** but does NOT tag any test.
Reason: tagging requires deciding `slow` / `live` per-test, which is
a test-author judgment best done by whoever knows the test's runtime
characteristics. The vocabulary is in place; follow-up PRs apply tags.

### Suggested tags (apply in follow-up PR)

| Test | Suggested marker | Why |
|---|---|---|
| `tests/integration/test_cherry_picks_live.py` | `@pytest.mark.live` | Direct Gemini calls; skipped without `GEMINI_API_KEY` |
| `tests/integration/test_cherry_picks.py` | `@pytest.mark.integration` (already by directory; explicit marker = belt-and-braces for CLI filtering) | File-read static scans |
| `tests/security/test_validation_authz_gate.py` | `@pytest.mark.security` | Already in `security/`; tag for cross-cutting filter |
| `tests/security/test_security_defenses.py` | `@pytest.mark.security` | Same |
| `tests/security/test_execute_plan_model.py` | `@pytest.mark.security` | Same |
| Hypothesis-driven tests (when unreleased commits land) | `@pytest.mark.slow` | 200+ example counts |

## What did NOT change

- No test bodies edited (except `test_cherry_picks.py` path fix —
  mechanical depth adjustment, no logic change)
- No tests renamed
- No conftest.py added or modified
- All 134 tests pass before and after

## CI implications

The new pytest.ini `addopts` line aligns with the operator's intent
("CI default: `-m 'not slow and not live'`"). The follow-up PR that
adds markers will start cutting CI time as `slow` tests get the tag.

Worth mirroring in `.github/workflows/ci.yml`: the existing test job
inherits `addopts` from pytest.ini automatically.

## Reproducing

```sh
pytest                              # default: skips slow + live
pytest -m ""                        # everything
pytest -m security                  # security tag only
pytest -m "integration and not live" # integration but no Gemini
pytest tests/unit                   # by directory
```

## Weekly tracking

| Week of | Files reorganised | Tests tagged | Pass count |
|---|---:|---:|---:|
| 2026-05-22 | 13 / 13 | 0 / 134 (vocabulary added; per-test tagging follow-up) | 134 / 134 |
