## 2025-05-15 - [Optimization of Supabase Schema Checks]
**Learning:** Checking column existence individually in a database table creates an N+1 query problem, which is especially costly in cloud databases like Supabase/PostgREST.
**Action:** Use bulk selection of all required columns as a fast-path, and query `information_schema.columns` via RPC as a robust fallback to identify missing columns in a single round-trip.
