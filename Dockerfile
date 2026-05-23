# Use the official Microsoft Playwright image which comes with all necessary dependencies
# This avoids complicated dependency installation for browser automation on Linux
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

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
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir --require-hashes -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Playwright browsers (chromium is enough for our scraper)
RUN playwright install chromium

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
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3).status==200 else 1)" || exit 1

# Start the FastAPI application via uvicorn.
# --no-server-header suppresses "Server: uvicorn" — avoids stack fingerprint leakage.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
