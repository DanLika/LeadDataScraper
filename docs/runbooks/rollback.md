# Rollback Runbook

When a deploy goes bad, **rollback first, root-cause second**. This doc
is the muscle-memory script for the rollback path. See
[`docs/runbooks/incidents.md`](incidents.md) §1 for the surrounding
incident-response flow.

## Cross-referenced infra

- **Faza 8.1** — auto-rollback on post-deploy smoke failure.
  `post-deploy-smoke.yml` runs after every deploy; if synthetic
  checks fail, the workflow triggers a Render API rollback to the
  previously verified digest. The manual rollback below is the
  fallback path when the auto chain didn't catch the regression.
- **Faza 7.5** — cosign-verified image deploys.
  `deploy-backend.yml` only rolls Render to a GHCR digest that
  passed SLSA3 provenance + cosign verify-attestation. Rolling back
  means pointing Render at the **previous** verified digest, which
  has the same cosign attestation guarantee.
- **Faza 8.10** — key rotation. If the rollback is being done because
  a secret leaked, follow
  [`docs/runbooks/incidents.md`](incidents.md) §5 in parallel
  ([`docs/secret-inventory.md`](../secret-inventory.md) is the source
  of truth for rotation cadence).

---

## 1. Auto-rollback chain (Faza 8.1)

Already in place — runs without operator action:

```
push main
  ↓
deploy-backend.yml (build + cosign verify + Render rollout)
  ↓
post-deploy-smoke.yml (synthetic checks against the new deploy)
  ↓
✅ green → keep deploy
❌ red   → workflow opens an issue + auto-trigger Render API rollback to
            previous verified digest
```

You don't normally do anything when this works. Watch
[`#alerts`](../alerting.md) — if a Discord ping says "auto-rolled back",
that's the signal to open the post-mortem
([`docs/runbooks/incidents.md`](incidents.md) §1.5).

The manual procedure below is for:

- The auto chain itself broke (rare).
- A regression that **passes** post-deploy smoke but is still bad
  (e.g. subtle UI breakage, AI cost spike, slow degradation).
- A security incident requiring an immediate rollback regardless of
  smoke status.

---

## 2. Manual rollback — Render dashboard (fast path)

Time to recovery: **~30 seconds** from clicking to traffic on the prior
digest.

1. **Identify the bad deploy.** Render dashboard → backend service →
   **Deploys** tab. The current deploy is at the top. Note the commit
   SHA + deploy timestamp.

2. **Identify the last known good deploy.** Scroll down the same list.
   The previous green deploy is your target. Confirm its smoke run
   passed via the linked GitHub Actions run (`post-deploy-smoke.yml`).

3. **Click "Roll back to this deploy"** on the target row. Render
   confirms; the rollout starts immediately. Watch the **Events** tab
   for the rollout to flip the active deploy.

4. **Verify recovery**:

   ```bash
   # Quick liveness
   curl -fsS -m 10 "$BACKEND_URL/" | jq

   # Synthetic monitor next cycle (within 5 min)
   gh run watch --workflow=synthetic-monitor.yml
   ```

   Wait for synthetic monitor to ping `✅ Recovered` in Discord
   ([`docs/alerting.md`](../alerting.md)).

5. **Open an incident issue** using the template in
   [`docs/runbooks/incidents.md`](incidents.md) §1.5. Label
   `incident`, severity tag, link the bad deploy + the rolled-back
   deploy.

6. **Schedule a post-mortem.** Same template. Goal: ship before the
   end of the next workday so the failure mode is fresh.

---

## 3. Manual rollback — git revert (slower, but git-traceable)

Time to recovery: **~5-10 minutes** (full CI + deploy cycle).

Use this path when the rollback should be captured in git history
(e.g. for the post-mortem write-up to point at a single revert PR
rather than a Render-dashboard event):

```bash
git checkout main
git pull
git log --oneline -5                          # find the bad commit
git revert <bad-sha>
git push origin main                           # triggers deploy-backend.yml
```

The revert PR re-fires the GHCR → cosign-verify → Render rollout
chain. Watch [`#alerts`](../alerting.md) for the deploy to land green,
then synthetic monitor for `✅ Recovered`.

> ⚠️ **Don't skip the cosign-verify step.** A revert that bypasses the
> normal CI (e.g. `git push --no-verify` or manually pushing an image
> to GHCR) loses the SLSA3 provenance guarantee — the rolled-back
> image stops being part of the verified chain of custody. Always go
> through the normal `push → workflow → cosign → Render` flow.

