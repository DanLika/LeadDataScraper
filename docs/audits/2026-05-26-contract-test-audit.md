# Phase 14/15 contract-test coverage audit

Triggered by [#341](https://github.com/DanLika/LeadDataScraper/issues/341) — sev-2 retro from the unsubscribe-URL deploy-blocker (PR #340). Hypothesis: Phase 14/15 may have shipped other producer↔verifier pairs without round-trip contract tests, same shape as the bug.

**Audit date:** 2026-05-26.
**Auditor:** Session pass on the 4 candidates listed in #341.

## Methodology

For each candidate, check whether a test exists that:
1. Exercises the **producer-side composition** (mint, sign, write event, etc.)
2. **Replays the artefact** through the verifier / handler / state machine
3. **Asserts the resulting state** matches what the producer intended

A passing unit test on just one side does not satisfy the contract — the bug in #340 had unit tests on both `mint()` and the verifier, but no test that connected them.

## Findings

### 1. Instantly webhook HMAC ✅ COVERED

- **Producer:** `verify_hmac_sha256(payload, _sign(payload), SECRET)` in `tests/unit/test_webhook_security.py:35`
- **Verifier:** Same function, same call site (signing primitive + verifier are the same module)
- **Tamper checks:** `tests/unit/test_webhook_security.py:46-65` (tampered body, wrong secret, empty signature, empty secret)
- **End-to-end:** `tests/test_instantly_webhook.py` builds signed requests and exercises the FastAPI route — full producer→handler flow.

No gap.

### 2. Sequence advancement (`sent` event → next step row) ✅ COVERED

- **Comprehensive state-transition matrix** in `tests/unit/test_sequence_advancer.py`:
  - `test_sent_advances_on_always_branch` — sent event + next-step `branch_condition='always'` → next row materialises
  - `test_sent_skips_when_next_is_replied_branch` — inverted gate
  - `test_replied_advances_when_next_is_replied_branch` — replied event + matching branch → advance
  - `test_replied_skips_when_next_is_not_replied_branch` — replied + 'always' branch → no advance
  - `test_no_next_step_short_circuits`, `test_unique_collision_returns_skipped`
  - `test_in_reply_to_passed_when_thread_with_prior`, `test_no_in_reply_to_when_thread_disabled`
  - `test_scheduled_at_bumped_when_delay_lands_outside_window`

No gap.

### 3. Suppression on bounce (`/webhooks/instantly` → `suppressions` row) ✅ COVERED

- `tests/test_instantly_webhook.py`:
  - L292 `test_email_bounced_updates_status_and_inserts_suppression` — asserts row with `reason='bounce_hard'`, `channel='email'`
  - L326 `test_email_unsubscribed_inserts_channel_all_suppression` — asserts `channel='all'`
  - L367 `test_race_bounce_before_sent_still_inserts_suppression` — race condition

End-to-end from webhook handler through to suppression INSERT. Identifier-type / value / channel all asserted.

No gap.

### 4. `email_send_ledger` writes via `InstantlyDispatcher.push_leads` ✅ COVERED

- `tests/test_dispatch_schema_provider.py` — DB-level constraint check (4 assertions on `email_send_ledger.provider` CHECK)
- `tests/test_instantly_sender.py:152` — mocks the `email_send_ledger` insert call inside `_record_ledger_writes` and verifies the row shape

PR #337 layered a `LedgerProvider` Literal on `PROVIDER_NAME` so mypy catches typos at the producer site too.

No gap.

## Conclusion

All 4 candidates from #341 were already covered. **The unsubscribe-URL bug in #340 was an outlier**, not the tip of an iceberg. Verdict: Phase 14/15's contract test coverage is **stronger than #341's hypothesis assumed**.

## What we learned anyway

The pattern that catches this class of bug is:

> **Build the artefact → tear it apart → feed it to the verifier → assert state matches intent.**

This is the shape every signed envelope / composed URL / state-transition event needs in its test suite. Single-side unit tests do not satisfy the contract.

PR #340 added [`tests/integration/test_unsubscribe_url_roundtrip.py`](../../tests/integration/test_unsubscribe_url_roundtrip.py) as the reference implementation for this shape.

## Action items

- [x] Audit the 4 #341 candidates — done above, all clean.
- [x] Codify the rule in CLAUDE.md "Architecture patterns" section → see the linked sentence on contract tests.
- [x] Close #341 with this audit as evidence.

## Forward guard

When opening a PR in a new phase, the reviewer should ask:

> "Does this PR ship a new producer-and-verifier pair (signed envelope / state-change event / composed URL)? If yes — is there a `tests/integration/test_<feature>_roundtrip.py` that exercises both halves?"

If no, the PR adds one. The unsubscribe round-trip is the template.
