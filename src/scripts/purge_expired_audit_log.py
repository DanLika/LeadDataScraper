"""Purge expired ``account_deletions`` audit rows.

GDPR Article 17 says the data subject's data must be erased. We keep a
30-day audit row (`account_deletions`) for fraud / contested-deletion
context. After 30 days, even that goes — leaving no trace beyond the
DELETE itself.

Runs daily from `security.yml` (alongside the other Supabase invariant
gates). Connects via `DATABASE_URL` (pooler URL, sslmode=require), same
as the other CI scripts.

Operator setup is zero — the SQL is one statement; no migrations beyond
the table itself (declared in `supabase_schema.sql`).

Exit codes:
    0 = ok (zero or more rows purged)
    2 = misconfigured (DATABASE_URL missing or unreachable)
"""

from __future__ import annotations

import os
import sys

import psycopg


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        return 2

    try:
        conn = psycopg.connect(url, autocommit=True)
    except psycopg.Error as e:
        print(f"ERROR: cannot connect to DATABASE_URL: {e}", file=sys.stderr)
        return 2

    try:
        # `RETURNING id` so we can log the row count; without it
        # supabase-pooler may suppress rowcount reporting depending on
        # statement-cache state.
        cur = conn.execute(
            "DELETE FROM public.account_deletions WHERE expires_at < now() RETURNING id"
        )
        purged = list(cur.fetchall())
    except psycopg.Error as e:
        print(f"ERROR: purge query failed: {e}", file=sys.stderr)
        conn.close()
        return 2
    finally:
        conn.close()

    print(f"purged {len(purged)} expired account_deletions row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
