# ADR-006: Google Gemini, not OpenAI / Anthropic, for AI

- **Status:** Accepted
- **Date:** 2026-05-22
- **Deciders:** Operator

## Context

The pipeline depends on an LLM for:

- CSV column mapping (`GeminiMapper`) — every upload, 1 call
- Outreach email + LinkedIn DM drafts (`/draft-outreach`, `/draft-linkedin`) —
  per click, 1 call each
- AI chat natural-language routing (`/ask`) — per submit, 1 call (plus
  downstream calls if the operator confirms a writing task)
- Strategic insights summarisation (`/insights`) — per dashboard view, 1 call
- Per-lead pain-points + email-hook + business-details + contact-details
  summaries (Deep Hunt + Enrichment) — 3–4 calls per lead
- Campaign message generate — per lead per generate, 1 call (in batches of
  hundreds)

Cost matters: a 100-lead campaign generate is 100 calls. A bulk Deep Hunt
on 200 leads is 600–800 calls. The choice of provider directly affects the
monthly bill.

Three candidates:

| Provider | Strengths | Weaknesses |
|---|---|---|
| **OpenAI** (gpt-4o / gpt-4o-mini) | Strongest ecosystem, JSON mode, function calling, broad client library support | Per-token cost is mid; the cheapest tier is still measurably above Gemini Flash |
| **Anthropic** (Claude 3.5 Haiku / Sonnet) | Excellent prose quality, strong refusal behaviour, large context | Cheapest model tier is more expensive than the alternatives at the per-token volume the pipeline runs |
| **Google Gemini** (Flash 1.5 / 2.0) | Cheapest per-token at the price tier the pipeline uses; first-class `response_schema` structured outputs; free tier for dev; large context | Single-vendor; safety filters can refuse benign content in edge cases |

## Decision

**Google Gemini exclusively**, via the `google.genai` Python SDK. Every
AI client constructor (`GeminiMapper`, `AgenticRouter`, `LeadHunter`,
`EnrichmentEngine`) reads `GEMINI_API_KEY` from env in `__init__`. The
prompt boundary is hardened the same way across every call site:

- `response_mime_type='application/json'` + `response_schema` for structured
  outputs.
- `GenerateContentConfig(system_instruction=_UNTRUSTED_DATA_SYSTEM_INSTRUCTION)`
  for any call that mixes static prompt text with DB-derived or scraped
  content.
- The data goes inside `<UNTRUSTED_DATA>...</UNTRUSTED_DATA>` fences. Any
  literal `</UNTRUSTED_DATA>` substring is stripped from the payload
  before embedding — JSON doesn't escape angle brackets, so a controlled
  lead field or page body could otherwise close the fence early.
- Lead fields never splice into prompt body text — placeholders like
  `[COMPANY NAME]` are used instead.

Locked in by `tests/test_prompt_injection_corpus.py` (15-payload corpus
through `fenced_json` + mocked-Gemini router/draft surfaces).

## Consequences

**Positive:**
- **Cost.** `test_ai_cost_budget.py` budgets ≤ $0.50 per 20-lead 100-call
  pipeline at current Flash pricing. Pricing constants pinned at the top of
  the test file. The live test fails CI if the bill drifts past the budget.
- **Free tier for dev.** Onboarding requires no paid plan
  (see [`docs/onboarding.md`](../onboarding.md) §2a).
- **First-class structured outputs.** `response_schema` is part of the
  contract, not bolted-on JSON-mode prompting. `tests/test_json_compliance.py`
  runs 50× per JSON-emitting call site and asserts 100% parse + schema match.
- **Prompt-injection defenses locked in across providers.** The
  fence + system-instruction pattern is portable if we ever switch.
- **Quality bar.** `test_outreach_golden_set.py`,
  `test_linkedin_golden_set.py`, `test_outreach_hallucination.py`, and the
  Gemini-as-judge tests verify the quality floor before model / prompt
  changes ship.

**Negative / trade-offs:**
- **Single-vendor lock-in.** Migrating to OpenAI or Anthropic later would
  require re-tuning every prompt + every structured-output schema. Free-form
  text drafts (outreach, LinkedIn) are largely portable; the
  `response_schema` JSON contracts are not — OpenAI's `response_format` and
  Anthropic's prompt-only JSON mode have different semantics.
- **Safety filters.** Gemini occasionally refuses benign content (a lead
  description that happens to mention a regulated industry, for example).
  Mitigated by `test_refusal_boundaries.py` running 6 known-malicious
  instructions against the router — any `dangerous` classification fails CI,
  catching scope drift either way.
- **SDK churn.** `google.genai` updates frequently. Pinned in
  `requirements.txt` via hashes (ADR-003 chain), but bumping is a deliberate
  event.
- **No streaming-first UX.** Drafts return as a single payload; the
  frontend doesn't progressively render. Acceptable at current per-call
  latency (~3–8 s for a draft).

## When to revisit

- If Gemini Flash pricing diverges 2×+ from the alternatives, re-run the
  cost comparison.
- If `test_refusal_boundaries.py` starts catching legitimate refusals (a
  lead description being refused as the "dangerous" category instead of
  "benign"), the safety-filter calibration may have drifted.
- If a downstream feature genuinely needs multimodal vision (PDF / image
  parsing of lead intake materials), Gemini Pro or one of the multimodal
  alternatives is on the table.
- Multi-provider strategy (Gemini for cheap calls, Claude for quality
  drafts) is the natural successor — at sufficient scale to justify the
  prompt-portability work.

## References

- `src/core/agentic_router.py`
- `src/processors/ai_mapper.py` (GeminiMapper)
- `src/processors/leadhunter.py`
- `src/scrapers/enrichment_engine.py`
- `tests/test_prompt_injection_corpus.py`
- `tests/test_ai_cost_budget.py`
- `tests/test_json_compliance.py`
- `tests/test_refusal_boundaries.py`
- CLAUDE.md → "API Security" → "Any Gemini call that mixes static prompt
  text with DB-derived data or scraped page content must fence the data"
- CLAUDE.md → "AI quality & safety test suite"
