# FAQ

Top questions answered up front so the support inbox stays focused on
the genuinely novel ones. Drives the auto-reply link in
[`docs/support-process.md`](support-process.md).

If a question lands in `support@_(your-domain)_` three times in a
month, **add it here**. The FAQ is the cheapest support reduction
tool you have.

---

## Getting started

### Q: How do I sign in?

Visit your deployment URL. You'll be redirected to `/login`. Use the
Supabase Auth email + password the operator created for your account.
There is no public signup — accounts are provisioned manually.

### Q: I forgot my password.

Email `support@_(your-domain)_`. The operator can issue a password
reset through Supabase Auth. Self-serve reset is not currently wired.

### Q: Where's the API documentation?

The `/docs` Swagger UI is **disabled by default in production** for
security ([CLAUDE.md → "Interactive docs"](../CLAUDE.md)). The
authoritative reference is
[`docs/runbooks/operator-guide.md`](runbooks/operator-guide.md) §11,
which lists every endpoint + rate limit.

If you need OpenAPI for tooling integration, set `ENABLE_DOCS=true`
on your backend `.env` in **dev only** — never enable in production.

---

## Privacy + data

### Q: How do I download all my data?

Dashboard → **Settings** → **My Data (GDPR)** → **"Download my data"**.

You'll get a ZIP with:

- `leads.csv` — every lead
- `campaigns.csv` — every campaign
- `messages.csv` — every campaign message
- `audit_log.json` — operator action history + export metadata

Rate-limited to **once per day**. If you need a fresher export within
the same day, email support and we'll do it manually.

See [`docs/legal/privacy-policy.md`](legal/privacy-policy.md) §7.

### Q: How do I delete my account?

Dashboard → **Settings** → **Danger Zone** → **"Delete my account"**.
You'll be asked to type `DELETE MY ACCOUNT` exactly to confirm.

Once confirmed:

- All your leads, campaigns, messages, and orchestration jobs are
  hard-deleted **immediately**.
- A 30-day audit row is retained (no business data — just operator
  email, timestamp, IP, row counts).
- After 30 days, the audit row is auto-purged. Nothing remains.

This action is **irreversible** — restore from a Supabase PITR snapshot
is the only path back. PITR retention is 7 days on Supabase Pro plan;
plan accordingly.

### Q: Where is my data stored?

Three places:

1. **Supabase Postgres** — leads, campaigns, messages, jobs, account
   deletion audit log. EU region (Frankfurt) when configured.
2. **Gemini API processing** — Google may briefly buffer prompts +
   completions per their API terms. We send the minimum necessary
   payload, fenced inside `<UNTRUSTED_DATA>` markers to prevent
   prompt-injection contamination.
3. **Sentry** (if enabled) — only error stack traces and request
   metadata. Lead PII headers are scrubbed before send.

See [`docs/legal/privacy-policy.md`](legal/privacy-policy.md) §6 for
the full sub-processor list.

### Q: Is my data used to train AI models?

No. Gemini API terms specify that customer data is not used for model
training. We do not export your data to any other AI provider.

### Q: Can other people see my data?

No. The pipeline is single-tenant by design
([ADR-001](adr/001-single-tenant-by-design.md)): each deployment has
one operator account. Cross-account access is architecturally
impossible — there's no shared layer that mixes operators' data.

---

## Lead operations

### Q: How do I import leads from a CSV?

Dashboard → drag the `.csv` file anywhere, OR click the **Upload CSV**
button. The system uses Gemini to auto-map your column headers to the
canonical schema, then upserts the rows on `unique_key`.

See [`docs/runbooks/operator-guide.md`](runbooks/operator-guide.md) §3a.

### Q: How do I find new leads from Google Maps?

Dashboard → sidebar → **Deep Discovery**. Enter a query (e.g.
"dentist") and location (e.g. "Mostar, BiH"). Click Start. Takes
30–60 seconds per ~20 results.

See [`docs/runbooks/operator-guide.md`](runbooks/operator-guide.md) §3b.

### Q: Why is one of my leads stuck at "Pending" for hours?

The orchestrator marks long-stuck leads (24+ hours) in
`check_orphans_and_zombies.py`, but doesn't auto-recover them.
Options:

1. Filter to Pending → click **Process All** to re-run the audit
   (idempotent).
2. Or edit the row in Supabase Studio and set `audit_status='Failed'`
   to take it out of the queue.

See [`docs/runbooks/operator-guide.md`](runbooks/operator-guide.md) §7b.

### Q: An audit shows "403 Forbidden". What's going on?

The target site is blocking scrapers. Three usual causes:

- **Cloudflare** or similar bot detection — the audit aborts cleanly
  with `403 Forbidden`. Try Deep Hunt (Playwright) instead — it
  renders the page in a real browser.
- **`robots.txt` disallow** — we honour the broader scrape boundary;
  drop the lead.
- **Geo-block** — Render's egress region may differ from your local
  IP's region.

