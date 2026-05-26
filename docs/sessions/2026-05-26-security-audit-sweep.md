# Session 2026-05-26 — security audit sweep (#348–#351)

Three consecutive security-audit invocations against a clean tree
(`/vibe-security` → `/security-audit:run` ×2 → `/security-audit:fix-review`).
240-file LARGE project. Posture entered the session production-grade;
four follow-up PRs shipped to close defense-in-depth gaps + one
compliance-adjacent functional drift.

## Findings → PRs

| # | Severity | Surface | PR | Status |
|---|---|---|---|---|
| 1 | Low (defensive parity) | Proxy admin-token allowlist drift + stale doc path | [#348](https://github.com/DanLika/LeadDataScraper/pull/348) | ✅ merged |
| 2 | Low (DiD) | `/upload` magic-byte content check | [#349](https://github.com/DanLika/LeadDataScraper/pull/349) | open |
| 3 | Medium (compliance) | Phase 15.2 list_unsubscribe wiring drift | [#350](https://github.com/DanLika/LeadDataScraper/pull/350) | open |
| 4 | Low (DiD) | Cookie `Secure` depends on NODE_ENV | [#351](https://github.com/DanLika/LeadDataScraper/pull/351) | open |

Deferred follow-up: Phase 15.1 dead-template-code deletion (~600 LOC).
Scope traced + saved to memory `phase_15_1_deletion_scope.md`.
Branch off NEW main only AFTER #350 merges (same `dispatch_tick.py`).

## #348 — proxy admin-token allowlist parity + stale path

Backend declares `verify_admin_token` on **four** destructive endpoints
(`/leads/clear`, `/leads/demo`, `/operator/account`, `/admin/gemini-budget`),
but `frontend/app/api/proxy/[...path]/route.ts:ADMIN_TOKEN_PATHS` only
injected on the first two. The other two would silently 403 through the
proxy. Zero UI call-sites today (verified
`grep -rn 'operator/account\|admin/gemini-budget' frontend/{app,components,utils}`
→ 1 false-positive comment match), so this is defensive parity — backend
already fails-closed correctly.

Also fixed CLAUDE.md path: real route is `/leads/demo`, not
`/leads/clear-demo` (renamed in earlier phase, doc never caught up).

**Diff**: +3 / −1 across `frontend/app/api/proxy/[...path]/route.ts` +
`CLAUDE.md`.

## #349 — `/upload` magic-byte content check

`validate_csv_metadata` trusts the client-supplied Content-Type. New
`validate_csv_content` rejects bodies that:

- contain a null byte in the first 1 KB, OR
- start with `PK\x03\x04` (ZIP/xlsx), `%PDF`, `\x89PNG`, `GIF8`,
  `\x7fELF`

Pandas tolerates malformed input so this isn't a parser-RCE fix — it
stops obvious binary blobs from reaching the background-task queue +
tempfile write. UTF-16-LE/BE BOMs (alternating nulls) now reject with
400; existing BOM test already allowed 400.

Test suite item 9 (`BOMB_PAYLOADS`) was already documenting the gap
with a "flip when hardening lands" comment — flipped to assert `400`,
added `pdf`/`elf`/`null-padded` cases. `test_zip_bomb_does_not_inflate_on_disk`
→ renamed `..._rejected_before_disk_write` — bomb never touches disk
because the check fires pre-tempfile-write.

## #350 — Phase 15.2 list_unsubscribe wiring drift

Phase 15.2 `build_send_payload` renders `payload.list_unsubscribe_url`
from `build_unsubscribe_url(unsubscribe_base, tracking_id)`. The
`dispatch_tick` worker then calls
`push_leads(leads_payload, message_ids=...)` — but `push_leads` /
`from_lds_lead` never read it. The URL fell on the floor; per-lead
RFC 8058 / Gmail-Yahoo 2024 one-click compliance was not threaded.

**Discovery sequence**: surfaced while tracing CRLF risk on rendered
subject/body. The trace also caught a bigger contract bug: rendered
`subject` + `body` + `in_reply_to_message_id` are ALL dropped by
`from_lds_lead`. Advisor catch via `docs/integrations/instantly.md:114`
— "campaign owns subject/body templates." Phase 15.1's
`subject_template` + `body_template` on `sequence_variants` are
**dead-code-data** on the `push_leads` path.

**Scope choice (user-confirmed twice)**:
1. Narrow #350 = thread `list_unsubscribe_url` only — defensible per
   `LDS_KEYS` Phase 14.2 PR β bridge convention.
2. Phase 15.1 dead-template deletion = separate ~600-LOC follow-up.

**Wire shape**:
- `push_leads(...)` gains `list_unsubscribe_urls: dict[unique_key →
  bare_URL]` kwarg.
- Dispatcher wraps each as `<URL>` per Instantly's custom-vars-to-header
  bridge.
- `from_lds_lead`'s existing `list_unsubscribe` kwarg (Phase 14.2 PR β)
  already routes the value into
  `custom_variables.list_unsubscribe` +
  `custom_variables.list_unsubscribe_post = "List-Unsubscribe=One-Click"`.
- `dispatch_tick` builds the dict during the per-message loop, only
  populates when `payload.list_unsubscribe_url` non-empty.

4 new tests in `tests/test_instantly_sender.py`; 27/27 existing
instantly + dispatch_tick tests green.

## #351 — cookie `Secure` flag unconditional

`hardenCookieOptions(options, isProd)` derived `isProd` from
`process.env.NODE_ENV === 'production'` in both call sites
(`middleware.ts:40`, `server.ts:21`). If NODE_ENV was misconfigured
in CI/deploy, `secure` fell through to `Boolean(options?.secure)`.
Practical mitigations (HSTS 2y+preload + Render TLS edge + Next.js
`next start` auto-NODE_ENV) keep the real-world window narrow, but
defense-in-depth fix is one constant.

After: `hardenCookieOptions(options)` — `secure: true` unconditional.
Localhost is a "trustworthy origin" per WHATWG so dev still works
(Chrome accepts `Secure` cookies on `http://localhost` since ~2018).

**Test count**: 1165 pass / 2 skipped TODO / 0 fail (was 1157 / 2 / 0).
Dropped dev-mode adversarial loop; unified PROD/DEV adversarial inputs
into single `ADVERSARIAL_INPUTS` asserting the same `secure=true`
invariant on every shape. New pins: "secure=true even when SDK passes
undefined / missing".

## Rejected agent hallucinations

Two findings from /security-audit:run agents that did NOT survive
primary-source verification:

1. **aiohttp 3.13.5 / CVE-2024-47176** — that CVE is the CUPS RCE
   (Linux print system), NOT aiohttp. Real recent aiohttp CVEs
   (23334 / 30251 / 52303 / 52304) all fixed pre-3.10.11; 3.13.5 is
   past every one. **Rejected**.
2. **jinja2 pin mismatch (`~=3.1.4` vs 3.1.6)** — PEP 440 `~=3.1.4`
   means `>=3.1.4, <3.2`. 3.1.6 is compatible by design.
   **Rejected** — no finding.

Self-correction: the second `/security-audit:run` agent re-verified
aiohttp 3.13.5 and confirmed it's safe.

## fix-review verdicts

Independent fix-review against all 4 PRs verified:

- Root cause addressed (not patched)
- All instances covered (no half-fix)
- Code paths complete (edge cases handled)
- Tests added or flipped where applicable
- Zero new attack surface or info leakage

All 4 PRs marked ✅ **fix is complete** by the review.

## Phase 15.1 deletion (deferred follow-up)

Per advisor + scope trace: only **template_renderer + thread_builder**
are fully dead. `variant_selector` STAYS (still picks variant for
`campaign_messages.variant_id` attribution — A/B tracking lives even
when Instantly campaign owns templates). `sequence_advancer` STAYS
(webhook-driven sequence orchestration, zero template coupling).

**Sequencing constraint**: deletion modifies `dispatch_tick.py`
heavily — same file #350 touches. Branching off current `origin/main`
(pre-#350) → 3-way rebase fight when #350 lands. Standby for #350
merge.

**Migration shape**: split code-only from schema. Drift gate would
fire between code-PR merge and operator-run `ALTER TABLE
sequence_variants DROP COLUMN subject_template, DROP COLUMN
body_template` if bundled. Two PRs.

Full dead-vs-alive partition saved to `memory/phase_15_1_deletion_scope.md`.

## Patterns worth pinning forward

1. **Hallucinated CVE check** — agent confidently named CVE-2024-47176
   for aiohttp. Always verify CVE ID against package's actual changelog
   or osv.dev before flagging. Confidence is not evidence.
2. **Advisor catches doc-vs-design drift** — without reading
   `docs/integrations/instantly.md:114`, the natural reflex was to wire
   subject/body into `custom_variables`. Doc says campaign owns
   templates; the wire would be a no-op in prod. Always check the
   primary-source doc before threading an "obvious" custom-var.
3. **Worktree-per-fix-PR scales under parallel sessions** — 11+ pgrep
   live; four worktrees / four branches; zero HEAD-swap accidents.
4. **Test "flip when hardening lands" pattern** — `BOMB_PAYLOADS` in
   `test_upload_attacks.py` was already documenting the gap with the
   exact assertion the future fix should flip. Cost of writing the
   "this is a gap" test up front: ~10 LOC. Saved future-me ~30
   minutes of test-shape research when the fix landed.
5. **Severity framing** — Phase 15.2 list_unsubscribe drift was
   "security-adjacent functional" — calling it "Medium security" in
   the PR title overstates and confuses reviewers. Use the precise
   frame.

## Cross-links

- `memory/phase_15_1_deletion_scope.md` — deferred deletion partition
- `memory/phase_15_dispatch_tick.md` — 10 dispatch invariants (mostly
  survive the upcoming deletion)
- `docs/integrations/instantly.md:114` — the campaign-owns-templates
  doc line that decided #350's scope
- PRs: [#348](https://github.com/DanLika/LeadDataScraper/pull/348),
  [#349](https://github.com/DanLika/LeadDataScraper/pull/349),
  [#350](https://github.com/DanLika/LeadDataScraper/pull/350),
  [#351](https://github.com/DanLika/LeadDataScraper/pull/351)
