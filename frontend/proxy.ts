import { type NextRequest } from 'next/server'
import { updateSession } from '@/utils/supabase/middleware'

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || ''

// Per-request nonce + Content-Security-Policy. The static CSP in
// `next.config.ts` is dropped in favour of this per-request value so that
// Next 16 RSC's inline `<script>self.__next_f.push(...)</script>` bootstrap
// blocks can be allow-listed via `'nonce-<n>' 'strict-dynamic'`. Setting
// `x-nonce` on the inbound request makes Next.js stamp the same nonce onto
// its emitted inline + dynamic-import script tags automatically
// (https://nextjs.org/docs/app/guides/content-security-policy).
function buildCsp(nonce: string): string {
  const directives = [
    "default-src 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    // Next inlines a tiny style block; keep 'unsafe-inline' for CSS only.
    "style-src 'self' 'unsafe-inline'",
    // Production: nonce + strict-dynamic. Dev: keep 'unsafe-eval' for HMR.
    process.env.NODE_ENV === 'production'
      ? `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`
      : `script-src 'self' 'unsafe-eval' 'unsafe-inline' 'nonce-${nonce}'`,
    `img-src 'self' data: blob: ${SUPABASE_URL}`.trim(),
    "font-src 'self' data:",
    `connect-src 'self' ${SUPABASE_URL} ${SUPABASE_URL.replace(/^https:/, 'wss:')}`.trim(),
  ]
  return directives.join('; ')
}

function generateNonce(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  // btoa over a binary string — edge-runtime safe; no Buffer.
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin)
}

export async function proxy(request: NextRequest) {
  const nonce = generateNonce()
  const csp = buildCsp(nonce)
  // Build a NEW Headers object — mutating request.headers in place does NOT
  // propagate to RSC under Next 16; only headers passed via
  // `NextResponse.next({ request: { headers } })` reach the renderer.
  const requestHeaders = new Headers(request.headers)
  requestHeaders.set('x-nonce', nonce)
  // Mirror CSP into the request headers too — some Next internals read it
  // there to choose strategies for streamed inline scripts.
  requestHeaders.set('Content-Security-Policy', csp)

  const response = await updateSession(request, requestHeaders)
  // Always set CSP on the response (next() and redirect() alike) so the
  // browser enforces the same policy that RSC stamped its scripts under.
  response.headers.set('Content-Security-Policy', csp)
  return response
}

export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * Feel free to modify this pattern to include more paths.
     */
    '/((?!_next/static|_next/image|favicon.ico|robots.txt|\\.well-known/|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)',
  ],
}
