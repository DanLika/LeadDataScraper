import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";
import createNextIntlPlugin from "next-intl/plugin";

// next-intl plugin wires getRequestConfig (./i18n/request.ts) into the
// build so every server render can resolve the per-request locale +
// load its messages JSON. The wrapper is the outermost layer below so
// the Sentry config is applied first, then next-intl wraps the result.
const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

// NOTE: Content-Security-Policy is set per-request in `frontend/proxy.ts`
// so the script-src directive can include a per-request `'nonce-<n>'`
// alongside `'strict-dynamic'`. Next 16 RSC streams its bootstrap as inline
// `<script>self.__next_f.push(...)</script>` blocks — a static
// `script-src 'self'` here would block hydration in prod (sev-1, see
// docs/findings/2026-05-22-csp-blocks-prod-hydration.md). All other
// security headers stay static below.

const baseHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  // Phase D backport from bookbed-website (CLAUDE.md "Cross-repo strategy").
  // COOP isolates the browsing context group — blocks Spectre-class
  // cross-window timing attacks and drops `window.opener` from any
  // other-origin window.
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  // CORP on our responses → other origins can't load them as
  // <img>/<script>/<iframe>. This site doesn't host cross-origin
  // embeddable assets; Supabase + Sentry are accessed via same-origin
  // proxy (/api/proxy/*) and tunnel (/monitoring), so `same-origin` is
  // safe. Skipped COEP `require-corp` — would demand explicit CORP on
  // every Supabase / Sentry response; defer until that's proven out.
  { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
  // Legacy Flash / Adobe Reader plugin policy — blocks crossdomain.xml
  // loading. Zero compatibility cost on a modern SPA.
  { key: "X-Permitted-Cross-Domain-Policies", value: "none" },
];

// HTML pages must not bfcache after sign-out. `_next/static` chunks remain
// cacheable (immutable content-hashed assets); the no-store + Vary: Cookie
// pair only applies to dynamic routes.
const pageNoCacheHeaders = [
  { key: "Cache-Control", value: "private, no-store, max-age=0" },
  { key: "Vary", value: "Cookie" },
];
// HSTS is gated to production. In dev (localhost/HTTP) the directive is
// either ignored or, if served briefly over HTTPS, pins the hostname for two
// years — annoying when local tooling certificates change.
if (process.env.NODE_ENV === "production") {
  baseHeaders.push({
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  });
}

// Release identifier for Sentry. Fallback chain (build-time, lowest to
// highest priority):
//   1. RENDER_GIT_COMMIT  — Render auto-injects on every build/deploy
//   2. SENTRY_RELEASE     — manual override (e.g. semver tag)
//   3. NEXT_PUBLIC_SENTRY_RELEASE — same, exposed in client bundle
// Resolved here so both server config + client bundle pick up the same
// string. `env.*` is the standard Next.js way to make a value available
// as `process.env.NEXT_PUBLIC_*` at runtime in the client.
const SENTRY_RELEASE =
  process.env.NEXT_PUBLIC_SENTRY_RELEASE ??
  process.env.SENTRY_RELEASE ??
  process.env.RENDER_GIT_COMMIT ??
  "unknown";

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_SENTRY_RELEASE: SENTRY_RELEASE,
  },
  poweredByHeader: false,
  async headers() {
    return [
      { source: "/(.*)", headers: baseHeaders },
      // Apply no-store only to HTML routes; exclude static assets.
      { source: "/", headers: pageNoCacheHeaders },
      { source: "/insights", headers: pageNoCacheHeaders },
      { source: "/campaigns", headers: pageNoCacheHeaders },
      { source: "/login", headers: pageNoCacheHeaders },
    ];
  },
  productionBrowserSourceMaps: false,
};

// Wrap with Sentry's Next.js config so the webpack plugin (a) uploads
// source maps to Sentry at build time using SENTRY_AUTH_TOKEN +
// SENTRY_ORG + SENTRY_PROJECT envs, (b) sets the release name to the
// git SHA via SENTRY_RELEASE / NEXT_PUBLIC_SENTRY_RELEASE, (c) tunnels
// client beacons through /monitoring so ad-blockers don't drop them.
//
// `hideSourceMaps: true` means we upload maps to Sentry but never ship
// them to the public — stack traces resolve in Sentry only, not in the
// browser DevTools (matches `productionBrowserSourceMaps: false` above).
//
// `silent: !process.env.CI` keeps `npm run build` quiet locally but
// loud in CI.
export default withNextIntl(
  withSentryConfig(nextConfig, {
    org: process.env.SENTRY_ORG,
    project: process.env.SENTRY_PROJECT,
    silent: !process.env.CI,
    widenClientFileUpload: true,
    // Sentry v10 renamed `hideSourceMaps` → `sourcemaps.deleteSourcemapsAfterUpload`.
    // Maps still upload to Sentry (used for symbolication) but are
    // deleted from the build output so the public CDN doesn't serve them.
    sourcemaps: { deleteSourcemapsAfterUpload: true },
    disableLogger: true,
    tunnelRoute: "/monitoring",
    release: { name: SENTRY_RELEASE },
  }),
);
