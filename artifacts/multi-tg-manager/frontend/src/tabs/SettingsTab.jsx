import { useEffect, useState } from 'react'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import { viDetail } from '../lib/vi'

export default function SettingsTab() {
  const toast = useToast()
  const [s, setS] = useState(null)
  const [busy, setBusy] = useState(false)
  const [tg, setTg] = useState(null)
  const [tgId, setTgId] = useState('')
  const [tgHash, setTgHash] = useState('')
  const [tgBusy, setTgBusy] = useState(false)
  const [tgReconnect, setTgReconnect] = useState(false)
  const [storage, setStorage] = useState(null)

  useEffect(() => {
    Endpoints.getSettings().then(setS).catch((e) => toast.error(e.message))
    Endpoints.storageStatus().then(setStorage).catch(() => {})
    Endpoints.getTelegramApi().then((r) => {
      setTg(r)
      setTgId(r?.api_id ? String(r.api_id) : '')
    }).catch((e) => toast.error(e.message))
  }, [])

  if (!s || !tg) return <div className="opacity-60">Đang tải cài đặt…</div>

  async function saveTelegramApi() {
    const apiId = Number.parseInt(tgId, 10)
    if (!Number.isInteger(apiId) || apiId <= 0) {
      toast.error('TG_API_ID phải là số nguyên dương')
      return
    }
    setTgBusy(true)
    try {
      const r = await Endpoints.putTelegramApi(apiId, tgHash.trim(), tgReconnect)
      setTg(r)
      setTgId(String(r.api_id || apiId))
      setTgHash('')
      setTgReconnect(false)
      const reconnect = r.reconnected || 0
      const failed = r.failed || 0
      if (failed) toast.error(`Đã lưu Telegram API. Kết nối lại thành công ${reconnect}; thất bại ${failed}.`)
      else if (r.reconnect_required) toast.success('Đã lưu Telegram API. Các client đang kết nối được giữ nguyên, không bị ngắt.')
      else toast.success(`Đã lưu Telegram API. Đã kết nối lại ${reconnect} tài khoản.`)
    } catch (e) {
      toast.error(e.message)
    } finally {
      setTgBusy(false)
    }
  }

  async function save() {
    setBusy(true)
    try {
      const r = await Endpoints.putSettings(s)
      setS(r)
      window.dispatchEvent(new CustomEvent('mtm-settings-changed', { detail: r }))
      toast.success('Đã lưu cài đặt')
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  async function exportJson() {
    try {
      const data = await Endpoints.exportJson()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `tai-khoan-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) { toast.error(e.message) }
  }

  return (
    <div className="max-w-2xl space-y-4">
      {storage && (
        <div className="nb-card p-5">
          <div className="flex items-center justify-between gap-3 mb-3">
            <h3 className="font-extrabold uppercase">Trạng thái lưu trữ trung tâm</h3>
            <span className={'nb-badge text-black ' + (storage.portable_ready ? 'bg-brand-ok' : 'bg-brand-warn')}>
              {storage.portable_ready ? 'Sẵn sàng chuyển máy chủ' : 'Chưa đồng bộ đủ'}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div>Database: <b>PostgreSQL</b></div>
            <div>Lưu trữ trình duyệt: <b>Không dùng</b></div>
            <div>Tài khoản SQL: <b>{storage.accounts}</b></div>
            <div>Session mã hóa: <b>{storage.session_blobs}/{storage.accounts}</b></div>
            <div>Thiếu session SQL: <b>{storage.missing_session_blobs}</b></div>
            <div>2FA đã nhớ trong SQL: <b>{storage.remembered_2fa}</b></div>
          </div>
          <p className="text-xs opacity-70 mt-3">
            File .session trên máy chủ chỉ là bộ nhớ đệm runtime và có thể được tái tạo từ PostgreSQL.
          </p>
        </div>
      )}

      <div className="nb-card p-5">
        <div className="flex items-center justify-between gap-3 mb-4">
          <h3 className="font-extrabold uppercase">Thông tin Telegram API</h3>
          <span className={'nb-badge text-black ' + (tg.configured ? 'bg-brand-ok' : 'bg-brand-warn')}>
            {tg.configured ? 'Đã cấu hình' : 'Chưa cấu hình'}
          </span>
        </div>
        <p className="text-xs opacity-70 mb-3">
          Nhập API ID và API Hash từ my.telegram.org. Các lần đăng nhập mới sẽ dùng ngay cặp thông tin đã lưu.
          Các client đang kết nối vẫn tiếp tục hoạt động trừ khi bạn chủ động yêu cầu kết nối lại. API Hash
          được mã hóa trong PostgreSQL và không bao giờ được trả lại trình duyệt sau khi lưu.
        </p>
        <label className="block mb-3">
          <div className="text-xs font-bold uppercase mb-1">TG_API_ID</div>
          <input type="number" min="1" className="nb-input" value={tgId}
            onChange={(e) => setTgId(e.target.value)} placeholder="12345678" />
        </label>
        <label className="block">
          <div className="text-xs font-bold uppercase mb-1">TG_API_HASH</div>
          <input type="password" className="nb-input" value={tgHash} autoComplete="new-password"
            onChange={(e) => setTgHash(e.target.value)}
            placeholder={tg.api_hash_set ? 'Đã lưu — để trống để giữ API Hash hiện tại' : 'API Hash gồm 32 ký tự'} />
          <p className="text-xs opacity-70 mt-1">
            {tg.api_hash_set
              ? 'Đã có API Hash được lưu. Chỉ nhập API Hash mới khi bạn muốn thay cặp thông tin.'
              : 'Bắt buộc khi cấu hình lần đầu.'}
          </p>
        </label>
        {tg.configured && (
          <label className="flex items-center gap-2 mt-3 text-sm">
            <input type="checkbox" checked={tgReconnect}
              onChange={(e) => setTgReconnect(e.target.checked)} />
            <span>Kết nối lại tất cả tài khoản đã lưu ngay bằng cặp API này</span>
          </label>
        )}
        <button className="nb-btn-pri mt-3" disabled={tgBusy} onClick={saveTelegramApi}>
          {tgBusy ? 'Đang áp dụng…' : 'Lưu và áp dụng Telegram API'}
        </button>
        {(tg.results || []).some((row) => row.status === 'failed') && (
          <div className="mt-3 border-2 border-black dark:border-white p-3">
            <div className="text-xs font-extrabold uppercase mb-2">Tài khoản kết nối lại thất bại</div>
            <div className="space-y-1 max-h-40 overflow-auto text-xs font-mono">
              {(tg.results || []).filter((row) => row.status === 'failed').slice(0, 20).map((row) => (
                <div key={row.account_id} className="flex gap-2 justify-between">
                  <span>{row.phone || `#${row.account_id}`}</span>
                  <span className="text-right opacity-70">{row.detail ? viDetail(row.detail) : 'Thất bại'}</span>
                </div>
              ))}
            </div>
            {(tg.results || []).filter((row) => row.status === 'failed').length > 20 && (
              <div className="text-[10px] opacity-60 mt-2">Chỉ hiển thị 20 lỗi đầu tiên.</div>
            )}
          </div>
        )}
      </div>

      <div className="nb-card p-5">
        <h3 className="font-extrabold uppercase mb-4">Giới hạn tần suất thao tác</h3>
        <p className="text-xs opacity-70 mb-3">
          Khoảng nghỉ giữa các thao tác cho từng tài khoản khi chạy hàng loạt. Nhiều tài khoản có thể chạy song song,
          nhưng mỗi tài khoản được điều tiết độc lập. Giá trị thấp chạy nhanh hơn nhưng tăng nguy cơ bị Telegram giới hạn.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <label>
            <div className="text-xs font-bold uppercase mb-1">Số giây tối thiểu giữa các thao tác</div>
            <input type="number" step="0.1" className="nb-input" value={s.rate_min}
              onChange={(e) => setS({ ...s, rate_min: Number(e.target.value) || 0 })} />
          </label>
          <label>
            <div className="text-xs font-bold uppercase mb-1">Số giây tối đa</div>
            <input type="number" step="0.1" className="nb-input" value={s.rate_max}
              onChange={(e) => setS({ ...s, rate_max: Number(e.target.value) || 0 })} />
          </label>
        </div>
        <label className="block mt-3">
          <div className="text-xs font-bold uppercase mb-1">Số tài khoản chạy song song (kích thước lô)</div>
          <input type="number" step="1" min="1" max="50" className="nb-input"
            value={s.concurrency ?? 5}
            onChange={(e) => setS({ ...s, concurrency: Math.max(1, parseInt(e.target.value) || 1) })} />
        </label>

        <div className="border-t-2 border-black dark:border-white mt-4 pt-4">
          <div className="font-extrabold uppercase text-sm mb-1">Gửi tin nhắn theo danh sách CSV / Excel</div>
          <p className="text-xs opacity-70 mb-3">
            Khoảng nghỉ mặc định giữa hai tin nhắn liên tiếp của cùng một session. Mỗi chiến dịch có thể ghi đè tạm thời ngay trong màn hình Tin nhắn.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <label>
              <div className="text-xs font-bold uppercase mb-1">Nghỉ tối thiểu (giây)</div>
              <input type="number" step="0.1" min="0" max="3600" className="nb-input"
                value={s.recipient_send_delay_min ?? 3}
                onChange={(e) => setS({ ...s, recipient_send_delay_min: Number(e.target.value) || 0 })} />
            </label>
            <label>
              <div className="text-xs font-bold uppercase mb-1">Nghỉ tối đa (giây)</div>
              <input type="number" step="0.1" min="0" max="3600" className="nb-input"
                value={s.recipient_send_delay_max ?? 7}
                onChange={(e) => setS({ ...s, recipient_send_delay_max: Number(e.target.value) || 0 })} />
            </label>
          </div>
          <p className="text-[10px] opacity-60 mt-2">
            Nếu hai giá trị bằng nhau, hệ thống dùng thời gian nghỉ cố định. Nếu khác nhau, mỗi lần gửi sẽ chọn ngẫu nhiên trong khoảng này.
          </p>
        </div>
      </div>

      <div className="nb-card p-5">
        <h3 className="font-extrabold uppercase mb-4">Tệp phiên đăng nhập</h3>
        <label className="block">
          <div className="text-xs font-bold uppercase mb-1">Đường dẫn thư mục phiên</div>
          <input className="nb-input" value={s.sessions_dir}
            onChange={(e) => setS({ ...s, sessions_dir: e.target.value })} />
          <p className="text-xs opacity-70 mt-1">Thay đổi có hiệu lực sau lần khởi động lại backend tiếp theo.</p>
        </label>
      </div>

      <div className="nb-card p-5">
        <h3 className="font-extrabold uppercase mb-4">Hành vi hệ thống</h3>
        <label className="flex items-center gap-2 mb-2">
          <input type="checkbox" checked={s.auto_reconnect}
            onChange={(e) => setS({ ...s, auto_reconnect: e.target.checked })} />
          <span>Tự động kết nối lại khi mất kết nối</span>
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={s.notification_sound}
            onChange={(e) => setS({ ...s, notification_sound: e.target.checked })} />
          <span>Thông báo trên máy tính khi có cảnh báo bảo mật mới</span>
        </label>
      </div>

      <div className="flex gap-2">
        <button className="nb-btn-pri" disabled={busy} onClick={save}>Lưu cài đặt</button>
        <button className="nb-btn" onClick={exportJson}>Xuất danh sách tài khoản JSON</button>
      </div>
    </div>
  )
}
