import { useEffect, useState, useMemo } from 'react'
import { CopyButton } from '../lib/CopyButton'
import { fmtTime } from '../lib/util'
import { Endpoints } from '../lib/api'
import AccountAvatar from '../components/AccountAvatar'
import { viSecurityType, viStatus } from '../lib/vi'

function Stat({ label, value, color = 'bg-white', hint }) {
  return (
    <div className={`nb-stat p-4 ${color}`}>
      <div className="text-[10px] uppercase font-extrabold tracking-tight">{label}</div>
      <div className="text-3xl font-extrabold mt-1 font-mono leading-none">{value}</div>
      {hint && <div className="text-[10px] opacity-70 mt-1">{hint}</div>}
    </div>
  )
}

function StatusBar({ stats, total }) {
  const c = stats.connected || 0
  const b = stats.banned || 0
  const d = Math.max(total - c - b, 0)
  if (total === 0) return null
  const pct = (n) => `${(n / total) * 100}%`
  return (
    <div className="flex h-4 border-2 border-black dark:border-white overflow-hidden">
      <div className="bg-brand-ok"   title={`${c} đã kết nối`}    style={{ width: pct(c) }} />
      <div className="bg-brand-warn" title={`${d} mất kết nối`} style={{ width: pct(d) }} />
      <div className="bg-brand-err"  title={`${b} bị cấm`}       style={{ width: pct(b) }} />
    </div>
  )
}

function StatusDot({ status }) {
  const c = status === 'connected' ? 'bg-brand-ok' : status === 'banned' ? 'bg-brand-err' : 'bg-brand-warn'
  return <span className={'inline-block w-2 h-2 ' + c + ' border border-black dark:border-white'} title={viStatus(status)} />
}

