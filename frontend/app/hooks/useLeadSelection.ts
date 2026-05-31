'use client';

import { useCallback, useMemo, useState } from 'react';

import type { Lead } from '../types/lead';

interface UseLeadSelectionResult {
  selectedIds: ReadonlySet<string>;
  count: number;
  isSelected: (uniqueKey: string) => boolean;
  toggleOne: (uniqueKey: string) => void;
  toggleAllVisible: () => void;
  clear: () => void;
  allVisibleSelected: boolean;
  someVisibleSelected: boolean;
}

/**
 * Client-side selection state for the LeadTable bulk-action toolbar.
 *
 * Internal `storedIds` keeps every key the user has ever clicked; the
 * exposed `selectedIds` is derived per-render as the intersection with
 * `visibleLeads`. Filter changes therefore shrink the displayed count
 * automatically without needing a setState-in-effect (lint flags that
 * pattern as cascading renders), and clearing a filter restores any
 * out-of-view selections the user previously made — friendlier than
 * silently discarding them on a filter toggle.
 *
 * Trade-off: `storedIds` may grow beyond what's currently visible, but
 * (a) it's capped by total lead count, and (b) `clear()` zeroes it.
 */
export function useLeadSelection(visibleLeads: ReadonlyArray<Lead>): UseLeadSelectionResult {
  const [storedIds, setStoredIds] = useState<Set<string>>(() => new Set());

  const visibleKeys = useMemo(() => {
    const s = new Set<string>();
    for (const lead of visibleLeads) {
      if (lead.unique_key) s.add(lead.unique_key);
    }
    return s;
  }, [visibleLeads]);

  const selectedIds = useMemo(() => {
    if (storedIds.size === 0) return storedIds;
    const out = new Set<string>();
    for (const key of storedIds) {
      if (visibleKeys.has(key)) out.add(key);
    }
    return out;
  }, [storedIds, visibleKeys]);

  const toggleOne = useCallback((uniqueKey: string) => {
    setStoredIds(prev => {
      const next = new Set(prev);
      if (next.has(uniqueKey)) next.delete(uniqueKey);
      else next.add(uniqueKey);
      return next;
    });
  }, []);

  const toggleAllVisible = useCallback(() => {
    setStoredIds(prev => {
      const visibleArr = Array.from(visibleKeys);
      const allCurrentlyOn = visibleArr.length > 0
        && visibleArr.every(k => prev.has(k));
      const next = new Set(prev);
      if (allCurrentlyOn) {
        for (const k of visibleArr) next.delete(k);
      } else {
        for (const k of visibleArr) next.add(k);
      }
      return next;
    });
  }, [visibleKeys]);

  const clear = useCallback(() => {
    setStoredIds(prev => (prev.size === 0 ? prev : new Set()));
  }, []);

  const isSelected = useCallback(
    (uniqueKey: string) => selectedIds.has(uniqueKey),
    [selectedIds],
  );

  const { allVisibleSelected, someVisibleSelected } = useMemo(() => {
    if (visibleLeads.length === 0) return { allVisibleSelected: false, someVisibleSelected: false };
    let all = true;
    let some = false;
    for (const lead of visibleLeads) {
      if (!lead.unique_key) { all = false; continue; }
      if (selectedIds.has(lead.unique_key)) some = true;
      else all = false;
    }
    return { allVisibleSelected: all, someVisibleSelected: some };
  }, [visibleLeads, selectedIds]);

  return {
    selectedIds,
    count: selectedIds.size,
    isSelected,
    toggleOne,
    toggleAllVisible,
    clear,
    allVisibleSelected,
    someVisibleSelected,
  };
}
