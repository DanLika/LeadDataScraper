"""Background workers — Render Cron entry points.

Each module exposes an idempotent ``run_*()`` coroutine + the CLI in
``scripts/`` invokes it. Workers are stateless beyond the DB; restart
recovery + concurrent-tick safety relies on idempotent UPDATE patterns
in the repository layer (see ``CampaignMessageRepository.claim_due_batch``).
"""
