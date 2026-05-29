# Use the official Microsoft Playwright image which comes with all necessary dependencies
# This avoids complicated dependency installation for browser automation on Linux.
#
# Tag v1.60.0-jammy is the LATEST published playwright/python tag at the
# time of this commit (verified 2026-05-28 against MCR
# /v2/playwright/python/tags/list). The image bundles Chromium and a set
# of apt-installed media/TLS libs whose upstream Ubuntu packages have
# rolling CVEs that Microsoft picks up on each rebuild. We inherit the
# rebuilds via Dependabot's `docker` ecosystem; in the meantime
# `.grype.yaml` in the repo root documents the accepted-risk allowlist
# (Chromium + libgnutls30 + libcaca0 + gstreamer1.0-plugins-good +
# libgstreamer-plugins-good1.0-0), each entry with a reachability
# analysis. Trivy still gates merge on CRITICAL + fixable-HIGH against
# the FULL image — the allowlist applies only to grype, the
# second-opinion tool. Issue #363 bucket #9 cleanup.
#
# The `@sha256:...` digest is pinned per /security-audit:run 2026-05-29 so
# a tag re-push at the registry can't silently swap the base layer between
# resolve-time and build-time. Digest fetched from the MCR manifest API
# (multi-arch index). Dependabot's docker ecosystem updates both the tag
# AND the digest atomically on each rebuild.
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy@sha256:aaa8048c7a7c414fab6ad809469eb35f13bbf5093038113eef851b3c4814ad77

# Build-time release tag for Sentry. Defaults to "unknown" if the build
# context didn't pass --build-arg GIT_SHA (e.g. `docker build .` locally).
# The deploy-backend.yml workflow passes the commit SHA so prod images
# carry the exact revision label. Sentry resolves source maps + commits
# against this string.
ARG GIT_SHA=unknown
ENV RELEASE_SHA=${GIT_SHA}

# Set work directory
WORKDIR /app

# Install build toolchain ONLY for pip wheel compilation, then purge in the
# same RUN layer so gcc/make/etc don't ship to the runtime image. Keeps the
# post-RCE local-privesc toolkit out of the container.
COPY requirements.txt .
# --require-hashes enforces sha256 verification of every wheel/sdist
# against the lockfile produced by `pip-compile --generate-hashes`.
# A PyPI tampering scenario where a package's content changes between
# resolve-time and install-time fails the install with a HashMismatch.
# Operator regenerates the lockfile via `make lock-python`.
# build-essential version pinned to the jammy archive snapshot at audit
# time (12.9ubuntu3 — stable since 2022-03-23, single source of truth
# for jammy/jammy-updates/jammy-security per packages.ubuntu.com). Pin
# prevents an apt-side rebuild between resolve and install from swapping
# the toolchain. Pkg is purged in this same RUN layer so it never lands
# in the runtime image — pin guards the BUILD-TIME toolchain only.
# Per /security-audit:run 2026-05-29.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential=12.9ubuntu3 \
    && pip install --no-cache-dir --require-hashes -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Playwright browsers (chromium is enough for our scraper).
# Cache lands in /ms-playwright by default; chown so the later USER pwuser
# can launch chromium (root-installed files would otherwise be readable but
# parts of the cache need write access for first-launch). The Microsoft
# Playwright image already has /ms-playwright; we chown the path that
# `playwright install` writes to.
RUN playwright install chromium \
    && chown -R pwuser:pwuser /ms-playwright /home/pwuser 2>/dev/null || true

# Copy project files
# We copy everything, including src/, backend/, and the root files
COPY . .

# Drop root. The Microsoft Playwright image ships with `pwuser` (uid 1000) pre-configured
# for browser automation; chown the app tree so log writes etc. succeed.
RUN chown -R pwuser:pwuser /app
USER pwuser

# Expose the default FastAPI port
EXPOSE 8000

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Container-level liveness — Render's external probe still owns prod
# health, but this lets `docker run` / local orchestrators detect a
# wedged uvicorn worker. `/` is the unauthenticated liveness probe.
# retries=2 + timeout=3s = wedged worker marked unhealthy in ~36s instead
# of ~90s under retries=3, while still tolerating one transient miss.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=2 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2).status==200 else 1)" || exit 1

# Start the FastAPI application via uvicorn.
# --no-server-header suppresses "Server: uvicorn" — avoids stack fingerprint leakage.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
