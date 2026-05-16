'use client'

import { Suspense, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { createClient } from '@/utils/supabase/client'

// Only accept same-origin relative paths. Allowlist-shaped: must match a
// strict character set so WHATWG URL parser cannot smuggle the redirect to
// another origin via control chars (\t / \n / \r get stripped by the parser
// and would otherwise let `/\t//evil.com` resolve to https://evil.com/),
// embedded backslashes (normalised to `/` for special-scheme URLs), or
// protocol-relative `//evil.com`. Prevents open-redirect → phishing-assist
// on the auth flow.
function sanitizeNext(raw: string | null): string {
  if (!raw) return '/'
  if (raw.length > 512) return '/'
  // Only printable ASCII path-safe chars. Explicitly excludes \t \n \r and
  // every other control byte, as well as `\` which WHATWG normalises to `/`.
  if (!/^\/[A-Za-z0-9._~\-/?#=&%+@:!$'()*,;]*$/.test(raw)) return '/'
  if (raw.startsWith('//')) return '/'
  return raw
}

function LoginForm() {
  const router = useRouter()
  const params = useSearchParams()
  const next = sanitizeNext(params.get('next'))

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: { preventDefault: () => void }) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    const supabase = createClient()
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    setBusy(false)
    if (error) {
      setError(error.message)
      return
    }
    router.replace(next)
    router.refresh()
  }

  return (
    <main
      style={{
        minHeight: '100dvh',
        display: 'grid',
        placeItems: 'center',
        background: 'var(--surface-base, #0b0b10)',
        color: 'var(--text-primary, #e8e8ee)',
        padding: '24px',
      }}
    >
      <form
        onSubmit={onSubmit}
        aria-labelledby="login-title"
        style={{
          width: 'min(380px, 100%)',
          background: 'var(--card-bg, #14141c)',
          border: '1px solid var(--border-subtle, #23232d)',
          borderRadius: 12,
          padding: 28,
          display: 'grid',
          gap: 16,
        }}
      >
        <h1 id="login-title" style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>
          Sign in
        </h1>
        <label style={{ display: 'grid', gap: 6, fontSize: 14 }}>
          <span>Email</span>
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{
              padding: '10px 12px',
              minHeight: 44,
              background: 'var(--surface-elevated, #1c1c26)',
              color: 'inherit',
              border: '1px solid var(--border, #2a2a36)',
              borderRadius: 8,
            }}
          />
        </label>
        <label style={{ display: 'grid', gap: 6, fontSize: 14 }}>
          <span>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{
              padding: '10px 12px',
              minHeight: 44,
              background: 'var(--surface-elevated, #1c1c26)',
              color: 'inherit',
              border: '1px solid var(--border, #2a2a36)',
              borderRadius: 8,
            }}
          />
        </label>
        {error && (
          <p role="alert" style={{ margin: 0, color: 'var(--error, #ef4444)', fontSize: 13 }}>
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy}
          aria-busy={busy}
          style={{
            minHeight: 44,
            padding: '10px 14px',
            borderRadius: 8,
            border: 'none',
            background: 'hsl(234, 89%, 64%)',
            color: '#fff',
            fontWeight: 600,
            cursor: busy ? 'progress' : 'pointer',
            opacity: busy ? 0.7 : 1,
          }}
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <p style={{ margin: 0, fontSize: 12, color: 'var(--text-muted, #888896)' }}>
          Accounts are provisioned in Supabase. Ask the operator if you need access.
        </p>
      </form>
    </main>
  )
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  )
}
