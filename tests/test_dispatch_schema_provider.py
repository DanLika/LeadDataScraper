"""Schema invariants for the multi-dispatcher provider columns (Phase 14.0).

Lives in the live-tier (`@pytest.mark.live`) because the assertions
exercise real PostgreSQL CHECK constraint behaviour — `psycopg` is
not in `requirements.txt` (backend talks to PostgREST over HTTPS),
so tests skip cleanly when the driver / DATABASE_URL aren't around.

Run via:

    pytest tests/test_dispatch_schema_provider.py -m live

Requires:
- `pip install 'psycopg[binary]>=3.1'` (CI installs inline; see
  `.github/workflows/ci.yml::concurrency-tests`).
- `DATABASE_URL` pointing at the Supabase pooler URI.
- PR #286 + this PR's ALTER applied to the target schema.
"""
from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set"),
]


@pytest.fixture
def conn():
    # psycopg3 `Connection.transaction()` owns commit/rollback; calling
    # `connection.rollback()` inside the block raises ProgrammingError.
    # Plain try/finally + outer rollback keeps the contract simple: every
    # test sees BEGIN, mutations are unconditionally rolled back on exit
    # so no synthetic rows ever land in the target database.
    connection = psycopg.connect(DATABASE_URL)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


class TestEmailSendLedgerProvider:
    """`email_send_ledger.provider` CHECK constraint."""

    def test_rejects_unknown_provider(self, conn):
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO public.email_send_ledger "
                    "(recipient_domain, provider) VALUES (%s, %s)",
                    ("test.example", "mailgun"),
                )

    @pytest.mark.parametrize("provider", ["resend", "instantly", "smtp", "heyreach"])
    def test_accepts_allowlisted_provider(self, conn, provider):
        domain = f"prov-test-{uuid.uuid4().hex[:8]}.example"
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.email_send_ledger "
                "(recipient_domain, provider) VALUES (%s, %s)",
                (domain, provider),
            )

    def test_default_provider_is_resend(self, conn):
        domain = f"default-test-{uuid.uuid4().hex[:8]}.example"
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.email_send_ledger (recipient_domain) "
                "VALUES (%s) RETURNING provider",
                (domain,),
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == "resend"

    def test_accepts_null_recipient_domain_for_heyreach(self, conn):
        # LinkedIn (HeyReach) sends have no recipient_domain; the NOT NULL
        # was dropped in Phase 14.0 so the dispatcher can write a tagged
        # ledger row without a synthetic placeholder.
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.email_send_ledger "
                "(recipient_domain, provider) VALUES (%s, %s)",
                (None, "heyreach"),
            )


class TestEmailSuppressionSource:
    """`email_suppression.source` (nullable forensic column) + CHECK."""

    def test_source_optional_for_manual_adds(self, conn):
        email = f"manual-{uuid.uuid4().hex[:8]}@example"
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.email_suppression "
                "(email, reason, source) VALUES (%s, %s, %s)",
                (email, "manual", None),
            )

    def test_source_records_provider_for_webhook_adds(self, conn):
        email = f"hook-{uuid.uuid4().hex[:8]}@example"
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.email_suppression "
                "(email, reason, source) VALUES (%s, %s, %s) "
                "RETURNING source",
                (email, "bounce", "resend"),
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == "resend"

    def test_rejects_unknown_source(self, conn):
        # Webhook-fed column; CHECK guards against attacker-influenced
        # bodies poisoning the forensic record.
        email = f"bad-source-{uuid.uuid4().hex[:8]}@example"
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO public.email_suppression "
                    "(email, reason, source) VALUES (%s, %s, %s)",
                    (email, "bounce", "mailgun"),
                )

    @pytest.mark.parametrize(
        "source", ["resend", "instantly", "smtp", "heyreach"]
    )
    def test_accepts_allowlisted_source(self, conn, source):
        email = f"src-ok-{uuid.uuid4().hex[:8]}@example"
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.email_suppression "
                "(email, reason, source) VALUES (%s, %s, %s)",
                (email, "bounce", source),
            )
