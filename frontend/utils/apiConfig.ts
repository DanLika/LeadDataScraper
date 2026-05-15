/**
 * Frontend talks to the backend through a Next.js server-route proxy at
 * /api/proxy/*. The proxy attaches the backend X-API-Key on the server side,
 * so the key is never shipped to the browser bundle.
 *
 * Callers keep using apiFetch(`${API_BASE_URL}/leads`) — only the base URL
 * changed.
 */
export const API_BASE_URL = '/api/proxy';

/**
 * Drop-in fetch wrapper. Kept for compatibility; the proxy now owns auth.
 *
 * Forces `cache: 'no-store'` so authed responses don't sit in the browser's
 * bfcache or any intermediate. The proxy stamps the matching
 * `Cache-Control: no-store` on responses; this pairs the defense on the
 * request side. Callers may still override via init.cache if they need
 * a specific cache mode.
 */
export function apiFetch(input: string | URL | Request, init?: RequestInit): Promise<Response> {
  return fetch(input, { cache: 'no-store', ...init });
}
