import { useState } from 'react'
import { CopyButton } from '../lib/CopyButton'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import AccountAvatar from './AccountAvatar'
import ConfirmModal from './ConfirmModal'
import { viReason } from '../lib/vi'

function statusBadge(status) {
  if (status === 'connected') return <span className="nb-badge bg-brand-ok text-black">Đã kết nối</span>
  if (status === 'banned')    return <span className="nb-badge bg-brand-err text-black">Bị cấm</span>
  return <span className="nb-badge bg-brand-warn text-black">Mất kết nối</span>
}

// gone_at comes from the backend as a naive UTC ISO string (no timezone), so we
// force UTC interpretation before diffing against the local clock.
function ago(iso) {
  if (!iso) return ''
  const norm = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z'
  const t = new Date(norm).getTime()
  if (Number.isNaN(t)) return ''
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 60) return 'vừa xong'
  const m = s / 60; if (m < 60) return `${Math.floor(m)} phút trước`
  const h = m / 60; if (h < 24) return `${Math.floor(h)} giờ trước`
  const d = h / 24; if (d < 30) return `${Math.floor(d)} ngày trước`
  return new Date(norm).toLocaleDateString('vi-VN')
}

const goneName = (g) => `${g.first_name || ''} ${g.last_name || ''}`.trim() || g.phone

