// Shared header-filter constants for `app/api/proxy/[...path]/route.ts`.
// Lives in `.mjs` so the Node built-in test runner can import it without a
// TypeScript toolchain. Keep in sync with the proxy route — the route
// re-exports these via `import` so there is exactly one source of truth.
//
// HOP_BY_HOP — per RFC 7230 §6.1 plus the platform-controlled client-IP
// headers we re-emit from the trusted source. Dropping these prevents
// double-buffering and trust laundering.
//
// STRIPPED_AUTH — server-side-injected auth headers. Any client-supplied
// value MUST be removed BEFORE the server re-injects from env so an
// attacker cannot smuggle their own `X-Admin-Token` to a backend route
// whose path falls outside `ADMIN_TOKEN_PATHS`. Backend rejection on
// wrong value is the practical gate; this strip is defense-in-depth so
// the header never traverses the trust boundary uninspected. Pinned by
// `frontend/utils/proxyHeaderFilter.test.mjs`.

export const HOP_BY_HOP = new Set([
  'host',
  'connection',
  'content-length',
  'transfer-encoding',
  'upgrade',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailers',
  'accept-encoding',
  'x-forwarded-for',
  'x-forwarded-host',
  'x-forwarded-proto',
  'x-real-ip',
  'forwarded',
]);

export const STRIPPED_AUTH = new Set(['x-api-key', 'x-admin-token']);

// Returns true when the proxy must drop the inbound header outright
// (before any server-side re-injection). Case-insensitive on the name,
// matching the Fetch-spec Headers contract.
export function shouldDropInboundHeader(name) {
  const k = String(name).toLowerCase();
  return HOP_BY_HOP.has(k) || STRIPPED_AUTH.has(k);
}
