# Test Result Schema (shared across terminals)

Every terminal writes one markdown file under `test-results/NN-<slug>.md` containing
**exactly one** table with this header:

```
| ID | Category | Target | Test | Status | Detail |
```

## Column rules

| Column   | Format                                                                 |
|----------|------------------------------------------------------------------------|
| ID       | `<PREFIX>-NNN` (zero-padded 3 digits). Per-terminal prefix below.     |
| Category | Free text — short noun ("Auth", "Modal", "CSP", "Mobile-360", "/leads")|
| Target   | URL path, component name, endpoint, viewport, locale, …               |
| Test     | One-sentence imperative ("Open settings modal closes on ESC")          |
| Status   | One of: `PASS` `FAIL` `SKIP` `BLOCKED` (case-sensitive)               |
| Detail   | If non-PASS: error text, screenshot path, repro, expected vs actual    |

## ID prefixes (one terminal = one prefix)

| Prefix | Terminal scope                                  |
|--------|--------------------------------------------------|
| SEC    | Security: CSP, headers, auth, CSRF, SSRF        |
| RESP   | Responsive: viewports, breakpoints, touch       |
| NAV    | Navigation: routes, links, deep-links, back/fwd |
| COMP   | Components: buttons, dialogs, forms, tables     |
| A11Y   | Accessibility: ARIA, keyboard, contrast, focus  |
| API    | Backend endpoints: status, schema, rate-limit   |

## Status semantics

- `PASS` — assertion held, no warnings.
- `FAIL` — assertion did not hold. `Detail` MUST include the observed value.
- `SKIP` — intentionally not exercised (note reason: env-gated, manual-only, out-of-scope).
- `BLOCKED` — could not run (dep missing, auth missing, infra down). `Detail` MUST name the blocker.

## File naming

`test-results/01-security.md`, `02-responsive.md`, `03-navigation.md`,
`04-components.md`, `05-a11y.md`, `06-api.md`. Aggregator globs `test-results/[0-9]*.md`.

## Aggregation

`python3 scripts/aggregate_test_results.py` walks every file matching `[0-9]*-*.md`,
parses the table, emits:

- `TEST_RESULTS.md` — repo root. Per-terminal totals + grand FAIL/BLOCKED section
  listing every non-pass row with its detail.
- `test-results/_summary.json` — `[{terminal, pass, fail, skip, blocked}, ...]`
  for CI consumption.
