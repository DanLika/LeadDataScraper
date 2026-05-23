# Email deliverability — DNS + Resend setup

**Sending domain:** `mail.leaddatascraper.com`
**Provider:** Resend
**Status:** plan-only (no DNS set, no Resend account provisioned yet)
**Driven by:** roadmap item 13.5 (dogfood prep, see `docs/roadmap.md`)
**Cost:** ~$10–15/yr (domain registration) + $20/mo Resend (first 50k emails)

This document is **the operator runbook** for setting up authenticated
email sending for LDS outreach. Phase: prep. **Do NOT wire
`email_sender.py` to live campaigns until every check in this doc
passes.** Sister doc for the wiring plan: `docs/email-dispatch-architecture.md`.

---

## 0 — Prerequisites (do these first)

### 0.1 Register the parent domain

`mail.leaddatascraper.com` is a subdomain of `leaddatascraper.com`. The
parent domain must be **registered and DNS-controllable** before any of
the records below can be set.

- Check availability: any registrar (Namecheap, Cloudflare, Porkbun,
  Gandi). Cloudflare is recommended — at-cost renewal, free DNSSEC, no
  upsells.
- Expected cost: $10–15/yr for `.com`.
- If `leaddatascraper.com` is already taken by a third party, pick a
  different parent domain and update every reference in this doc + in
  `docs/secret-inventory.md` + in the eventual Resend dashboard
  configuration.
- DNSSEC: enable at the registrar (one click on Cloudflare). Protects
  the DKIM/SPF/DMARC records from spoofed-resolver attacks.

### 0.2 Create a monitored mailbox for DMARC reports

DMARC reports (RUA/RUF) go to a mailbox you control. Pick one of:

- `dmarc@leaddatascraper.com` — needs a mailbox on the parent domain.
  Cheapest route: Google Workspace Business Starter ($6/mo) or FastMail
  ($3/mo) on `leaddatascraper.com`.
- Forward to an existing inbox: register `leaddatascraper.com` at
  Cloudflare and use Cloudflare Email Routing (free) to forward
  `dmarc@leaddatascraper.com` → operator's real inbox.

**Why this matters:** DMARC `p=none` (the ramp's starting position) is
*report-only* — without a monitored RUA inbox, the entire DMARC
investment is invisible. The reports tell you which IPs are sending
"as you" — that's how you find legitimate forwarders (Google Groups,
mailing lists) and shady spoofers before tightening to `quarantine`.

DMARC reports are XML, often hundreds per week. Use a free analyzer
inbox-side: dmarcian (free tier 1k reports/mo), Postmark DMARC Digests,
or self-host parsedmarc. **Required** — raw XML is not human-readable
at scale.

### 0.3 Provision Resend

1. Sign up at `https://resend.com` with operator email.
2. **Region**: EU (Frankfurt) — closest to expected operator + recipient
   audience (Croatia/BiH dogfood). Reduces latency and keeps PII in EU
   jurisdiction.
3. Plan: start on free tier (3000/mo, 100/day) to validate flow. Upgrade
   to Pro ($20/mo, 50k/mo) once dogfood proves out.
4. Enable two-factor auth on the Resend account.
5. Create an API key scoped to **sending only** (no domain management,
   no team admin). Store in backend `.env` as `RESEND_API_KEY`. Add row
   to `docs/secret-inventory.md` (rotation cadence: quarterly).
6. **Add the sending domain via Resend dashboard**: "Domains" → "Add
   domain" → enter `mail.leaddatascraper.com`. Resend's wizard will
   generate the exact DKIM CNAMEs you need.

---

## 1 — DNS records to set

⚠️ **DKIM values are per-Resend-account.** Do NOT copy the placeholder
strings below verbatim — go to Resend dashboard after adding the
domain and copy the three CNAMEs Resend generated *for your account*.
The DKIM keys, selectors, and target hostnames are all account-scoped.

All records below are set on the **parent zone**
(`leaddatascraper.com`), even though the sending subdomain is
`mail.leaddatascraper.com`. The "host" / "name" column shows what to
enter at the DNS provider — most providers accept either the full FQDN
or the relative label (left-of-zone).

### 1.1 SPF (Sender Policy Framework)

```
Type:  TXT
Host:  mail.leaddatascraper.com
Value: v=spf1 include:_spf.resend.com ~all
TTL:   3600
```

Notes:
- `~all` = soft-fail (recommended during ramp); switch to `-all` once
  dogfood is stable (~30 days).
- Do **not** set a `v=spf1` record on the parent `leaddatascraper.com`
  unless that zone also sends mail. Only the sending subdomain needs
  SPF.

### 1.2 DKIM (DomainKeys Identified Mail)

Resend generates **three CNAMEs** when you add the domain. They look
like:

```
Type:  CNAME
Host:  resend._domainkey.mail.leaddatascraper.com
Value: <opaque value Resend gives you>.dkim.resend.com
TTL:   3600
```

