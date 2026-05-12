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
 */
export function apiFetch(input: string | URL | Request, init?: RequestInit): Promise<Response> {
  return fetch(input, init);
}
