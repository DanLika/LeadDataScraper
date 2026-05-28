"""Repository layer — pure PostgREST I/O.

Repositories translate the supabase-py client surface into a typed,
domain-agnostic API. Services depend on repositories; handlers depend
on services. No HTTP, no Pydantic, no business logic in this layer.

Pattern established in Phase 14.2 with ``SuppressionRepository``;
existing domain code (leads, campaigns, orchestration) migrates here
as it stabilizes (see PR #192 for the campaigns precedent).
"""
