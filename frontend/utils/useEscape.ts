'use client';

import { useEffect } from 'react';

/**
 * Run `onEscape` when the user presses Escape, but only while `active` is true.
 *
 * Used by routes that need to close a single dismissible (mobile drawer,
 * lightweight popover, etc.). Modals on / already share one combined
 * handler — this hook is for simpler "open one thing, close on ESC" cases
 * on /insights and /campaigns where there's no other modal stack to
 * coordinate with.
 */
export function useEscape(onEscape: () => void, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onEscape();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onEscape, active]);
}
