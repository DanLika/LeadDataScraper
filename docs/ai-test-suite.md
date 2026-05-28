# AI quality & safety test suite

Extracted from `CLAUDE.md` (2026-05-26 shrink; original ~164k chars). Restored to docs/ to keep CLAUDE.md under the harness threshold without losing content.

- **AI quality & safety test suite** (offline + live tiers under `tests/`):
  - **Offline (CI-default, no GEMINI_API_KEY needed)**:
    - `test_prompt_snapshots.py` — "prompts are code" guardrail. 8 Gemini
      call sites, SHA256-hashed in `tests/fixtures/prompt_snapshots.json`.
      Any drift forces an intentional review; regenerate baseline with
      `UPDATE_PROMPT_SNAPSHOTS=1 pytest tests/test_prompt_snapshots.py`.
    - `test_endpoint_hardening.py` — every authed endpoint × 7 concerns
      (missing/wrong API key, empty body, extra fields, max-length+1,
      adversarial Unicode/NUL/zero-width/RTL/emoji, rate-limit boundary,
      admin-token guard on `DELETE /leads/clear`). `httpx.AsyncClient` +
      `ASGITransport`; ~170 assertions in 1.1s. Fresh app per test class
      so slowapi memory storage resets. **Note: code returns 403 not 401
      on auth failures — the test asserts real behaviour.** Adversarial
      codepoints built via `chr(0x200b)` so source stays pure ASCII
      (semgrep bidi-detector clean).
    - `test_pydantic_models_meta.py` — auto-discovers every `BaseModel`
      in `backend.main` and enforces `extra='forbid'`, `max_length` on
      every string + list, `Literal` on enum-shaped fields
      (`channel/status/task/kind/role`). Reads `FieldInfo.metadata`
      (Pydantic v2 canonical constraint location). New models can't ship
      without hardening.
    - `test_agentic_router_behavior.py` — every `ExecutableTask` value
      dispatches without raising; arbitrary / SQL-injection-shaped /
      missing task names reject with **zero Gemini calls** (counter
      asserted); injection payloads in `params.query_text` land inside
      an `UNTRUSTED_DATA` fence with `system_instruction` set;
      non-existent `unique_key` short-circuits before Gemini; DB never
      receives raw injection strings as filter args.
    - `test_ssrf_guard_regression.py` — 25 reject cases via `subTest`
      (loopback, AWS/GCP metadata, k8s `*.cluster.local`, RFC1918,
      disallowed schemes, userinfo confusion, decimal/hex-encoded IPs)
      + benign-URL allowlist + dedicated DNS-rebind test
      (getaddrinfo public→private; second call raises).
    - `test_outreach_score_properties.py` — fixed-fixture + hypothesis
      (skipped if hypothesis absent). **Pinned finding:
      `calculate_outreach_score` does NOT read `seo_score`** —
      `test_seo_score_does_not_affect_score` locks current behaviour so
      a future refactor that wires it in trips loudly.
    - `test_segment_stability.py` — 20 leads × 5 runs.
      **`segment_lead` is pure-Python regex, not Gemini** — test is a
      regression guard for a future Gemini-backed segmenter AND a
      contract pin on the 11-label `KNOWN_LABELS` vocabulary.
  - **Live tier (skipped without GEMINI_API_KEY)** — run before model /
    prompt changes:
    - `test_outreach_golden_set.py`, `test_linkedin_golden_set.py` —
      10-lead quality bar + Gemini-as-judge (avg ≥ 7.5).
    - `test_outreach_hallucination.py` — 5 sparse leads (name + website
      only). Two-layer detection: regex (number-claims, named-title
      claims, 35+ tech tokens) + judge (every claim, `verifiable=bool`).
      ANY invented claim fails. Judge sees the exact `lead_data` dict
      the writer saw — synced to `agentic_router.py:389`.
    - `test_ask_determinism.py` — 20× same instruction → same task;
      `params.query` pairwise cosine ≥ 0.90 via `text-embedding-004`.
      Documents that schema doesn't declare `limit`.
    - `test_pain_points_consistency.py` — 50 calls; intra-lead pairwise
      Jaccard ≥ 0.60 AND inter-lead < 0.30 (catches input-blind generic
      output via 12-category synonym taxonomy).
    - `test_ai_mapper_golden.py` — 15 CSV header variants spanning
      English/Bosnian/French/German/Spanish + BOM-prefix + SQL injection
      + prompt injection + ambiguous "contact" + junk columns. 100% on
      canonicals; `custom_assert` per edge case.
    - `test_i18n_outreach.py` — BiH/Croatian leads (`Kovačević`, `Žito`,
      `Đurić`) through outreach + LinkedIn + mapper. Mojibake fingerprint
      sweep, 60-word BCS function-word slop detector, diacritic-
      preservation guard (catches silent ASCII transliteration).
    - `test_refusal_boundaries.py` — 6 malicious instructions
      (delete_leads, bulk_spam, phishing_bank, scrape_private_social,
      threatening_legal, doxx_owners). Classifier: refusal / benign /
      foreclosed / dangerous. ANY `dangerous` fails. Full transcript JSON
      dumped to a tempfile; path printed each run.
    - `test_json_compliance.py` — 50× per JSON-emitting call site
      (mapper, insights, hooks, enrich). 100% parse + schema required.
      Failure message points at `response_mime_type='application/json'`
      + `response_schema` as the canonical fix.
    - `test_ai_cost_budget.py` — 100-call pipeline budget per 20 leads:
      ≤200k input, ≤50k output, ≤8k single-call, ≤$0.50 total. Per-task
      breakdown printed on every run. Pricing constants pinned at top.
    - `test_insights_quality.py` — 50-lead seeded fixture
      (audit_status mix, score range, lead_source distribution). 5 calls
      + 5 judges. No-invented-numbers check uses an allowed-set from
      ground truth (counts + percentages ±1). Judge avg ≥ 8. Documents
      that `_get_strategic_insights` SELECTs only 5 fields.
    - `test_campaign_diversity.py` — 20 dentists, identical audit
      profile, only company/contact differs. Subject pairwise Jaccard
      ≤ 0.30 (after `COMPANY_NOUN_WORDS` masking) + opening-sentence
      cosine < 0.85. Catches "personalization theater".
  - **Critical pinned findings** (do NOT lose these on refactors —
    each lives in a test docstring):
    1. `seo_score` is not an input to `calculate_outreach_score`.
    2. `segment_lead` is pure regex, not Gemini.
    3. `_get_strategic_insights` SELECTs only
       `name,company_name,audit_status,seo_score,lead_source`.
    4. `discovery_search` / `run_massive_pipeline` tool schemas don't
       declare `limit`.
    5. `verify_api_key` returns 403, not 401.
    6. Discovery and SEO audit are NOT Gemini calls — excluded from cost
       budget.
  - **Run targeting**:
    - Full suite: `pytest tests/`
    - Offline-only (~5s, no API key): `pytest tests/test_endpoint_hardening.py
      tests/test_pydantic_models_meta.py tests/test_agentic_router_behavior.py
      tests/test_ssrf_guard_regression.py tests/test_prompt_snapshots.py
      tests/test_outreach_score_properties.py tests/test_segment_stability.py`
    - Live quality: `GEMINI_API_KEY=... pytest tests/test_*golden*.py
      tests/test_*hallucination*.py tests/test_*determinism*.py
      tests/test_*consistency*.py tests/test_*i18n*.py tests/test_*refusal*.py
      tests/test_*json_compliance*.py tests/test_*cost_budget*.py
      tests/test_*insights_quality*.py tests/test_*diversity*.py`
