import { viDetail, viStatus } from '../lib/vi'

export default function ResultModal({ title, success = 0, failed = 0, skipped = 0, results = [], onClose }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="nb-card p-6 w-full max-w-lg max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-extrabold uppercase tracking-tight">{title}</h2>
          <button className="nb-btn !py-1 !px-2" onClick={onClose}>✕</button>
        </div>
        <div className="flex gap-2 mb-4 flex-wrap">
          <span className="nb-badge bg-brand-ok text-black">{success} thành công</span>
          <span className="nb-badge bg-brand-err text-black">{failed} thất bại</span>
          <span className="nb-badge bg-brand-warn text-black">{skipped} bỏ qua</span>
        </div>
        <div className="space-y-1">
          {results.map((r, i) => (
            <div key={i} className="nb-card-sm p-2 text-sm flex items-center gap-2">
              <span className={'nb-badge ' + (r.status === 'ok' ? 'bg-brand-ok' : r.status === 'failed' ? 'bg-brand-err' : 'bg-brand-warn') + ' text-black'}>{viStatus(r.status)}</span>
              <span className="font-mono text-xs">{r.phone}</span>
              {r.detail && <span className="text-xs opacity-70 truncate">{viDetail(r.detail)}</span>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
