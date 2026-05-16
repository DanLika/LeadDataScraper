'use server'

import { redirect } from 'next/navigation'
import { createClient } from '@/utils/supabase/server'

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
  if (!/^\/[A-Za-z0-9._~\-/?#=&%+@:!$'()*,;]*$/.test(raw)) return '/'
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

  const supabase = await createClient()
  const { error } = await supabase.auth.signInWithPassword({ email, password })
  if (error) {
    // Surface Supabase's generic message — it doesn't reveal user-existence,
    // and the underlying response is also rate-limited at the Supabase edge.
    return { error: error.message }
  }

  // Cookies were set by the server.ts setAll floor (HttpOnly, SameSite=Lax,
  // Secure in prod). redirect() throws — never returns, so the prev-state
  // type isn't returned from this branch.
  redirect(next)
}
