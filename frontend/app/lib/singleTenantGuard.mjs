// Single-tenant invariant guard for `app/api/proxy/[...path]/route.ts`.
// Lives in `.mjs` so the Node built-in test runner can import it without a
// TypeScript toolchain — see `singleTenantGuard.test.mjs`. The route
// imports this helper instead of inlining the comparison so the predicate
// has a single audit-friendly source of truth + a pin.
//
// Threat model: a 2nd Supabase user provisioned post-boot (admin panel
// click, accident, or attacker with service-role) authenticates via
// /login, the proxy `supabase.auth.getUser()` call returns a valid
// session for that 2nd user, and without an additional email check the
// proxy server-side-injects X-API-Key + forwards to backend. Backend's
// `_assert_single_tenant_if_enforced` is BOOT-only (lifespan), so it
// cannot catch a user provisioned after the last deploy. The proxy is
// the only place the Supabase JWT is materialised server-side; the
// guard must live here.
//
// Semantics:
//   * `expectedEmail` empty → opt-in not configured, no enforcement.
//     Matches the backend lifespan check's `OPERATOR_EMAIL=""` skip.
//   * `expectedEmail` set + user is null → caller didn't authenticate
//     (caller-side 401 path, not this guard's concern).
//   * `expectedEmail` set + user.email mismatch (case-insensitive,
//     trim) → reject with 403 `single_tenant_violation`.
//   * `expectedEmail` set + user.email match → allow.
//
// Returns:
//   * `{ allowed: true }` — request may proceed.
//   * `{ allowed: false, status, errorCode }` — early-return shape the
//     route maps to JSON `{ error: errorCode }` with the given status.

/**
 * @param {{ user: { email?: string | null } | null, expectedEmail: string }} args
 * @returns {{ allowed: true } | { allowed: false, status: 403, errorCode: 'single_tenant_violation' }}
 */
export function checkSingleTenant({ user, expectedEmail }) {
  const expected = (expectedEmail || '').trim().toLowerCase();
  if (!expected) {
    return { allowed: true };
  }
  const actual = ((user && user.email) || '').trim().toLowerCase();
  if (actual !== expected) {
    return {
      allowed: false,
      status: 403,
      errorCode: 'single_tenant_violation',
    };
  }
  return { allowed: true };
}
