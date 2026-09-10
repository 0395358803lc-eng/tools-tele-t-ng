import { useEffect, useRef, useState } from 'react'
import { Endpoints } from '../lib/api'
import { useToast } from '../lib/toast.jsx'
import ProgressModal from '../components/ProgressModal.jsx'
import ReactionBuilderModal from '../components/ReactionBuilderModal.jsx'
import { useBulkProgress } from '../lib/useBulkProgress'
import { confirmVi } from '../lib/confirmVi'

function AccountPicker({ accounts, ids, setIds }) {
  return (
    <div className="nb-card-sm p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="font-bold text-xs uppercase">Tài khoản</span>
        <button className="nb-btn !py-0.5 !px-1 text-[10px]" onClick={() => setIds(accounts.map((a) => a.id))}>Tất cả</button>
        <button className="nb-btn !py-0.5 !px-1 text-[10px]" onClick={() => setIds([])}>Không chọn</button>
        <span className="text-xs opacity-70 ml-auto">{ids.length} đã chọn</span>
      </div>
      <div className="flex flex-wrap gap-1 max-h-32 overflow-auto">
        {accounts.map((a) => (
          <label key={a.id} className={'nb-badge cursor-pointer ' + (ids.includes(a.id) ? 'bg-brand-pri text-black' : 'bg-white text-black')}>
            <input type="checkbox" className="mr-1"
              checked={ids.includes(a.id)}
              onChange={() => setIds((arr) => arr.includes(a.id) ? arr.filter((x) => x !== a.id) : [...arr, a.id])}
            />
            {(a.first_name || a.phone).slice(0, 14)}
          </label>
        ))}
      </div>
    </div>
  )
}

const keyOf = (e) => (e.custom_emoji_id ? `c:${e.custom_emoji_id}` : `s:${e.emoji}`)

// Split account ids across emojis by percentage. Shuffled so it's fair.
// Leftover accounts (when total% < 100) simply don't react. custom_emoji_id is
// carried through so premium custom emoji reactions reach the backend.
function distribute(ids, emojis) {
  const shuffled = [...ids]
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]]
  }
  const N = shuffled.length
  let cursor = 0
  const reactions = []
  for (const e of emojis) {
    let count = Math.min(Math.round((e.pct / 100) * N), N - cursor)
    const slice = shuffled.slice(cursor, cursor + count)
    cursor += count
    if (slice.length) reactions.push({ emoji: e.emoji, custom_emoji_id: e.custom_emoji_id || null, account_ids: slice })
  }
  return reactions
}

