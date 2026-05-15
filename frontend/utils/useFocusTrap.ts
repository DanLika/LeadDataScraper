'use client';

import { useEffect, RefObject } from 'react';

/**
 * Trap Tab / Shift+Tab focus inside the given container while `active` is true.
 *
 * WCAG 2.4.3 (Focus Order): keyboard users in an open modal must not be able
 * to Tab into background content. We listen for keydown on the container and
 * cycle focus at the first / last focusable boundary.
 *
 * The container should be the modal root (the element with role="dialog").
 * Pass the open flag as `active` so the trap deactivates when the modal closes.
 */
export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
) {
  useEffect(() => {
    if (!active || !ref.current) return;
    const root = ref.current;

    // Remember whoever had focus when the modal opened so we can hand it
    // back on close. WCAG 2.4.3 — keyboard users should not be dropped
    // back to <body> after dismissing a dialog.
    const opener = document.activeElement as HTMLElement | null;

    const getFocusables = (): HTMLElement[] => {
      const nodes = root.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      return Array.from(nodes).filter((el) => el.offsetParent !== null);
    };

    const first = getFocusables()[0];
    if (first && !root.contains(document.activeElement)) {
      first.focus();
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const focusables = getFocusables();
      if (focusables.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    root.addEventListener('keydown', onKey);
    return () => {
      root.removeEventListener('keydown', onKey);
      // Restore focus to the opener if it's still in the document and focusable.
      if (opener && document.contains(opener) && typeof opener.focus === 'function') {
        opener.focus();
      }
    };
  }, [active, ref]);
}
