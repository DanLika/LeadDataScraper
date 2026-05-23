# Mutation kill rates — baseline 2026-05-23

Mutation testing answers the question coverage % can't: "Do the tests
actually ASSERT the right behavior, or just execute the code?" — by
mutating each line (flip `<` to `<=`, swap `and` for `or`, delete a
return, etc.) and re-running the suite. A mutant the suite fails to
catch ("survived") indicates a missing assertion.

This is the first executed baseline of `.github/workflows/mutation-test.yml`
(authored as Phase 7.12, never run until 2026-05-23). Threshold
`MIN_KILL_RATE: 80%`.

## Toolchain

- `mutmut==2.4.5` pinned — 3.x rewrote the CLI around config files;
  `--paths-to-mutate` + `--runner` only exist on 2.x and the
  workflow + the user-facing playbook depend on that surface.
- Python **3.11.14** (homebrew). mutmut 2.4.5 + pony.orm cannot
  pickle `itertools.count` on Python 3.14, so the project's `.venv`
  (3.14) isn't usable for mutmut. The CI workflow targets 3.12 —
  the closest local match was 3.11; bytecode + pony compatibility
  are the same.
- Tests run via the venv's pytest in mutmut's `--runner`.
- Invoke via `python -m mutmut` (NOT `mutmut` directly) — a parallel
  type-cov session in this user's environment renames
  `/tmp/mutmut-venv/bin/mutmut` to `mutmut.PAUSED-by-typecov-session`
  mid-run, which kills bare-binary invocations with exit code 127.
  `python -m mutmut` resolves through Python's package mechanism and
  is unaffected.

## Per-file kill rates

| File | Mutants | Killed | Survived | Suspicious | Timeout | Kill rate |
|---|---:|---:|---:|---:|---:|---:|
| `src/utils/prompt_safety.py` | 23 | 23 | 0 | 0 | 0 | ✅ **100.00%** |
| `src/utils/ssrf_guard.py` | 45 | 41 | 3 | 1 | 2 | ✅ **91.11%** |
| `src/processors/leadhunter.py` | — | — | — | — | — | ⚠️ **skipped** (see below) |

## Survivor breakdown

### `src/utils/prompt_safety.py`  — 0 survivors

**Baseline before kill round:** 14/23 killed = 60.87% (just above the
`<60%` HARD STOP, but well below the 80% target).

9 survivors all in `tests/test_prompt_injection_corpus.py` —
substring-`in` assertions on the `_UNTRUSTED_DATA_SYSTEM_INSTRUCTION`
constant and on `fenced_json` / `fenced_text` output passed through
`XX...XX`-wrapped mutations because the original substrings still
appeared in the mutated output.

**Fix:** added 3 test classes at the tail of the file
(`TestSystemInstructionContent`, `TestFencedJsonExactness`,
`TestFencedTextExactness`) that assert **exact-equality** on the
assembled outputs and the full canonical instruction string. Substring
checks survive XX-wrapping; `assertEqual` does not.

| ID | Mutation | How killed |
|---|---|---|
| 1-4 | Each line of `_UNTRUSTED_DATA_SYSTEM_INSTRUCTION` wrapped with `XX...XX` | `test_instruction_is_exact_canonical_string` — `assertEqual` on the full assembled string |
| 5 | `_UNTRUSTED_DATA_SYSTEM_INSTRUCTION = None` | `test_instruction_is_non_empty_string` — `assertIsNotNone` + min length |
| 6 | `ensure_ascii=False` → `ensure_ascii=True` | `test_unicode_passes_through_unescaped` — asserts `Kovačević` / `Žito` ride through unescaped, no `\uXXXX` form present |
| 9 | `fenced_json` replacement string mutated | `test_close_tag_replacement_yields_exact_output` — `assertEqual` on full output |
| 16 | `fenced_text(None)` output mutated | `test_none_yields_exact_empty_fence` — `assertEqual` on empty-fence form |
| 18 | `fenced_text` replacement string mutated | `test_close_tag_replacement_yields_exact_output` — `assertEqual` |

### `src/utils/ssrf_guard.py`  — 3 survivors (all equivalent mutations)

**Baseline before kill round:** 22/45 killed = 48.89% — **critical**
(below the user-spec `<60%` HARD STOP for security-critical files).
Two structural gaps:

1. **`SSRFGuardResolver` not tested at all.** The class is installed
   on every aiohttp client by `seo_audit.py` and `enrichment_engine.py`
   but every test in the suite went through `assert_safe_url` instead.
   Mutating any line of the resolver (`port=0` default, `in
   _BLOCKED_HOSTS`, `r["host"]`, the `continue` in the exception
   branch) survived because the resolver code was never executed.
2. **`_BLOCKED_HOSTS` membership not deterministically tested.** The
   reject sweep passed `dns_ip=None` for blocked hostnames; the test
   relied on real DNS to fail (`gaierror` → SSRFError) or resolve to a
   non-global IP. Either path catches the URL, so mutating the
   `_BLOCKED_HOSTS` set didn'\''t fail the test.

**Fix:** added 5 test classes
(`TestBlockedHostsMembership`, `TestSSRFGuardResolver`,
`TestSSRFErrorMessages`, `TestMultiResultDNS`,
`TestAssertSafeUrlMessages`) that:

