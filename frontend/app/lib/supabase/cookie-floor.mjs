/**
 * True floor for Supabase session cookies. Spread the SDK's options first,
 * then hard-set protected keys so Supabase can tighten (Strict / longer
 * maxAge) but never loosen (SameSite=None / httpOnly=false).
 *
 * `secure: true` unconditionally — localhost is a "trustworthy origin" per
 * WHATWG so dev still works, and we don't depend on NODE_ENV being set
 * correctly in CI/deploy to keep session cookies encrypted.
 *
 * Pure function so it's unit-testable without a Next.js request context.
 * Used by frontend/app/lib/supabase/middleware.ts and server.ts — both cookie
 * write paths share the same contract, pinned by cookie-floor.test.mjs.
 */
export function hardenCookieOptions(options, cookieName, currentHost) {
  const requestedStrict =
    typeof options?.sameSite === 'string' &&
    options.sameSite.toLowerCase() === 'strict'

  const hardened = {
    ...options,
    sameSite: requestedStrict ? 'strict' : 'lax',
    httpOnly: true,
    secure: true,
  }

  // __Host- prefix validation
  if (typeof cookieName === 'string' && cookieName.startsWith('__Host-')) {
    hardened.path = '/'
    delete hardened.domain
  } else if (typeof hardened.domain === 'string' && typeof currentHost === 'string') {
    // Domain narrowing
    const cleanDomain = hardened.domain.startsWith('.') ? hardened.domain.slice(1) : hardened.domain
    const cleanHost = currentHost.split(':')[0] // strip port if any

    if (cleanDomain !== cleanHost && !cleanHost.endsWith('.' + cleanDomain)) {
       // Disallow domains that are not the current host or a valid parent domain
       delete hardened.domain
    } else if (!cleanDomain.includes('.')) {
       // Disallow TLD-only or single-label domains as a precaution if they match
       // Usually `localhost` is allowed but `domain=localhost` is technically not needed.
       // The browser rejects `.com`, we reject it here too.
       if (cleanDomain !== 'localhost') {
         delete hardened.domain
       }
    }
  }

  return hardened
}
