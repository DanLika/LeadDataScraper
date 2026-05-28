'use client'

import { Suspense, useActionState } from 'react'
import { useFormStatus } from 'react-dom'
import { useSearchParams } from 'next/navigation'
import { signInAction, type LoginActionState } from './actions'

/**
 * The page is now a thin <form action={signInAction}> wrapper. signInWithPassword
 * runs server-side in actions.ts so the Supabase session cookie is set with
 * HttpOnly via the server.ts floor — JS can no longer read the access token
 * via document.cookie. Path-sanitisation lives in actions.ts too.
 *
 * useFormStatus drives the busy state of the submit button so we keep the
 * aria-busy semantics from the previous client-component version.
 */

function SubmitButton() {
  const { pending } = useFormStatus()
  return (
    <button
      type="submit"
      disabled={pending}
      aria-busy={pending}
      style={{
        minHeight: 44,
        padding: '10px 14px',
        borderRadius: 8,
        border: 'none',
        background: 'hsl(234, 89%, 64%)',
        color: '#fff',
        fontWeight: 600,
        cursor: pending ? 'progress' : 'pointer',
        opacity: pending ? 0.7 : 1,
      }}
    >
      {pending ? 'Signing in…' : 'Sign in'}
    </button>
  )
}

function LoginForm() {
  const params = useSearchParams()
  const nextRaw = params.get('next') ?? '/'
  const [state, formAction] = useActionState<LoginActionState, FormData>(
    signInAction,
    undefined,
  )

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
        action={formAction}
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
        <input type="hidden" name="next" value={nextRaw} />
        <label style={{ display: 'grid', gap: 6, fontSize: 14 }}>
          <span>Email</span>
          <input
            type="email"
            name="email"
            autoComplete="email"
            required
            // fontSize: 16 prevents iOS Safari auto-zoom on focus (RESP-016).
            // <16px triggers zoom + sticky viewport that doesn't reset on blur.
            style={{
              padding: '10px 12px',
              minHeight: 44,
              fontSize: 16,
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
            name="password"
            autoComplete="current-password"
            required
            // fontSize: 16 — see email input comment above (iOS zoom-on-focus guard).
            style={{
              padding: '10px 12px',
              minHeight: 44,
              fontSize: 16,
              background: 'var(--surface-elevated, #1c1c26)',
              color: 'inherit',
              border: '1px solid var(--border, #2a2a36)',
              borderRadius: 8,
            }}
          />
        </label>
        {state?.error && (
          <p role="alert" style={{ margin: 0, color: 'var(--error, #ef4444)', fontSize: 13 }}>
            {state.error}
          </p>
        )}
        <SubmitButton />
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
