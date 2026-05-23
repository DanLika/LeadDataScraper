# Privacy Policy

> ⚠️ **Draft. NOT lawyer-reviewed.** Run this past competent legal counsel
> for your jurisdiction before publishing it on a customer-facing surface.
> Templates that match GDPR (EU) + CCPA (California) + LGPD (Brazil)
> are available from Iubenda, TermsFeed, and most law firms; pick one
> that fits the operating territory. This file is a starting outline of
> what the policy needs to **cover**, not the legally-binding text.

**Effective date**: _(set on publish)_

**Operator**: _(your legal entity name + address; required for GDPR)_

**Contact for privacy matters**: _(privacy@your-domain — required for
GDPR data-subject requests)_

---

## 1. Who we are

LeadDataScraper is a single-tenant lead-scraping + outreach pipeline
operated by a single human (the "operator"). When deployed as a personal
internal tool, there is no second user. If you are reading this policy
because LeadDataScraper has been commercialized (e.g. as part of
BookBed.io), the operator is the named business above and you are the
subject whose data is processed by them.

## 2. What data we collect

| Category | Source | Examples |
|---|---|---|
| **Operator identity** | Provisioned in Supabase Auth at deploy time | Email address (`OPERATOR_EMAIL` env) |
| **Lead data** | CSV uploads + Google-Maps scrapes initiated by the operator | Business name, website URL, email (when public), phone, address, social-media links, SEO/tech-stack signals, AI-generated pain-point summaries |
| **Usage telemetry** | Backend `/metrics` ingest + Sentry | Web-vitals (CLS / INP / LCP) per page, uncaught error stack traces, request latency, request ID |
| **Operator action history** | Backend writes to `orchestration_jobs` | Job type (audit / hunt / discovery / enrich / pipeline), status, timestamps, filters used |
| **Server logs** | Render captures stdout | Per-request structured JSON: timestamp, level, request ID, route, duration, error context. PII headers (X-API-Key, X-Admin-Token, Cookie) are scrubbed (`backend/main.py::_scrub_sensitive`) |

We do **not** collect:

- Marketing tracking pixels or third-party cookies.
- Browser fingerprints beyond what `navigator.userAgent` exposes by
  default.
- Cross-site behavioural data.
- Voice / video / biometric data.

## 3. Legal basis for processing (GDPR Art. 6)

| Processing | Lawful basis |
|---|---|
| Storing lead data the operator scraped | Legitimate interest — operating the operator's own outreach pipeline. The operator is responsible for the lawfulness of the underlying scrape under local law (GDPR recital 47, ePrivacy directive). |
| Operator authentication | Contractual necessity (operating their account). |
| Error tracking via Sentry | Legitimate interest — service reliability. |
| Performance telemetry | Legitimate interest — service quality. |

If a scraped lead is also a data subject under GDPR (an EU citizen / EU
business contact), the operator acts as a data controller under
Article 14 and is responsible for the Art. 14 notice to the data
subject (or for documenting why an Art. 14(5) exemption applies).
LeadDataScraper does not send that notice on the operator's behalf.

## 4. Why we collect each category

- **Operator identity**: authenticate access; enforce single-tenancy
  ([ADR-001](../adr/001-single-tenant-by-design.md)).
- **Lead data**: the entire product. Scraped + enriched + drafted +
  exported per the operator's workflow.
- **Usage telemetry**: detect performance regressions
  ([`docs/observability.md`](../observability.md)) + reliability
  monitoring ([`docs/alerting.md`](../alerting.md)).
- **Operator action history**: audit trail for the operator's own
  reference, recovery from interrupted background jobs
  ([`backup-verify-deep.yml`](../../.github/workflows/backup-verify-deep.yml)),
  and the GDPR data-export endpoint.
- **Server logs**: operational debugging only. Retained 30 days on
  Render's log retention, then auto-purged.

## 5. Retention

| Data | Retained while | Purged when |
|---|---|---|
| Lead data (`leads`, `campaigns`, `campaign_messages`, `orchestration_jobs`) | Operator account active | Operator invokes `DELETE /operator/account` (GDPR Art. 17), OR the account is sunsetted |
| Account deletion audit row (`account_deletions`) | **30 days** after deletion | Automatically by `src/scripts/purge_expired_audit_log.py` (daily cron in `security.yml`). After 30 days, **no trace of the deletion remains**. |
| Operator email (Supabase Auth user) | Until operator deletes the auth user in Supabase dashboard | Hard delete by Supabase |
| Sentry events | Sentry retention (default 30–90 days depending on plan) | Sentry auto-purge |
| Render logs | Render's default retention (~30 days on starter plan) | Render auto-purge |
| Web-vitals telemetry | 0 days persisted (logged-only; not stored in DB) | Immediate |

## 6. Third parties (sub-processors)

LeadDataScraper passes data to the following third-party services in
the course of normal operation. Each is bound by its own privacy policy
+ data processing terms.

