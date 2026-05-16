import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'

export async function createClient() {
  const cookieStore = await cookies()

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll()
        },
        setAll(cookiesToSet) {
          // Mirror middleware.ts true-floor: Supabase can tighten (Strict,
          // longer maxAge) but never loosen. HttpOnly is always true — JS
          // never needs to read the session cookie. Secure is pinned in
          // production. Important on Server Action paths (e.g. login) where
          // the cookie is set directly during the action handler, not via
          // a middleware-mediated request.
          try {
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
              cookieStore.set(name, value, hardened)
            })
          } catch {
            // The `setAll` method was called from a Server Component, which
            // is read-only for cookies. Safe to ignore — middleware refreshes
            // the session on the next request.
          }
        },
      },
    }
  )
}
