/**
 * Force a URL to a safe `http:`/`https:` absolute form, or `''` when the
 * input carries a non-web scheme (`javascript:`, `data:`, …) or can't be
 * parsed. Use for every `href` built from attacker-controllable data.
 */
export function ensureProtocol(url: string | null | undefined): string

/**
 * Open-redirect guard for the login `?next=` parameter. Returns the
 * input only if it is a strict same-origin relative path; otherwise
 * returns `'/'`. Rejects protocol-relative `//host`, control chars,
 * backslashes, and `@`/`:` (userinfo phishing-display form).
 */
export function sanitizeNext(raw: string | null | undefined): string
