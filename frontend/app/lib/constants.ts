/**
 * Operational constants — one place for every numeric policy in the
 * frontend.
 *
 * Anything an operator might want to tune lives here. Anything that
 * is intrinsic-to-the-code (HTTP status codes, regex group indices,
 * Tailwind/CSS magic numbers that are part of the visual design)
 * does NOT — those stay inline because moving them would only add
 * indirection.
 *
 * Grouped by domain. When adding a new constant:
 *   - Pick the right section, or add a new section header if no fit
 *   - Name it for the policy, not the value (`MAX_UPLOAD_BYTES_UI`
 *     not `TEN_MB`)
 *   - Suffix with the unit (`_MS` / `_BYTES` / `_PX`) — prefer
 *     suffixes over inline comments
 *   - Mark `as const` so TypeScript narrows the literal type
 *
 * Cross-language parity: anything mirrored in `src/utils/constants.py`
 * carries a `BACKEND PARITY` note. There is no automatic check; keep
 * both files in sync by hand and flag drift in PR review.
 */


// === Layout breakpoints =========================================
// CSS breakpoints used in `useEffect`-driven responsive logic. CSS
// media queries that live in stylesheets can keep their inline pixel
// values — these constants exist for JS-side comparisons only.

// All breakpoint / duration constants are typed as `number` (not literal
// types via `as const`) because they're consumed in arithmetic and
// passed into `useState`. A literal type would narrow the useState
// generic to e.g. `SetStateAction<1024>`, which then can't accept
// `window.innerWidth: number`.

/** Mobile drawer collapses at or below this width. */
export const MOBILE_BREAKPOINT_PX: number = 1024;

/** Tablet layout starts here (used as the lower edge of the
 * `windowWidth >= TABLET_MIN_PX && windowWidth < MOBILE_BREAKPOINT_PX`
 * tablet detection in AIChat). */
export const TABLET_MIN_PX: number = 768;

/** Window-resize handler debounce. Prevents thrashing during a drag. */
export const RESIZE_DEBOUNCE_MS: number = 150;


// === User-feedback durations ====================================
// All in ms. Distinct from `RESIZE_DEBOUNCE_MS` — these are
// "tell the user something happened, then quietly fade".

/** Toast notification auto-dismiss. */
export const TOAST_AUTO_DISMISS_MS: number = 3500;

/** Status message auto-dismiss (default). */
export const STATUS_MESSAGE_AUTO_DISMISS_MS: number = 4000;

/** Status message auto-dismiss (short — used on quick-confirm flows
 * like "campaign started" where the operator doesn't need to read it
 * twice). */
export const STATUS_MESSAGE_SHORT_MS: number = 3000;

/** "Copied to clipboard" / "Saved" inline check duration. */
export const COPY_FEEDBACK_MS: number = 2000;

/** Delay before refreshing the leads view after a mutation — gives
 * the backend's stats cache a window to invalidate before the next
 * fetch arrives. */
export const POST_ACTION_REFRESH_DELAY_MS: number = 5000;

/** `URL.revokeObjectURL` delay after a download trigger — small but
 * non-zero so Chrome has a chance to start the download before the
 * blob URL invalidates. */
export const BLOB_URL_REVOKE_DELAY_MS: number = 1000;


// === Upload caps ================================================

/** UI-side soft cap on uploads. The frontend rejects above this with
 * an inline "file too large" message before sending anything. Lower
 * than the proxy hard cap so the operator gets actionable feedback
 * instead of a 413 from the server. */
export const MAX_UPLOAD_BYTES_UI: number = 10 * 1024 * 1024;  // 10 MiB

/** Hard cap on every request body passing through `/api/proxy/*`.
 *
 * BACKEND PARITY — must equal `MAX_UPLOAD_BYTES` in
 * `src/utils/constants.py`. The proxy enforces this defense-in-depth
 * even though backend `/upload` also enforces it; the proxy is the
 * external boundary. */
export const MAX_PROXY_BODY_BYTES: number = 50 * 1024 * 1024;  // 50 MiB
