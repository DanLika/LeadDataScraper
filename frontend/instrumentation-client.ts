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
    // Tunnel client beacons through same-origin /monitoring so ad-blockers
    // (uBlock / Brave / DDG / 1Blocker) don't match `*.sentry.io` and drop
    // them. `withSentryConfig.tunnelRoute` in `next.config.ts` ALSO sets
    // this via build-time env injection — keeping it explicit here is
    // belt-and-braces, since the auto-injection was empirically failing
    // in prod (RESP-044, /monitoring → 404). Handler:
    // `app/monitoring/route.ts`. Same-origin POST → CSP `connect-src
    // 'self'` already permits it; no header change needed.
    tunnel: '/monitoring',
  });
}

// Next.js 16 hook so Sentry can wrap navigation-tracing. Exported for the
// framework to pick up automatically.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
