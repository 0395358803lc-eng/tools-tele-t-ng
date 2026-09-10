import { useEffect, useRef, useState } from 'react'
import { viStatus } from '../lib/vi'

const STATUS_COLOR = {
  ok: 'bg-brand-ok',
  failed: 'bg-brand-err',
  skipped: 'bg-brand-warn',
  pending: 'bg-brand-violet',
  partial: 'bg-brand-info',
}

function Badge({ label, n, color }) {
  if (!n) return null
  return <span className={'nb-badge text-black ' + color}>{n} {label}</span>
}

// Live progress for a streaming bulk task. `progress` comes from useBulkProgress().
export default function ProgressModal({ progress, onClose }) {
  const listRef = useRef(null)
  const startRef = useRef(null)
  const [elapsed, setElapsed] = useState(0)
  // auto-scroll to newest row as they stream in
  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [progress?.rows?.length])

  // elapsed-seconds timer: start when a run opens, stop when it's done
  const running = progress && !progress.done
  useEffect(() => {
    if (!progress) { startRef.current = null; setElapsed(0); return }
    if (startRef.current == null) startRef.current = Date.now()
    if (!running) return
    const t = setInterval(() => setElapsed((Date.now() - startRef.current) / 1000), 200)
    return () => clearInterval(t)
  }, [progress, running])

  if (!progress) return null
  const { title, total, current, success, failed, skipped, pending, partial, currentName, rows, done, error } = progress
  const pct = total > 0 ? Math.round((current / total) * 100) : (done ? 100 : 0)

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div className="nb-card p-6 w-full max-w-lg max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-extrabold uppercase tracking-tight">{title || 'Đang thực hiện…'}</h2>
          <button className="nb-btn !py-1 !px-2" onClick={onClose} disabled={!done} title={done ? 'Đóng' : 'Đang chạy…'}>✕</button>
        </div>

        {/* progress bar */}
        <div className="h-4 border-2 border-black dark:border-white overflow-hidden mb-2">
          <div className="h-full bg-brand-pri transition-all" style={{ width: `${pct}%` }} />
        </div>
        <div className="flex items-center gap-2 mb-3 flex-wrap text-xs">
          <span className="font-mono font-bold">{current}/{total || '…'}</span>
          <Badge label="thành công" n={success} color="bg-brand-ok" />
          <Badge label="thất bại" n={failed} color="bg-brand-err" />
          <Badge label="đang chờ" n={pending} color="bg-brand-violet" />
          <Badge label="một phần" n={partial} color="bg-brand-info" />
          <Badge label="bỏ qua" n={skipped} color="bg-brand-warn" />
          <span className="font-mono opacity-60">{elapsed.toFixed(1)}s</span>
          <span className="ml-auto opacity-70">
            {done ? (error ? 'Đã dừng' : 'Hoàn tất') : (currentName ? `→ ${currentName}` : 'Đang bắt đầu…')}
          </span>
        </div>

        {error && (
          <div className="nb-card-sm p-2 text-sm bg-brand-err text-black mb-2">{error}</div>
        )}

        <div ref={listRef} className="space-y-1 overflow-auto flex-1">
          {rows.map((r, i) => (
            <div key={i} className="nb-card-sm p-2 text-sm flex items-center gap-2">
              <span className={'nb-badge text-black ' + (STATUS_COLOR[r.status] || 'bg-white')}>{viStatus(r.status)}</span>
              <span className="font-medium truncate">{r.name}</span>
              {r.detail && <span className="text-xs opacity-70 truncate ml-auto">{r.detail}</span>}
            </div>
          ))}
        </div>

        {done && (
          <button className="nb-btn-pri mt-3 w-full" onClick={onClose}>Đóng</button>
        )}
      </div>
    </div>
  )
}
