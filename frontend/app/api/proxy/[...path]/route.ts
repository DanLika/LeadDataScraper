import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const BACKEND_URL = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
const API_SECRET_KEY = process.env.API_SECRET_KEY || '';

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

  const { path } = await ctx.params;
  const subPath = (path || []).map(encodeURIComponent).join('/');
  const search = req.nextUrl.search || '';
  const target = `${BACKEND_URL.replace(/\/$/, '')}/${subPath}${search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  headers.set('X-API-Key', API_SECRET_KEY);

  // Trust only platform-injected client IP. The standard XFF/X-Real-IP were
  // stripped above (HOP_BY_HOP) because clients can forge them when Next is
  // exposed directly. x-vercel-forwarded-for is set by the Vercel edge and
  // unreachable to clients; we re-emit it as XFF so the backend's rate limiter
  // can bucket per user.
  const trustedIp = req.headers.get('x-vercel-forwarded-for') || '';
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
