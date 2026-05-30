import { createServerClient } from '@supabase/ssr'
import { cookies, headers } from 'next/headers'
import { hardenCookieOptions } from './cookie-floor.mjs'

export async function createClient() {
  const cookieStore = await cookies()
  const headersList = await headers()
  const requestHost = headersList.get('host') ?? undefined

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll()
        },
        setAll(cookiesToSet) {
          // Cookie floor lives in cookie-floor.mjs (pure helper, unit-tested).
          // Used on Server Action paths (e.g. login) where the cookie is set
          // directly during the action handler, not via middleware.
          try {
            cookiesToSet.forEach(({ name, value, options }) => {
              cookieStore.set(name, value, hardenCookieOptions(options, requestHost))
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
