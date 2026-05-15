import { createServerClient } from '@supabase/ssr'
import { NextResponse, type NextRequest } from 'next/server'

export async function updateSession(request: NextRequest) {
  let supabaseResponse = NextResponse.next({
    request,
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
            request,
          })
          // True floor: spread options first, then hard-set protected keys
          // so Supabase can tighten (Strict / longer maxAge) but never loosen.
          // SameSite: keep 'strict' if Supabase asked for it, otherwise pin
          //   to 'lax' (rejects 'none' even if a future SDK sets it).
          // HttpOnly: always true — JS access to the session cookie is never
          //   needed by this app.
          // Secure: pinned in production regardless of what Supabase passes;
          //   in dev we leave it off so http://localhost works.
          const isProd = process.env.NODE_ENV === 'production'
          cookiesToSet.forEach(({ name, value, options }) => {
            const requestedStrict =
              (options?.sameSite as string | undefined)?.toLowerCase() === 'strict'
            const hardened = {
              ...options,
              sameSite: (requestedStrict ? 'strict' : 'lax') as 'lax' | 'strict',
              httpOnly: true,
              secure: isProd ? true : Boolean(options?.secure),
            }
            supabaseResponse.cookies.set(name, value, hardened)
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
    isPublicPrefix('/api/auth')
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
