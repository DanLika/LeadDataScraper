import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@/utils/supabase/server'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// Mirror /api/proxy: reject state-changing POSTs whose Origin isn't on the
// allowlist. SameSite=Lax already blocks cookie-bearing cross-site fetch, but
// a top-level form-POST from an attacker's page could still hit this endpoint
// with the session cookie attached and log the user out. Cheap defence-in-depth.
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS || 'http://localhost:3000')
  .split(',')
  .map((o) => o.trim())
  .filter(Boolean)

export async function POST(req: NextRequest) {
  const origin = req.headers.get('origin')
  if (origin && !ALLOWED_ORIGINS.includes(origin)) {
    return NextResponse.json({ error: 'origin not allowed' }, { status: 403 })
  }
  const supabase = await createClient()
  await supabase.auth.signOut()
  return NextResponse.json({ ok: true })
}
