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
export async function apiFetch(input: string | URL | Request, init?: RequestInit): Promise<Response> {
  // Offline → enqueue mutations for replay on reconnect, fail-fast GETs.
  // We don't pretend the request succeeded; callers still see a rejection
  // and the OfflineBanner shows the queued count + auto-drains on online.
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    const method = (init?.method || 'GET').toUpperCase();
    const url = typeof input === 'string' ? input : input.toString();
    if (method !== 'GET' && method !== 'HEAD') {
      const { offlineQueue } = await import('./offlineQueue');
      offlineQueue.enqueue(`${method} ${url}`, url, init || {});
      throw new Error('Offline — request queued for retry');
    }
    throw new Error('Offline — no network');
  }
  const response = await fetch(input, { cache: 'no-store', ...init });
  // Middleware redirects unauthenticated proxy traffic to /login (HTML).
  // Without this guard, callers do `await resp.json()` on the login HTML
  // and crash with `Unexpected token '<' is not valid JSON`. Surface as
  // an auth boundary instead — caller can route to /login.
  if (response.redirected && response.url.includes('/login')) {
    if (typeof window !== 'undefined') {
      window.location.href = '/login';
    }
    throw new Error('Session expired');
  }
  // 401 from /api/proxy/* means the Supabase session expired or got revoked
  // (proxy revalidates getUser() on every call — see app/api/proxy/[...path]
  // /route.ts:99). Bounce to /login with ?next= so the user returns to the
  // page they were on, instead of letting every caller pop a generic error
  // toast and stay on a dead authed shell.
  if (response.status === 401 && typeof window !== 'undefined') {
    const path = window.location.pathname + window.location.search;
    // Don't loop if we're already at /login or hitting auth endpoints.
    const onLogin = window.location.pathname.startsWith('/login');
    if (!onLogin) {
      window.location.href = `/login?next=${encodeURIComponent(path)}`;
      throw new Error('Session expired');
    }
  }
  return response;
}
