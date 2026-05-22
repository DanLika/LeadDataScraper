/**
 * Force a URL to a safe `http:`/`https:` absolute form, or `''` when the
 * input carries a non-web scheme (`javascript:`, `data:`, …) or can't be
 * parsed. Use for every `href` built from attacker-controllable data.
 */
export function ensureProtocol(url: string | null | undefined): string
