"""Service layer — business logic between handlers and repositories.

Pattern (from CLAUDE.md "Layered architecture"): handlers do auth +
rate-limit + Pydantic validation + HTTP error mapping; services take
typed primitives (NOT Pydantic instances) so non-HTTP callers
(workers / CLIs / background tasks) don't depend on backend.main;
repositories do pure PostgREST I/O.

Phase 15.3 adds:
  * template_renderer — Jinja2 SandboxedEnvironment + var allowlist
    + cold-AUP unsubscribe_url enforcement
  * variant_selector  — weighted random with VARIANT_SELECTOR_ALLOW_SEED
    gating for test-only determinism
  * thread_builder    — payload assembly + PriorMessageNotReadyError
    race handling for thread-with-prior steps
  * variant_service   — orchestrates validation + repo.create
"""