export default function Sidebar({ accounts, gone = [], selectedId, onSelect, onAdd, onDeleted, onGoneChange }) {
  const toast = useToast()
  const [pendingDelete, setPendingDelete] = useState(null)  // account pending removal
  const [confirmRemoveAll, setConfirmRemoveAll] = useState(false)
  const [passwordRemoveAll, setPasswordRemoveAll] = useState(false)
  const [removeAllPassword, setRemoveAllPassword] = useState('')
  const [removeAllBusy, setRemoveAllBusy] = useState(false)
  const [goneOpen, setGoneOpen] = useState(false)

  // Banned accounts drop out of the active list (they live in Gone/Banned).
  // Serial numbers are the 1-based position in this active list.
  const active = accounts.filter((a) => a.status !== 'banned')
  const totalAccounts = accounts.length

  async function confirmDelete() {
    const a = pendingDelete
    setPendingDelete(null)
    if (!a) return
    try {
      await Endpoints.deleteAccount(a.id)
      toast.success('Đã xóa tài khoản')
      onDeleted?.()
    } catch (err) { toast.error(err.message) }
  }

  function askRemoveAllPassword() {
    setConfirmRemoveAll(false)
    setRemoveAllPassword('')
    setPasswordRemoveAll(true)
  }

  function closeRemoveAllPassword() {
    if (removeAllBusy) return
    setPasswordRemoveAll(false)
    setRemoveAllPassword('')
  }

  async function removeAllAccounts() {
    if (!removeAllPassword) {
      toast.error('Vui lòng nhập mật khẩu ứng dụng')
      return
    }
    setRemoveAllBusy(true)
    try {
      const r = await Endpoints.removeAllAccounts(removeAllPassword)
      toast.success(`Đã xóa ${r.removed || 0} tài khoản`)
      setPasswordRemoveAll(false)
      setRemoveAllPassword('')
      onDeleted?.()
      onSelect?.(null)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setRemoveAllBusy(false)
    }
  }

  async function clearGone() {
    try { await Endpoints.clearGoneAccounts(); onGoneChange?.() }
    catch (err) { toast.error(err.message) }
  }
  async function dismissGone(id) {
    try { await Endpoints.deleteGoneAccount(id); onGoneChange?.() }
    catch (err) { toast.error(err.message) }
  }

  return (
    <aside className="w-[320px] border-r-2 border-black dark:border-white bg-white dark:bg-zinc-900 flex flex-col">
      <div className="p-3 border-b-2 border-black dark:border-white flex items-center justify-between">
        <span className="font-extrabold uppercase tracking-tight">Tài khoản ({active.length})</span>
      </div>
      <div className="flex-1 overflow-auto">
        {active.length === 0 && (
          <div className="p-4 text-sm opacity-70">Chưa có tài khoản. Hãy thêm tài khoản đầu tiên bên dưới.</div>
        )}
        {active.map((a, i) => {
          const sel = a.id === selectedId
          const serial = i + 1
          return (
            <div
              key={a.id}
              onClick={() => onSelect(a.id)}
              className={
                'flex items-start gap-2 p-3 border-b-2 border-black dark:border-white cursor-pointer transition-colors ' +
                (sel ? 'bg-brand-pri text-black' : 'hover:bg-zinc-100 dark:hover:bg-zinc-800')
              }
            >
              <div className="w-5 shrink-0 self-center text-right font-mono text-xs font-bold opacity-50 select-none">
                {serial}
              </div>
              <AccountAvatar account={a} size={40} showOnline />
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-bold truncate">
                    {(a.first_name + ' ' + a.last_name).trim() || a.phone}
                  </div>
                  {statusBadge(a.status)}
                </div>
                <div className="flex items-center gap-1 text-xs font-mono truncate">
                  <span className="truncate">{a.phone}</span>
                  <CopyButton value={a.phone} label="số điện thoại" />
                </div>
                {a.username ? (
                  <div className="flex items-center gap-1 text-xs font-mono truncate opacity-80">
                    <span className="truncate">@{a.username}</span>
                    <CopyButton value={a.username} label="tên người dùng" />
                  </div>
                ) : (
                  <div className="text-xs opacity-50 italic">chưa có @username</div>
                )}
                <div className="flex items-center gap-2 mt-1">
                  {a.has_2fa && <span className="nb-badge bg-brand-violet text-black">2FA</span>}
                  {a.unread_security > 0 && (
                    <span className="nb-badge bg-brand-err text-black">{a.unread_security} mới</span>
                  )}
                  <button
                    className="ml-auto text-[10px] underline opacity-60 hover:opacity-100"
                    onClick={(e) => { e.stopPropagation(); setPendingDelete(a) }}
                  >xóa</button>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {gone.length > 0 && (
        <div className="border-t-2 border-black dark:border-white shrink-0">
          <button
            onClick={() => setGoneOpen((o) => !o)}
            className="w-full flex items-center gap-2 p-3 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <span className="font-extrabold uppercase tracking-tight text-sm">Đã loại / Bị cấm</span>
            <span className="nb-badge bg-brand-err text-black">{gone.length}</span>
            <span className="ml-auto opacity-60">{goneOpen ? '▲' : '▼'}</span>
          </button>
          {goneOpen && (
            <div className="max-h-72 overflow-auto border-t border-black/20 dark:border-white/20">
              {gone.map((g) => (
                <div key={g.id} className="px-3 py-2 border-b border-black/15 dark:border-white/15 text-xs">
                  <div className="flex items-center gap-2">
                    <span className="font-bold truncate">{goneName(g)}</span>
                    <span className={'nb-badge text-black ' + (g.reason === 'banned' ? 'bg-brand-err' : 'bg-brand-warn')}>
                      {viReason(g.reason)}
                    </span>
                    <span className="ml-auto font-mono opacity-60 shrink-0">trước đây #{g.old_serial ?? '—'}</span>
                  </div>
                  <div className="font-mono opacity-70 truncate">
                    {g.phone}{g.username ? ` · @${g.username}` : ''}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="opacity-50">{ago(g.gone_at)}</span>
                    <button
                      className="ml-auto text-[10px] underline opacity-50 hover:opacity-100"
                      onClick={() => dismissGone(g.id)}
                    >bỏ khỏi lịch sử</button>
                  </div>
                </div>
              ))}
              <div className="p-2 text-center">
                <button className="text-[10px] underline opacity-60 hover:opacity-100" onClick={clearGone}>
                  xóa toàn bộ lịch sử
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="p-3 border-t-2 border-black dark:border-white shrink-0">
        <button
          className="nb-btn-err w-full mb-2"
          disabled={totalAccounts === 0 || removeAllBusy}
          onClick={() => setConfirmRemoveAll(true)}
        >
          Xóa tất cả tài khoản
        </button>
        <button className="nb-btn-pri w-full" onClick={onAdd}>+ Thêm tài khoản</button>
      </div>

      {pendingDelete && (
        <ConfirmModal
          title="Xóa tài khoản?"
          message={`${(pendingDelete.first_name + ' ' + pendingDelete.last_name).trim() || pendingDelete.phone}\n${pendingDelete.phone}\n\nTệp phiên sẽ bị xóa. Thao tác này không thể hoàn tác.`}
          confirmLabel="Có, xóa"
          cancelLabel="Không, giữ lại"
          danger
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}

      {confirmRemoveAll && (
        <ConfirmModal
          title="Xóa tất cả tài khoản?"
          message={`Thao tác này sẽ xóa toàn bộ ${totalAccounts} tài khoản, dừng các client và xóa tệp phiên cục bộ.\n\nBạn có muốn tiếp tục không?`}
          confirmLabel="Có"
          cancelLabel="Không"
          danger
          onConfirm={askRemoveAllPassword}
          onCancel={() => setConfirmRemoveAll(false)}
        />
      )}

      {passwordRemoveAll && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={closeRemoveAllPassword}>
          <div className="nb-card p-6 w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
            <h2 className="font-extrabold uppercase tracking-tight mb-2">Nhập mật khẩu</h2>
            <div className="text-sm opacity-80 mb-4">
              Nhập mật khẩu ứng dụng để xác nhận xóa toàn bộ tài khoản.
            </div>
            <input
              type="password"
              className="nb-input mb-4"
              value={removeAllPassword}
              onChange={(e) => setRemoveAllPassword(e.target.value)}
              placeholder="Mật khẩu ứng dụng"
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter') removeAllAccounts() }}
              disabled={removeAllBusy}
            />
            <div className="flex gap-2 justify-end">
              <button className="nb-btn" disabled={removeAllBusy} onClick={closeRemoveAllPassword}>Hủy</button>
              <button className="nb-btn-err" disabled={removeAllBusy || !removeAllPassword} onClick={removeAllAccounts}>
                {removeAllBusy ? 'Đang xóa...' : 'Xóa tất cả'}
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}
