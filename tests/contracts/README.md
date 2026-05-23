# Endpoint contracts

Each `.json` file in this directory is a JSON-Schema-backed contract for a
single backend endpoint. The runner at
`.github/scripts/contract-smoke.mjs` reads every file, calls the endpoint,
and asserts the response matches.

Catches "someone removed a field a downstream consumer depended on" and
"response Content-Type silently changed".

## File shape

```json
{
  "name": "leads_get",
  "method": "GET",
  "path": "/leads",
  "query": "?limit=1",
  "expected_status": 200,
  "expected_content_type": "application/json",
  "auth": "api_key",
  "schema": {
    "type": "object",
    "required": ["leads"],
    "properties": {
      "leads": { "type": "array" }
    }
  }
}
```

- `auth`: `"none"` | `"api_key"` (sends `X-API-Key`) | `"admin"` (sends
  both `X-API-Key` and `X-Admin-Token`).
- `body`: present for `POST/PUT/PATCH` — serialized as JSON.
- `schema`: validate only the SHAPE, not the values. The point is to
  catch field removals / type changes, not to assert business-logic
  output.

## Adding a new endpoint

1. Copy one of the existing contracts.
2. Edit `name`, `method`, `path`, and `schema`.
3. Keep `additionalProperties: false` only on the top-level object so a
   new optional field doesn't silently slip in undocumented. Inside
   nested objects, leave `additionalProperties` unset.

## Running locally

```bash
# Install ajv into the runner's scratch space.
npm install --no-save ajv@8

BACKEND_URL=https://<prod> \
API_SECRET_KEY=<key> \
CONTRACTS_DIR=tests/contracts \
node .github/scripts/contract-smoke.mjs
```

## Coverage roadmap

The four bootstrap contracts cover liveness, `/leads`, `/health/schema`,
and `/ask`. The 28 remaining endpoints (`/campaigns`, `/campaigns/{id}`,
`/process-lead`, `/draft-outreach`, `/insights`, `/upload`,
`/orchestrator/*`, `/export*`, …) should each get a contract — add as
you touch them. A missing contract is a known-known the runner won't
catch; a stale contract that doesn't run is worse.
