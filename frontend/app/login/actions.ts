'use server'

import { redirect } from 'next/navigation'
import { headers } from 'next/headers'
import { createClient } from '@/utils/supabase/server'
import { checkLoginRate, clearLoginRate } from '@/utils/loginThrottle'

// Same trusted-IP header the /api/proxy route reads, so the rate-limit
// bucket key matches across the auth path and the API path. Anything else
// is forgeable when Next is exposed directly.
const TRUSTED_CLIENT_IP_HEADER = (process.env.TRUSTED_CLIENT_IP_HEADER || 'x-vercel-forwarded-for').toLowerCase()

/**
 * Only accept same-origin relative paths. Allowlist-shaped: must match a
 * strict character set so WHATWG URL parser cannot smuggle the redirect to
 * another origin via control chars (\t / \n / \r get stripped by the parser
 * and would otherwise let `/\t//evil.com` resolve to https://evil.com/),
 * embedded backslashes (normalised to `/` for special-scheme URLs), or
 * protocol-relative `//evil.com`. Mirrors the client-side sanitizeNext that
 * used to live in page.tsx.
 */
function sanitizeNext(raw: string | null | undefined): string {
  if (!raw) return '/'
  if (raw.length > 512) return '/'
  // `@` and `:` are excluded to avoid `/@evil.com/...` phishing-display
  // patterns that mimic the userinfo URL form. Neither is needed for
  // legitimate same-origin paths in this app.
  if (!/^\/[A-Za-z0-9._~\-/?#=&%+!$'()*,;]*$/.test(raw)) return '/'
  if (raw.startsWith('//')) return '/'
  return raw
}

export type LoginActionState = { error: string | null } | undefined

export async function signInAction(
  _prevState: LoginActionState,
  formData: FormData,
): Promise<LoginActionState> {
  const email = String(formData.get('email') ?? '').trim()
  const password = String(formData.get('password') ?? '')
  const next = sanitizeNext(formData.get('next') as string | null)

  if (!email || !password) {
    return { error: 'Email and password are required.' }
  }

  // App-layer brute-force gate. Bounded at 5 attempts per 60s per trusted
  // IP. Supabase rate-limits at its edge too; this layer is in front of
  // signInWithPassword so a self-hosted Supabase / looser edge config
  // doesn't leave the operator account exposed. Counter increments on
  // every attempt regardless of outcome — only a successful credential
  // check (below) clears it.
  const hdrs = await headers()
  const trustedIp = (hdrs.get(TRUSTED_CLIENT_IP_HEADER) || '').split(',')[0]?.trim() || null
  const rate = checkLoginRate(trustedIp)
  if (!rate.allowed) {
    return { error: `Too many sign-in attempts. Try again in ${rate.retryAfterSeconds}s.` }
  }

  const supabase = await createClient()
  const { error } = await supabase.auth.signInWithPassword({ email, password })
  if (error) {
    // Surface Supabase's generic message — it doesn't reveal user-existence,
    // and the underlying response is also rate-limited at the Supabase edge.
    return { error: error.message }
  }

  // Successful credential check — release the per-IP counter so a
  // legitimate user who fat-fingered the password a few times before
  // succeeding doesn't lock themselves out for the rest of the window.
  clearLoginRate(trustedIp)

  // Cookies were set by the server.ts setAll floor (HttpOnly, SameSite=Lax,
  // Secure in prod). redirect() throws — never returns, so the prev-state
  // type isn't returned from this branch.
  redirect(next)
}
