import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@/utils/supabase/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Render's `fromService.property: host` returns a bare hostname (no scheme).
// Prepend `https://` if no scheme is present. In production the resolved URL
// must be `https://` UNLESS the host is loopback (127.0.0.1 / localhost /
// *.localhost) — `npm run start` against a local backend is a valid
// integration-test path and pre-empting that breaks builds + local prod
// smoke tests. Anything else in production must be HTTPS so a misconfigured
// `BACKEND_URL` cannot silently downgrade prod traffic to plaintext over
// the Render network. The assertion runs at request time (inside `forward`)
// so module load during `next build` cannot trip it.
const _LOOPBACK_HOSTS_RE = /^https?:\/\/(127\.0\.0\.1|localhost|[^/]+\.localhost)(:\d+)?(\/|$)/i;
function _resolveBackendUrl(): string {
  const raw = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
  return /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
}
function _assertBackendSchemeAllowed(url: string): void {
  if (process.env.NODE_ENV !== 'production') return;
  if (url.startsWith('https://')) return;
  if (_LOOPBACK_HOSTS_RE.test(url)) return;
  throw new Error(`BACKEND_URL must use https:// in production; got ${url}`);
}
const BACKEND_URL = _resolveBackendUrl();
const API_SECRET_KEY = process.env.API_SECRET_KEY || '';
// Platform-injected client-IP header. Defaults to Vercel; set to
// 'x-forwarded-for' on Render or other XFF-using hosts. Never trust a header
// that clients can set when Next is the public entry point.
const TRUSTED_CLIENT_IP_HEADER = (process.env.TRUSTED_CLIENT_IP_HEADER || 'x-vercel-forwarded-for').toLowerCase();
// Origin allowlist for state-changing methods. Comma-separated. Defaults to
// the dev origin; production must set ALLOWED_ORIGINS to its public URL(s).
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS || 'http://localhost:3000')
  .split(',')
  .map((o) => o.trim())
  .filter(Boolean);
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

// Paths that require X-Admin-Token injection in addition to X-API-Key.
// Add new destructive routes here — keep this list audit-friendly. Match is
// exact on the joined dynamic segments (no query string, no prefixing).
const ADMIN_TOKEN_PATHS = new Set<string>([
  'leads/clear',
  'leads/clear-demo',
]);

// Match backend `/upload`'s MAX_UPLOAD_BYTES (50 MB). Defense-in-depth
// against an authed caller POSTing gigabyte-class bodies and forcing the
// Next.js process to buffer them in memory while waiting on upstream.
const MAX_PROXY_BODY_BYTES = 50 * 1024 * 1024;

const HOP_BY_HOP = new Set([
  'host',
  'connection',
  'content-length',
  'transfer-encoding',
  'upgrade',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailers',
  'accept-encoding',
  'x-forwarded-for',
  'x-forwarded-host',
  'x-forwarded-proto',
  'x-real-ip',
  'forwarded',
]);

