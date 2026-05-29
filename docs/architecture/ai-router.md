# AI Router invariants (`src/core/agentic_router.py`)

Sourced from CLAUDE.md 2026-05-29 slim.

- `route_instruction()` attaches `lead_index` (unique_key + name + company_name, ≤200 rows) to Gemini contents so model can resolve "Audit Alpha Tech" → `seo_audit(unique_key=...)`. Without context, model bails "data insufficient" on every per-lead prompt.
- `_execute_database_query()` selects `unique_key, name, company_name, audit_status, seo_score, lead_source, email, phone, website, high_risk_flag, segment`. Query prompt embeds definitions ("high risk" = `high_risk_flag` true OR `seo_score<50` OR `audit_status=='Failed'`; "healthy" = Completed + score≥70 + not high-risk) so answers match UI filter semantics.
- `/ask` auto-executes `DATABASE_QUERY`/`STATUS_CHECK`/`GET_INSIGHTS` (read-only) and surfaces `result.answer/message/formatted-insights/summary`. `task=="UNKNOWN"` surfaces `plan.raw` (small-talk) instead of a confusing plan card.
- `/execute` rejects extra fields (`extra='forbid'`). `/ask` plan includes `reasoning`; frontend strips it before POST (`handleExecutePlan` builds `{task, params}` only) — without strip every Confirm 422s.
- `_get_status_summary()` returns one-line summary as both `answer` + `summary`.
- `_get_strategic_insights()` (PR #245) fetches DB-wide count via separate `select("unique_key", count="exact").limit(1)` (one scalar — keeps finding #3 intact) and embeds `GROUND TRUTH` block. **CI side-effect**: changes prompt body → `tests/test_prompt_snapshots.py` fails until SHA256 regenerated via `UPDATE_PROMPT_SNAPSHOTS=1`.
- `_generate_outreach_draft()` returns `{draft, subject, lead_name, lead_email, operator_name}`. Subject parsed via **atomic-group regex** `^(?>\s*)Subject(?>[ \t]*):(?>[ \t]*)([^\r\n]*)\r?\n` — previous form was O(n²) ReDoS, fixed. `OPERATOR_NAME` env defaults "Your Name". Pinned: `tests/test_redos.py::TestSubjectParserReDoSRegression`.