export default function DashboardTab({ stats, accounts, onSelect, onChange }) {
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState('all')   // all|connected|disconnected|banned|2fa|alerts
  const [recentAlerts, setRecentAlerts] = useState([])
  const [marking, setMarking] = useState(false)
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 50

  useEffect(() => {
    Endpoints.securityMessages(undefined, true).then((m) => setRecentAlerts((m || []).slice(0, 10))).catch(() => {})
  }, [stats.unread_security])

  async function markAllRead() {
    setMarking(true)
    try {
      await Endpoints.markAllRead()
      setRecentAlerts([])
      onChange?.()
    } catch (e) {
      // surface nothing intrusive; alerts will reappear on next refresh if it failed
    } finally {
      setMarking(false)
    }
  }

  const filtered = useMemo(() => {
    const qq = q.trim().toLowerCase()
    return accounts.filter((a) => {
      if (filter === 'connected'    && a.status !== 'connected')    return false
      if (filter === 'disconnected' && a.status === 'connected')    return false
      if (filter === 'banned'       && a.status !== 'banned')       return false
      if (filter === '2fa'          && !a.has_2fa)                  return false
      if (filter === 'alerts'       && (a.unread_security || 0) === 0) return false
      if (!qq) return true
      const hay = `${a.first_name} ${a.last_name} ${a.username} ${a.phone}`.toLowerCase()
      return hay.includes(qq)
    })
  }, [accounts, q, filter])

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount)
  const paged = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  useEffect(() => { setPage(1) }, [q, filter, accounts.length])

  const totalAlerts = stats.unread_security || 0
  const without2fa = stats.total - stats.with_2fa
  const onlineCount = accounts.filter((a) => a.is_online).length

  return (
    <div className="space-y-4">
      {/* TOP STATS */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3">
        <Stat label="Tổng tài khoản" value={stats.total} />
        <Stat label="Đã kết nối"      value={stats.connected} color="bg-brand-ok" />
        <Stat label="Mất kết nối"   value={Math.max(stats.total - stats.connected - stats.banned, 0)} color="bg-brand-warn" />
        <Stat label="Bị cấm"         value={stats.banned}    color="bg-brand-err" />
        <Stat label="Đã bật 2FA"    value={stats.with_2fa}  color="bg-brand-violet"
              hint={without2fa > 0 ? `${without2fa} chưa bật 2FA` : 'tất cả đã được bảo vệ'} />
        <Stat label="Cảnh báo chưa đọc"  value={totalAlerts}     color={totalAlerts > 0 ? 'bg-brand-err' : 'bg-white'} />
      </div>

      {/* HEALTH BAR */}
      <div className="nb-card p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="font-extrabold uppercase text-sm">Tình trạng tài khoản</div>
          <div className="text-xs opacity-70">
            <span className="font-bold">{onlineCount}</span> đang trực tuyến •{' '}
            <span className="font-bold">{stats.connected}</span> đã kết nối •{' '}
            <span className="font-bold">{stats.with_2fa}/{stats.total}</span> có 2FA
          </div>
        </div>
        <StatusBar stats={stats} total={stats.total || 1} />
        <div className="flex gap-4 mt-2 text-[11px] flex-wrap">
          <span className="flex items-center gap-1"><span className="w-3 h-3 bg-brand-ok border border-black" /> Đã kết nối ({stats.connected})</span>
          <span className="flex items-center gap-1"><span className="w-3 h-3 bg-brand-warn border border-black" /> Mất kết nối ({Math.max(stats.total - stats.connected - stats.banned, 0)})</span>
          <span className="flex items-center gap-1"><span className="w-3 h-3 bg-brand-err border border-black" /> Bị cấm ({stats.banned})</span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* ACCOUNT TABLE (2/3) */}
        <div className="lg:col-span-2 nb-card p-4">
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <div className="font-extrabold uppercase">Tất cả tài khoản ({filtered.length}/{accounts.length})</div>
            <div className="ml-auto flex gap-2 items-center flex-wrap">
              <input className="nb-input !w-44 !py-1 text-xs" placeholder="Tìm tên / số điện thoại / @"
                value={q} onChange={(e) => setQ(e.target.value)} />
              <select className="nb-input !w-auto !py-1 text-xs" value={filter} onChange={(e) => setFilter(e.target.value)}>
                <option value="all">Tất cả</option>
                <option value="connected">Đã kết nối</option>
                <option value="disconnected">Mất kết nối</option>
                <option value="banned">Bị cấm</option>
                <option value="2fa">Có 2FA</option>
                <option value="alerts">Có cảnh báo chưa đọc</option>
              </select>
            </div>
          </div>

          {filtered.length === 0 && (
            <div className="text-sm opacity-60 p-4 text-center">
              {accounts.length === 0
                ? 'Chưa có tài khoản. Hãy thêm tài khoản từ thanh bên.'
                : 'Không có tài khoản nào khớp bộ lọc này.'}
            </div>
          )}

          <div className="overflow-auto max-h-[calc(100vh-360px)]">
            <table className="w-full text-sm">
              <thead className="text-[10px] uppercase font-extrabold sticky top-0 bg-white dark:bg-zinc-900 border-b-2 border-black dark:border-white">
                <tr>
                  <th className="text-left p-2">Tài khoản</th>
                  <th className="text-left p-2">Số điện thoại</th>
                  <th className="text-left p-2">Tên người dùng</th>
                  <th className="text-left p-2">Trạng thái</th>
                  <th className="text-left p-2">2FA</th>
                  <th className="text-left p-2">Cảnh báo</th>
                  <th className="text-left p-2">Hoạt động gần nhất</th>
                </tr>
              </thead>
              <tbody>
                {paged.map((a) => (
                  <tr key={a.id} onClick={() => onSelect(a.id)}
                      className="border-b border-zinc-300 dark:border-zinc-700 hover:bg-brand-pri hover:text-black cursor-pointer">
                    <td className="p-2">
                      <div className="flex items-center gap-2">
                        <AccountAvatar account={a} size={28} />
                        <span className="font-bold truncate max-w-[140px]">{(a.first_name + ' ' + a.last_name).trim() || '—'}</span>
                        {a.is_online && <span className="w-2 h-2 bg-brand-ok border border-black" title="đang trực tuyến" />}
                      </div>
                    </td>
                    <td className="p-2 font-mono text-xs">
                      <span className="inline-flex items-center gap-1">{a.phone}<CopyButton value={a.phone} /></span>
                    </td>
                    <td className="p-2 font-mono text-xs">
                      {a.username
                        ? <span className="inline-flex items-center gap-1">@{a.username}<CopyButton value={a.username} /></span>
                        : <span className="opacity-40">—</span>}
                    </td>
                    <td className="p-2">
                      <span className="inline-flex items-center gap-1"><StatusDot status={a.status} /> <span className="text-xs uppercase">{viStatus(a.status)}</span></span>
                    </td>
                    <td className="p-2">
                      {a.has_2fa
                        ? <span className="nb-badge bg-brand-violet text-black">BẬT</span>
                        : <span className="nb-badge bg-zinc-200 text-zinc-700">TẮT</span>}
                    </td>
                    <td className="p-2">
                      {a.unread_security > 0
                        ? <span className="nb-badge bg-brand-err text-black">{a.unread_security}</span>
                        : <span className="opacity-40">—</span>}
                    </td>
                    <td className="p-2 text-xs opacity-70">
                      {a.last_seen ? fmtTime(a.last_seen) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          {filtered.length > PAGE_SIZE && (
            <div className="flex items-center justify-end gap-2 mt-3 text-xs">
              <button className="nb-btn !py-1 !px-2" disabled={safePage <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>Trước</button>
              <span className="font-mono">Trang {safePage}/{pageCount}</span>
              <button className="nb-btn !py-1 !px-2" disabled={safePage >= pageCount} onClick={() => setPage((p) => Math.min(pageCount, p + 1))}>Sau</button>
            </div>
          )}
          </div>
        </div>

        {/* RECENT ALERTS (1/3) */}
        <div className="nb-card p-4 h-fit">
          <div className="flex items-center justify-between mb-3">
            <div className="font-extrabold uppercase">Cảnh báo gần đây</div>
            <div className="flex items-center gap-2">
              {totalAlerts > 0 && (
                <button className="nb-btn !py-0.5 !px-2 text-[10px] uppercase font-extrabold"
                        onClick={markAllRead} disabled={marking}>
                  {marking ? '…' : 'Đánh dấu đã đọc tất cả'}
                </button>
              )}
              <span className="nb-badge bg-brand-err text-black">{totalAlerts}</span>
            </div>
          </div>
          {recentAlerts.length === 0 && (
            <div className="text-sm opacity-60">Không có cảnh báo bảo mật chưa đọc.</div>
          )}
          <div className="space-y-2 max-h-[60vh] overflow-auto">
            {recentAlerts.map((m) => {
              const acc = accounts.find((a) => a.id === m.account_id)
              return (
                <div key={m.id} className="nb-card-sm p-2 text-xs" onClick={() => acc && onSelect(acc.id)}>
                  <div className="flex items-center gap-1 mb-1">
                    <span className="nb-badge bg-brand-warn text-black !text-[9px]">{viSecurityType(m.type)}</span>
                    <span className="opacity-70 ml-auto">{fmtTime(m.received_at)}</span>
                  </div>
                  <div className="font-mono whitespace-pre-wrap line-clamp-3">{m.message_text}</div>
                  {acc && <div className="text-[10px] opacity-60 mt-1">→ {(acc.first_name || acc.phone)}</div>}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
