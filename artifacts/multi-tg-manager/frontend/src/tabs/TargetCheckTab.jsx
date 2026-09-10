import { useState } from 'react'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import { viDetail } from '../lib/vi'

function ItemList({ title, items, tone }) {
  if (!items.length) return null
  return (
    <div className="nb-card-sm p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className={'nb-badge text-black ' + tone}>{title}</span>
        <span className="text-xs opacity-60">{items.length}</span>
      </div>
      <div className="space-y-2 max-h-72 overflow-auto">
        {items.map((r) => (
          <div key={r.id} className="border-2 border-black dark:border-white p-2 text-sm">
            <div className="flex items-center gap-2">
              <span className="font-bold truncate">{r.name}</span>
              <span className="text-xs opacity-60 ml-auto font-mono">{r.phone}</span>
            </div>
            <div className="text-xs opacity-70 mt-1">{viDetail(r.detail)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function TargetCheckTab() {
  const toast = useToast()
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  async function search() {
    const t = target.trim()
    if (!t) {
      toast.error('Nhập tên người dùng hoặc liên kết')
      return
    }
    setBusy(true)
    setResult(null)
    try {
      const r = await Endpoints.targetCheck(t)
      setResult(r)
    } catch (e) {
      toast.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  const totals = result
    ? {
        present: result.present?.length || 0,
        absent: result.absent?.length || 0,
        skipped: result.skipped?.length || 0,
        failed: result.failed?.length || 0,
      }
    : null

  return (
    <div className="space-y-4">
      <div className="nb-card p-4">
        <div className="font-extrabold uppercase">Kiểm tra đối tượng Telegram</div>
        <div className="text-sm opacity-70 mt-1">
          Kiểm tra một bot, kênh, nhóm hoặc tên người dùng trên tất cả tài khoản đang kết nối. Hệ thống sẽ cho biết tài khoản nào đã dùng/tham gia và tài khoản nào chưa.
        </div>
        <div className="flex gap-2 mt-3">
          <input
            className="nb-input"
            placeholder="@username hoặc liên kết t.me"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') search() }}
          />
          <button className="nb-btn-pri" disabled={busy} onClick={search}>
            {busy ? 'Đang kiểm tra...' : 'Kiểm tra tất cả tài khoản'}
          </button>
        </div>
      </div>

      {totals && (
        <div className="flex flex-wrap gap-2">
          <span className="nb-badge bg-brand-ok text-black">đã tìm thấy {totals.present}</span>
          <span className="nb-badge bg-brand-warn text-black">chưa tìm thấy {totals.absent}</span>
          <span className="nb-badge bg-brand-violet text-black">bỏ qua {totals.skipped}</span>
          <span className="nb-badge bg-brand-err text-black">thất bại {totals.failed}</span>
        </div>
      )}

      {result && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ItemList
            title={result.peer?.kind === 'bot' || result.peer?.kind === 'user' ? 'Chưa sử dụng' : 'Chưa tham gia'}
            items={result.absent || []}
            tone="bg-brand-warn"
          />
          <ItemList
            title={result.peer?.kind === 'bot' || result.peer?.kind === 'user' ? 'Đã sử dụng' : 'Đã tham gia'}
            items={result.present || []}
            tone="bg-brand-ok"
          />
          <ItemList title="Bỏ qua" items={result.skipped || []} tone="bg-brand-violet" />
          <ItemList title="Thất bại" items={result.failed || []} tone="bg-brand-err" />
        </div>
      )}
    </div>
  )
}
