# Dogfood plan — Dalmatia vacation rentals (2 weeks)

**Start date:** 2026-05-25 (Monday)
**End date:** 2026-06-07 (Sunday, Week 2 close)
**Final report due:** 2026-06-08
**Roadmap item:** 13.15 (Phase 13 dogfood-only cut, see [`roadmap.md`](roadmap.md))

This is the **first real-world use** of the LDS pipeline. Goal: validate
discover → audit → hunt → draft → send → reply for a real vertical with
real revenue intent, before opening the codebase to anyone else.

---

## 1 · Scope (locked)

### Target vertical

Vacation rental apartments on the Croatian Dalmatian coast. Cities:

- Split (largest pool)
- Šibenik
- Zadar
- Dubrovnik

**Why this vertical:** matches BookBed.io's actual target market (see
[`bookbed-crossover.md`](bookbed-crossover.md)). Dogfood validates the
real GTM hypothesis, not a convenience proxy.

### Volume

| Week | Discovered | Audited | Emails sent |
|------|-----------|---------|-------------|
| 1    | 50        | 30      | 10          |
| 2    | 100       | 60      | 25          |

Volumes are deliberately low — the goal is **quality signal**, not
throughput. Cost estimate at this volume: ~$2–3 of Gemini calls + Render
+ Supabase storage (well under any budget threshold).

### Geographic scope

Dalmatian coast only. **Do NOT** mix in Zagreb / Istria / interior —
keeps the messaging hypothesis testable. If reply rate is good and we
want to validate generalization, that's Week 3+ work, not in scope here.

---

## 2 · Constraints (load-bearing)

### 2.1 · Email path: Open-in-Gmail manual

Email dispatch (roadmap 13.4) and DKIM/SPF/DMARC (13.5) are **plan-only**
— see [`email-deliverability.md`](email-deliverability.md) and
[`email-dispatch-architecture.md`](email-dispatch-architecture.md). No
Resend account, no DNS records, no `/send-outreach` endpoint.

**The operator sends every email manually via the Gmail deep-link.** Path:

1. Click **Draft outreach** on a lead row
2. Review the draft (modal shows subject + body separately)
3. Click **Open in Gmail** (`frontend/app/page.tsx:1461`) — opens Gmail
   compose with `to`, `subject`, `body` pre-filled via `mailto:` URI
4. Operator finishes composition + clicks Send **in Gmail**

**Reply tracking** is manual:

- Create a Gmail label `lds-dogfood-2026-05`
- Apply label to every outbound message before sending
- A Gmail filter on `from:` of the recipient applies the label to replies
  too — gives a single thread view of the campaign
- Daily: count `label:lds-dogfood-2026-05 in:sent` and `label:lds-dogfood-2026-05 in:inbox`

**No automated bounce / open / click tracking** until 13.4 + 13.5 ship.
This is OK for the dogfood — manual review of 35 emails total is
trivial; deliverability stats become useful when volume crosses ~100/wk.

### 2.2 · Language: English

The AI prompts (`src/core/agentic_router.py`, `src/processors/leadhunter.py`)
output English. hr-HR localization (roadmap 13.1) is not shipped.

**Operator review checklist additions** (apply before sending each email):

- Recipient is English-comfortable. Croatian rental hosts who manage
  international bookings on Booking.com / Airbnb are. Local-only
  operators may not be — skip them or translate manually.
- Sender signature (`OPERATOR_NAME` env var) is set to a name that
  reads naturally in an English email.

If reply rate at Week 1 close < 2%, English-language friction is a
likely contributor — escalate 13.1 hr-HR.

---

## 3 · Success metrics

Tracked daily in [`dogfood-log-week-1.md`](dogfood-log-week-1.md) +
[`dogfood-report-2026-06.md`](dogfood-report-2026-06.md).

