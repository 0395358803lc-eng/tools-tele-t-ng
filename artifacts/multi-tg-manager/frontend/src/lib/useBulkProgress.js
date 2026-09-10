import { useState, useCallback } from 'react'
import { viDetail } from './vi'

const EMPTY = {
  title: '', total: 0, current: 0,
  success: 0, failed: 0, skipped: 0, pending: 0, partial: 0,
  currentName: '', rows: [], done: false, error: null,
}

// Drives a live NDJSON bulk run into a progress object for <ProgressModal/>.
// run(title, starter) where starter(onEvent) kicks off a streaming Endpoints call.
export function useBulkProgress() {
  const [progress, setProgress] = useState(null)

  const run = useCallback(async (title, starter) => {
    setProgress({ ...EMPTY, title })
    try {
      await starter((evt) => {
        if (evt.type === 'progress') {
          setProgress((p) => ({
            ...p,
            total: evt.total, current: evt.current,
            success: evt.success, failed: evt.failed,
            skipped: evt.skipped, pending: evt.pending || 0, partial: evt.partial || 0,
            currentName: evt.account_name,
            rows: [...p.rows, { name: evt.account_name, status: evt.status, detail: evt.detail ? viDetail(evt.detail) : '' }],
          }))
        } else if (evt.type === 'done') {
          setProgress((p) => ({
            ...p,
            total: evt.total, current: evt.total,
            success: evt.success, failed: evt.failed,
            skipped: evt.skipped, pending: evt.pending || 0, partial: evt.partial || 0,
            currentName: '', done: true,
          }))
        }
      })
    } catch (e) {
      setProgress((p) => ({ ...(p || EMPTY), title, done: true, error: viDetail(e.message) }))
    }
  }, [])

  const close = useCallback(() => setProgress(null), [])
  return { progress, run, close }
}
