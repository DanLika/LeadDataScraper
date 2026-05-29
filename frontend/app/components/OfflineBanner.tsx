'use client';

import { useEffect, useSyncExternalStore } from 'react';
import { offlineQueue } from '@/app/lib/offlineQueue';

function subscribeOnline(cb: () => void) {
  window.addEventListener('online', cb);
  window.addEventListener('offline', cb);
  return () => {
    window.removeEventListener('online', cb);
    window.removeEventListener('offline', cb);
  };
}
const getOnlineSnapshot = () => navigator.onLine;
const getOnlineServerSnapshot = () => true;

/**
 * Sticky offline banner. Mounts once at the layout level so it shows on
 * every authed page. Reads `navigator.onLine` and listens to the online /
 * offline events; renders nothing when the browser is online AND the
 * queue is empty.
 *
 * On reconnect, the queue auto-drains via offlineQueue.install() — the
 * banner just reflects the count.
 */
export default function OfflineBanner() {
  const isOnline = useSyncExternalStore(
    subscribeOnline,
    getOnlineSnapshot,
    getOnlineServerSnapshot,
  );
  const queuedCount = useSyncExternalStore(
    (cb) => offlineQueue.subscribe(cb),
    () => offlineQueue.size(),
    () => 0,
  );

  useEffect(() => {
    offlineQueue.install();
  }, []);

  if (isOnline && queuedCount === 0) return null;

  const message = !isOnline
    ? `Offline — ${queuedCount} action${queuedCount === 1 ? '' : 's'} queued. Will retry when reconnected.`
    : `Reconnected — retrying ${queuedCount} queued action${queuedCount === 1 ? '' : 's'}…`;

  return (
    <div
      data-testid="offline-banner"
      role="status"
      aria-live="polite"
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 700,
        background: isOnline ? 'var(--primary)' : 'var(--warning, #d97706)',
        color: '#fff',
        textAlign: 'center',
        padding: '8px 12px',
        fontSize: '0.85rem',
        fontWeight: 600,
        boxShadow: '0 2px 6px rgba(0,0,0,0.2)',
      }}
    >
      {message}
    </div>
  );
}
