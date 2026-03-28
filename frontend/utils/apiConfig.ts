export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

const API_KEY = process.env.NEXT_PUBLIC_API_KEY || '';

/**
 * Authenticated fetch wrapper that injects the X-API-Key header.
 * Drop-in replacement for `fetch()` — same signature, same return type.
 */
export function apiFetch(input: string | URL | Request, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (API_KEY) {
    headers.set('X-API-Key', API_KEY);
  }
  return fetch(input, { ...init, headers });
}