The lead's `last_error` field has the full exception text.

### Q: Why are some campaign messages empty / generic?

The Gemini draft can be lower-quality when the source lead has sparse
data (no `business_details`, no `pain_points`, no `key_offerings`).
Run **Deep Hunt** on that lead first to enrich it — drafts after
enrichment are dramatically richer.

---

## AI features

### Q: Why did the AI chat give me a wrong answer?

The AI chat (`/ask`) uses Gemini routing to translate your
natural-language question into a typed task. Possible causes for a
wrong answer:

- The question crossed multiple tasks ("audit then export then…") —
  the router picks one. Break it into separate questions.
- The data context Gemini saw was incomplete — only the 5
  fields (`name, company_name, audit_status, seo_score, lead_source`)
  are sent for query-answering tasks. Anything beyond that requires a
  follow-up.
- Hallucination — drafts and insights sometimes invent fields. The
  `test_outreach_hallucination.py` + `test_insights_quality.py` tests
  pin baseline behaviour; if you've found a new failure mode, report
  it.

### Q: Why are AI features (drafts, chat, insights) failing all at once?

Likely a Gemini outage. Check <https://status.cloud.google.com> for
"Generative AI" incidents. See
[`docs/runbooks/incidents.md`](runbooks/incidents.md) §3 for the
mitigation procedure (essentially: pause new bulk jobs, retry once
recovered).

### Q: How do I reduce AI costs?

Three biggest levers, in descending impact:

1. **Filter before acting.** Don't run **Process All** on 1000
   unfiltered leads when you only care about a segment.
2. **Don't bulk-Hunt or run Full Pipeline** unless you need it. Hunt
   is 3–4 Gemini calls per lead; that adds up.
3. **Segment-filter campaigns** before clicking **Generate**.

Detailed cost map:
[`docs/runbooks/operator-guide.md`](runbooks/operator-guide.md) §8.

---

## Performance + reliability

### Q: The dashboard is slow today.

Three quick checks:

1. Hit `/` (the liveness probe) directly. If slow, the backend itself
   is degraded.
2. Check [status.<your-domain>] — current backend / Supabase / 30d
   uptime.
3. Check the Network tab in DevTools. If `/api/proxy/leads` is slow,
   the Supabase pooler may be saturated.

The block-detector middleware logs WARN every time a handler holds the
event loop for > 100ms — those show in Render logs as `slow handler`
events with structured `duration_ms` fields.

### Q: I keep getting "backend unreachable" toasts.

The Next.js proxy returns this when:

- The backend has restarted and the lifespan check is hung
  ([`docs/runbooks/incidents.md`](runbooks/incidents.md) §1.3d).
- Supabase is itself down (Supabase status page is the source of
  truth).
- The deploy chain broke — last commit's status badge is red.

Check the status page first. If the status page shows green, retry in
30 seconds. If the issue persists, email support.

### Q: How often is service expected to be up?

Best-effort 99% monthly uptime. We don't offer a contractual SLA at
the personal-/internal-deployment tier; commercialized plans may
include one.

See [`docs/legal/terms.md`](legal/terms.md) §6.

---

## Billing (when commercialized)

### Q: Where do I see my invoice?

The payment processor (Stripe / LemonSqueezy / Paddle) sends invoices
to your billing email. You can also access them through the processor's
customer portal — the link is in your subscription confirmation.

### Q: I cancelled and want a refund.

Email `support@_(your-domain)_`. We handle refunds case-by-case at
operator discretion. Data export + deletion rights still apply
regardless of payment state.

### Q: Can I get an invoice with my VAT ID on it?

Yes — update your billing details in the payment processor's customer
portal. New invoices include the VAT ID automatically.

---

## Misc

### Q: I'd like a feature that doesn't exist.

Open a GitHub Issue with the `enhancement` label, or email
`support@_(your-domain)_`. The roadmap lives at
[`docs/roadmap.md`](roadmap.md). Feature requests with multiple
upvotes get prioritized.

### Q: I think I found a security issue.

**Don't post it publicly.** Email `security@_(your-domain)_` (or the
contact in [`SECURITY.md`](../SECURITY.md)). We respond within 72h
and ack with a credit + the patch timeline.

### Q: Can I self-host?

The pipeline is open(ish)-source-shaped (single repo, MIT/-style
licence if commercialized that way). Self-hosting requires:

- A Supabase project (free tier OK for personal scale).
- A Google AI Studio API key.
- A Render account (or another container host with Playwright support
  — see [ADR-007](adr/007-render-not-vercel-for-backend.md)).

See [`docs/onboarding.md`](onboarding.md) for the full local setup.

### Q: Where is BookBed.io fit in?

LeadDataScraper is the lead-pipeline core. BookBed.io is a planned
commercialization layer on top — landing page, pricing, payment
processing, multi-customer support. See
[`docs/roadmap.md`](roadmap.md).
