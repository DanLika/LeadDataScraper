/**
 * Hardened URL-scheme guard for rendering attacker-controllable links.
 *
 * Lead `website` + social-profile fields come from Google-Maps scrapes
 * and CSV uploads — both attacker-controllable. React does NOT sanitise
 * `href` values, so `<a href="javascript:…">` or `<a href="data:…">`
 * built from a scraped field is a live DOM-XSS vector.
 *
 * `ensureProtocol` forces every candidate through the WHATWG `URL`
 * parser and an exact `http:` / `https:` protocol allowlist; anything
 * else (`javascript:`, `data:`, `file:`, malformed) collapses to `''`,
 * which renders as an inert `<a href="">`.
 *
 * Pure function, no DOM — unit-tested in url.test.mjs. Kept as `.mjs`
 * (not `.ts`) so `node --test` can run it without a build step, mirror-
 * ing utils/supabase/cookie-floor.mjs.
 */
export function ensureProtocol(url) {
  if (!url) return '';
  const trimmed = String(url).trim();
  if (!trimmed) return '';
  const candidate = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
  try {
    const u = new URL(candidate);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';
    return u.toString();
  } catch {
    return '';
  }
}

/**
 * Open-redirect guard for the login `?next=` parameter.
 *
 * Accepts ONLY same-origin relative paths. Allowlist-shaped: the value
 * must match a strict character set so the WHATWG URL parser cannot
 * smuggle the redirect to another origin via control chars (`\t \n \r`
 * are stripped by the parser and would otherwise let `/\t//evil.com`
 * resolve to `https://evil.com/`), embedded backslashes (normalised to
 * `/` for special-scheme URLs), or a protocol-relative `//evil.com`.
 *
 * `@` and `:` are deliberately excluded from the allowlist so a value
 * like `/@evil.com/foo` — which mimics the `user@host` userinfo URL
 * form and is a phishing-display aid — can't pass. Neither character is
 * needed for a legitimate same-origin path in this app.
 *
 * Anything that fails the checks collapses to `'/'` (the app root).
 */
export function sanitizeNext(raw) {
  if (!raw) return '/';
  if (typeof raw !== 'string') return '/';
  if (raw.length > 512) return '/';
  if (!/^\/[A-Za-z0-9._~\-/?#=&%+!$'()*,;]*$/.test(raw)) return '/';
  if (raw.startsWith('//')) return '/';
  return raw;
}
