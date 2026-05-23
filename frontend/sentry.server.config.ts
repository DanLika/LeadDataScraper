// Sentry init for the Node.js runtime (Next.js server-side render, route
// handlers, server actions). Loaded by `instrumentation.ts::register` when
// `process.env.NEXT_RUNTIME === 'nodejs'`.
//
// `SENTRY_DSN` is a SECRET on the server side — it's the same value as
// `NEXT_PUBLIC_SENTRY_DSN` used in the browser, but routing it through the
// public env adds it to the bundle. Keep both set to the same string.

import * as Sentry from '@sentry/nextjs';

const dsn = process.env.SENTRY_DSN;
if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.SENTRY_ENVIRONMENT ?? 'production',
    release: process.env.SENTRY_RELEASE ?? process.env.NEXT_PUBLIC_SENTRY_RELEASE ?? 'unknown',
    sampleRate: 1.0,          // capture every error
    tracesSampleRate: 0.1,    // 10% transaction sampling
    sendDefaultPii: false,
    maxBreadcrumbs: 50,
    beforeSend(event) {
      // Scrub our custom auth headers (Sentry's default scrubber doesn't
      // know about X-API-Key / X-Admin-Token). Keep `cookie` + `authorization`
      // belt-and-braces in case Sentry's defaults regress.
      const req = event.request;
      if (req?.headers) {
        const headers = req.headers as Record<string, string>;
        for (const key of Object.keys(headers)) {
          const lk = key.toLowerCase();
          if (lk === 'x-api-key' || lk === 'x-admin-token' || lk === 'cookie' || lk === 'authorization') {
            headers[key] = '[scrubbed]';
          }
        }
      }
      return event;
    },
  });
}
