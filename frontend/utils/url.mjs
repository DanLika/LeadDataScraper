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
