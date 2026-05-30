type SameSite = 'lax' | 'strict' | 'none' | boolean | undefined

export interface SupabaseCookieOptions {
  sameSite?: SameSite
  httpOnly?: boolean
  secure?: boolean
  maxAge?: number
  path?: string
  domain?: string
  expires?: Date
  [k: string]: unknown
}

export interface HardenedCookieOptions extends SupabaseCookieOptions {
  sameSite: 'lax' | 'strict'
  httpOnly: true
  secure: true
}

export function hardenCookieOptions(
  options: SupabaseCookieOptions | undefined,
  cookieName?: string,
): HardenedCookieOptions
