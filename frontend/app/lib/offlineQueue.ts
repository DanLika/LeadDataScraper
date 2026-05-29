'use client';

/**
 * Tiny in-memory offline queue. Mutations (POST/PUT/PATCH/DELETE) that fire
 * while `navigator.onLine === false` get enqueued; on the next `online`
 * event the queue drains serially and re-issues the requests.
 *
 * Contract / caveats (intentional, documented):
 *   - GET requests are never queued — re-fetch will happen via the UI anyway.
 *   - Bodies are captured eagerly so the original Request can be cloned.
 *   - No idempotency guard. A queued POST that succeeded server-side and
 *     replied 5xx still gets retried — the caller knows whether the action
 *     is safe to retry. Today's only mutations are user-confirmed
 *     (Audit All, Generate Messages, etc.) and tolerate replay because the
 *     backend de-dups by unique_key / job state.
 *   - Memory-only. A full page reload between offline and online loses the
 *     queue. Acceptable for the single-operator browser session.
 *
 * Visible to React via the snapshot subscription pattern (useSyncExternalStore).
 */

type Listener = () => void

export type QueuedRequest = {
  id: number
  label: string
  url: string
  method: string
  headers: Record<string, string>
  body: ArrayBuffer | string | null
  enqueuedAt: number
}

class OfflineQueueImpl {
  private items: QueuedRequest[] = []
  private listeners = new Set<Listener>()
  private nextId = 1
  private wired = false

  size(): number {
    return this.items.length
  }

  snapshot(): QueuedRequest[] {
    return this.items.slice()
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private notify(): void {
    for (const l of this.listeners) l()
  }

  /**
   * Enqueue a Request descriptor. Returns the assigned id so callers can
   * log / correlate.
   */
  enqueue(label: string, url: string, init: RequestInit): number {
    const id = this.nextId++
    const body =
      init.body == null
        ? null
        : typeof init.body === 'string'
          ? init.body
          : init.body instanceof ArrayBuffer
            ? init.body.slice(0)
            : null // FormData / Blob bodies aren't reliably re-issuable; drop
    this.items.push({
      id,
      label,
      url,
      method: (init.method || 'POST').toUpperCase(),
      headers: { ...((init.headers as Record<string, string>) || {}) },
      body,
      enqueuedAt: Date.now(),
    })
    this.notify()
    return id
  }

  async drain(): Promise<{ ok: number; failed: number }> {
    let ok = 0
    let failed = 0
    while (this.items.length > 0) {
      const next = this.items[0]
      try {
        const resp = await fetch(next.url, {
          method: next.method,
          headers: next.headers,
          body: next.body as BodyInit | null,
          cache: 'no-store',
        })
        if (resp.ok) {
          ok += 1
        } else {
          failed += 1
        }
      } catch {
        failed += 1
      }
      this.items.shift()
      this.notify()
    }
    return { ok, failed }
  }

  // Idempotent — safe to call multiple times. The OfflineBanner mounts and
  // calls this on first render; subsequent mounts no-op.
  install(): void {
    if (this.wired || typeof window === 'undefined') return
    this.wired = true
    window.addEventListener('online', () => {
      // Fire-and-forget drain. UI subscribes via snapshot to see the count
      // tick down.
      void this.drain()
    })
  }

  // Test-only.
  _reset(): void {
    this.items = []
    this.nextId = 1
    this.notify()
  }
}

export const offlineQueue = new OfflineQueueImpl()
