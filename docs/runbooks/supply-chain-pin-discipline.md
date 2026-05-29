# Supply-chain pin discipline (npm carets + Docker digest + apt versions)

**Status**: LIVE 2026-05-29 by PR (this branch). Codifies the three pin
classes audited by `/security-audit:run` 2026-05-29.

## Why pin

Carets in `package.json` and bare tags in `Dockerfile` let a registry
swap the resolved version between the moment a dependency was approved
in review and the moment a CI / Vercel / Render builder installs it. The
lockfile (`package-lock.json`, `requirements.txt --require-hashes`) is
the safety net — but the lockfile only protects builds that go through
`npm ci` / `pip install --require-hashes`. Any developer running `npm
install` or `pip install -r requirements.in` against a floating spec
will regenerate the lockfile to a new minor, and that regenerated
lockfile then becomes the next-build truth.

The pin classes below close that loop at the SOURCE-of-truth level, not
just the lockfile level.

## Pin class A — npm exact-version

Applies to security-critical frontend deps in `frontend/package.json`:

| Package | Pin | Note |
|---|---|---|
| `next` | exact | framework — RCE class |
| `react`, `react-dom` | exact | RSC + hydration boundary |
| `@supabase/ssr`, `@supabase/supabase-js` | exact | auth + DB |
| `@sentry/nextjs` | exact | error sink with token |
| `next-intl` | exact | request-time locale routing |
| `recharts` | exact | renders attacker-derived data (lead counts, segments) |
| `web-vitals` | exact | beacon payload reaches `/api/proxy/metrics` |

Symptom of a regression: a PR diff shows `^X.Y.Z` reappearing on any of
the rows above. The fix is `"^X.Y.Z" → "X.Y.Z"`.

Non-security UI deps (`lucide-react`, `@tanstack/react-virtual`,
type-only devDependencies) may keep carets — they don't reach the
runtime trust boundary and tightening them creates Dependabot noise
without benefit.

## Pin class B — Docker base by sha256 digest

The `Dockerfile` `FROM` line carries BOTH a human-readable tag AND a
`@sha256:<digest>` content-addressable pin:

```
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy@sha256:aaa8048c7a7c414fab6ad809469eb35f13bbf5093038113eef851b3c4814ad77
```

The tag is for humans (so the diff says "Playwright 1.60 on Ubuntu
jammy"). The digest is the integrity gate — a tag re-push at MCR can't
swap the layers underneath.

### Refreshing the digest

When Dependabot's `docker` ecosystem opens a PR to bump the tag (e.g.
`v1.60.0-jammy` → `v1.61.0-jammy`), it updates the digest atomically.
Manual refresh recipe if you ever need it:

```bash
curl -sI \
  -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
  -H "Accept: application/vnd.oci.image.index.v1+json" \
  -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
  -H "Accept: application/vnd.oci.image.manifest.v1+json" \
  https://mcr.microsoft.com/v2/playwright/python/manifests/v<TAG>-jammy \
  | grep -i docker-content-digest
```

Drop the returned `sha256:...` after the `@` in the `FROM` line.

## Pin class C — apt package version in Dockerfile

`build-essential` (toolchain for pip wheel compilation) is version-pinned:

```
apt-get install -y --no-install-recommends build-essential=12.9ubuntu3
```

`12.9ubuntu3` is the current jammy archive snapshot (stable since
2022-03-23; same in jammy / jammy-updates / jammy-security).
`build-essential` is purged in the same `RUN` layer so it never lands
in the runtime image — the pin therefore guards the BUILD-TIME
toolchain only, not the runtime attack surface.

### Refreshing the version

```bash
# query the live archive (no docker required)
curl -s https://packages.ubuntu.com/jammy/amd64/build-essential/download \
  | grep -oE 'build-essential_[0-9][^_]+' | head -1
```

When the Ubuntu archive bumps `build-essential`, update the `=<version>`
suffix in the `Dockerfile` apt line.

## Recurrence guard

`gh-workflows/container-scan.yml` runs Trivy + Grype on every PR. A
re-pushed base with new CVEs would still flip Trivy CRITICAL → red even
if a tag-only `FROM` masked the change. The digest pin makes the build
deterministic FIRST; Trivy detects WHAT changed second.

CI gate that would catch a regression in class A:

```bash
# pseudo-gate, not currently wired
grep -E '"\^' frontend/package.json \
  | grep -E '("next"|"react"|"react-dom"|"@supabase|"@sentry|"next-intl"|"recharts"|"web-vitals")'
```

A non-empty match should fail CI. Add if a regression lands.

## Related runbooks

- [`lockfile-drift-recovery.md`](lockfile-drift-recovery.md) — cluster
  #1 of issue #363, when `npm ci` rejects a lockfile/package.json
  mismatch.
- [`render-env-push.md`](render-env-push.md) — adjacent supply-chain
  surface (env keys travel a different boundary).