| Service | What it sees | Region | DPA available |
|---|---|---|---|
| **Supabase** (managed PostgreSQL + Auth) | All persisted data (`leads`, `campaigns`, `campaign_messages`, `orchestration_jobs`, `account_deletions`, Auth users) | EU (Frankfurt) when configured — see project setting | <https://supabase.com/legal/dpa> |
| **Google Gemini API** | Lead names, websites, page content during enrichment; CSV column headers during upload mapping; operator's natural-language chat instructions to `/ask` | Google-managed (depends on API version) | <https://cloud.google.com/terms/data-processing-terms> |
| **Render** (hosting) | All backend + frontend traffic + structured logs | US / EU depending on region selection | <https://render.com/legal/dpa> |
| **Google Maps** (via Playwright scrape) | Lead-discovery queries (e.g. "dentist Mostar"); LeadDataScraper sends a query, parses the public results page. No user data is transmitted other than the search query. | Google-managed | N/A — read-only public page access |
| **Sentry** (error tracking, optional) | Stack traces, request metadata, web-vitals values. PII headers + `/upload` request body are scrubbed before send. | Sentry-managed (EU or US per org config) | <https://sentry.io/legal/dpa/> |
| **GitHub** (source + CI) | Source code only. No production data. | GitHub-managed | <https://docs.github.com/en/site-policy/privacy-policies/github-data-protection-agreement> |
| **Discord** (alerting webhook, optional) | Operational alert messages (no lead data) | Discord-managed | <https://support.discord.com/hc/en-us/sections/115001225394> |

If you are an EU data subject and your data lives in a non-EU
sub-processor's region, the operator relies on the Standard
Contractual Clauses (SCCs) attached to each sub-processor's DPA as the
transfer mechanism (GDPR Art. 46).

## 7. Your rights (GDPR Art. 15–22 + CCPA equivalents)

| Right | How to exercise |
|---|---|
| **Access** (Art. 15) | `GET /operator/data-export` returns a ZIP with leads, campaigns, messages, and a JSON audit log of operator actions. Rate-limited to 1/day. |
| **Portability** (Art. 20) | Same endpoint — output is CSV + JSON, both machine-readable formats. |
| **Erasure** (Art. 17, "right to be forgotten") | `DELETE /operator/account` with confirmation phrase. A 30-day audit row is retained for fraud / contested-deletion windows; after 30 days, **no trace of the deletion remains**. |
| **Rectification** (Art. 16) | Edit data directly through the dashboard (lead detail panel) or contact the operator. |
| **Restriction** (Art. 18) | Pause the operator's account by setting `OPERATOR_EMAIL` unset OR scaling the backend to 0 instances; documented in `docs/runbooks/incidents.md` §4.3a (STOP WRITES). |
| **Objection** (Art. 21) | Contact the operator. |
| **Withdraw consent** (Art. 7) | Where processing is based on consent (not the case for any current LeadDataScraper processing — all is legitimate-interest or contract). |
| **Complaint** (Art. 77) | Lodge with your supervisory authority. For BiH: AZLP. For broader EU: the data protection authority of your habitual residence. |

For all rights except erasure + export, contact the operator at the
email above. Response within **30 days** per GDPR Art. 12(3).

## 8. Children

LeadDataScraper is a B2B operations tool. We do not knowingly collect
data from anyone under 16. If you believe a minor's data has ended up
in the system, contact the operator and we will erase it.

## 9. Security

The operator runs the pipeline behind multiple layers of security gating
documented in [`CLAUDE.md`](../../CLAUDE.md) (API key + Origin gate +
RLS deny-all on tables + service-role-only backend access + SSRF
defense + CSV-injection guard + audit trail). Incident response
procedure: [`docs/runbooks/incidents.md`](../runbooks/incidents.md).

If we become aware of a personal-data breach affecting EU subjects, we
notify the supervisory authority within **72 hours** per GDPR Art. 33
and affected subjects without undue delay per Art. 34.

## 10. Cookies

The operator-facing dashboard sets:

- **Supabase Auth session cookies** — `HttpOnly`, `Secure`, `SameSite=Lax`,
  required to maintain the operator's signed-in session. No other cookies
  are set. No tracking cookies. No third-party cookies.

## 11. Changes to this policy

We notify the operator (and, if commercialized, all users) at least
30 days before substantive changes via email + dashboard banner.
Backwards-incompatible changes (new third-party sub-processor, new data
category) require fresh consent or a renewed legitimate-interest balance
test.

Past versions of this file live in git history at
<https://github.com/_(repo)_/commits/main/docs/legal/privacy-policy.md>.

## 12. Contact

For privacy questions or to exercise any of the rights in §7:

- **Email**: _(privacy@your-domain)_
- **Postal address**: _(if required by jurisdiction)_
- **Data Protection Officer**: _(if applicable; required for GDPR if the
  operator falls under Art. 37 designation criteria)_

---

> 🚨 **Reminder**: this file is a structural template. It enumerates
> the topics a real policy must cover. The **wording** must be tailored
> to your jurisdiction, your operating model, and your sub-processor
> footprint by a lawyer who knows your situation. Templates from
> reputable vendors (Iubenda, TermsFeed, GetTerms) are a better
> starting point than this draft for the actual prose.
