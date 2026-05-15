import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@/utils/supabase/server'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// Mirror /api/proxy: reject state-changing POSTs whose Origin isn't on the
// allowlist. Modern browsers always send Origin on cross-origin POST per
// WHATWG Fetch — reject both mismatched and missing so we fail closed on
// edge-case clients. SameSite=Lax already blocks cookie-bearing cross-site
// fetch; this is belt-and-braces.
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS || 'http://localhost:3000')
  .split(',')
  .map((o) => o.trim())
  .filter(Boolean)

const NO_STORE_HEADERS = { 'Cache-Control': 'no-store' } as const

export async function POST(req: NextRequest) {
  const origin = req.headers.get('origin')
  if (!origin || !ALLOWED_ORIGINS.includes(origin)) {
    return NextResponse.json({ error: 'origin not allowed' }, { status: 403, headers: NO_STORE_HEADERS })
  }
  const supabase = await createClient()
  await supabase.auth.signOut()
  return NextResponse.json({ ok: true }, { headers: NO_STORE_HEADERS })
}
