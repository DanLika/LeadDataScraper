# Support Process

Today: single-operator = you. Tomorrow (if commercialized): structured
ticket flow, SLA, escalation. This doc captures the structure so when
you scale to a second human (contractor / hire / community moderator),
the process is documented and ready, not improvised.

---

## 1. Channels

| Channel | Purpose | Response target |
|---|---|---|
| **support@_(your-domain)_** | Primary inbound. Auto-replies with ETA + doc links. Routes to GitHub Issues. | **24h initial response** |
| **GitHub Issues** (label `support`) | Authoritative ticket tracker. One issue per request. | Per-issue SLA below. |
| **Discord `#alerts`** | Operator-facing only. Not customer-facing. | — |
| **Status page** at `status.<your-domain>` | Customer-facing health. Auto-updated from synthetic monitor. | Real-time. |

Single-operator phase: you check `support@` once a day. Auto-reply
sets the 24h expectation honestly. After 6 months at single-operator
volume, evaluate whether to graduate to Intercom / Plain / Crisp / Help
Scout.

---

## 2. Auto-reply template

Configure on the inbox provider (Gmail / Fastmail / Postmark / Mailgun
inbound):

```
Subject: Re: <original subject>

Hi,

Thanks for reaching out about <product>. We've received your message
and will respond within 24 hours.

While you wait, you might find an answer here:
  • FAQ:           https://<your-domain>/docs/faq
  • Operator guide: https://<your-domain>/docs/operator-guide
  • Status page:    https://status.<your-domain>

Privacy / GDPR requests:
  • Export your data: dashboard → Settings → "Download my data"
  • Delete your account: dashboard → Settings → Danger Zone
  • Or email privacy@<your-domain> for human assistance

— <operator name>
```

Honest expectations beat polished promises. 24h with an actual reply
beats "instant" with a robotic fob-off.

---

## 3. SLA tiers

Match the severity tiers in
[`docs/runbooks/incidents.md`](runbooks/incidents.md) §severity-tiers.

| Severity | Examples | Initial response | Resolution target |
|---|---|---|---|
| **SEV-1** | Service down for a customer, data loss, security incident, billing failure preventing access | **1h** | **4h** |
| **SEV-2** | One feature broken, partial degradation, AI feature unavailable | **4h** | **next workday** |
| **SEV-3** | Question, feature request, cosmetic issue, doc gap, "how do I…" | **24h** | **best effort within 7d** |

Single-operator caveat: resolution targets are aspirational, not
contractual, until a paid plan offers a higher-tier SLA. The auto-reply
sets the only contractual expectation.

---

## 4. Ticket lifecycle

```
                    customer@inbound
                            │
                            ▼
                   ┌────────────────┐
                   │ support@ inbox │  ← auto-reply within 1 min
                   └────────┬───────┘
                            │ operator opens
                            ▼
                   ┌────────────────┐
                   │  triage (5min) │  ← assess SEV, link prior tickets
                   └────────┬───────┘
                            │
                            ▼
              ┌─────────────────────────────┐
              │ open GitHub Issue           │  ← single source of truth
              │  • label: support           │
              │  • label: sev-1 / sev-2 / 3 │
              │  • assignee: operator       │
              │  • title: TLDR              │
              └─────────────┬───────────────┘
                            │
              ┌─────────────┴───────────────┐
              │ Reply to customer with      │
              │ acknowledgement + link to   │
              │ the public ticket (if any). │
              │ Set realistic ETA.          │
              └─────────────┬───────────────┘
                            │
                            ▼
                   ┌────────────────┐
                   │ work the issue │
                   └────────┬───────┘
                            │
                ┌───────────┴───────────┐
                ▼                       ▼
       fix + ship + tell        request more info /
       customer it's done       wait on customer
                │                       │
                └───────────┬───────────┘
                            ▼
                   close issue + ask
                   for 1-line follow-up
                   (was the fix right?)
```

Issues stay open until the customer confirms (or 7 days of silence,
documented).

---

## 5. Internal templates

Save these as GitHub Issue templates under
`.github/ISSUE_TEMPLATE/` (out of scope for this doc, but suggested):

### `support-bug.md`
```yaml
labels: [support]
body:
  - heading: "Describe the bug"
  - heading: "Reproduction steps"
  - heading: "Expected behavior"
  - heading: "Actual behavior"
  - heading: "Environment (browser, OS, time of failure)"
  - heading: "Customer email (private — operator-only)"
  - heading: "Severity proposal"
```

### `support-request.md`
```yaml
labels: [support]
body:
  - heading: "What does the customer want?"
  - heading: "Current behavior (if applicable)"
  - heading: "Customer email"
  - heading: "Severity proposal"
```

### `support-billing.md`
```yaml
labels: [support, billing]
body:
  - heading: "What's the billing question?"
  - heading: "Stripe / LemonSqueezy customer ID (if known)"
  - heading: "Customer email"
```