| Metric | Definition | Target (Week 1) | Kill threshold |
|--------|-----------|----------------|----------------|
| **Discovery success rate** | % discovered leads with both website + email after enrichment | ≥ 50% | < 25% → discovery prompt bug |
| **Audit success rate** | % with non-trivial `seo_score` (not NULL, > 0) | ≥ 80% | < 50% → audit pipeline bug |
| **Draft acceptance rate** | % of drafted emails operator approves to send (vs deletes/rewrites) | ≥ 40% | < 20% → prompt quality bug |
| **Email reply rate** | % replies / emails sent | ≥ 5% | n/a Week 1 (sample too small) |
| **Conversion intent rate** | % replies expressing interest in BookBed product | ≥ 20% | n/a Week 1 |

Kill thresholds = pause and fix before continuing. Targets = signal the
pipeline is working as designed.

---

## 4 · Daily routine (~30 min/day)

### Morning (09:00 local)

1. Check Discord for the daily digest (auto-posted at 09:00 CET via
   `.github/workflows/daily-dogfood-digest.yml`). Scan for:
   - Lead count delta (should be growing during active days)
   - Storage delta (alarm at >100 MB/day — likely a runaway job)
   - Gemini budget used vs ceiling (should be < 50% at this volume)
   - Orphans/zombies count (auto-healed but acknowledge in log)
2. Open Gmail. Count `label:lds-dogfood-2026-05` matches. Log replies.

### Active work session (~20 min, every other day)

1. Open the dashboard. Run discovery if Week 1 quota not hit.
2. Audit + hunt new leads (background — closes the dashboard tab).
3. Review draft queue. For each:
   - Approve → click **Open in Gmail** → send manually
   - Reject → click **Regenerate** or mark "skip" in log
4. Log anything weird in [`dogfood-log-week-1.md`](dogfood-log-week-1.md).

### Evening (optional)

Reply to any prospect interest within 24 h. Track in log.

---

## 5 · Bug triage during dogfood

Categories (assign in dogfood-log):

- **CRIT** — pipeline broken, dogfood halted. Fix today.
- **HIGH** — reduces signal quality (wrong info on leads, AI hallucination
  in drafts). Fix this week.
- **MED** — UX papercut, slow page, confusing label. Backlog for next
  iteration.
- **LOW** — cosmetic. Capture and forget.

Hard rule: **do not fix non-CRIT bugs during dogfood**. The point is
to collect signal, not refactor.

---

## 6 · Kill criteria (stop the dogfood early)

Halt and revisit the roadmap if any of these trip mid-experiment:

- Discovery success rate < 25% after 2 days (the Maps scraper is
  fundamentally broken for this vertical)
- Draft acceptance rate < 20% after 10 reviews (the AI prompt produces
  unusable output for this vertical)
- Any CRIT bug that doesn't have a same-day fix
- Cumulative Gemini cost trends to > $10/week (10x budget)
- Real GDPR / spam complaint received (escalate to legal, document
  per [`incidents.md`](runbooks/incidents.md))

---

## 7 · End-of-dogfood deliverables

- [`dogfood-log-week-1.md`](dogfood-log-week-1.md) populated daily with bugs + observations
- [`dogfood-report-2026-06.md`](dogfood-report-2026-06.md) — final report
  with metrics table, bug count by severity, cost breakdown, top
  operator pain points, go/no-go decision for multi-tenant prep

Final report decides: do we open LDS to a second user (multi-tenant
migration per roadmap 3–6 months), or stay single-tenant until BookBed
SaaS lands?

---

## 8 · Setup checklist (do these before Day 1)

- [ ] Set `OPERATOR_NAME` env var on backend to a real name (signs every draft email)
- [ ] Verify `/admin/gemini-budget` endpoint returns valid state (smoke: `curl -H "X-API-Key: ..." /admin/gemini-budget`)
- [ ] Configure `DISCORD_WEBHOOK_URL` repo secret if not already set (digest workflow no-ops without it)
- [ ] Create Gmail label `lds-dogfood-2026-05`
- [ ] Set up Gmail filter: matches each recipient as outbound is sent (or auto-apply via Boomerang/SaneBox)
- [ ] Manual smoke: run the digest script locally once before relying on the cron:
      ```
      DATABASE_URL=... BACKEND_URL=https://... API_SECRET_KEY=... \
        python -m src.scripts.daily_dogfood_digest
      ```
      All three are independently optional — the digest skips sections it
      can't read. Verify Gemini-budget section shows real usage, not "0 used".