- Mock DNS to return a **public IP** so `_BLOCKED_HOSTS` is the ONLY
  thing that can reject the URL — kills any blocklist entry mutation
  immediately.
- Exercise `SSRFGuardResolver.resolve` directly with mocked parent
  resolver — kills `port=`, `in _BLOCKED_HOSTS`, `r["host"]`, `ip =
  None`, etc.
- Use `assertRaisesRegex` with **anchored** regex (`^Blocked
  hostname:`) to lock error-message wording so `XX...XX` mid-line
  mutations no longer satisfy the match.
- Mock `getaddrinfo` to return `[unparseable, private_ip]` so the
  `continue` → `break` mutation in the iteration loop now visibly
  fails (break skips the private IP).

**Surviving 3** — all genuinely equivalent or cosmetic; accepted as
non-actionable:

| ID | Mutation | Why accepted |
|---|---|---|
| 18 | `host.lower().rstrip(".")` → `host.lower().rstrip("XX.XX")` | After `.lower()`, host contains no uppercase `X`. `rstrip` is case-sensitive, so both forms strip identical chars from any hostname. Equivalent. |
| 25 | `is_multicast or is_reserved or is_unspecified or not is_global` → `is_multicast and is_reserved or ...` | All multicast and reserved IPs are also classified `not is_global` by Python\'s `ipaddress` module, so the OR chain still triggers. Equivalent for IPv4/IPv6 address space. |
| 39 | `port: int = 0` → `port: int = 1` (aiohttp resolver default) | Cosmetic — aiohttp doesn\'t dispatch on this default; security irrelevant. |

### `src/processors/leadhunter.py`  — skipped this baseline

**Skipped — not run to completion.** Two issues blocked the run:

1. **Scope mismatch (expected).** `--paths-to-mutate` mutates the
   entire 812-LOC file; the available test runner
   (`tests/test_outreach_score_properties.py -k
   TestOutreachScoreFixedFixtures`) only exercises
   `calculate_outreach_score` and its `_score_*` helpers (~120 LOC
   out of 812). The other ~85% of the file (DDG search, social
   extraction, subpage scraping, Gemini chat) is covered only by
   live `test_outreach_golden_set` / `test_outreach_hallucination`
   tests that require `GEMINI_API_KEY`. Mutating those code paths
   under the fast runner produced an expected ~5-10% kill rate
   trending; the partial cache (cleared by a concurrent session) had
   9 killed out of ~470 attempted before the run was aborted.
2. **Parallel-session interference.** A concurrent `typecov` agent
   in this user\'s environment (see CLAUDE.md "Auto-branch hook
   caveat" + the `mutmut.PAUSED-by-typecov-session` rename behaviour)
   modified `src/processors/leadhunter.py` mid-run (added
   `src/utils/gemini_types` imports for type coverage). The test
   file then failed pytest collection because the new imports didn'\''t
   resolve — mutmut bailed at the baseline-run step.

**Recommendation for the next baseline:**

- Run leadhunter mutmut in an isolated git worktree (per CLAUDE.md
  "git-worktree HEAD-swap mitigation" recommendation, Session
  2026-05-23). The worktree pins the source tree against parallel
  branch swaps and the `M src/processors/leadhunter.py` problem.
- Either (a) accept ~5-10% kill rate as the documented floor with a
  scope note, OR (b) restrict mutmut to the score subgraph by
  setting `--paths-to-exclude` for the async scraper methods OR
  authoring a dedicated `src/processors/outreach_score.py` module
  that calculate_outreach_score + helpers move into.

The weekly `mutation-test.yml` job is the actual gate for this file
going forward; this manual baseline only covers the two
security-critical files that needed structural test fixes.

## Reproducing

```bash
# venv setup (one-time)
python3.11 -m venv /tmp/mutmut-venv
/tmp/mutmut-venv/bin/pip install -r requirements.txt
/tmp/mutmut-venv/bin/pip install \'mutmut==2.4.5\' pytest pytest-subtests hypothesis

# per target
cd /Users/duskolicanin/git/LeadDataScraper
rm -rf .mutmut-cache

# Y.1 ssrf_guard (~3min)
/tmp/mutmut-venv/bin/python -m mutmut run \
  --paths-to-mutate src/utils/ssrf_guard.py \
  --runner \'/tmp/mutmut-venv/bin/python -m pytest tests/test_ssrf_guard_regression.py -x -q --disable-warnings\' \
  --simple-output --no-progress

# Y.2 prompt_safety (~1min)
/tmp/mutmut-venv/bin/python -m mutmut run \
  --paths-to-mutate src/utils/prompt_safety.py \
  --runner \'/tmp/mutmut-venv/bin/python -m pytest tests/test_prompt_injection_corpus.py -x -q --disable-warnings\' \
  --simple-output --no-progress

# per-survivor inspection
/tmp/mutmut-venv/bin/python -m mutmut results
/tmp/mutmut-venv/bin/python -m mutmut show <id>

# authoritative count via SQLite cache (mutmut\'s text output omits the
# Killed bucket header when everything passes)
sqlite3 .mutmut-cache \'SELECT status, COUNT(*) FROM mutant GROUP BY status\'
```
