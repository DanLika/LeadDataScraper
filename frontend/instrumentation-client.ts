// Sentry init for the browser, deferred post-FCP.
//
// Static `import * as Sentry from '@sentry/nextjs'` previously placed the
// 142 KB gz SDK chunk in `rootMainFiles`, so every anon visit to /login
// paid the parse + load cost before LCP (see
// `docs/bundle-audit-2026-05-29` finding P0). The dynamic `import()` below
// relocates that chunk out of the initial bundle; it streams in after
// `requestIdleCallback` fires post-FCP, with a `setTimeout(100ms)`
// fallback for browsers lacking rIC (notably Safari ≤16).
//
// Tradeoff: errors thrown in the ~50–200 ms between page load and idle
// fire are NOT captured. Acceptable here — hydration errors typically
// surface after the SDK is live anyway.
//
// `NEXT_PUBLIC_SENTRY_DSN` is intentionally public; Sentry DSNs are
// designed to be embeddable in client code (auth is enforced at the
// project ingest endpoint).

import type * as SentryNs from '@sentry/nextjs';

type RouterTransitionFn = typeof SentryNs.captureRouterTransitionStart;

let routerTransitionHandler: RouterTransitionFn = () => {};

// Next 16 picks up this synchronous module-level export at framework
// boot; the value stays a no-op until the deferred SDK chunk loads and
// rebinds the handler below.
export const onRouterTransitionStart: RouterTransitionFn = (...args) =>
  routerTransitionHandler(...args);

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn && typeof window !== 'undefined') {
  const loadAndInitSentry = async () => {
    const Sentry = await import('@sentry/nextjs');
    Sentry.init({
      dsn,
      environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? 'production',
      release: process.env.NEXT_PUBLIC_SENTRY_RELEASE ?? 'unknown',
      sampleRate: 1.0,
      tracesSampleRate: 0.1,
      sendDefaultPii: false,
      maxBreadcrumbs: 50,
      // Same-origin tunnel keeps ad-blockers (uBlock / Brave / DDG /
      // 1Blocker) from matching `*.sentry.io` and dropping ingestion.
      // `withSentryConfig.tunnelRoute` in `next.config.ts` ALSO sets
      // this via build-time env injection; explicit here is
      // belt-and-braces, since the auto-injection was empirically
      // failing in prod (RESP-044, /monitoring → 404). Handler:
      // `app/monitoring/route.ts`. Same-origin POST → CSP
      // `connect-src 'self'` already permits it.
      tunnel: '/monitoring',
      // Strip BrowserTracing from the default integration list. Saves
      // ~30 KB gz inside the deferred chunk; route-tracing breadcrumbs
      // are lost, but manual `Sentry.startSpan(...)` calls in code
      // still work.
      integrations: (defaults) =>
        defaults.filter((i) => i.name !== 'BrowserTracing'),
    });
    routerTransitionHandler = Sentry.captureRouterTransitionStart;
  };

  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(() => {
      void loadAndInitSentry();
    });
  } else {
    setTimeout(() => {
      void loadAndInitSentry();
    }, 100);
  }
}
