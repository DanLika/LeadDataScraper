'use client';

import { useEffect, useRef } from 'react';

/**
 * Run `onEscape` when the user presses Escape, but only while `active` is true.
 *
 * Mirrors useFocusTrap's shape. `onEscape` is kept in a ref so callers can
 * pass an inline arrow without forcing the listener to re-register on every
 * render — only `active` flipping (re-)wires the listener.
 *
 * Used by routes that need to close a single dismissible (mobile drawer,
 * lightweight popover, etc.). Modals on / share one combined handler — this
 * hook is for simpler "open one thing, close on ESC" cases on /insights and
 * /campaigns where there's no other modal stack to coordinate with.
 */
export function useEscape(onEscape: () => void, active: boolean) {
  const cbRef = useRef(onEscape);
  cbRef.current = onEscape;

  useEffect(() => {
    if (!active) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cbRef.current();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [active]);
}

/**
 * Selector for the mobile-drawer toggle button. Centralised so a future
 * rename/translation of the aria-label only edits this constant.
 */
export const BURGER_SELECTOR = 'button[aria-label="Open menu"]';

/**
 * Move keyboard focus to the element matched by `selector`, deferred to the
 * next animation frame so React has committed any pending state change first.
 * Returns silently if no element matches — caller can pass any safe selector.
 *
 * Pulled out of the three drawer-close callsites that all need to restore
 * focus to the burger after dismiss.
 */
export function restoreFocus(selector: string) {
  requestAnimationFrame(() => {
    (document.querySelector(selector) as HTMLElement | null)?.focus();
  });
}
