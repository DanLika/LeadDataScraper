// Sentry init for the browser. Sentry v8+ canonical location replaces the
// older `sentry.client.config.ts`.
//
// `NEXT_PUBLIC_SENTRY_DSN` is intentionally public — Sentry DSNs are
// designed to be embeddable in client code; the project ingest endpoint
// enforces auth on the Sentry side, not at the DSN level. The DSN ends up
// in the client bundle by design.

import * as Sentry from '@sentry/nextjs';

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? 'production',
    release: process.env.NEXT_PUBLIC_SENTRY_RELEASE ?? 'unknown',
    sampleRate: 1.0,         // capture every error
    tracesSampleRate: 0.1,   // 10% transaction sampling for perf
    sendDefaultPii: false,
    maxBreadcrumbs: 50,
    // Tunnel client beacons through /monitoring (configured in next.config.ts)
    // so ad-blockers don't drop ingestion. Same-origin so no CSP change.
  });
}

// Next.js 16 hook so Sentry can wrap navigation-tracing. Exported for the
// framework to pick up automatically.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
