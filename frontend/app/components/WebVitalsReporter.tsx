'use client';

import { useEffect } from 'react';

/**
 * Web Vitals beacon — fires once per page load. CLS / INP / LCP / FCP / TTFB
 * collected via the `web-vitals` lib, then sent to `/api/proxy/metrics`
 * via `navigator.sendBeacon` so the report goes out even if the user
 * is navigating away. Proxy adds X-API-Key + same-origin check.
 *
 * Why a client component imported into the root layout vs the per-page:
 * the layout file is a Server Component by default; effects live in
 * client islands. This one renders nothing — it's purely a side-effect
 * registration. Idempotent under React Strict Mode (`web-vitals` itself
 * dedups callbacks).
 *
 * Why sendBeacon over fetch: beacon survives the page unload, has its
 * own kernel-buffered queue, doesn't block navigation, and the response
 * body is irrelevant — fire-and-forget by design. Falls back to fetch
 * with keepalive when sendBeacon is unavailable (very old browsers).
 */
function sendBeaconPayload(payload: Record<string, unknown>) {
  try {
    const url = '/api/proxy/metrics';
    const body = JSON.stringify(payload);
    if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
      // Use a Blob with explicit JSON Content-Type — bare sendBeacon
      // defaults to text/plain;charset=UTF-8 which the FastAPI
      // Pydantic-validation pipeline would 422 on.
      const blob = new Blob([body], { type: 'application/json' });
      const ok = navigator.sendBeacon(url, blob);
      if (ok) return;
    }
    // Fallback for environments without sendBeacon. `keepalive: true`
    // mimics the no-block-on-unload guarantee. Wrapped in catch because
    // a metrics failure must never affect the user's experience.
    fetch(url, {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body,
    }).catch(() => {});
  } catch {
    // Swallow — analytics MUST NOT break the page.
  }
}

export default function WebVitalsReporter() {
  useEffect(() => {
    // Dynamic import: web-vitals is small (~3 KB gz) but there's no
    // reason to pay for it on the SSR HTML or block first paint.
    let cancelled = false;
    (async () => {
      try {
        const { onCLS, onINP, onLCP, onFCP, onTTFB } = await import('web-vitals');
        if (cancelled) return;

        // web-vitals callbacks fire once-per-metric for INP/LCP/FCP/TTFB,
        // and possibly multiple times for CLS (it can grow during the
        // visit). We pass the lib's `Metric` shape straight through to
        // the backend which Pydantic-validates the subset we care about.
        const path = window.location.pathname || '/';

        const report = (metric: {
          name: string;
          value: number;
          rating: string;
          id: string;
        }) => {
          sendBeaconPayload({
            name: metric.name,
            value: Number(metric.value.toFixed(3)),
            rating: metric.rating,
            path,
            id: metric.id,
          });
        };

        // `reportAllChanges: true` makes LCP and CLS fire eagerly on every
        // value update rather than waiting for the page to enter the hidden
        // state. Without this we observed zero beacons in the first 10s of a
        // still-active tab (#226 Phase 15 P2). web-vitals also installs its
        // own pagehide + visibilitychange listeners internally, so the final
        // snapshot still ships when the tab closes.
        onCLS(report, { reportAllChanges: true });
        onINP(report);
        onLCP(report, { reportAllChanges: true });
        onFCP(report);
        onTTFB(report);
      } catch {
        // web-vitals dep missing / network error → silently skip.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return null;
}
