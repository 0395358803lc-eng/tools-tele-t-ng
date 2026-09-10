import { useEffect, useState, useCallback } from 'react'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import { fmtTime } from '../lib/util'
import ProgressModal from '../components/ProgressModal.jsx'
import { useBulkProgress } from '../lib/useBulkProgress'
import { viSecurityType } from '../lib/vi'
import { confirmVi } from '../lib/confirmVi'

const TYPE_COLORS = {
  login_code:       'bg-brand-warn',
  new_login:        'bg-brand-err',
  '2fa_change':     'bg-brand-violet',
  account_deletion: 'bg-brand-err',
  unknown:          'bg-white',
}

function AccountRow({ account, onChange }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState([])
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const m = await Endpoints.securityMessages(account.id)
      setMsgs(m)
      try {
        const s = await Endpoints.tgSessions(account.id)
        setSessions(s)
      } catch { setSessions([]) }
    } catch (e) { toast.error(e.message) } finally { setLoading(false) }
  }, [account.id, toast])

  useEffect(() => { if (open) load() }, [open, load])

  async function markRead(id) {
    try { await Endpoints.markRead(id); await load(); onChange?.() } catch (e) { toast.error(e.message) }
  }
  async function markAllRead() {
    try { await Endpoints.markAllRead(account.id); await load(); onChange?.() } catch (e) { toast.error(e.message) }
  }
  async function killSession(hash) {
    if (!(await confirmVi('Chấm dứt phiên đăng nhập này?'))) return
    try { await Endpoints.terminateSession(account.id, hash); await load() } catch (e) { toast.error(e.message) }
  }
  async function killOthers() {
    if (!(await confirmVi('Chấm dứt TẤT CẢ phiên đăng nhập khác?'))) return
    try { await Endpoints.terminateOthers(account.id); await load() } catch (e) { toast.error(e.message) }
  }

  async function backfill() {
    setLoading(true)
    try {
      await Endpoints.backfillSecurity(account.id, 50)
      await load()
      toast.success('Đã tải các tin nhắn mới nhất từ Telegram')
    } catch (e) { toast.error(e.message) } finally { setLoading(false) }
  }

  return (
    <div className="nb-card-sm mb-3">
      <div
        className="px-4 py-3 flex items-center gap-3 cursor-pointer hover:bg-zinc-100 dark:hover:bg-zinc-800"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="font-bold flex-1">
          {(account.first_name + ' ' + account.last_name).trim() || account.phone}
        </span>
        {account.has_2fa
          ? <span className="nb-badge bg-brand-violet text-black">Đã bật 2FA</span>
          : <span className="nb-badge bg-brand-warn text-black">Chưa bật 2FA</span>}
        {account.unread_security > 0 && (
          <span className="nb-badge bg-brand-err text-black">{account.unread_security} mới</span>
        )}
        <span className="opacity-60 text-sm">{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div className="px-4 pb-4 border-t-2 border-black dark:border-white">
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <span className="font-bold text-sm uppercase">Tin nhắn dịch vụ Telegram</span>
            <span className="text-[10px] opacity-60">từ “Telegram” (+42777, user_id 777000)</span>
            <button className="nb-btn !py-1 !px-2 text-xs ml-auto" onClick={backfill} disabled={loading}>
              {loading ? '…' : 'Tải 50 tin mới nhất'}
            </button>
            <button className="nb-btn !py-1 !px-2 text-xs" onClick={markAllRead}>Đánh dấu đã đọc tất cả</button>
            <button className="nb-btn !py-1 !px-2 text-xs" onClick={load}>Làm mới</button>
          </div>
          {loading && <div className="text-sm opacity-60 mt-2">Đang tải…</div>}
          {!loading && msgs.length === 0 && (
            <div className="text-sm opacity-60 mt-2">
              Chưa có tin nhắn dịch vụ Telegram. Hãy chọn “Tải 50 tin mới nhất” để lấy lịch sử từ Telegram.
            </div>
          )}
          <div className="space-y-2 mt-2">
            {msgs.map((m) => (
              <div key={m.id} className={'nb-card-sm p-3 ' + (m.is_read ? 'opacity-60' : '')}>
                <div className="flex items-center gap-2 mb-1">
                  <span className={`nb-badge ${TYPE_COLORS[m.type] || 'bg-white'} text-black`}>{viSecurityType(m.type)}</span>
                  {!m.is_read && <span className="w-2 h-2 rounded-full bg-brand-err inline-block" />}
                  <span className="text-xs opacity-70 ml-auto">{fmtTime(m.received_at)}</span>
                </div>
                <div className={'whitespace-pre-wrap text-sm font-mono ' + (m.is_read ? '' : 'font-bold')}>
                  {m.message_text}
                </div>
                {!m.is_read && (
                  <button className="nb-btn !py-1 !px-2 text-xs mt-2" onClick={() => markRead(m.id)}>
                    Đánh dấu đã đọc
                  </button>
                )}
              </div>
            ))}
          </div>

          <div className="mt-5 flex items-center gap-2">
            <span className="font-bold text-sm uppercase">Phiên đăng nhập đang hoạt động</span>
            <button className="nb-btn-err !py-1 !px-2 text-xs ml-auto" onClick={killOthers}>Chấm dứt tất cả phiên khác</button>
          </div>
          <div className="space-y-2 mt-2">
            {sessions.length === 0 && <div className="text-sm opacity-60">Chưa có dữ liệu phiên đăng nhập.</div>}
            {sessions.map((s) => (
              <div key={s.hash} className="nb-card-sm p-3 flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <div className="font-bold text-sm truncate">
                    {s.device || s.app_name || 'Không xác định'} {s.is_current && <span className="nb-badge bg-brand-ok text-black ml-1">hiện tại</span>}
                  </div>
                  <div className="text-xs opacity-70 truncate">
                    {s.platform} • {s.ip} • {s.country} • {fmtTime(s.date_created)}
                  </div>
                </div>
                {!s.is_current && (
                  <button className="nb-btn-err !py-1 !px-2 text-xs" onClick={() => killSession(s.hash)}>
                    Chấm dứt
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// Bulk change/set the Two-Step (2FA) password across many accounts at once.
function Bulk2faPanel({ accounts, onChange }) {
  const toast = useToast()
  const { progress, run, close } = useBulkProgress()
  const [open, setOpen] = useState(false)
  const [ids, setIds] = useState([])
  const [newPwd, setNewPwd] = useState('')
  const [newPwd2, setNewPwd2] = useState('')
  const [hint, setHint] = useState('')
  const [bank, setBank] = useState([])        // current-password attempt bank (max 5)
  const [bankInput, setBankInput] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [knownCount, setKnownCount] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    Endpoints.twofaKnown().then((r) => setKnownCount(r?.count ?? 0)).catch(() => setKnownCount(null))
  }, [open])

  const allChecked = ids.length === accounts.length && accounts.length > 0
  const toggleAll = () => setIds(allChecked ? [] : accounts.map((a) => a.id))
  const toggle = (id) => setIds((arr) => arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id])

  function addBank() {
    const p = bankInput.trim()
    if (!p) return
    if (bank.length >= 5) { toast.error('Chỉ được nhập tối đa 5 mật khẩu hiện tại'); return }
    if (bank.includes(p)) { toast.info('Mật khẩu này đã được thêm'); setBankInput(''); return }
    setBank((arr) => [...arr, p]); setBankInput('')
  }
  const removeBank = (p) => setBank((arr) => arr.filter((x) => x !== p))

  async function start() {
    if (ids.length === 0) { toast.error('Hãy chọn ít nhất một tài khoản'); return }
    if (!newPwd) { toast.error('Hãy nhập mật khẩu 2FA mới'); return }
    if (newPwd.trim() !== newPwd2.trim()) { toast.error('Hai mật khẩu mới không khớp nhau'); return }
    if (!(await confirmVi(
      `Đặt/thay đổi mật khẩu xác minh hai bước cho ${ids.length} tài khoản?\n\n` +
      `Với tài khoản đã có 2FA, hệ thống sẽ thử các mật khẩu đã ghi nhớ` +
      `${bank.length ? ` và ${bank.length} mật khẩu bạn vừa nhập` : ''} (tối đa 5 lần thử mỗi tài khoản).`
    ))) return
    setBusy(true)
    await run(`2FA hàng loạt — ${ids.length} tài khoản`, (onEvent) =>
      Endpoints.bulk2fa({ account_ids: ids, new_password: newPwd, hint, password_bank: bank }, onEvent))
    setBusy(false)
    onChange?.()  // refresh 2FA counts
  }

  return (
    <div className="nb-card p-4 mb-4">
      <div className="flex items-center gap-2 cursor-pointer" onClick={() => setOpen((o) => !o)}>
        <span className="font-extrabold uppercase">Mật khẩu xác minh hai bước (2FA) hàng loạt</span>
        <span className="nb-badge bg-brand-violet text-black">đổi / đặt cho nhiều tài khoản</span>
        <span className="opacity-60 text-sm ml-auto">{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div className="mt-3 space-y-3">
          <div className="text-xs opacity-70">
            Đặt mật khẩu xác minh hai bước mới cho mọi tài khoản đã chọn. Tài khoản <b>chưa có</b> 2FA sẽ được
            bật 2FA. Tài khoản <b>đã có</b> 2FA cần mật khẩu hiện tại — hệ thống thử mật khẩu đã ghi nhớ
            của từng tài khoản trước (được lưu khi đăng nhập), sau đó thử danh sách bên dưới, tối đa
            5 lần cho mỗi tài khoản.
            {knownCount != null && <> Hiện hệ thống đang ghi nhớ <b>{knownCount}</b> mật khẩu từ quá trình đăng nhập.</>}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label>
              <div className="text-xs font-bold uppercase mb-1">Mật khẩu 2FA mới</div>
              <input type={showPwd ? 'text' : 'password'} className="nb-input" value={newPwd}
                onChange={(e) => setNewPwd(e.target.value)} placeholder="mật khẩu mới cho tất cả" />
            </label>
            <label>
              <div className="text-xs font-bold uppercase mb-1">Xác nhận mật khẩu mới</div>
              <input type={showPwd ? 'text' : 'password'} className="nb-input" value={newPwd2}
                onChange={(e) => setNewPwd2(e.target.value)} placeholder="nhập lại mật khẩu mới" />
            </label>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={showPwd} onChange={(e) => setShowPwd(e.target.checked)} />
              Hiện mật khẩu
            </label>
            <label className="flex items-center gap-2 text-xs flex-1 min-w-[180px]">
              <span className="font-bold uppercase">Gợi ý (không bắt buộc)</span>
              <input className="nb-input !py-1" maxLength={20} value={hint}
                onChange={(e) => setHint(e.target.value)} placeholder="tối đa 20 ký tự" />
            </label>
          </div>

          {/* current-password attempt bank */}
          <div className="nb-card-sm p-3">
            <div className="text-xs font-bold uppercase mb-1">Mật khẩu hiện tại để thử (tối đa 5)</div>
            <div className="text-[11px] opacity-60 mb-2">
              Dùng cho các tài khoản đã có 2FA nhưng hệ thống chưa ghi nhớ mật khẩu. Các mật khẩu sẽ được thử lần lượt cho đến khi đúng.
            </div>
            <div className="flex gap-2 mb-2">
              <input type={showPwd ? 'text' : 'password'} className="nb-input !py-1 text-sm" value={bankInput}
                onChange={(e) => setBankInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addBank() } }}
                placeholder="thêm mật khẩu hiện tại" disabled={bank.length >= 5} />
              <button className="nb-btn !px-3" onClick={addBank} disabled={bank.length >= 5}>Thêm</button>
            </div>
            <div className="flex flex-wrap gap-1">
              {bank.length === 0 && <span className="text-[11px] opacity-50">Chưa thêm mật khẩu nào.</span>}
              {bank.map((p, i) => (
                <span key={i} className="nb-badge bg-white text-black flex items-center gap-1">
                  <span className="font-mono text-[11px] normal-case">{showPwd ? p : '•'.repeat(Math.min(p.length, 8))}</span>
                  <button className="opacity-60 hover:opacity-100" onClick={() => removeBank(p)}>✕</button>
                </span>
              ))}
            </div>
          </div>

          {/* account picker */}
          <div className="nb-card-sm p-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-bold uppercase">Tài khoản</span>
              <button className="nb-btn !py-0.5 !px-2 text-[11px]" onClick={toggleAll}>{allChecked ? 'Bỏ chọn' : 'Chọn tất cả'}</button>
              <span className="text-xs opacity-70 ml-auto">{ids.length} đã chọn</span>
            </div>
            <div className="flex flex-wrap gap-1 max-h-40 overflow-auto">
              {accounts.map((a) => (
                <label key={a.id} className={'nb-badge cursor-pointer flex items-center gap-1 ' + (ids.includes(a.id) ? 'bg-brand-pri text-black' : 'bg-white text-black')}>
                  <input type="checkbox" checked={ids.includes(a.id)} onChange={() => toggle(a.id)} />
                  <span>{((a.first_name || '') + ' ' + (a.last_name || '')).trim() || a.phone}</span>
                  {a.has_2fa
                    ? <span className="text-[8px] font-bold text-brand-violet" title="đã có 2FA">2FA</span>
                    : <span className="text-[8px] font-bold opacity-40" title="chưa có 2FA">tắt</span>}
                </label>
              ))}
            </div>
          </div>

          <button className="nb-btn-pri w-full" disabled={busy} onClick={start}>
            {busy ? 'Đang thực hiện…' : `Đổi / đặt 2FA cho ${ids.length} tài khoản`}
          </button>
        </div>
      )}

      <ProgressModal progress={progress} onClose={close} />
    </div>
  )
}

function BulkTerminateSessionsPanel({ accounts, onChange }) {
  const toast = useToast()
  const { progress, run, close } = useBulkProgress()
  const [busy, setBusy] = useState(false)
  const accountCount = accounts.length

  async function start() {
    if (accountCount === 0) {
      toast.error('Không có tài khoản nào khả dụng')
      return
    }
    if (!(await confirmVi(
      `Chấm dứt các phiên Telegram khác trên ${accountCount} tài khoản?\n\n` +
      `Phiên hiện tại mà ứng dụng đang dùng sẽ được giữ hoạt động cho từng tài khoản. ` +
      `Phiên đăng nhập trang Quản lý Telegram của bạn sẽ không bị đăng xuất.`
    ))) return

    setBusy(true)
    await run(`Chấm dứt phiên — ${accountCount} tài khoản`, (onEvent) =>
      Endpoints.terminateOthersAll(onEvent))
    setBusy(false)
    onChange?.()
  }

  return (
    <div className="nb-card p-4 mb-4">
      <div className="flex items-start sm:items-center gap-3 flex-col sm:flex-row">
        <div className="flex-1">
          <div className="font-extrabold uppercase">Phiên đăng nhập của tất cả tài khoản</div>
          <div className="text-sm opacity-70">
            Một lần bấm sẽ xóa mọi phiên đăng nhập/thiết bị Telegram khác khỏi tất cả tài khoản đang kết nối, đồng thời giữ phiên Telegram hiện tại của ứng dụng hoạt động.
          </div>
        </div>
        <button className="nb-btn-err w-full sm:w-auto" disabled={busy || accountCount === 0} onClick={start}>
          {busy ? 'Đang thực hiện...' : `Chấm dứt phiên khác trên tất cả (${accountCount})`}
        </button>
      </div>

      <ProgressModal progress={progress} onClose={close} />
    </div>
  )
}

export default function SecurityTab({ accounts, onChange }) {
  return (
    <div>
      <div className="nb-card p-4 mb-4">
        <div className="font-extrabold uppercase">Trung tâm bảo mật</div>
        <div className="text-sm opacity-70">
          Toàn bộ tin nhắn từ tài khoản dịch vụ chính thức của Telegram (hiển thị trên điện thoại là <b>“Telegram”</b> / <b>+42777</b>, user_id nội bộ <b>777000</b>) theo từng tài khoản. Tin nhắn mới cũng kích hoạt thông báo trên máy tính. Dùng “Tải 50 tin mới nhất” để lấy lịch sử cho tài khoản mới thêm.
        </div>
      </div>
      <BulkTerminateSessionsPanel accounts={accounts} onChange={onChange} />
      <Bulk2faPanel accounts={accounts} onChange={onChange} />
      {accounts.length === 0 && <div className="opacity-60">Chưa có tài khoản.</div>}
      {accounts.map((a) => <AccountRow key={a.id} account={a} onChange={onChange} />)}
    </div>
  )
}