```
Type:  CNAME
Host:  <selector-2>.<scope>._domainkey.mail.leaddatascraper.com
Value: <opaque>.dkim.resend.com
TTL:   3600
```

```
Type:  CNAME
Host:  <selector-3>.<scope>._domainkey.mail.leaddatascraper.com
Value: <opaque>.dkim.resend.com
TTL:   3600
```

Notes:
- Resend rotates DKIM keys behind the CNAME — that's why CNAMEs are
  used instead of static TXT keys. Set them once and they stay valid
  through Resend's rotations.
- DKIM verification at Resend dashboard typically completes within
  30 minutes of DNS propagation. The dashboard shows a green check
  when verified.

### 1.3 DMARC (Domain-based Message Authentication, Reporting & Conformance)

**Day 1 record** (report-only mode — the ramp starts here):

```
Type:  TXT
Host:  _dmarc.leaddatascraper.com
Value: v=DMARC1; p=none; pct=100; rua=mailto:dmarc@leaddatascraper.com; ruf=mailto:dmarc@leaddatascraper.com; fo=1; adkim=r; aspf=r
TTL:   3600
```

DMARC is set on the **parent domain**, not the subdomain — DMARC is
inherited downward. A subdomain DMARC record would override but is
not needed.

**Ramp schedule** — adjust the `p=` and `pct=` values over ~6 weeks
based on RUA reports:

| Week | Policy | pct | Rationale |
|------|--------|-----|-----------|
| 0-2  | `p=none` | 100 | Monitor only. Inspect RUA reports for false-positives (legit forwarders, mailing lists). |
| 3    | `p=quarantine` | 25 | Quarantine 25% of failures. If RUA shows zero legit IPs failing, proceed. |
| 4    | `p=quarantine` | 50 | Ramp pct. |
| 5    | `p=quarantine` | 100 | All non-aligned mail to junk folder. |
| 6+   | `p=reject` | 100 | Reject at SMTP. Only flip when 2 weeks of `p=quarantine pct=100` reports show clean. |

**Anti-fast-forward warning**: Do not jump to `p=quarantine` or
`p=reject` on day 1. Mistakes here cause **all legitimate mail** —
including operator's own personal-inbox replies if they forward
through Google Groups, internal forwarders, etc. — to silently
disappear. Two weeks of `p=none` reports is the minimum. dmarcian's
free tier is sufficient for parsing.

**`fo=1`** = generate forensic reports (RUF) for any auth failure
(default `fo=0` only reports DKIM+SPF dual-fail). More signal.

**`adkim=r`, `aspf=r`** = relaxed alignment. Strict alignment
(`adkim=s`) would require the DKIM `d=` to exactly match `Header-From`
domain — overkill for outbound-only and breaks Resend's default
`d=mail.leaddatascraper.com` setup if you ever change the
`Header-From` value.

### 1.4 Return-Path / MAIL FROM (bounce handling)

Resend sets this automatically via its own infrastructure — no DNS
record needed on operator side beyond the SPF + DKIM above. Bounces
are surfaced via:
- Resend dashboard "Logs" tab
- Webhook events (`email.bounced`, `email.delivered`, etc.) — wiring
  plan in `docs/email-dispatch-architecture.md`

### 1.5 MX records — NOT NEEDED for outbound-only

