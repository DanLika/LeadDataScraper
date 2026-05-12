import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@/utils/supabase/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const BACKEND_URL = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
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

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  if (!API_SECRET_KEY) {
    return NextResponse.json(
      { error: 'API_SECRET_KEY not configured on server' },
      { status: 500 },
    );
  }

  // 1) Auth gate. Middleware already redirects HTML pages, but proxy traffic
  // (fetch/XHR) skips the redirect — so we re-check the session here. Without
  // this, anyone who can reach the proxy URL gets the server-side API key
  // attached to every request.
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  }

  // 2) Origin gate for state-changing methods — defence-in-depth CSRF block.
  // Same-origin XHR sends Origin; cross-origin attempts with a different
  // Origin are rejected even if the attacker tricks a logged-in user into
  // visiting their site (browser still sends our session cookie because we
  // use SameSite=Lax, but the Origin header reveals the cross-site call).
  if (!SAFE_METHODS.has(req.method.toUpperCase())) {
    const origin = req.headers.get('origin');
    if (origin && !ALLOWED_ORIGINS.includes(origin)) {
      return NextResponse.json({ error: 'origin not allowed' }, { status: 403 });
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
    const body = await req.arrayBuffer();
    if (body.byteLength > 0) init.body = body;
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch {
    return NextResponse.json(
      { error: 'Upstream backend unreachable' },
      { status: 502 },
    );
  }

  const respHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) respHeaders.set(key, value);
  });

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
