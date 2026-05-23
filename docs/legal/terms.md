# Terms of Service

> ⚠️ **Draft. NOT lawyer-reviewed.** A Terms of Service is a contract.
> Have one drafted (or at minimum reviewed) by counsel licensed in your
> operating jurisdiction before publishing on a customer-facing surface.
> Vendor templates (Iubenda, TermsFeed, GetTerms, plus the GitHub
> CONTRIBUTOR-friendly [Pivotal Terms](https://pivotalterms.com/)) are
> available — pick one that's been litigated in your country.

**Effective date**: _(set on publish)_

**Operator**: _(your legal entity name + jurisdiction)_

**Contact**: _(legal@your-domain)_

---

## 1. Acceptance

By accessing LeadDataScraper or any service that bundles it (including,
when commercialized, BookBed.io and any FlutterFlow-marketplace
template that embeds it), you agree to these Terms. If you do not
agree, do not use the service.

## 2. The service

LeadDataScraper is a single-operator lead-scraping + AI-enrichment
pipeline. Operator-facing features include CSV/Maps lead discovery,
SEO audit, contact extraction (Deep Hunt), outreach draft generation,
campaign management, and exports. Documentation:
[`docs/runbooks/operator-guide.md`](../runbooks/operator-guide.md).

## 3. Eligibility + accounts

- You must be at least 18 (or the age of majority in your jurisdiction).
- You must have authority to enter this contract on behalf of any
  entity you represent.
- The single-operator design ([ADR-001](../adr/001-single-tenant-by-design.md))
  means one human account per deployment. Sharing credentials violates
  these Terms.
- You are responsible for keeping `OPERATOR_EMAIL`, `API_SECRET_KEY`,
  `ADMIN_TOKEN`, Supabase Auth password, and all sub-processor API
  keys confidential. Loss / exposure triggers the rotation procedure
  in [`docs/runbooks/incidents.md`](../runbooks/incidents.md) §5.

## 4. Acceptable use

You may use the service to:

- Scrape publicly accessible business directories (Google Maps), public
  websites, and your own CSV imports.
- Enrich, score, and draft outreach for the leads you've collected.
- Export your own data via `GET /operator/data-export`.

You may **not** use the service to:

- Scrape sites in violation of their `robots.txt`, ToS, or applicable
  CFAA / Computer Misuse Act statutes.
- Send unsolicited bulk email or DMs at scale beyond what
  jurisdiction-specific anti-spam law (CAN-SPAM, CASL, GDPR Art. 6 +
  ePrivacy) permits. The pipeline drafts — **you send**, and **you bear
  the legal risk** of the send.
- Process special-category data (GDPR Art. 9: race, ethnicity, health,
  sexual orientation, religion, trade-union membership, biometric,
  genetic) through the pipeline without a separate explicit basis.
- Process children's data (under 16) intentionally.
- Reverse-engineer, decompile, or attempt to extract source code, model
  weights, or prompt content from the pipeline beyond what
  fair-use / fair-dealing exceptions permit in your jurisdiction.
- Resell, white-label, or sublicense the pipeline without written
  agreement.
- Attempt to bypass rate limits, the X-Admin-Token gate, or the
  destructive-confirmation gate (`DELETE /operator/account`).

## 5. Operator content

You retain ownership of every CSV you upload + every lead you scrape.
LeadDataScraper acquires a limited license to your content **only as
necessary to operate the service** — store it in Supabase, run AI
enrichment via Gemini, build outreach drafts, export back to you.

We **do not** train AI models on your data. Gemini's API terms govern
their separate processing — see
[`docs/legal/privacy-policy.md`](privacy-policy.md) §6.

## 6. Service availability + SLA

This is **best effort, not contractual SLA**, unless a separate paid
agreement says otherwise. Synthetic-monitor + cost-monitor +
status-page infrastructure are documented in
[`docs/alerting.md`](../alerting.md) and `docs/status-page-setup.md`.

The operator commits to:

- Best-effort 99% monthly uptime.
- Incident response per
  [`docs/runbooks/incidents.md`](../runbooks/incidents.md) severity
  tiers (SEV-1 immediate, SEV-2 within an hour, SEV-3 within a workday).
- 24h initial response on `support@your-domain`
  ([`docs/support-process.md`](../support-process.md)).

We do not guarantee that AI features (drafts, chat, insights, contact
enrichment) will be available during third-party Gemini outages — see
[`docs/runbooks/incidents.md`](../runbooks/incidents.md) §3.

## 7. Fees

_(Required when commercialized; remove for the personal deployment.)_

- Pricing: see <https://your-domain/pricing>.
- Payment processor: _(Stripe / LemonSqueezy / Paddle)_. Invoices issued
  by the payment processor.
- Refunds: case-by-case at operator discretion; data-export + deletion
  rights apply regardless of payment status.
- Tax: prices are pre-tax unless explicitly stated; you are responsible
  for any tax owed in your jurisdiction.

## 8. Termination

You may terminate at any time via `DELETE /operator/account` with the
required confirmation phrase. A 30-day audit row is retained
([`docs/legal/privacy-policy.md`](privacy-policy.md) §5).

The operator may suspend or terminate your account on:

- Material breach of these Terms (incl. acceptable-use violations).
- Non-payment (when commercialized).
- Legal compulsion (court order, regulatory action).
- Service shutdown — with 30 days' notice + a final export window.

## 9. Disclaimers

THE SERVICE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO MERCHANTABILITY, FITNESS FOR
A PARTICULAR PURPOSE, AND NON-INFRINGEMENT. To the maximum extent
permitted by applicable law, the operator disclaims all warranties.

In particular: **AI-generated outreach drafts may contain factual
errors, hallucinations, or off-tone phrasing**. The operator is not
liable for any sent message that turns out to be wrong, offensive, or
counter-productive. Always review drafts before sending.

## 10. Limitation of liability

To the maximum extent permitted by applicable law, the operator's
total cumulative liability for all claims arising from your use of the
service is limited to the greater of:

- **EUR 100**, or
- the fees paid by you to the operator in the **12 months** preceding
  the claim.

Neither party is liable for indirect, consequential, incidental,
special, punitive, or exemplary damages. Some jurisdictions don't
allow these limitations; in those jurisdictions, liability is limited
to the maximum extent permitted by law.

## 11. Indemnification

You agree to indemnify and hold harmless the operator from any claim
arising out of (a) your violation of these Terms, (b) the lawfulness
of any data you import into the service, or (c) the lawfulness of any
outreach message you send using drafts the service produced.

## 12. FlutterFlow / marketplace extensions

If this service is being delivered to you bundled inside a third-party
template (e.g. a FlutterFlow marketplace template that calls
LeadDataScraper APIs):

- These Terms apply to your use of the LeadDataScraper portion.
- The FlutterFlow marketplace seller's terms apply to the template
  bundling.
- Conflicts resolve in favour of the user's right to data export +
  erasure (which can never be contractually waived under GDPR / CCPA).

The marketplace seller is responsible for:

- Disclosing that LeadDataScraper is a sub-processor in their own
  privacy notice.
- Passing on the data-export + erasure rights to their end users.
- Maintaining an up-to-date attribution + license file shipping with
  the template.

## 13. Governing law + dispute resolution

These Terms are governed by the laws of _(your jurisdiction)_. Disputes
will be resolved in the courts of _(your jurisdiction)_, except where
local consumer-protection law requires otherwise.

For EU subjects: nothing in this section prevents you from bringing a
claim before the courts of your habitual residence (Brussels Ia
Regulation Art. 18).

## 14. Changes

We notify the operator (and, if commercialized, all users) at least
30 days before substantive changes via email + dashboard banner. If
you don't agree to the change, your remedy is termination per §8.

## 15. Contact

- General + legal: _(legal@your-domain)_
- Privacy / GDPR: _(privacy@your-domain)_
- Support: _(support@your-domain)_ — see
  [`docs/support-process.md`](../support-process.md).

---

> 🚨 **Reminder**: don't ship this verbatim. It enumerates the topics a
> real ToS must cover and the structure most ToS use, but the wording
> + jurisdiction + liability caps + acceptable-use must be tuned by
> counsel to your situation. Pay $200 for a template review; it pays
> back the first time anyone challenges anything.