`mail.leaddatascraper.com` has no MX record. Replies to outreach emails
should be sent to an operator-controlled inbox (e.g. `outreach@bookbed.io`
or operator's personal address), not to the sending domain. The
`Reply-To` header on outbound messages will direct replies there.

If you later want to accept replies on the same domain (e.g. for
reply-tracking webhooks), set up MX → Resend's inbound parser at that
point — out of scope for the deliverability ramp.

---

## 2 — Validation

### 2.1 Resend dashboard verification

After setting DNS, wait 5–30 min for propagation, then in Resend
dashboard:
- "Domains" → `mail.leaddatascraper.com` → all three of SPF / DKIM /
  Custom Return-Path show ✅ green.
- If still pending after 1 hour, use `dig` to confirm propagation:
  ```
  dig TXT mail.leaddatascraper.com +short
  dig CNAME resend._domainkey.mail.leaddatascraper.com +short
  dig TXT _dmarc.leaddatascraper.com +short
  ```

### 2.2 mail-tester.com (target 10/10)

1. Visit `https://www.mail-tester.com` — get a one-time `test-<id>@srv1.mail-tester.com` address.
2. From Resend dashboard → "Emails" → "Send test" — send a real test
   message (with non-trivial body, not "test 123") to the mail-tester
   address.
3. Click "Then check your score" — target **10/10**.

Common deductions and fixes:
- **-1.0 missing DMARC**: DNS not propagated yet, or `_dmarc` record
  on wrong host (it goes on the parent, not subdomain).
- **-1.0 unsigned**: DKIM CNAMEs not yet picked up by Resend. Wait,
  then re-send.
- **-0.5 SpamAssassin URIBL**: linked domain on a blocklist. If the
  test message linked to `bookbed.io` and that domain is clean, this
  shouldn't fire — usually it's caused by a URL shortener.
- **-0.x HTML/text mismatch**: include a `text/plain` part alongside
  `text/html`. `email_sender.py` already does this — verify.
- Anything < 9.0 — fix before sending real outreach.

### 2.3 Gmail / Outlook seed tests

Send to operator's own Gmail + Outlook addresses. Confirm:
- Arrives in **Inbox** (not Spam, not Promotions tab in Gmail).
- Gmail "show original" header view shows: `SPF: PASS`, `DKIM: PASS`,
  `DMARC: PASS` (all three, all green).
- Outlook's "View message source" shows `Authentication-Results:` with
  the three pass entries.

If any of the three fail in real-world inbox testing, do NOT proceed
to live outreach until resolved — production deliverability will be
worse than the mail-tester score suggests.

---

## 3 — Propagation expectations

| Provider      | Typical | Max  |
|---------------|---------|------|
| Cloudflare    | 1-5 min | 1h   |
| Namecheap     | 5-30 min| 24h  |
| Google Domains| 5-30 min| 24h  |
| GoDaddy       | 30-60min| 48h  |

`dig +short` resolves cached values immediately on cold-start; if you
have a record cached at 3600 TTL elsewhere (corporate DNS,
home-router), wait for that TTL to expire OR `dig @8.8.8.8` against a
public resolver to bypass.

---

## 4 — Operational notes

### 4.1 Subdomain isolation

Sending from `mail.leaddatascraper.com` (not the root `leaddatascraper.com`)
isolates **reputation**. If outreach gets reported as spam, the parent
domain's reputation — used for any future marketing site, transactional
emails, login-confirmation flows — is untouched. Cold-outreach
deliverability is inherently riskier than transactional; isolation is
the standard pattern.

If LDS-driven outreach reputation is later excellent (low bounce, low
spam-report) you could consider sending from the root for higher trust
— but the upside is small and the downside is total reputation loss on
a single bad campaign.

### 4.2 List-Unsubscribe headers (RFC 8058)

`email_sender.py` should set `List-Unsubscribe` and
`List-Unsubscribe-Post: List-Unsubscribe=One-Click` on every outbound
message. Gmail enforces this for senders > 5000/day; mandatory in
practice even at low volume. Wiring detail in
`docs/email-dispatch-architecture.md`.

### 4.3 Warm-up

Resend handles IP warm-up automatically on shared IPs (Pro plan). On
dedicated-IP plans (not chosen here), you'd ramp send volume from
~50/day to 50k over 4 weeks. Not applicable for dogfood scope.

### 4.4 Bounce + spam-report handling

Resend tracks both. Backend should mark `campaign_messages` for hard
bounces as `status='bounced'` (already a CHECK constraint allowlist
value) and never retry. Spam reports should add the recipient to a
suppression list and skip future campaigns. Wiring plan in
`docs/email-dispatch-architecture.md`.

### 4.5 GDPR / privacy

- Recipient email is **PII** under GDPR (Croatia, recipient
  jurisdiction).
- Outreach must offer one-click opt-out (List-Unsubscribe) and honor it
  within 10 business days (CAN-SPAM) — Resend's suppression list
  handles this if you wire the webhook.
- Privacy policy at `docs/legal/privacy-policy.md` must mention the
  email-sending lawful basis (legitimate-interest with opt-out) and
  retention period for opt-out records (indefinite).

---

## 5 — Step-by-step setup checklist

```
[ ] Register leaddatascraper.com at Cloudflare (or chosen registrar)
[ ] Enable DNSSEC at registrar
[ ] Create dmarc@leaddatascraper.com mailbox or forward
[ ] Sign up at resend.com, EU region, 2FA on
[ ] Create RESEND_API_KEY (sending-only scope)
[ ] Resend dashboard: Add domain mail.leaddatascraper.com → copy 3 DKIM CNAMEs
[ ] Set SPF TXT record on mail.leaddatascraper.com
[ ] Set 3 DKIM CNAME records (values from Resend dashboard, NOT this doc)
[ ] Set DMARC TXT record on _dmarc.leaddatascraper.com (p=none day 1)
[ ] Wait 30 min, run dig checks
[ ] Resend dashboard: all 3 checks green
[ ] mail-tester.com → target 10/10
[ ] Gmail seed test: SPF + DKIM + DMARC all pass, inbox (not Promotions)
[ ] Outlook seed test: all three pass
[ ] Add RESEND_API_KEY to docs/secret-inventory.md (quarterly rotation)
[ ] Sign up at dmarcian (or chosen analyzer) for RUA report parsing
[ ] Calendar reminder: week 3 — flip DMARC p=quarantine pct=25
[ ] Calendar reminder: week 6 — flip DMARC p=reject (after 2 weeks clean)
```

Only after every box checked does the wiring plan in
`docs/email-dispatch-architecture.md` become safe to execute.