---

## 6. Common ticket types + canned response patterns

### "How do I export my data?"

```
Hi <name>,

You can self-serve your data export through the dashboard:

  Settings → My Data (GDPR) → "Download my data"

The export is a ZIP containing CSV files for leads, campaigns,
messages, and a JSON audit log of operator actions. Rate-limited to
1 per day.

If anything is missing or unclear in the export, let me know which
field — happy to dig in.

— <operator>
```

### "I want to delete my account"

```
Hi <name>,

You can self-serve account deletion via the dashboard:

  Settings → Danger Zone → "Delete my account"

You'll be asked to type "DELETE MY ACCOUNT" to confirm. Once
confirmed:

  • All your leads, campaigns, messages, and orchestration jobs are
    immediately erased.
  • A 30-day audit row is kept for fraud-prevention purposes (no
    business data, just operator email + timestamp + row counts).
  • After 30 days, the audit row is auto-purged. No trace remains.

Confirmed?

— <operator>
```

### "Something's broken / down"

```
Hi <name>,

Thanks for reporting. I can see your symptoms on my end too — we're
investigating.

I've opened internal ticket <#issue-id> and will update here when:
  1. The fix is in progress (next update: <time>)
  2. The fix is rolled out (you'll get a separate email)

You can also follow the public status page at
https://status.<your-domain> for real-time updates.

— <operator>
```

### "I have a feature request"

```
Hi <name>,

Thanks! Feature requests are great signal even when we can't ship
them immediately. I've logged yours as <#issue-id> with the
"enhancement" label.

I prioritize based on:
  • How many users have asked for the same thing
  • Whether it unlocks a workflow that's currently blocked vs. just
    nicer
  • Implementation cost

I'd love to know:
  • What problem you're trying to solve (vs. the specific fix you're
    proposing) — sometimes there's an existing workaround.
  • How often you'd use it.

— <operator>
```

---

## 7. Knowledge base

Top-level entry points the customer can find without contacting
support:

- [`docs/faq.md`](faq.md) — top-10 questions.
- [`docs/runbooks/operator-guide.md`](runbooks/operator-guide.md) —
  every dashboard workflow.
- [`docs/legal/privacy-policy.md`](legal/privacy-policy.md) — privacy +
  GDPR rights.
- Status page — service health in real time.

Surface these in the auto-reply (step 2).

---

## 8. Escalation

Single-operator phase: you are the operator + L1 + L2 + L3 + on-call.

When you graduate (more than ~30 minutes/week on support work, or a
second human is involved):

```
L1 — front-line (Plain / Intercom / Help Scout volunteer)
  ↓ ack within 1h, attempt canned-response resolution
L2 — operator
  ↓ open ticket, work the fix
L3 — escalate externally (lawyer / payment provider / Render support / Supabase support)
```

Document the L1 → L2 hand-off rule:

> "Escalate to L2 immediately if: (a) the customer reports data loss
> or privacy violation, (b) the issue affects more than 5 customers
> simultaneously (look for the same symptom in
> `support@inbox` within the last 2 hours), (c) the canned-response
> doesn't fit and the volunteer doesn't have a clear next action."

---

## 9. Metrics worth tracking (once you're commercialized)

Don't track these in the single-operator phase — overhead exceeds
value. Once volume justifies:

- **Ticket volume per week**, segmented by SEV.
- **First-response time** (target: P50 < 4h, P95 < 24h).
- **Time-to-resolution** by SEV.
- **CSAT** — single-question follow-up: "Was this resolved well?
  1 / 2 / 3 / 4 / 5."
- **Top 5 ticket categories** by volume — drives FAQ + UX improvements.

When you hit ~20 tickets / week, look at Plain or Intercom for
structured tooling — GitHub Issues will start to chafe.

---

## 10. When to wire to Intercom / Plain / etc.

Triggers to graduate beyond GitHub Issues + email:

- > 20 inbound/week sustained for 4+ weeks.
- A second human is on the support rota.
- Customers ask for chat (vs. email).
- Need to track customer accounts as first-class entities (not just
  email addresses).

Plain (<https://plain.com>) is the typical step from "GitHub Issues +
auto-reply" because it stays minimal. Intercom is the next step beyond
that. Help Scout is the email-flavored alternative. Compare prices +
GDPR posture before picking; document the choice in a new ADR.

---

## References

- [`docs/runbooks/incidents.md`](runbooks/incidents.md) — incident
  response (SEV-1 procedure)
- [`docs/runbooks/operator-guide.md`](runbooks/operator-guide.md) —
  what the operator can self-serve
- [`docs/faq.md`](faq.md) — pre-emptive answers for the auto-reply
- [`docs/legal/privacy-policy.md`](legal/privacy-policy.md) — GDPR
  rights handled by self-serve endpoints
