import { useEffect, useState, useRef } from 'react'
import { Endpoints } from '../lib/api'
import { fetchAvatar, invalidate as invalidateAvatar } from '../lib/avatar'
import { useToast } from '../lib/toast.jsx'
import { CopyButton } from '../lib/CopyButton'
import ChatPanel from '../components/ChatPanel'
import { viDetail } from '../lib/vi'

function Section({ title, children }) {
  return (
    <div className="nb-card p-4 mb-4">
      <h3 className="font-extrabold uppercase tracking-tight mb-3">{title}</h3>
      {children}
    </div>
  )
}

export default function ProfileTab({ account, onRefresh }) {
  const toast = useToast()
  const [fn, setFn] = useState('')
  const [ln, setLn] = useState('')
  const [bio, setBio] = useState('')
  const [un, setUn] = useState('')
  const [unStatus, setUnStatus] = useState({ checking: false, ok: null, reason: '' })
  const [photoUrl, setPhotoUrl] = useState(null)
  const fileRef = useRef(null)

  useEffect(() => {
    if (!account) return
    setFn(account.first_name || '')
    setLn(account.last_name || '')
    setBio(account.bio || '')
    setUn(account.username || '')
    setUnStatus({ checking: false, ok: null, reason: '' })
    fetchAvatar(account.id).then(setPhotoUrl).catch(() => setPhotoUrl(null))
  }, [account?.id])

  if (!account) return <div className="opacity-60">Chọn một tài khoản từ thanh bên.</div>

  async function saveProfileField(field, value) {
    try {
      await Endpoints.updateProfile(account.id, { [field]: value })
      toast.success('Đã lưu thông tin hồ sơ')
      onRefresh?.()
    } catch (e) { toast.error(e.message) }
  }

  async function checkUsername() {
    if (!un || un === account.username) return
    setUnStatus({ checking: true, ok: null, reason: '' })
    try {
      const r = await Endpoints.checkUsername(account.id, un)
      setUnStatus({ checking: false, ok: r.available, reason: r.reason ? viDetail(r.reason) : '' })
    } catch (e) { setUnStatus({ checking: false, ok: false, reason: e.message }) }
  }

  async function saveUsername() {
    try {
      await Endpoints.updateUsername(account.id, un)
      toast.success('Đã lưu tên người dùng')
      onRefresh?.()
    } catch (e) { toast.error(e.message) }
  }

  async function uploadPhoto(e) {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      await Endpoints.uploadPhoto(account.id, file)
      toast.success('Đã cập nhật ảnh đại diện')
      invalidateAvatar(account.id)
      setPhotoUrl(await fetchAvatar(account.id))
    } catch (err) { toast.error(err.message) }
  }

  return (
    <div className="flex flex-col xl:flex-row gap-4 items-start">
      <div className="flex-1 max-w-3xl min-w-0 w-full">
      <Section title={`${account.first_name || ''} ${account.last_name || ''}`.trim() || account.phone}>
        <div className="flex items-center gap-4 mb-3">
          {photoUrl ? (
            <img src={photoUrl} alt="" className="w-20 h-20 border-2 border-black dark:border-white object-cover" />
          ) : (
            <div className="w-20 h-20 border-2 border-black dark:border-white flex items-center justify-center font-extrabold">Chưa có</div>
          )}
          <div>
            <input ref={fileRef} type="file" accept="image/*" onChange={uploadPhoto} className="hidden" />
            <button className="nb-btn" onClick={() => fileRef.current?.click()}>Tải ảnh lên</button>
          </div>
          <div className="ml-auto text-sm flex items-center gap-2 font-mono">
            {account.phone}<CopyButton value={account.phone} />
          </div>
        </div>
      </Section>

      <Section title="Họ tên">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-bold uppercase">Tên</label>
            <input className="nb-input" value={fn} onChange={(e) => setFn(e.target.value)} />
            <button className="nb-btn-pri mt-2" onClick={() => saveProfileField('first_name', fn)}>Lưu tên</button>
          </div>
          <div>
            <label className="text-xs font-bold uppercase">Họ</label>
            <input className="nb-input" value={ln} onChange={(e) => setLn(e.target.value)} />
            <button className="nb-btn-pri mt-2" onClick={() => saveProfileField('last_name', ln)}>Lưu họ</button>
          </div>
        </div>
      </Section>

      <Section title="Tên người dùng (@username)">
        <div className="flex items-center gap-2">
          <span className="font-mono">@</span>
          <input className="nb-input" value={un} onChange={(e) => { setUn(e.target.value); setUnStatus({ checking: false, ok: null, reason: '' }) }} />
          <button className="nb-btn" onClick={checkUsername} disabled={!un || unStatus.checking}>
            {unStatus.checking ? 'Đang kiểm tra…' : 'Kiểm tra'}
          </button>
          <button className="nb-btn-pri" onClick={saveUsername} disabled={unStatus.ok === false}>Lưu tên người dùng</button>
        </div>
        {unStatus.ok === true && <div className="text-sm mt-2 text-green-600 dark:text-green-400 font-bold">Có thể sử dụng</div>}
        {unStatus.ok === false && <div className="text-sm mt-2 text-red-500 font-bold">Không thể sử dụng: {unStatus.reason}</div>}
      </Section>

      <Section title="Tiểu sử (tối đa 70 ký tự)">
        <textarea
          className="nb-input min-h-[80px]"
          maxLength={70}
          value={bio}
          onChange={(e) => setBio(e.target.value)}
        />
        <div className="flex items-center justify-between mt-2">
          <span className="text-xs opacity-60">{bio.length}/70</span>
          <button className="nb-btn-pri" onClick={() => saveProfileField('bio', bio)}>Lưu tiểu sử</button>
        </div>
      </Section>
      </div>
      <ChatPanel account={account} className="w-full xl:flex-1 xl:max-w-xl min-w-0 xl:sticky xl:top-4" />
    </div>
  )
}