---

## 4. Manual rollback — Render API (when dashboard is unavailable)

If you're on the road / phone / dashboard is down, the rollback can be
issued directly via the Render API. Requires `RENDER_API_KEY` (same
secret used by `deploy-backend.yml`).

```bash
# 1. Get the last 5 deploys for the service:
curl -fsS \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/$RENDER_BACKEND_SERVICE_ID/deploys?limit=5" \
  | jq '.[] | {id, commit:.commit.id, status, createdAt}'

# 2. Pick the previous green deploy's `commit.id`. Re-deploy it:
curl -fsS -X POST \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  "https://api.render.com/v1/services/$RENDER_BACKEND_SERVICE_ID/deploys" \
  -d '{"clearCache": false, "commitId": "<previous-good-sha>"}'

# 3. Watch deploy progress:
curl -fsS \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/$RENDER_BACKEND_SERVICE_ID/deploys" \
  | jq '.[0] | {id, status, createdAt}'
```

This bypasses the GHCR + cosign chain (Render pulls fresh from git);
acceptable for an emergency, but the next normal deploy should be
through the full chain to restore the audit trail.

---

## 5. Rollback drill (quarterly)

**Run a deliberate rollback every quarter** to keep the procedure
muscle-memory + catch regressions in the rollback tooling itself.

### Drill protocol

1. **Schedule** — pick a low-traffic window (Sunday morning UTC works
   for the single-operator setup).

2. **Pre-drill announce** — post in `#alerts` Discord: "🧪 Rollback
   drill starting at HH:MM — backend will dip briefly." This is the
   training opportunity to make sure ops comms are wired.

3. **Inject a harmless regression** — push a one-line PR that
   intentionally fails post-deploy smoke (e.g. add a `/` handler that
   returns 500). Merge. Watch:

   - Does `post-deploy-smoke.yml` catch it? (auto-rollback path)
   - Does Discord ping fire?
   - Does the auto-rollback land Render on the prior digest?

4. **If auto chain didn't fire** — execute the manual procedure (§2 or
   §3 above). Time the recovery.

5. **Document** — fill a drill log:

   ```markdown
   # YYYY-MM-DD — Rollback drill

   **Outcome**: pass / partial / fail
   **MTTR**: <minutes from regression push to /` green again>
   **Auto-rollback fired**: yes / no
   **Discord ping latency**: <seconds>
   **Issues found**: <list>
   **Action items**: <list>
   ```

   Save in `docs/runbooks/drills/YYYY-MM-DD-rollback.md`.

6. **Restore** — revert the harmless regression. Confirm full green
   state.

### Drill cadence + accountability

| When | Owner | Output |
|---|---|---|
| Q1 / Q2 / Q3 / Q4 (one Sunday per quarter) | operator | drill log in `docs/runbooks/drills/` |

Drift signal: if the drill log is more than 100 days old, the next
synthetic-monitor cron run can grep `docs/runbooks/drills/` mtime and
ping Discord. (Future improvement — not implemented.)

---

## 6. Quick reference

```
30-SECOND PATH (Render dashboard)
1. Render → backend service → Deploys
2. Click "Roll back to this deploy" on prior green
3. curl $BACKEND_URL/ → expect {"status":"ok"}
4. Watch Discord for synthetic-monitor `✅ Recovered`
5. Open incident issue + schedule post-mortem

5-MINUTE PATH (git revert, audit-trail-preserving)
1. git revert <bad-sha> && git push
2. Watch deploy-backend.yml + post-deploy-smoke.yml
3. Watch Discord for `✅ Recovered`
4. Open incident issue + schedule post-mortem

PHONE-ONLY PATH (Render API)
See §4. Requires RENDER_API_KEY + RENDER_BACKEND_SERVICE_ID.
```

---

## References

- `.github/workflows/deploy-backend.yml` — the rollout chain (Faza 7.5)
- `.github/workflows/post-deploy-smoke.yml` — auto-rollback trigger
  (Faza 8.1)
- [`docs/runbooks/incidents.md`](incidents.md) §1 — backend-down
  incident response
- [`docs/secret-inventory.md`](../secret-inventory.md) — secret rotation
  (Faza 8.10)
- [`docs/ci-architecture.md`](../ci-architecture.md) — full CI inventory
- [`docs/alerting.md`](../alerting.md) — Discord routing