// Common no-store headers for all early returns so error responses can't be cached.
const NO_STORE_HEADERS = { 'Cache-Control': 'no-store' } as const;

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  // Request-time guard — runs in the live serverless/edge environment, not
  // at build time. Fails closed with 500 if prod is configured to talk to a
  // non-HTTPS, non-loopback backend.
  try {
    _assertBackendSchemeAllowed(BACKEND_URL);
  } catch (e) {
    return NextResponse.json(
      { error: (e instanceof Error ? e.message : 'BACKEND_URL scheme invalid') },
      { status: 500, headers: NO_STORE_HEADERS },
    );
  }
  if (!API_SECRET_KEY) {
    return NextResponse.json(
      { error: 'API_SECRET_KEY not configured on server' },
      { status: 500, headers: NO_STORE_HEADERS },
    );
  }

  // 1) Auth gate. Middleware already redirects HTML pages, but proxy traffic
  // (fetch/XHR) skips the redirect — so we re-check the session here. Without
  // this, anyone who can reach the proxy URL gets the server-side API key
  // attached to every request.
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401, headers: NO_STORE_HEADERS });
  }

  // 2) Origin gate for state-changing methods — defence-in-depth CSRF block.
  // Modern browsers always send Origin on cross-origin POST/PUT/DELETE; reject
  // both mismatched and missing Origin so we fail closed on edge-case clients.
  // Cookies are SameSite=Lax already; this is belt-and-braces.
  if (!SAFE_METHODS.has(req.method.toUpperCase())) {
    const origin = req.headers.get('origin');
    if (!origin || !ALLOWED_ORIGINS.includes(origin)) {
      return NextResponse.json({ error: 'origin not allowed' }, { status: 403, headers: NO_STORE_HEADERS });
    }
  }

  const { path } = await ctx.params;
  const subPath = (path || []).map(encodeURIComponent).join('/');
  const search = req.nextUrl.search || '';
  const target = `${BACKEND_URL.replace(/\/$/, '')}/${subPath}${search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  headers.set('X-API-Key', API_SECRET_KEY);

  // Destructive paths require X-Admin-Token (CLAUDE.md security model).
  // Inject from server-side env only — never read from client. Clients
  // cannot set this header on their own; the gate is auth (session) + the
  // operator having configured ADMIN_TOKEN in the Next env. Path list is
  // ADMIN_TOKEN_PATHS at the top of this file — exact match on the
  // joined dynamic segments, so prefix collisions like `leads/clear-cache`
  // can't accidentally inherit the admin token.
  const joinedPath = (path || []).join('/');
  if (ADMIN_TOKEN_PATHS.has(joinedPath)) {
    const adminToken = process.env.ADMIN_TOKEN || '';
    if (adminToken) headers.set('X-Admin-Token', adminToken);
  }

  // Trust only the platform-injected client-IP header (TRUSTED_CLIENT_IP_HEADER
  // env). Standard XFF/X-Real-IP/Forwarded were stripped via HOP_BY_HOP because
  // clients can forge them when Next is exposed directly. The trusted header
  // (x-vercel-forwarded-for on Vercel, x-forwarded-for on Render) is read from
  // the ORIGINAL request before stripping; we re-emit its first hop as XFF so
  // the backend's rate limiter can bucket per user.
  const trustedIp = req.headers.get(TRUSTED_CLIENT_IP_HEADER) || '';
  if (trustedIp) headers.set('X-Forwarded-For', trustedIp.split(',')[0].trim());

  const method = req.method.toUpperCase();
  const init: RequestInit & { duplex?: 'half' } = {
    method,
    headers,
    redirect: 'manual',
    cache: 'no-store',
  };

  if (method !== 'GET' && method !== 'HEAD') {
    // Fast-fail on declared Content-Length so an attacker cannot stream
    // an oversized body to make us buffer the prefix before rejecting.
    const declaredLength = Number(req.headers.get('content-length') ?? '0');
    if (declaredLength > MAX_PROXY_BODY_BYTES) {
      return NextResponse.json(
        { error: 'Payload too large' },
        { status: 413, headers: NO_STORE_HEADERS },
      );
    }
    const body = await req.arrayBuffer();
    // Belt-and-braces: chunked / unset Content-Length still gets capped
    // after buffering. Match backend `/upload`'s 50 MB ceiling so the
    // failure mode is consistent regardless of whether the cap trips at
    // the proxy or at FastAPI.
    if (body.byteLength > MAX_PROXY_BODY_BYTES) {
      return NextResponse.json(
        { error: 'Payload too large' },
        { status: 413, headers: NO_STORE_HEADERS },
      );
    }
    if (body.byteLength > 0) init.body = body;
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch {
    return NextResponse.json(
      { error: 'Upstream backend unreachable' },
      { status: 502, headers: NO_STORE_HEADERS },
    );
  }

  const respHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    const k = key.toLowerCase();
    // Drop Server header on the way out — defends against fingerprint leak
    // if uvicorn is ever started without --no-server-header.
    if (HOP_BY_HOP.has(k) || k === 'server') return;
    respHeaders.set(key, value);
  });
  // API responses must not be cached anywhere (browser, intermediate proxies).
  respHeaders.set('Cache-Control', 'no-store');

  return new NextResponse(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: respHeaders,
  });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const DELETE = forward;
export const PATCH = forward;
export const OPTIONS = forward;