export default function MessagingTab({ accounts, selected }) {
  const toast = useToast()
  const { progress, run, close } = useBulkProgress()

  const [target, setTarget] = useState('')
  const [text, setText] = useState('')
  const [bulkIds, setBulkIds] = useState([])
  const [busy, setBusy] = useState(false)

  // recipient list CSV / Excel
  const recipientFileRef = useRef(null)
  const [recipientFile, setRecipientFile] = useState(null)
  const [recipientPreview, setRecipientPreview] = useState(null)
  const [recipientPreviewBusy, setRecipientPreviewBusy] = useState(false)
  const [recipientIds, setRecipientIds] = useState([])
  const [recipientText, setRecipientText] = useState('')
  const [recipientDelayMin, setRecipientDelayMin] = useState(3)
  const [recipientDelayMax, setRecipientDelayMax] = useState(7)

  // react
  const [postLink, setPostLink] = useState('')
  const [emojis, setEmojis] = useState([{ emoji: '🔥', pct: 100 }]) // [{emoji, pct}]
  const [reactModal, setReactModal] = useState(false)
  const [reactIds, setReactIds] = useState([])

  // view
  const [viewLink, setViewLink] = useState('')
  const [viewIds, setViewIds] = useState([])

  // wipe DM / chat by username (deletes the whole conversation, both sides)
  const [wipeTarget, setWipeTarget] = useState('')
  const [wipeIds, setWipeIds] = useState([])

  const totalPct = emojis.reduce((s, e) => s + (Number(e.pct) || 0), 0)
  const connectedAccounts = accounts.filter((a) => a.status === 'connected')
  const activeRecipientIds = recipientIds.filter((id) => connectedAccounts.some((a) => a.id === id))
  const recipientTotal = recipientPreview?.valid || 0
  const recipientDistribution = activeRecipientIds.map((id, index) => {
    const account = connectedAccounts.find((a) => a.id === id)
    const base = activeRecipientIds.length ? Math.floor(recipientTotal / activeRecipientIds.length) : 0
    const extra = activeRecipientIds.length && index < (recipientTotal % activeRecipientIds.length) ? 1 : 0
    return { account, count: base + extra }
  })

  useEffect(() => {
    Endpoints.getSettings().then((cfg) => {
      setRecipientDelayMin(Number(cfg.recipient_send_delay_min ?? 3))
      setRecipientDelayMax(Number(cfg.recipient_send_delay_max ?? 7))
    }).catch(() => {})
  }, [])

  async function sendOne() {
    if (!selected) { toast.error('Hãy chọn một tài khoản trước'); return }
    setBusy(true)
    try {
      await Endpoints.sendMessage(selected.id, target, text)
      toast.success('Đã gửi!')
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  async function sendBulk() {
    if (bulkIds.length === 0 || !target || !text) { toast.error('Hãy chọn tài khoản, đối tượng và nhập nội dung tin nhắn'); return }
    if (!(await confirmVi(`Gửi bằng ${bulkIds.length} tài khoản?`))) return
    setBusy(true)
    await run(`Gửi hàng loạt (${bulkIds.length} tài khoản)`, (onEvent) => Endpoints.bulkSend(bulkIds, target, text, onEvent))
    setBusy(false)
  }

  async function chooseRecipientFile(file) {
    setRecipientFile(file || null)
    setRecipientPreview(null)
    if (!file) return
    setRecipientPreviewBusy(true)
    try {
      const preview = await Endpoints.previewRecipientFile(file)
      setRecipientPreview(preview)
    } catch (e) {
      setRecipientFile(null)
      if (recipientFileRef.current) recipientFileRef.current.value = ''
      toast.error(e.message)
    } finally {
      setRecipientPreviewBusy(false)
    }
  }

  async function sendRecipientList() {
    if (!recipientFile || !recipientPreview) { toast.error('Hãy chọn tệp CSV hoặc Excel hợp lệ'); return }
    if (activeRecipientIds.length === 0) { toast.error('Hãy chọn ít nhất một tài khoản đang kết nối'); return }
    if (!recipientText.trim()) { toast.error('Hãy nhập nội dung tin nhắn'); return }
    const delayMin = Number(recipientDelayMin)
    const delayMax = Number(recipientDelayMax)
    if (!Number.isFinite(delayMin) || !Number.isFinite(delayMax) || delayMin < 0 || delayMax < 0 || delayMin > 3600 || delayMax > 3600 || delayMax < delayMin) {
      toast.error('Khoảng nghỉ phải từ 0 đến 3600 giây và tối đa không nhỏ hơn tối thiểu')
      return
    }
    const total = recipientPreview.valid || 0
    if (!(await confirmVi(
      `Gửi tin nhắn tới ${total} người nhận bằng ${activeRecipientIds.length} tài khoản?\n\n` +
      `Mỗi người nhận chỉ được gán cho đúng 1 session. Khoảng nghỉ giữa hai lần gửi của cùng session: ${delayMin}–${delayMax} giây.`
    ))) return
    setBusy(true)
    await run(`Gửi danh sách (${total} người nhận)`, (onEvent) =>
      Endpoints.sendRecipientFile(activeRecipientIds, recipientText.trim(), recipientFile, delayMin, delayMax, onEvent)
    )
    setBusy(false)
  }

  async function doReact() {
    if (reactIds.length === 0 || !postLink || emojis.length === 0) { toast.error('Hãy chọn tài khoản, liên kết và biểu cảm'); return }
    if (totalPct > 100) { toast.error('Tổng tỷ lệ không được vượt quá 100%'); return }
    const reactions = distribute(reactIds, emojis)
    if (reactions.length === 0) { toast.error('Hãy tăng tỷ lệ — hiện chưa có tài khoản nào được phân bổ'); return }
    setBusy(true)
    await run('Thả cảm xúc bài viết', (onEvent) => Endpoints.react(postLink, reactions, onEvent))
    setBusy(false)
  }

  async function doView() {
    if (viewIds.length === 0 || !viewLink) { toast.error('Hãy chọn tài khoản và nhập liên kết'); return }
    setBusy(true)
    await run(`Mở bài viết (${viewIds.length} tài khoản)`, (onEvent) => Endpoints.view(viewIds, viewLink, onEvent))
    setBusy(false)
  }

  async function doWipe() {
    const t = wipeTarget.trim()
    if (wipeIds.length === 0 || !t) { toast.error('Hãy chọn tài khoản và nhập @username'); return }
    if (!(await confirmVi(
      `XÓA TOÀN BỘ cuộc trò chuyện với "${t}" trên ${wipeIds.length} tài khoản?\n\n` +
      `Mọi tin nhắn trong cuộc trò chuyện sẽ bị xóa ở CẢ HAI PHÍA\n` +
      `và cuộc trò chuyện sẽ bị xóa hoàn toàn khỏi danh sách.\n\n` +
      `Đây là thao tác VĨNH VIỄN và không thể hoàn tác.`
    ))) return
    setBusy(true)
    await run(`Xóa cuộc trò chuyện — ${t} (${wipeIds.length} tài khoản)`, (onEvent) => Endpoints.bulkWipeChat(wipeIds, t, onEvent))
    setBusy(false)
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div className="nb-card p-4">
        <h3 className="font-extrabold uppercase mb-3">Gửi tin nhắn</h3>
        <input className="nb-input mb-2" placeholder="@username hoặc liên kết trò chuyện"
          value={target} onChange={(e) => setTarget(e.target.value)} />
        <textarea className="nb-input min-h-[100px] mb-2" placeholder="Nội dung tin nhắn"
          value={text} onChange={(e) => setText(e.target.value)} />
        <div className="flex gap-2">
          <button className="nb-btn-pri flex-1" disabled={busy || !target || !text} onClick={sendOne}>
            Gửi (1 tài khoản)
          </button>
        </div>
        <div className="mt-4">
          <AccountPicker accounts={accounts} ids={bulkIds} setIds={setBulkIds} />
        </div>
        <button className="nb-btn mt-3 w-full" disabled={busy} onClick={sendBulk}>
          Gửi hàng loạt bằng {bulkIds.length} tài khoản
        </button>
      </div>

      <div className="nb-card p-4">
        <h3 className="font-extrabold uppercase mb-3">Thả cảm xúc bài viết</h3>
        <input className="nb-input mb-2" placeholder="https://t.me/tenkenh/123"
          value={postLink} onChange={(e) => setPostLink(e.target.value)} />

        {/* chosen reactions summary + open the % builder popup */}
        <div className="nb-card-sm p-3 mb-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="font-bold text-xs uppercase">Biểu cảm</span>
            <button className="nb-btn !py-0.5 !px-2 text-[11px] ml-auto" onClick={() => setReactModal(true)}>
              Chọn biểu cảm & tỷ lệ %
            </button>
          </div>
          {emojis.length === 0 ? (
            <div className="text-xs opacity-60">Chưa chọn biểu cảm — nhấn “Chọn biểu cảm & tỷ lệ %”.</div>
          ) : (
            <div className="flex flex-wrap gap-1">
              {emojis.map((e) => (
                <span key={keyOf(e)} className="nb-badge bg-white text-black flex items-center gap-1">
                  <span className="text-base leading-none">{e.emoji}</span>
                  {e.custom_emoji_id && <span className="text-[9px] font-bold text-brand-violet" title="emoji tùy chỉnh">★</span>}
                  <span className="font-mono text-[11px]">{e.pct}%</span>
                </span>
              ))}
              <span className={'text-[11px] ml-auto font-bold self-center ' + (totalPct > 100 ? 'text-brand-err' : 'opacity-60')}>
                tổng {totalPct}%
              </span>
            </div>
          )}
        </div>

        <AccountPicker accounts={accounts} ids={reactIds} setIds={setReactIds} />
        <button className="nb-btn-pri mt-3 w-full" disabled={busy} onClick={doReact}>
          Gửi biểu cảm ({reactIds.length} tài khoản)
        </button>
      </div>

      <div className="nb-card p-4 lg:col-span-2">
        <h3 className="font-extrabold uppercase mb-1">Gửi theo danh sách CSV / Excel</h3>
        <div className="text-[11px] opacity-70 mb-3">
          Chọn tệp <b>CSV, XLS hoặc XLSX</b> chứa số điện thoại có mã quốc gia hoặc username Telegram.
          Hệ thống tự loại trùng, chuẩn hóa dữ liệu và chia người nhận luân phiên cho các tài khoản gửi đã chọn.
        </div>

        <input
          ref={recipientFileRef}
          type="file"
          className="hidden"
          accept=".csv,.xls,.xlsx,text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={(e) => chooseRecipientFile(e.target.files?.[0] || null)}
        />
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <button className="nb-btn" disabled={busy || recipientPreviewBusy} onClick={() => recipientFileRef.current?.click()}>
            {recipientPreviewBusy ? 'Đang đọc tệp…' : 'Chọn tệp CSV / Excel'}
          </button>
          <span className="text-xs font-mono opacity-70 break-all">
            {recipientFile ? recipientFile.name : 'Chưa chọn tệp'}
          </span>
          {recipientFile && (
            <button className="nb-btn !py-1 text-xs" onClick={() => {
              setRecipientFile(null); setRecipientPreview(null)
              if (recipientFileRef.current) recipientFileRef.current.value = ''
            }}>Bỏ tệp</button>
          )}
        </div>

        {recipientPreview && (
          <div className="nb-card-sm p-3 mb-3">
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="nb-badge bg-brand-ok text-black">Hợp lệ: {recipientPreview.valid}</span>
              <span className="nb-badge bg-white text-black">Số điện thoại: {recipientPreview.phones}</span>
              <span className="nb-badge bg-white text-black">Username: {recipientPreview.usernames}</span>
              <span className="nb-badge bg-brand-warn text-black">Trùng: {recipientPreview.duplicates}</span>
              <span className="nb-badge bg-white text-black">Không hợp lệ: {recipientPreview.invalid}</span>
            </div>
            {(recipientPreview.sample || []).length > 0 && (
              <div className="mt-2">
                <div className="text-[10px] font-bold uppercase opacity-60 mb-1">Xem trước tối đa 20 người nhận</div>
                <div className="flex flex-wrap gap-1">
                  {recipientPreview.sample.map((r, i) => (
                    <span key={`${r.value}-${i}`} className="nb-badge bg-white text-black font-mono text-[10px]">{r.value}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <textarea
          className="nb-input min-h-[100px] mb-3"
          placeholder="Nội dung tin nhắn gửi tới từng người nhận"
          value={recipientText}
          onChange={(e) => setRecipientText(e.target.value)}
        />
        <AccountPicker accounts={connectedAccounts} ids={recipientIds} setIds={setRecipientIds} />

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
          <label>
            <div className="text-xs font-bold uppercase mb-1">Nghỉ tối thiểu giữa 2 lần gửi / session</div>
            <input type="number" min="0" max="3600" step="0.1" className="nb-input"
              value={recipientDelayMin}
              onChange={(e) => setRecipientDelayMin(e.target.value)} />
          </label>
          <label>
            <div className="text-xs font-bold uppercase mb-1">Nghỉ tối đa giữa 2 lần gửi / session</div>
            <input type="number" min="0" max="3600" step="0.1" className="nb-input"
              value={recipientDelayMax}
              onChange={(e) => setRecipientDelayMax(e.target.value)} />
          </label>
        </div>
        <div className="text-[10px] opacity-60 mt-1">
          Hệ thống chọn ngẫu nhiên một khoảng nghỉ trong giới hạn trên sau mỗi lần gửi của cùng session. Đặt hai giá trị bằng nhau để dùng thời gian cố định.
        </div>

        {recipientPreview && activeRecipientIds.length > 0 && (
          <div className="nb-card-sm p-3 mt-3">
            <div className="text-[10px] font-extrabold uppercase mb-2">Phân bổ dự kiến — không trùng người nhận</div>
            <div className="flex flex-wrap gap-2">
              {recipientDistribution.map(({ account, count }) => (
                <span key={account.id} className="nb-badge bg-white text-black">
                  {(account.first_name || account.phone).slice(0, 18)}: {count}
                </span>
              ))}
            </div>
            <div className="text-[10px] opacity-60 mt-2">
              Tổng {recipientTotal} người nhận được chia cho {activeRecipientIds.length} session; chênh lệch giữa các session tối đa 1 người nhận.
            </div>
          </div>
        )}

        <button
          className="nb-btn-pri mt-3 w-full"
          disabled={busy || recipientPreviewBusy || !recipientPreview || activeRecipientIds.length === 0 || !recipientText.trim()}
          onClick={sendRecipientList}
        >
          Gửi tới {recipientPreview?.valid || 0} người nhận bằng {activeRecipientIds.length} tài khoản
        </button>
        <div className="text-[10px] opacity-60 mt-2">
          Chấp nhận ví dụ: <span className="font-mono">+84912345678</span>, <span className="font-mono">84912345678</span>,
          <span className="font-mono"> @username</span> hoặc cột có tiêu đề <b>Số điện thoại / Phone / Username / Usname / Người nhận</b>.
        </div>
      </div>

      <div className="nb-card p-4 lg:col-span-2">
        <h3 className="font-extrabold uppercase mb-3">Xem / Mở bài viết</h3>
        <input className="nb-input mb-2" placeholder="https://t.me/tenkenh/123"
          value={viewLink} onChange={(e) => setViewLink(e.target.value)} />
        <AccountPicker accounts={accounts} ids={viewIds} setIds={setViewIds} />
        <button className="nb-btn-pri mt-3" disabled={busy} onClick={doView}>
          Mở bài viết ({viewIds.length})
        </button>
      </div>

      <div className="nb-card p-4 lg:col-span-2">
        <h3 className="font-extrabold uppercase mb-1 text-brand-err">Xóa toàn bộ tin nhắn / cuộc trò chuyện</h3>
        <div className="text-[11px] opacity-70 mb-3">
          Dán @username (hoặc liên kết t.me). Với mỗi tài khoản đã chọn, TOÀN BỘ cuộc trò chuyện
          với người dùng đó sẽ bị xóa ở <b>cả hai phía</b> và cuộc trò chuyện cũng bị gỡ khỏi danh sách.
          Đây là thao tác vĩnh viễn, không thể hoàn tác.
        </div>
        <input className="nb-input mb-2" placeholder="@username hoặc https://t.me/username"
          value={wipeTarget} onChange={(e) => setWipeTarget(e.target.value)} />
        <AccountPicker accounts={accounts} ids={wipeIds} setIds={setWipeIds} />
        <button className="nb-btn-err mt-3" disabled={busy || wipeIds.length === 0 || !wipeTarget.trim()} onClick={doWipe}>
          Xóa cuộc trò chuyện trên {wipeIds.length} tài khoản
        </button>
      </div>

      {reactModal && (
        <ReactionBuilderModal
          accountCount={reactIds.length}
          accountId={reactIds[0] ?? selected?.id ?? accounts[0]?.id ?? null}
          postLink={postLink}
          initial={emojis}
          onConfirm={(list) => { setEmojis(list); setReactModal(false) }}
          onClose={() => setReactModal(false)}
        />
      )}

      <ProgressModal progress={progress} onClose={close} />
    </div>
  )
}
