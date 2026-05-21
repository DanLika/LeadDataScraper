/**
 * True floor for Supabase session cookies. Spread the SDK's options first,
 * then hard-set protected keys so Supabase can tighten (Strict / longer
 * maxAge) but never loosen (SameSite=None / httpOnly=false).
 *
 * Pure function so it's unit-testable without a Next.js request context.
 * Used by frontend/utils/supabase/middleware.ts and server.ts — both cookie
 * write paths share the same contract, pinned by cookie-floor.test.mjs.
 */
export function hardenCookieOptions(options, isProd) {
  const requestedStrict =
    typeof options?.sameSite === 'string' &&
    options.sameSite.toLowerCase() === 'strict'
  return {
    ...options,
    sameSite: requestedStrict ? 'strict' : 'lax',
    httpOnly: true,
    secure: isProd ? true : Boolean(options?.secure),
  }
}
