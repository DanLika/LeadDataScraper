/**
 * In-process per-IP throttle for the login Server Action.
 *
 * Supabase Auth already rate-limits at the edge, but raising that limit (or
 * a future self-hosted Supabase with looser defaults) leaves the operator
 * account brute-forceable. This is a defence-in-depth layer that runs in
 * the Next.js process before signInWithPassword is ever called.
 *
 * Constraints (acceptable for the single-operator, single-instance deploy
 * this repo targets):
 *  - Map lives in process memory; multi-instance deploys would need a
 *    shared store (Redis / Upstash). This is intentional — the design
 *    assumes one Render worker per service.
 *  - Bucket key is the trusted client-IP header (same pattern as the proxy
 *    in app/api/proxy/[...path]/route.ts). A request without that header
 *    falls back to a synthetic `unknown` bucket — coarse but it still
 *    caps the absolute attack rate.
 */

type Bucket = { count: number; windowStart: number };

const WINDOW_MS = 60_000;
const MAX_ATTEMPTS_PER_WINDOW = 5;
// Cap the Map so a flood of unique IPs can't pin memory.
const MAX_BUCKETS = 10_000;

const buckets = new Map<string, Bucket>();

/**
 * Returns true when the caller is allowed to attempt a sign-in, false when
 * they have exceeded the per-window quota. The check increments the
 * counter regardless of the eventual sign-in outcome — failed attempts
 * count, so a brute-force loop is bounded even when each guess returns
 * `Invalid credentials`.
 */
export function checkLoginRate(ip: string | null | undefined): { allowed: boolean; retryAfterSeconds: number } {
  const key = (ip && ip.trim()) || 'unknown';
  const now = Date.now();

  // Opportunistic compaction — drop expired buckets when the Map grows.
  // Avoids a separate timer/sweeper which would be wrong-shaped in a
  // request-scoped module. If a single-window flood of unique IPs never
  // ages anything out, fall back to evicting the oldest bucket so the cap
  // is hard, not advisory.
  if (buckets.size > MAX_BUCKETS) {
    const sizeBefore = buckets.size;
    for (const [k, b] of buckets) {
      if (now - b.windowStart > WINDOW_MS) buckets.delete(k);
    }
    if (buckets.size === sizeBefore) {
      let oldestKey: string | undefined;
      let oldestStart = Infinity;
      for (const [k, b] of buckets) {
        if (b.windowStart < oldestStart) {
          oldestStart = b.windowStart;
          oldestKey = k;
        }
      }
      if (oldestKey !== undefined) buckets.delete(oldestKey);
    }
  }

  const existing = buckets.get(key);
  if (!existing || now - existing.windowStart > WINDOW_MS) {
    buckets.set(key, { count: 1, windowStart: now });
    return { allowed: true, retryAfterSeconds: 0 };
  }

  existing.count += 1;
  if (existing.count > MAX_ATTEMPTS_PER_WINDOW) {
    const retryMs = Math.max(0, WINDOW_MS - (now - existing.windowStart));
    return { allowed: false, retryAfterSeconds: Math.ceil(retryMs / 1000) };
  }
  return { allowed: true, retryAfterSeconds: 0 };
}

/** Resets the bucket for the given IP on successful login — the user passed
 * the credential check, so failed-attempt history is no longer interesting
 * and we don't want a legitimate-but-typo-prone session to lock itself
 * out. */
export function clearLoginRate(ip: string | null | undefined): void {
  const key = (ip && ip.trim()) || 'unknown';
  buckets.delete(key);
}
