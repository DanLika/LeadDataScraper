// Sentry init for the Edge runtime. Same shape as `sentry.server.config.ts`
// but Edge-runtime-safe (no Node-only integrations). The proxy in
// `frontend/proxy.ts` runs at the edge, so any error there flows through
// this init.

import * as Sentry from '@sentry/nextjs';

const dsn = process.env.SENTRY_DSN;
if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.SENTRY_ENVIRONMENT ?? 'production',
    release: process.env.SENTRY_RELEASE ?? process.env.NEXT_PUBLIC_SENTRY_RELEASE ?? 'unknown',
    sampleRate: 1.0,
    tracesSampleRate: 0.1,
    sendDefaultPii: false,
    maxBreadcrumbs: 50,
  });
}
