import { handleTunnelRequest } from '@sentry/core';
import { NextRequest, NextResponse } from 'next/server';

// Sentry client-beacon tunnel. Same-origin endpoint that forwards
// envelope POSTs to the project's Sentry ingest URL, so ad-blockers
// (uBlock / Brave / DDG / 1Blocker) that match the `*.sentry.io` ingest
// hostname can't silently drop browser error telemetry. Configured
// client-side via `Sentry.init({ tunnel })` in `instrumentation-client.ts`
// and (redundantly) via `withSentryConfig.tunnelRoute` in `next.config.ts`.
//
// Implementation: delegates to `@sentry/core`'s canonical
// `handleTunnelRequest`. The previous bespoke handler rejected every
// real envelope with HTTP 415 because (1) it hard-checked
// `Content-Type: application/x-sentry-envelope`, but Sentry's tunnel
// transport deliberately sends `text/plain;charset=UTF-8` to skip the
// CORS preflight, and (2) it built the upstream URL without
// `?sentry_key=…&sentry_version=7&sentry_client=…`, which Sentry's
// ingest also rejects. The canonical handler addresses both: it
// ignores Content-Type, parses the envelope header for DSN validation
// (anti-SSRF), and uses `getEnvelopeEndpointWithUrlEncodedAuth` to
// build the upstream URL with the required auth params.

export const runtime = 'edge';
export const dynamic = 'force-dynamic';

// Envelope cap. Real envelopes (events + transactions + sessions + RUM
// vitals + a few breadcrumbs) almost always fit in 64 KB; 1 MB is a
// generous DoS guard that still lets pathological breadcrumb floods
// through. Fast-path check via Content-Length before the body is read.
const MAX_ENVELOPE_BYTES = 1_000_000;

const _DSN = process.env.NEXT_PUBLIC_SENTRY_DSN || '';
const _ALLOWED_DSNS: string[] = _DSN ? [_DSN] : [];

export async function POST(request: NextRequest) {
  // Build-time or runtime DSN missing — 204 keeps the client SDK quiet
  // (it treats 2xx as success) and avoids spamming the operator with
  // synthetic 5xx noise in their own dashboards.
  if (_ALLOWED_DSNS.length === 0) {
    return new NextResponse(null, { status: 204 });
  }

  const declaredLength = Number(request.headers.get('content-length') || '0');
  if (declaredLength > MAX_ENVELOPE_BYTES) {
    return new NextResponse(null, { status: 413 });
  }

  return handleTunnelRequest({ request, allowedDsns: _ALLOWED_DSNS });
}

// Sentry's tunnel transport uses text/plain to avoid CORS preflight,
// so OPTIONS shouldn't normally be hit. Answering 204 is cheap and
// avoids surprise CORS rejections if a non-default transport ever
// triggers one.
export async function OPTIONS() {
  return new NextResponse(null, { status: 204 });
}
