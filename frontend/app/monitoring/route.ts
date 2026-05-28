import { NextRequest, NextResponse } from 'next/server';

// Sentry client-beacon tunnel. Same-origin endpoint that forwards
// `application/x-sentry-envelope` POSTs to the project's Sentry ingest
// URL, so ad-blockers (uBlock / Brave / DDG / 1Blocker) that match the
// `*.sentry.io` ingest hostname can't silently drop browser error
// telemetry. Configured client-side via `Sentry.init({ tunnel })` in
// `instrumentation-client.ts` and (redundantly) via
// `withSentryConfig.tunnelRoute` in `next.config.ts`.
//
// This file exists as a manual fallback to the Sentry webpack plugin's
// virtual route handler. The plugin-generated handler was returning 404
// in prod (RESP-044 in `test-results/10-mobile.md` — verified via
// `GET /monitoring` + `POST /monitoring` against
// `lead-scraper-frontend.onrender.com`, both 404). A physical
// `app/monitoring/route.ts` always resolves in the Next 16 App Router
// regardless of plugin auto-injection state, so this guarantees the
// tunnel works whether or not Sentry's build step ran cleanly.

export const runtime = 'edge';
export const dynamic = 'force-dynamic';

// Envelope cap. Real envelopes (events + transactions + sessions + RUM
// vitals + a few breadcrumbs) almost always fit in 64 KB; 1 MB is a
// generous DoS guard that still lets pathological breadcrumb floods
// through. Anything larger is dropped at the proxy with 413.
const MAX_ENVELOPE_BYTES = 1_000_000;

const _DSN = process.env.NEXT_PUBLIC_SENTRY_DSN || '';

function _parseDsn(dsn: string): { host: string; projectId: string } | null {
  try {
    const u = new URL(dsn);
    // DSN format: https://<publicKey>@<host>/<projectId>
    const projectId = u.pathname.replace(/^\/+/, '');
    if (!u.hostname || !projectId) return null;
    return { host: u.hostname, projectId };
  } catch {
    return null;
  }
}

const _CONFIG = _parseDsn(_DSN);

export async function POST(request: NextRequest) {
  // Build-time or runtime DSN missing — 204 keeps the client SDK quiet
  // (it treats 2xx as success) and avoids spamming the operator with
  // synthetic 5xx noise in their own dashboards.
  if (!_CONFIG) {
    return new NextResponse(null, { status: 204 });
  }

  const ct = request.headers.get('content-type') || '';
  if (!ct.startsWith('application/x-sentry-envelope')) {
    return new NextResponse(null, { status: 415 });
  }

  const buf = await request.arrayBuffer();
  if (buf.byteLength > MAX_ENVELOPE_BYTES) {
    return new NextResponse(null, { status: 413 });
  }

  // First line of an envelope is a JSON header containing the DSN that
  // the SDK was configured with. Validate it matches THIS project so
  // the tunnel can't be used to ferry envelopes to arbitrary Sentry
  // projects (anti-abuse).
  try {
    const text = new TextDecoder().decode(buf);
    const firstLine = text.split('\n', 1)[0];
    const header = JSON.parse(firstLine) as { dsn?: string };
    if (!header.dsn) {
      return new NextResponse(null, { status: 400 });
    }
    const incoming = _parseDsn(header.dsn);
    if (!incoming || incoming.host !== _CONFIG.host || incoming.projectId !== _CONFIG.projectId) {
      return new NextResponse(null, { status: 403 });
    }
  } catch {
    return new NextResponse(null, { status: 400 });
  }

  const upstream = `https://${_CONFIG.host}/api/${_CONFIG.projectId}/envelope/`;
  try {
    const upstreamResponse = await fetch(upstream, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-sentry-envelope' },
      body: buf,
      // 5s — Sentry ingest typically responds in <200ms; anything slower
      // is the upstream having a bad day and the client doesn't need to
      // block on it. AbortSignal.timeout is edge-runtime safe (Node 18+).
      signal: AbortSignal.timeout(5_000),
    });
    return new NextResponse(null, { status: upstreamResponse.ok ? 200 : 502 });
  } catch {
    return new NextResponse(null, { status: 502 });
  }
}

// Sentry sometimes preflights with OPTIONS when tunneling cross-origin;
// same-origin tunneling shouldn't need it, but answering 204 is cheap
// and avoids surprise CORS rejections from browser fetch().
export async function OPTIONS() {
  return new NextResponse(null, { status: 204 });
}
