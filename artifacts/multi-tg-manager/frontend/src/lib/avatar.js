import { Endpoints } from './api'

// Cache: account_id -> { dataUrl: string | null, ts: number }
// null dataUrl means "we asked, account has no photo" — don't re-fetch every render.
const _cache = new Map()
// In-flight promises keyed by account id, so multiple components asking at once share one request.
const _inflight = new Map()
// Subscribers per id, invoked when the cache entry updates.
const _subs = new Map()

const TTL_MS = 5 * 60 * 1000 // 5 min — pic doesn't change often

function notify(id) {
  const set = _subs.get(id)
  if (!set) return
  for (const cb of set) { try { cb(get(id)) } catch {} }
}

export function get(id) {
  const e = _cache.get(id)
  return e ? e.dataUrl : undefined  // undefined = unknown, null = none, string = url
}

export function subscribe(id, cb) {
  let set = _subs.get(id)
  if (!set) { set = new Set(); _subs.set(id, set) }
  set.add(cb)
  return () => { set.delete(cb); if (set.size === 0) _subs.delete(id) }
}

export function invalidate(id) {
  const prev = _cache.get(id)?.dataUrl
  if (prev?.startsWith?.('blob:')) URL.revokeObjectURL(prev)
  _cache.delete(id)
  _inflight.delete(id)
  notify(id)
}

export async function fetchAvatar(id) {
  const cached = _cache.get(id)
  if (cached && Date.now() - cached.ts < TTL_MS) return cached.dataUrl
  if (_inflight.has(id)) return _inflight.get(id)
  const p = (async () => {
    try {
      const r = await fetch(`/api/accounts/${id}/profile/photo`, { credentials: 'include' })
      if (r.status === 204) {
        _cache.set(id, { dataUrl: null, ts: Date.now() })
        return null
      }
      if (!r.ok) throw new Error(`avatar ${r.status}`)
      const blob = await r.blob()
      const url = URL.createObjectURL(blob)
      const prev = _cache.get(id)?.dataUrl
      if (prev?.startsWith?.('blob:')) URL.revokeObjectURL(prev)
      _cache.set(id, { dataUrl: url, ts: Date.now() })
      return url
    } catch {
      _cache.set(id, { dataUrl: null, ts: Date.now() })
      return null
    } finally {
      _inflight.delete(id)
      notify(id)
    }
  })()
  _inflight.set(id, p)
  return p
}
