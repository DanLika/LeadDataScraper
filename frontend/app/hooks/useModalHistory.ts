import { useEffect, useRef } from 'react';

type ModalMarker = 'settings' | 'discovery';

interface HistoryStateWithModal {
  lds_modal?: ModalMarker;
}

export interface UseModalHistoryOptions {
  open: boolean;
  onClose: () => void;
  marker: ModalMarker;
  urlParam: 'openSettings' | 'openDiscovery';
  preventClose?: () => boolean;
}

export function useModalHistory({
  open,
  onClose,
  marker,
  urlParam,
  preventClose,
}: UseModalHistoryOptions): void {
  const onCloseRef = useRef(onClose);
  const preventCloseRef = useRef(preventClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    preventCloseRef.current = preventClose;
  }, [preventClose]);

  useEffect(() => {
    if (!open) return;
    if (typeof window === 'undefined') return;

    // Cross-page arrival (e.g. /campaigns → sidebar Settings → /?openSettings=1):
    // router.replace at page.tsx:175 strips the param immediately after, which
    // would clobber any history entry we push here. Skip pushState so the back
    // button naturally returns to the originating page (matches typical UX —
    // back undoes the cross-page nav). Dashboard unmount tears down the modal.
    const params = new URLSearchParams(window.location.search);
    const cameFromUrl = params.get(urlParam) === '1';
    if (cameFromUrl) return;

    // Always push, even if the current state already carries our marker.
    // Early-return on a marker match would deadlock React 19 StrictMode dev:
    // mount-1 push → cleanup schedules async history.back → remount runs
    // synchronously and would short-circuit before the async pop completes,
    // leaving the modal without a listener.
    window.history.pushState({ lds_modal: marker } satisfies HistoryStateWithModal, '');

    const onPop = (event: PopStateEvent) => {
      const popped = event.state as HistoryStateWithModal | null;
      if (popped?.lds_modal === marker) return;
      if (preventCloseRef.current?.()) {
        window.history.pushState({ lds_modal: marker } satisfies HistoryStateWithModal, '');
        return;
      }
      onCloseRef.current();
    };
    window.addEventListener('popstate', onPop);

    return () => {
      window.removeEventListener('popstate', onPop);
      const current = window.history.state as HistoryStateWithModal | null;
      if (current?.lds_modal === marker) {
        window.history.back();
      }
    };
  }, [open, marker, urlParam]);
}
