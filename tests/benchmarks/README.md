# Phase 16 — reply classifier bench reports

Empty by design. Runner output lands here:

- `reply_classifier_bench_<model>_<utc-iso>.json` — full per-row results + scores
- `reply_classifier_bench_<model>_<utc-iso>.md` — human summary table

## Run

```sh
ANTHROPIC_API_KEY=sk-... python scripts/run_reply_classifier_bench.py
```

Optional flags:

- `--model claude-sonnet-4-6-...` — compare Sonnet vs default Haiku 4.5
- `--limit N` — bench first N rows (smoke during prompt iteration)
- `--no-cache` — disable ephemeral system-prompt cache (cold-prompt latency)

## Targets

- Clear-case accuracy ≥ 85%
- p95 latency ≤ 2.0s

Both are pinned in `scripts/run_reply_classifier_bench.py` as
`TARGET_ACCURACY_CLEAR` + `TARGET_LATENCY_P95_S`. Failing either flips
the markdown summary's Targets section to `FAIL`.

## Status

Bench has NOT been run yet on this branch — `ANTHROPIC_API_KEY`
absent from `~/.bookbed-secrets` (memory note
`phase16_classifier_bench_2026-05-30.md`). Operator action: drop the
key in, run the script, commit the report files alongside.
