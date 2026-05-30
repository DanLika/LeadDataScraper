import { createServerClient } from '@supabase/ssr'
import { NextResponse, type NextRequest } from 'next/server'
import { hardenCookieOptions } from './cookie-floor.mjs'

// `requestHeaders` is the (possibly augmented) Headers object that should be
// forwarded to the downstream Next renderer. `proxy.ts` injects `x-nonce`
// into it so Next 16 RSC can stamp the per-request nonce onto its
// auto-emitted inline `<script>` bootstrap blocks. Without the explicit
// `{ request: { headers: ... } }` shape, mutations to `request.headers` do
// NOT propagate to RSC — verified empirically (zero `nonce="…"` attrs on
// the `__next_f.push(...)` tags otherwise).
export async function updateSession(
  request: NextRequest,
  requestHeaders: Headers = request.headers,
) {
  let supabaseResponse = NextResponse.next({
    request: { headers: requestHeaders },
  })

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll()
        },
        setAll(cookiesToSet) {
          // In-memory mirror onto the request object so downstream handlers in
          // this same request cycle see the refreshed values. These cookies
          // never reach the browser — only the response.cookies.set() below
          // appears in Set-Cookie.
          // nosemgrep
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value))
          supabaseResponse = NextResponse.next({
            request: { headers: requestHeaders },
          })
          // Cookie floor lives in cookie-floor.mjs (pure helper, unit-tested).
          // Supabase can tighten (Strict / longer maxAge) but never loosen.
          cookiesToSet.forEach(({ name, value, options }) => {
            supabaseResponse.cookies.set(
              name,
              value,
              hardenCookieOptions(options, name),
            )
          })
        },
      },
    }
  )

  // IMPORTANT: Avoid writing any logic between createServerClient and
  // supabase.auth.getUser(). A simple mistake can make it very hard to debug
  // issues with users being logged out unnecessarily.

  const {
    data: { user },
  } = await supabase.auth.getUser()

  // Anonymous traffic is redirected to /login. Auth callback + /login itself
  // stay open so users can sign in. /api/auth/* handles credential exchange.
  // The /api/proxy/[...path] route re-checks auth.getUser() — defence-in-depth
  // because middleware doesn't run on every Node-runtime route shape.
  // Exact match or trailing-slash subpath only — prevents an accidental future
  // route like `/login-internal` or `/authentication-guide` from being made
  // public by string-prefix overlap.
  const path = request.nextUrl.pathname
  const isPublicPrefix = (prefix: string) =>
    path === prefix || path.startsWith(prefix + '/')
  const isPublic =
    isPublicPrefix('/login') ||
    isPublicPrefix('/auth') ||
    isPublicPrefix('/api/auth') ||
    // Sentry tunnel route (configured as `tunnelRoute: '/monitoring'` in
    // next.config.ts's withSentryConfig). Sentry's @sentry/nextjs creates
    // this route at build time to proxy client beacons to sentry.io,
    // bypassing ad-blockers. The route must reach an unauthenticated user
    // too — errors on /login itself happen BEFORE a session exists, and
    // those events are exactly the ones an operator most wants to see in
    // Sentry. Public-allowlisting the path keeps the boundary tight (only
    // the tunnel route, not arbitrary /monitoring/*).
    isPublicPrefix('/monitoring') ||
    // Web-vitals beacons (`WebVitalsReporter` mounted in `app/layout.tsx`)
    // fire on EVERY page including `/login` — before a session exists.
    // Without the allowlist the beacon POST 307→/login and the LCP / INP
    // for the auth screen are lost (Phase 15 finding #4). Backend gates
    // `/metrics` with X-API-Key + slowapi rate-limit; the payload is a
    // bounded Pydantic WebVitalsMetric (Literal-allowlisted name,
    // bounded value/rating/path/id) so opening the path to anonymous
    // POST doesn't widen any attack surface.
    path === '/api/proxy/metrics'
  if (!user && !isPublic) {
    const url = request.nextUrl.clone()
    url.pathname = '/login'
    url.searchParams.set('next', path + request.nextUrl.search)
    return NextResponse.redirect(url)
  }

  // IMPORTANT: You *must* return the supabaseResponse object as it is. If you're
  // creating a new response object with NextResponse.next() make sure to:
  // 1. Pass the request in it, like so:
  //    const myNewResponse = NextResponse.next({ request })
  // 2. Copy over the cookies, like so:
  //    myNewResponse.cookies.setAll(supabaseResponse.cookies.getAll())
  // 3. Change the myNewResponse object to fit your needs, but avoid mutating
  //    the cookies!
  // 4. Finally, return myNewResponse.
  // If this is not done, you may be causing the browser and server to go out
  // of sync and terminate the user's session prematurely.

  return supabaseResponse
}
