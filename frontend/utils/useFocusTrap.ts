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

    const getFocusables = (): HTMLElement[] => {
      const nodes = root.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      return Array.from(nodes).filter((el) => el.offsetParent !== null);
    };

    // Move initial focus inside the modal if focus is somewhere else.
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
    return () => root.removeEventListener('keydown', onKey);
  }, [active, ref]);
}
