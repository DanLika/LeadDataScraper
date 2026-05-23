// Next.js standard hook called once per runtime (nodejs + edge) at boot.
// We import the matching Sentry config; if SENTRY_DSN is unset inside that
// file, Sentry init is a no-op so dev without a DSN stays clean.
//
// See: https://nextjs.org/docs/app/building-your-application/optimizing/instrumentation
//      https://docs.sentry.io/platforms/javascript/guides/nextjs/

export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    await import('./sentry.server.config');
  }
  if (process.env.NEXT_RUNTIME === 'edge') {
    await import('./sentry.edge.config');
  }
}

// Next.js 15+ instrumentation hook for capturing uncaught errors out of
// route handlers / server components. Sentry v10 exposes
// `captureRequestError` (was `onRequestError` in v8/v9); the local
// wrapper keeps the Next.js hook name + SDK-major-version stable.
import * as Sentry from '@sentry/nextjs';

export const onRequestError = Sentry.captureRequestError;
