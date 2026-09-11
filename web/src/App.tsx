import { FormEvent, useEffect, useMemo, useState } from "react";
import QRCode from "qrcode";
import {
  Link,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { api, ApiError, AccountStatus, Item, Job, RuntimeConfig, TelegramAccount, ManagerProfile, ManagerSession, ManagerSecurityMessage, ManagerDialog, ManagerChatMessage, ManagerPeer, ManagerTargetCheck, ManagerAudit, QrLoginState } from "./api";

const navItems = [
  { to: "/", label: "Tổng quan", icon: "⌂", end: true },
  { to: "/account", label: "Tài khoản", icon: "◉" },
  { to: "/profile", label: "Hồ sơ", icon: "◎" },
  { to: "/security", label: "Bảo mật", icon: "◆" },
  { to: "/groups", label: "Nhóm & Kênh", icon: "#" },
  { to: "/messages", label: "Tin nhắn", icon: "✉" },
  { to: "/checker", label: "Kiểm tra", icon: "✓" },
  { to: "/bulk", label: "Hàng loạt", icon: "⇶" },
  { to: "/settings", label: "Cài đặt", icon: "⚙" },
];

function App() {
  const [user, setUser] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);
  useEffect(() => {
    api.me().then((data) => setUser(data.username)).catch(() => setUser(null)).finally(() => setChecking(false));
  }, []);
  if (checking) return <div className="splash"><div className="brand-mark">TS</div><span>Đang kết nối workspace…</span></div>;
  if (!user) return <Routes><Route path="*" element={<Login onLoggedIn={setUser} />} /></Routes>;
  return (
    <Routes>
      <Route element={<Shell user={user} onLogout={() => setUser(null)} />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/account" element={<AccountPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/security" element={<SecurityPage />} />
        <Route path="/groups" element={<GroupsPage />} />
        <Route path="/messages" element={<MessagesPage />} />
        <Route path="/checker" element={<Dashboard />} />
        <Route path="/bulk" element={<BulkPage />} />
        <Route path="/import" element={<ImportPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/jobs/:jobId" element={<JobDetail />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

function Login({ onLoggedIn }: { onLoggedIn: (username: string) => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const data = await api.login(username, password);
      onLoggedIn(data.username);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Đăng nhập thất bại.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-page">
      <div className="login-decoration">
        <div className="orb orb-one" /><div className="orb orb-two" />
        <div className="login-copy"><span className="eyebrow">TELEGRAM SCOPE</span><h1>Kiểm tra dữ liệu<br /><em>rõ ràng hơn.</em></h1><p>Quản lý các job kiểm tra số điện thoại, theo dõi tiến độ và xuất kết quả trong một workspace riêng tư.</p></div>
      </div>
      <form className="login-card" onSubmit={submit}>
        <div className="brand-row"><div className="brand-mark">TS</div><div><strong>Telegram Scope</strong><span>Private workspace</span></div></div>
        <div className="login-heading"><span className="eyebrow">WELCOME BACK</span><h2>Đăng nhập workspace</h2><p>Sử dụng thông tin Web UI đã cấu hình trên máy chủ.</p></div>
        <label>Tên đăng nhập<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" /></label>
        <label>Mật khẩu<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
        {error && <div className="alert error">{error}</div>}
        <button className="button primary wide" disabled={busy}>{busy ? "Đang xác thực…" : "Đăng nhập"} <span>→</span></button>
        <p className="login-foot">Phiên đăng nhập được giữ trong 12 giờ.</p>
      </form>
    </div>
  );
}

function Shell({ user, onLogout }: { user: string; onLogout: () => void }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [loggingOut, setLoggingOut] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [light, setLight] = useState(false);
  const [accounts, setAccounts] = useState<TelegramAccount[]>([]);
  const current = navItems.find((item) => item.end ? location.pathname === item.to : location.pathname.startsWith(item.to));
  const refreshAccounts = () => api.accounts().then(({ items }) => setAccounts(items)).catch(() => {});
  useEffect(() => { refreshAccounts(); const id = window.setInterval(refreshAccounts, 30000); return () => window.clearInterval(id); }, []);
  useEffect(() => { api.uiPreferences().then((v) => setLight(v.theme === "light")).catch(() => {}); }, []);
  async function toggleTheme() {
    const next = !light;
    setLight(next);
    try { await api.setUiPreferences(next ? "light" : "dark"); }
    catch { setLight(!next); }
  }
  async function logout() {
    setLoggingOut(true);
    try { await api.logout(); } finally { onLogout(); navigate("/"); setLoggingOut(false); }
  }
  const connected = accounts.filter((a) => a.state === "AUTHORIZED" || a.state === "IN_USE").length;
  return (
    <div className={`mtm-shell ${light ? "mtm-light" : ""}`}>
      <header className="mtm-header">
        <button className="nb-btn square" onClick={() => setSidebarOpen((v) => !v)} title="Ẩn/hiện danh sách tài khoản">☰</button>
        <div className="mtm-brand"><strong>QUẢN LÝ TELEGRAM</strong><small>Checker + Multi TG Manager</small></div>
        <div className="mtm-header-spacer" />
        <div className="mtm-head-stat"><span>Tài khoản</span><b>{accounts.length}</b></div>
        <div className="mtm-head-stat"><span>Kết nối</span><b>{connected}</b></div>
        <span className="mtm-sql-badge">● PostgreSQL</span>
        <button className="nb-btn square" onClick={toggleTheme} title="Đổi giao diện sáng/tối">{light ? "☾" : "☀"}</button>
        <button className="nb-btn square" onClick={logout} disabled={loggingOut} title="Đăng xuất">⏻</button>
      </header>
      <div className="mtm-body">
        {sidebarOpen && <aside className="mtm-account-rail">
          <div className="rail-title"><strong>Tài khoản ({accounts.length})</strong><Link to="/account">＋</Link></div>
          <div className="rail-scroll">
            {accounts.length === 0 ? <div className="rail-empty">Chưa có tài khoản Telegram.</div> : accounts.map((a, index) =>
              <Link to="/account" className={`rail-account ${a.is_default ? "selected" : ""}`} key={a.id}>
                <span className="rail-index">{index + 1}</span>
                <span className={`rail-avatar ${a.state.toLowerCase()}`}>{(a.label || "T").slice(0, 1).toUpperCase()}</span>
                <span className="rail-copy"><strong>{a.label}</strong><small>{a.phone}</small><em>{a.state === "AUTHORIZED" ? "Đã kết nối" : a.state === "IN_USE" ? "Đang chạy job" : a.state === "LOGIN_IN_PROGRESS" ? "Đang xác thực" : "Chưa kết nối"}</em></span>
                {a.is_default && <span className="rail-default">★</span>}
              </Link>)}
          </div>
          <div className="rail-footer"><Link className="nb-btn nb-primary wide" to="/account">＋ Thêm tài khoản</Link></div>
        </aside>}
        <main className="mtm-main">
          <nav className="mtm-tabs">{navItems.map((item) => <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => isActive ? "nb-tab active" : "nb-tab"}><span>{item.icon}</span>{item.label}</NavLink>)}</nav>
          <div className="mtm-page-head"><div><span>WORKSPACE / {current?.label || "Chi tiết job"}</span><h1>{current?.label || "Chi tiết job"}</h1></div><div className="mtm-page-actions"><Link className="nb-btn" to="/import">↥ Nhập file</Link><Link className="nb-btn nb-primary" to="/checker">＋ Tạo job</Link></div></div>
          <div className="mtm-content"><Outlet /></div>
        </main>
      </div>
    </div>
  );
}

function AccountPicker({ accounts, value, onChange }: { accounts: TelegramAccount[]; value: string; onChange: (id: string) => void }) {
  return <label className="manager-account-picker">Tài khoản Telegram<select value={value} onChange={(e) => onChange(e.target.value)}><option value="">Chọn tài khoản</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.label} · {a.phone} · {a.state === "AUTHORIZED" ? "Đã kết nối" : a.state}</option>)}</select></label>;
}

function ProfilePage() {
  const [accounts, setAccounts] = useState<TelegramAccount[]>([]);
  const [id, setId] = useState("");
  const [profile, setProfile] = useState<ManagerProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [usernameState, setUsernameState] = useState("");
  const [photoVersion, setPhotoVersion] = useState(0);
  const [photoMissing, setPhotoMissing] = useState(false);
  useEffect(() => { api.accounts().then(({ items }) => { setAccounts(items); const a=items.find(x=>x.is_default)||items[0]; if(a) setId(a.id); }); }, []);
  useEffect(() => { if(!id){setProfile(null);return;} setError(""); setUsernameState(""); setPhotoMissing(false); api.managerProfile(id).then(setProfile).catch((e)=>setError(e.message)); }, [id]);
  async function save(e: FormEvent){e.preventDefault(); if(!profile||!id)return; setBusy(true);setError("");setNotice("");try{const p=await api.updateManagerProfile(id,{first_name:profile.first_name,last_name:profile.last_name,about:profile.about});await api.updateManagerUsername(id,profile.username);setProfile({...profile,...p});setNotice("Đã cập nhật hồ sơ Telegram.");}catch(e){setError(e instanceof Error?e.message:"Không thể cập nhật hồ sơ.");}finally{setBusy(false);}}
  async function checkUsername(){if(!id||!profile)return;setUsernameState("Đang kiểm tra…");try{const r=await api.managerCheckUsername(id,profile.username);setUsernameState(r.available?"Username khả dụng.":`Không khả dụng${r.reason?` (${r.reason})`:""}.`);}catch(e){setUsernameState(e instanceof Error?e.message:"Không thể kiểm tra username.");}}
  async function uploadPhoto(file?:File){if(!id||!file)return;setBusy(true);setError("");try{await api.managerUploadPhoto(id,file);setPhotoMissing(false);setPhotoVersion(v=>v+1);setNotice("Đã cập nhật ảnh đại diện.");}catch(e){setError(e instanceof Error?e.message:"Không thể cập nhật ảnh.");}finally{setBusy(false);}}
  return <section><div className="intro-row"><div><span className="eyebrow">PROFILE MANAGER</span><h2>Hồ sơ <em>Telegram.</em></h2><p>Đọc và cập nhật hồ sơ bằng chính session PostgreSQL của account.</p></div></div><AccountPicker accounts={accounts} value={id} onChange={setId}/>{error&&<div className="alert error">{error}</div>}{notice&&<div className="alert success">{notice}</div>}{profile&&<div className="profile-manager-grid"><div className="manager-card profile-photo-card"><div className="profile-photo-wrap">{!photoMissing?<img src={api.managerPhotoUrl(id,photoVersion)} alt="Avatar Telegram" onError={()=>setPhotoMissing(true)}/>:<div className="profile-photo-fallback">{(profile.first_name||profile.username||"T").slice(0,1).toUpperCase()}</div>}</div><strong>{[profile.first_name,profile.last_name].filter(Boolean).join(" ")||profile.username||"Telegram account"}</strong><small>{profile.username?`@${profile.username}`:"Chưa có username"}</small><label className="button ghost compact file-button">↥ Đổi ảnh<input type="file" accept="image/jpeg,image/png,image/webp" onChange={(e)=>uploadPhoto(e.target.files?.[0])}/></label></div><form className="manager-card form-grid" onSubmit={save}><label>Tên<input value={profile.first_name} onChange={(e)=>setProfile({...profile,first_name:e.target.value})}/></label><label>Họ<input value={profile.last_name} onChange={(e)=>setProfile({...profile,last_name:e.target.value})}/></label><label className="span-2">Username<div className="inline-manager-input"><input value={profile.username} onChange={(e)=>{setProfile({...profile,username:e.target.value});setUsernameState("");}} placeholder="username"/><button type="button" className="button ghost compact" onClick={checkUsername}>Kiểm tra</button></div>{usernameState&&<small>{usernameState}</small>}</label><label className="span-2">Bio<textarea value={profile.about} onChange={(e)=>setProfile({...profile,about:e.target.value})} maxLength={140}/></label><div className="span-2 manager-meta"><span>ID <b>{profile.id??"—"}</b></span><span>Premium <b>{profile.premium?"Có":"Không"}</b></span><span>Verified <b>{profile.verified?"Có":"Không"}</b></span></div><div className="span-2 manager-actions"><button className="button primary" disabled={busy}>{busy?"Đang lưu…":"Lưu hồ sơ"}</button></div></form></div>}</section>;
}

function SecurityPage() {
  const [accounts,setAccounts]=useState<TelegramAccount[]>([]);const[id,setId]=useState("");const[sessions,setSessions]=useState<ManagerSession[]>([]);const[messages,setMessages]=useState<ManagerSecurityMessage[]>([]);const[audit,setAudit]=useState<ManagerAudit[]>([]);const[busy,setBusy]=useState(false);const[error,setError]=useState("");const[notice,setNotice]=useState("");
  useEffect(()=>{api.accounts().then(({items})=>{setAccounts(items);const a=items.find(x=>x.is_default)||items[0];if(a)setId(a.id);});},[]);
  const load=()=>{if(!id)return;setBusy(true);setError("");Promise.all([api.managerSessions(id),api.managerSecurityMessages(id),api.managerAudit()]).then(([a,b,c])=>{setSessions(a.items);setMessages(b.items);setAudit(c.items.filter(x=>!x.account_id||x.account_id===id).slice(0,20));}).catch(e=>setError(e.message)).finally(()=>setBusy(false));};
  useEffect(load,[id]);
  async function terminate(hash:string){if(!id||!window.confirm("Chấm dứt phiên Telegram này?"))return;try{await api.terminateManagerSession(id,hash);setNotice("Đã chấm dứt phiên Telegram.");load();}catch(e){setError(e instanceof Error?e.message:"Không thể chấm dứt phiên.");}}
  async function terminateOthers(){if(!id||!window.confirm("Chấm dứt tất cả phiên Telegram khác, giữ nguyên phiên hiện tại?"))return;setBusy(true);setError("");try{const r=await api.terminateOtherManagerSessions(id);setNotice(`Đã chấm dứt ${r.terminated} phiên; ${r.failed} lỗi.`);load();}catch(e){setError(e instanceof Error?e.message:"Không thể chấm dứt các phiên khác.");}finally{setBusy(false);}}
  return <section><div className="intro-row"><div><span className="eyebrow">SECURITY CENTER</span><h2>Bảo mật <em>tài khoản.</em></h2><p>Phiên Telegram, thông báo 777000 mã hóa trong SQL và audit thao tác.</p></div><div className="manager-actions"><button className="button danger" onClick={terminateOthers} disabled={busy||!id}>Chấm dứt phiên khác</button><button className="button ghost" onClick={load} disabled={busy}>↻ Làm mới</button></div></div><AccountPicker accounts={accounts} value={id} onChange={setId}/>{error&&<div className="alert error">{error}</div>}{notice&&<div className="alert success">{notice}</div>}<div className="manager-two-col"><div className="manager-card"><div className="manager-title"><h3>Phiên đăng nhập</h3><span>{sessions.length}</span></div>{sessions.map(x=><div className="security-row" key={x.hash}><div><strong>{x.device_model||x.app_name||"Thiết bị Telegram"}{x.current&&<em>Phiên hiện tại</em>}</strong><small>{[x.platform,x.system_version,x.app_name,x.app_version].filter(Boolean).join(" · ")}</small><small>{[x.ip,x.country,x.region].filter(Boolean).join(" · ")}</small></div>{!x.current&&<button className="button danger compact" onClick={()=>terminate(x.hash)}>Chấm dứt</button>}</div>)}{!sessions.length&&!busy&&<div className="manager-empty">Không có dữ liệu phiên.</div>}</div><div className="manager-card"><div className="manager-title"><h3>Telegram 777000</h3><span>{messages.length}</span></div>{messages.map(x=><div className="security-message" key={String(x.id)}><small>{x.date?formatDate(x.date):"—"}</small><p>{x.text}</p></div>)}{!messages.length&&!busy&&<div className="manager-empty">Chưa có thông báo.</div>}</div></div><div className="manager-card audit-card"><div className="manager-title"><h3>Audit SQL gần đây</h3><span>{audit.length}</span></div>{audit.map(x=><div className="audit-row" key={x.id}><code>{x.action}</code><span>{x.status}</span><small>{formatDate(x.created_at)}</small></div>)}{!audit.length&&<div className="manager-empty">Chưa có thao tác quản trị được ghi nhận.</div>}</div></section>;
}

function GroupsPage() {
  const[accounts,setAccounts]=useState<TelegramAccount[]>([]);const[id,setId]=useState("");const[groups,setGroups]=useState<ManagerDialog[]>([]);const[target,setTarget]=useState("");const[busy,setBusy]=useState(false);const[error,setError]=useState("");
  useEffect(()=>{api.accounts().then(({items})=>{setAccounts(items);const a=items.find(x=>x.is_default)||items[0];if(a)setId(a.id);});},[]);
  const load=()=>{if(!id)return;setBusy(true);api.managerGroups(id).then(r=>setGroups(r.items)).catch(e=>setError(e.message)).finally(()=>setBusy(false));};useEffect(load,[id]);
  async function join(e:FormEvent){e.preventDefault();if(!id||!target.trim())return;setBusy(true);setError("");try{await api.managerJoin(id,target.trim());setTarget("");load();}catch(e){setError(e instanceof Error?e.message:"Không thể tham gia.");setBusy(false);}}
  async function leave(g:ManagerDialog){if(!id||!window.confirm(`Rời ${g.title}?`))return;setError("");try{await api.managerLeave(id,g.username?`@${g.username}`:g.id);load();}catch(e){setError(e instanceof Error?e.message:"Không thể rời nhóm.");}}
  return <section><div className="intro-row"><div><span className="eyebrow">GROUPS & CHANNELS</span><h2>Nhóm & <em>Kênh.</em></h2><p>Quản lý membership bằng account đang chọn.</p></div></div><div className="manager-toolbar"><AccountPicker accounts={accounts} value={id} onChange={setId}/><form onSubmit={join}><input value={target} onChange={(e)=>setTarget(e.target.value)} placeholder="@username hoặc t.me/+invite"/><button className="button primary" disabled={busy||!target.trim()}>＋ Tham gia</button></form></div>{error&&<div className="alert error">{error}</div>}<div className="manager-card"><div className="manager-title"><h3>Danh sách hiện tại</h3><span>{groups.length}</span></div><div className="group-grid">{groups.map(g=><div className="group-card" key={g.id}><div className="group-icon">{g.is_channel&&!g.is_group?"C":"G"}</div><div><strong>{g.title||g.id}</strong><small>{g.username?`@${g.username}`:g.id} · {g.unread_count} chưa đọc</small></div><button className="button ghost compact" onClick={()=>leave(g)}>Rời</button></div>)}</div>{!groups.length&&!busy&&<div className="manager-empty">Không có nhóm/kênh hoặc chưa tải được dữ liệu.</div>}</div></section>;
}

function MessagesPage() {
  const [accounts,setAccounts]=useState<TelegramAccount[]>([]);const[id,setId]=useState("");const[chatInput,setChatInput]=useState("");const[peer,setPeer]=useState<ManagerPeer|null>(null);const[chat,setChat]=useState<ManagerChatMessage[]>([]);const[text,setText]=useState("");const[target,setTarget]=useState("");const[targetResult,setTargetResult]=useState<ManagerTargetCheck|null>(null);const[postLink,setPostLink]=useState("");const[emoji,setEmoji]=useState("👍");const[reactions,setReactions]=useState<string[]>([]);const[busy,setBusy]=useState("");const[error,setError]=useState("");const[notice,setNotice]=useState("");
  useEffect(()=>{api.accounts().then(({items})=>{setAccounts(items);const a=items.find(x=>x.is_default)||items[0];if(a)setId(a.id);});},[]);
  useEffect(()=>{setPeer(null);setChat([]);setError("");if(id)api.managerReactions(id).then(r=>{setReactions(r.items);if(r.items.length&&!r.items.includes(emoji))setEmoji(r.items[0]);}).catch(()=>setReactions([]));},[id]);
  async function openChat(e:FormEvent){e.preventDefault();if(!id||!chatInput.trim())return;setBusy("open");setError("");try{const r=await api.managerOpenChat(id,chatInput.trim());setPeer(r.peer);setChat(r.messages);setNotice(r.started?"Đã mở bot/referral và tải hội thoại.":"Đã mở hội thoại.");}catch(e){setError(e instanceof Error?e.message:"Không thể mở hội thoại.");}finally{setBusy("");}}
  async function refreshChat(){if(!id||!peer)return;setBusy("history");try{const r=await api.managerChatHistory(id,peer.ref||peer.id);setChat(r.messages);}catch(e){setError(e instanceof Error?e.message:"Không thể tải lịch sử.");}finally{setBusy("");}}
  async function sendChat(e:FormEvent){e.preventDefault();if(!id||!peer||!text.trim())return;setBusy("send");setError("");try{const r=await api.managerChatSend(id,peer.ref||peer.id,text.trim());setChat(v=>[...v,r.message]);setText("");}catch(e){setError(e instanceof Error?e.message:"Không thể gửi tin nhắn.");}finally{setBusy("");}}
  async function checkTarget(e:FormEvent){e.preventDefault();if(!target.trim())return;setBusy("target");setError("");try{setTargetResult(await api.managerTargetCheck(target.trim(),[]));}catch(e){setError(e instanceof Error?e.message:"Không thể kiểm tra target.");}finally{setBusy("");}}
  async function react(){if(!id||!postLink.trim()||!emoji)return;setBusy("react");setError("");try{await api.managerReact(id,postLink.trim(),emoji);setNotice(`Đã reaction ${emoji}.`);}catch(e){setError(e instanceof Error?e.message:"Không thể reaction.");}finally{setBusy("");}}
  async function view(){if(!id||!postLink.trim())return;setBusy("view");setError("");try{const r=await api.managerView(id,postLink.trim());setNotice(`Đã ghi nhận view${r.views!=null?` · tổng hiện tại ${r.views}`:""}.`);}catch(e){setError(e instanceof Error?e.message:"Không thể ghi nhận view.");}finally{setBusy("");}}
  return <section><div className="intro-row"><div><span className="eyebrow">MESSAGING & TARGET TOOLS</span><h2>Tin nhắn <em>Telegram.</em></h2><p>Mở hội thoại, đọc lịch sử, kiểm tra target và thao tác bài viết bằng SQL session hiện tại.</p></div></div><AccountPicker accounts={accounts} value={id} onChange={setId}/>{error&&<div className="alert error">{error}</div>}{notice&&<div className="alert success">{notice}</div>}<div className="manager-two-col messages-layout"><div className="manager-card chat-panel"><form className="chat-open" onSubmit={openChat}><input value={chatInput} onChange={e=>setChatInput(e.target.value)} placeholder="@username, t.me/user hoặc bot referral link"/><button className="button primary" disabled={busy==="open"||!id||!chatInput.trim()}>{busy==="open"?"Đang mở…":"Mở chat"}</button></form>{peer?<><div className="chat-head"><div><strong>{peer.title}</strong><small>{peer.username?`@${peer.username}`:peer.id} · {peer.kind||"chat"}</small></div><button className="button ghost compact" onClick={refreshChat} disabled={busy==="history"}>↻</button></div><div className="chat-history">{chat.map(m=><div className={`chat-bubble ${m.out?"out":"in"}`} key={`${m.id}-${m.date||""}`}><p>{m.text||m.media||"[message]"}</p><small>{m.date?formatDate(m.date):""}</small></div>)}{!chat.length&&<div className="manager-empty">Chưa có lịch sử tin nhắn.</div>}</div><form className="chat-compose" onSubmit={sendChat}><textarea value={text} onChange={e=>setText(e.target.value)} placeholder="Nhập tin nhắn…" maxLength={4096}/><button className="button primary" disabled={busy==="send"||!text.trim()}>Gửi</button></form></>:<div className="manager-empty tall">Mở một username/link để hiển thị hội thoại.</div>}</div><div className="manager-stack"><form className="manager-card" onSubmit={checkTarget}><h3>Target check</h3><p className="muted">Kiểm tra target trên toàn bộ account đang sẵn sàng; account có job sẽ được bỏ qua.</p><input value={target} onChange={e=>setTarget(e.target.value)} placeholder="@user, @bot, @channel hoặc t.me/..."/><button className="button ghost wide" disabled={busy==="target"||!target.trim()}>{busy==="target"?"Đang kiểm tra…":"Kiểm tra tất cả account"}</button>{targetResult&&<div className="target-summary"><span>Present <b>{targetResult.present.length}</b></span><span>Absent <b>{targetResult.absent.length}</b></span><span>Skipped <b>{targetResult.skipped.length}</b></span><span>Failed <b>{targetResult.failed.length}</b></span>{targetResult.results.map(r=><div className={`target-row ${r.status}`} key={r.id}><strong>{r.label||r.id}</strong><small>{r.detail}</small></div>)}</div>}</form><div className="manager-card"><h3>Reaction / View bài viết</h3><p className="muted">Link dạng t.me/channel/123 hoặc t.me/c/id/123.</p><input value={postLink} onChange={e=>setPostLink(e.target.value)} placeholder="https://t.me/channel/123"/><div className="post-tool-row"><select value={emoji} onChange={e=>setEmoji(e.target.value)}>{(reactions.length?reactions:["👍","❤️","🔥","👏"]).map(x=><option key={x} value={x}>{x}</option>)}</select><button className="button ghost" onClick={react} disabled={busy==="react"||!postLink.trim()}>Reaction</button><button className="button primary" onClick={view} disabled={busy==="view"||!postLink.trim()}>+ View</button></div></div></div></div></section>;
}

function BulkPage() {
  const [accounts,setAccounts]=useState<TelegramAccount[]>([]);
  const [selected,setSelected]=useState<Record<string,boolean>>({});
  const [target,setTarget]=useState("");
  const [busy,setBusy]=useState("");
  const [results,setResults]=useState<string[]>([]);
  useEffect(()=>{api.accounts().then(({items})=>setAccounts(items));},[]);
  const ids=accounts.filter(a=>selected[a.id]&&a.state==="AUTHORIZED").map(a=>a.id);
  function show(result:{results:{account_id:string;status:string;detail?:string}[]}){
    setResults(result.results.map(r=>`${accounts.find(a=>a.id===r.account_id)?.label||r.account_id}: ${r.status==="ok"?"OK":`Lỗi${r.detail?` · ${r.detail}`:""}`}`));
  }
  async function run(kind:"join"|"leave"|"terminate"){
    if(!ids.length)return;
    if((kind==="join"||kind==="leave")&&!target.trim())return;
    if(kind==="leave"&&!window.confirm(`Rời ${target.trim()} bằng ${ids.length} tài khoản đã chọn?`))return;
    if(kind==="terminate"&&!window.confirm(`Chấm dứt TẤT CẢ phiên Telegram khác trên ${ids.length} tài khoản? Phiên SQL hiện tại được giữ lại.`))return;
    setBusy(kind);setResults([]);
    try{
      const r=kind==="join"?await api.managerBulkJoin(ids,target.trim()):kind==="leave"?await api.managerBulkLeave(ids,target.trim()):await api.managerBulkTerminateOthers(ids);
      show(r);
    }catch(e){setResults([e instanceof Error?e.message:"Không thể thực hiện thao tác hàng loạt."]);}
    finally{setBusy("");}
  }
  return <section><div className="intro-row"><div><span className="eyebrow">BULK OPERATIONS</span><h2>Hàng <em>loạt.</em></h2><p>Điều phối các thao tác quản trị trên nhiều account. Account đang có job sẽ bị backend chặn riêng.</p></div><Link className="button primary" to="/checker">Mở Multi Job Checker →</Link></div>
    <div className="manager-two-col"><div className="manager-card"><div className="manager-title"><h3>Chọn tài khoản</h3><span>{ids.length}</span></div>{accounts.map(a=><label className="bulk-account" key={a.id}><input type="checkbox" checked={!!selected[a.id]} disabled={a.state!=="AUTHORIZED"} onChange={(e)=>setSelected({...selected,[a.id]:e.target.checked})}/><span><strong>{a.label}</strong><small>{a.phone} · {a.state}</small></span></label>)}</div>
      <div className="manager-card"><h3>Nhóm & Kênh</h3><p className="muted">Target có thể là @username, ID hoặc invite link phù hợp.</p><input value={target} onChange={(e)=>setTarget(e.target.value)} placeholder="@username hoặc invite link"/><div className="bulk-action-row"><button className="button primary" disabled={!!busy||!ids.length||!target.trim()} onClick={()=>run("join")}>{busy==="join"?"Đang tham gia…":"＋ Tham gia"}</button><button className="button ghost danger" disabled={!!busy||!ids.length||!target.trim()} onClick={()=>run("leave")}>{busy==="leave"?"Đang rời…":"Rời target"}</button></div><hr/><h3>Bảo mật phiên</h3><p className="muted">Chấm dứt mọi Telegram authorization khác, giữ phiên SQL đang dùng bởi ứng dụng.</p><button className="button danger wide" disabled={!!busy||!ids.length} onClick={()=>run("terminate")}>{busy==="terminate"?"Đang xử lý…":"Chấm dứt các session khác"}</button>{results.map((r,i)=><div className="bulk-result" key={i}>{r}</div>)}</div></div>
  </section>;
}
function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [notice, setNotice] = useState("");
  const refresh = () => api.jobs().then((data) => setJobs(data.items)).catch(() => {}).finally(() => setLoading(false));
  useEffect(() => {
    refresh();
    const events = new EventSource("/api/events");
    events.onmessage = () => refresh();
    const timer = window.setInterval(refresh, 30000);
    return () => { events.close(); window.clearInterval(timer); };
  }, []);
  const stats = useMemo(() => ({
    total: jobs.length,
    active: jobs.filter((j) => j.active || ["RUNNING", "RATE_LIMITED"].includes(j.status)).length,
    found: jobs.reduce((sum, j) => sum + j.found, 0),
    errors: jobs.reduce((sum, j) => sum + j.errors, 0),
  }), [jobs]);
  async function action(job: Job, name: "start" | "pause" | "resume" | "cancel" | "delete") {
    try {
      if (name === "delete") await api.deleteJob(job.job_id);
      else await api.action(job.job_id, name);
      setNotice(name === "delete" ? "Đã xóa job." : "Đã cập nhật trạng thái job.");
      refresh();
      window.setTimeout(() => setNotice(""), 2600);
    } catch (err) { setNotice(err instanceof Error ? err.message : "Không thể cập nhật job."); }
  }
  return <section>
    <div className="intro-row"><div><span className="eyebrow">OPERATIONS CENTER</span><h2>Chào bạn, <em>admin.</em></h2><p>Theo dõi toàn bộ hoạt động kiểm tra từ một nơi.</p></div><button className="button primary" onClick={() => setShowCreate(true)}>＋ Tạo job kiểm tra</button></div>
    {notice && <div className="toast">{notice}</div>}
    <div className="stats-grid"><Stat label="Tổng số job" value={stats.total} note="Trong workspace" tone="blue" /><Stat label="Đang chạy" value={stats.active} note="Cập nhật tự động" tone="green" /><Stat label="Tài khoản tìm thấy" value={stats.found} note="Từ tất cả job" tone="violet" /><Stat label="Lỗi cần xử lý" value={stats.errors} note="Kiểm tra lại" tone="orange" /></div>
    <div className="section-heading"><div><h3>Job gần đây</h3><p>{jobs.length ? `${jobs.length} job trong workspace` : "Bắt đầu bằng một danh sách số điện thoại."}</p></div><Link to="/import" className="text-link">Nhập từ file →</Link></div>
    {loading ? <div className="empty-state"><span className="spinner" /> Đang tải dữ liệu…</div> : jobs.length === 0 ? <Empty onCreate={() => setShowCreate(true)} /> : <div className="job-list">{jobs.map((job) => <JobRow key={job.job_id} job={job} onAction={action} />)}</div>}
    {showCreate && <CreateModal onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); refresh(); }} />}
  </section>;
}

function Stat({ label, value, note, tone }: { label: string; value: number; note: string; tone: string }) {
  return <div className="stat-card"><div className={`stat-icon ${tone}`}>{tone === "blue" ? "↗" : tone === "green" ? "◉" : tone === "violet" ? "♢" : "!"}</div><span className="stat-label">{label}</span><strong>{value.toLocaleString("vi-VN")}</strong><small>{note}</small></div>;
}

function Empty({ onCreate }: { onCreate: () => void }) {
  return <div className="empty-state large"><div className="empty-icon">＋</div><h3>Chưa có job nào</h3><p>Tạo job đầu tiên để bắt đầu kiểm tra số điện thoại Telegram.</p><button className="button primary" onClick={onCreate}>Tạo job đầu tiên</button></div>;
}

function JobRow({ job, onAction }: { job: Job; onAction: (job: Job, action: "start" | "pause" | "resume" | "cancel" | "delete") => void }) {
  const percent = job.total ? Math.round((job.processed / job.total) * 100) : 0;
  const canStart = ["CREATED", "FAILED"].includes(job.status) && !job.active;
  const canResume = job.status === "PAUSED" && !job.active;
  return <div className="job-row"><div className="job-main"><div className="job-avatar">#</div><div><Link className="job-name" to={`/jobs/${job.job_id}`}>{job.name || job.job_id}</Link><small>{job.job_id} · {formatDate(job.created_at)}</small></div></div><div className="job-progress"><div className="progress-head"><span>{job.processed.toLocaleString()} / {job.total.toLocaleString()} số</span><strong>{percent}%</strong></div><div className="progress"><i style={{ width: `${percent}%` }} /></div></div><Status status={job.status} /><div className="row-actions">{canStart && <button className="icon-button" title="Bắt đầu" onClick={() => onAction(job, "start")}>▶</button>}{canResume && <button className="icon-button" title="Tiếp tục" onClick={() => onAction(job, "resume")}>▶</button>}{job.active && <button className="icon-button" title="Tạm dừng" onClick={() => onAction(job, "pause")}>Ⅱ</button>} {!job.active && !["COMPLETED", "CANCELLED"].includes(job.status) && <button className="icon-button" title="Xóa" onClick={() => onAction(job, "delete")}>⌫</button>}<Link className="icon-button" to={`/jobs/${job.job_id}`}>→</Link></div></div>;
}

function Status({ status }: { status: string }) {
  const labels: Record<string, string> = { CREATED: "Mới tạo", RUNNING: "Đang chạy", RATE_LIMITED: "Giới hạn tốc độ", PAUSED: "Đã tạm dừng", COMPLETED: "Hoàn tất", FAILED: "Lỗi", CANCELLED: "Đã hủy" };
  return <span className={`status ${status.toLowerCase()}`}><i />{labels[status] || status}</span>;
}

function CreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [mode, setMode] = useState<"SINGLE" | "MULTI">("SINGLE");
  const [numbers, setNumbers] = useState("");
  const [name, setName] = useState("");
  const [autoStart, setAutoStart] = useState(true);
  const [accounts, setAccounts] = useState<TelegramAccount[]>([]);
  const [accountId, setAccountId] = useState("");
  const [datasets, setDatasets] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { api.accounts().then(({ items }) => { setAccounts(items); const d = items.find((a) => a.is_default) || items[0]; if (d) setAccountId(d.id); }).catch(() => {}); }, []);
  const selectedAccounts = accounts.filter((a) => Object.prototype.hasOwnProperty.call(datasets, a.id));
  const multiReady = selectedAccounts.length >= 2 && selectedAccounts.every((a) => !autoStart || a.state === "AUTHORIZED") && selectedAccounts.every((a) => datasets[a.id]?.trim());
  function toggleAccount(id: string) { setDatasets((current) => { const next = { ...current }; if (Object.prototype.hasOwnProperty.call(next, id)) delete next[id]; else next[id] = ""; return next; }); }
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      if (mode === "SINGLE") {
        await api.createJob({ mode, phone_numbers: numbers, job_name: name || undefined, telegram_account_id: accountId || undefined, auto_start: autoStart });
      } else {
        if (selectedAccounts.length < 2) throw new Error("Hãy chọn ít nhất 2 tài khoản Telegram.");
        const account_batches = selectedAccounts.map((a) => ({ telegram_account_id: a.id, phone_numbers: datasets[a.id] || "" }));
        await api.createJob({ mode, job_name: name || undefined, account_batches, auto_start: autoStart });
      }
      onCreated();
    } catch (err) { setError(err instanceof Error ? err.message : "Không thể tạo job."); }
    finally { setBusy(false); }
  }
  return <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}><form className="modal multi-job-modal" onSubmit={submit}>
    <div className="modal-head"><div><span className="eyebrow">NEW JOB</span><h2>Tạo job kiểm tra</h2></div><button type="button" className="close" onClick={onClose}>×</button></div>
    <label>Tên job <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Ví dụ: Danh sách tháng 9" /></label>
    <div className="mode-switch"><button type="button" className={mode === "SINGLE" ? "active" : ""} onClick={() => setMode("SINGLE")}>1 tài khoản</button><button type="button" className={mode === "MULTI" ? "active" : ""} onClick={() => setMode("MULTI")}>Nhiều tài khoản song song</button></div>
    {mode === "SINGLE" ? <>
      <label>Tài khoản Telegram<select value={accountId} onChange={(e) => setAccountId(e.target.value)}><option value="">Chưa chọn tài khoản</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.label} · {a.phone}{a.is_default ? " · Mặc định" : ""}</option>)}</select><small>Job sẽ luôn nhớ tài khoản này kể cả khi bạn đổi account mặc định.</small></label>
      <label>Số điện thoại <textarea required value={numbers} onChange={(e) => setNumbers(e.target.value)} placeholder="+84912345678, +84987654321" /><small>Ngăn cách bằng dấu phẩy hoặc xuống dòng.</small></label>
    </> : <div className="multi-datasets">
      <div className="multi-help">Chọn tối thiểu 2 tài khoản. Mỗi tài khoản có danh sách số riêng và chạy bằng worker độc lập.</div>
      {accounts.map((a) => { const selected = Object.prototype.hasOwnProperty.call(datasets, a.id); return <div className={`dataset-card ${selected ? "selected" : ""}`} key={a.id}>
        <label className="dataset-head"><input type="checkbox" checked={selected} onChange={() => toggleAccount(a.id)} /><span><strong>{a.label}</strong><small>{a.phone} · {a.state === "AUTHORIZED" ? "Sẵn sàng" : a.state === "IN_USE" ? "Đang được job khác sử dụng" : "Chưa đăng nhập"}</small></span></label>
        {selected && <textarea required value={datasets[a.id]} onChange={(e) => setDatasets((current) => ({ ...current, [a.id]: e.target.value }))} placeholder={`Danh sách số riêng cho ${a.label}\n+84912345678\n+84987654321`} />}
      </div>; })}
      {!accounts.length && <div className="alert error">Chưa có tài khoản Telegram. Hãy thêm và đăng nhập tài khoản trước.</div>}
    </div>}
    <label className="check-label"><input type="checkbox" checked={autoStart} onChange={(e) => setAutoStart(e.target.checked)} /> Bắt đầu ngay sau khi tạo</label>
    {mode === "MULTI" && autoStart && selectedAccounts.some((a) => a.state !== "AUTHORIZED") && <div className="alert error">Muốn bắt đầu ngay, tất cả tài khoản đã chọn phải ở trạng thái Sẵn sàng.</div>}
    {error && <div className="alert error">{error}</div>}
    <div className="modal-actions"><button type="button" className="button ghost" onClick={onClose}>Hủy</button><button className="button primary" disabled={busy || (mode === "SINGLE" ? (autoStart && !accountId) : !multiReady)}>{busy ? "Đang tạo…" : mode === "MULTI" ? "Tạo job song song" : "Tạo job"}</button></div>
  </form></div>;
}

function ImportPage() {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [accounts, setAccounts] = useState<TelegramAccount[]>([]);
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { api.accounts().then(({ items }) => { setAccounts(items); const d = items.find((a) => a.is_default) || items[0]; if (d) setAccountIds([d.id]); }).catch(() => {}); }, []);
  function toggleAccount(id: string) { setAccountIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]); }
  async function submit(e: FormEvent) {
    e.preventDefault(); if (!file) return setError("Chọn file CSV, TXT hoặc XLSX trước.");
    if (!accountIds.length) return setError("Chọn ít nhất một tài khoản Telegram.");
    setBusy(true); setError("");
    try { const job = await api.importJob(file, name, accountIds[0], accountIds); navigate(`/jobs/${job.job_id}`); }
    catch (err) { setError(err instanceof Error ? err.message : "Không thể import file."); }
    finally { setBusy(false); }
  }
  return <section className="narrow"><div className="intro-row"><div><span className="eyebrow">DATA INGESTION</span><h2>Nhập <em>danh sách.</em></h2><p>Tải CSV, TXT hoặc XLSX. Chọn nhiều tài khoản để hệ thống tự loại trùng và chia đều target.</p></div></div>
    <form className="upload-card" onSubmit={submit}><div className={`dropzone ${file ? "selected" : ""}`}><input id="file" type="file" accept=".csv,.txt,.xlsx,text/csv,text/plain,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(e) => setFile(e.target.files?.[0] || null)} /><label htmlFor="file"><span className="upload-icon">↥</span><strong>{file ? file.name : "Kéo file vào đây hoặc chọn từ máy"}</strong><small>{file ? `${(file.size / 1024).toFixed(1)} KB · Sẵn sàng import` : "CSV, TXT, XLSX · cột phone/number hoặc cột đầu tiên"}</small></label></div>
      <label>Tên job <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Ví dụ: Import khách hàng tháng 9" /></label>
      <div className="field-group"><span className="field-label">Tài khoản Telegram</span><div className="account-picker">{accounts.map((a) => <label className="check-label" key={a.id}><input type="checkbox" checked={accountIds.includes(a.id)} onChange={() => toggleAccount(a.id)} /> {a.label} · {a.phone}{a.is_default ? " · Mặc định" : ""}</label>)}</div></div>
      {accountIds.length > 1 && <div className="info-panel"><strong>Chia đều tự động</strong><span>{accountIds.length} tài khoản được chọn. Target được chuẩn hóa E.164, loại trùng toàn cục rồi chia round-robin; một target chỉ thuộc một nhánh.</span></div>}
      {error && <div className="alert error">{error}</div>}<div className="form-footer"><span className="muted">{accountIds.length > 1 ? `Sẽ tạo job nhiều nhánh cho ${accountIds.length} tài khoản.` : "Job sẽ gắn cố định với account đã chọn."}</span><button className="button primary" disabled={busy || !file || !accountIds.length}>{busy ? "Đang import…" : "Tạo job từ file →"}</button></div>
    </form><div className="info-panel"><strong>Định dạng được hỗ trợ</strong><span>CSV/XLSX nhận cột <code>phone</code>, <code>number</code>, <code>phone_number</code> hoặc <code>số điện thoại</code>. TXT dùng mỗi dòng một target.</span></div></section>;
}

function JobDetail() {
  const { jobId = "" } = useParams();
  const [job, setJob] = useState<Job | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [page, setPage] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  const pageSize = 100;
  const load = () => Promise.all([api.job(jobId), api.items(jobId, new URLSearchParams({ page: String(page), page_size: String(pageSize), ...(query ? { q: query } : {}), ...(status ? { status } : {}) }).toString())]).then(([j, data]) => { setJob(j); setItems(data.items); setTotalItems(data.total); }).catch((err) => setNotice(err instanceof Error ? err.message : "Không thể tải job.")).finally(() => setLoading(false));
  useEffect(() => {
    load();
    const events = new EventSource("/api/events");
    events.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (!payload.job_id || payload.job_id === jobId) load();
      } catch { /* keep fallback polling */ }
    };
    const timer = window.setInterval(load, 30000);
    return () => { events.close(); window.clearInterval(timer); };
  }, [jobId, query, status, page]);
  async function action(name: "start" | "pause" | "resume" | "cancel") { try { await api.action(jobId, name); load(); } catch (err) { setNotice(err instanceof Error ? err.message : "Không thể cập nhật job."); } }
  if (loading && !job) return <div className="empty-state"><span className="spinner" /> Đang tải job…</div>;
  if (!job) return <div className="empty-state"><h3>Không tìm thấy job</h3><Link to="/" className="button primary">Về tổng quan</Link></div>;
  const percent = job.total ? Math.round((job.processed / job.total) * 100) : 0;
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  return <section><div className="detail-back"><Link to="/">← Tất cả job</Link></div><div className="detail-heading"><div><span className="eyebrow">JOB DETAIL</span><h2>{job.name || job.job_id}</h2><p>{job.job_id} · Tạo lúc {formatDate(job.created_at)}</p></div><div className="detail-actions">{job.active ? <button className="button ghost" onClick={() => action("pause")}>Ⅱ Tạm dừng</button> : !["COMPLETED", "CANCELLED"].includes(job.status) && <button className="button primary" onClick={() => action(job.status === "PAUSED" ? "resume" : "start")}>▶ {job.status === "PAUSED" ? "Tiếp tục" : "Bắt đầu"}</button>}{!["COMPLETED", "CANCELLED"].includes(job.status) && <button className="button ghost danger" onClick={() => action("cancel")}>× Hủy job</button>}<a className="button ghost" href={`/api/jobs/${job.job_id}/export?format=csv`}>↓ CSV</a></div></div>{notice && <div className="alert error">{notice}</div>}<div className="detail-stats"><div><span>Tiến độ</span><strong>{percent}%</strong><div className="progress"><i style={{ width: `${percent}%` }} /></div></div><Metric label="Đã xử lý" value={job.processed} /><Metric label="Tìm thấy" value={job.found} /><Metric label="Lỗi" value={job.errors} /><Metric label="Còn lại" value={job.pending} /></div>{job.job_mode === "MULTI" && job.branches && job.branches.length > 0 && <div className="branch-panel"><div className="section-heading"><div><h3>Nhánh tài khoản</h3><p>{job.branches.length} tài khoản xử lý song song với dữ liệu riêng.</p></div></div><div className="branch-grid">{job.branches.map((branch) => <div className="branch-card" key={branch.job_id}><div className="branch-head"><div><strong>{branch.telegram_account_phone || "Tài khoản Telegram"}</strong><small>{branch.name}</small></div><Status status={branch.status} /></div><div className="branch-metrics"><span>Đã xử lý <b>{branch.processed}/{branch.total}</b></span><span>Tìm thấy <b>{branch.found}</b></span><span>Còn lại <b>{branch.pending}</b></span></div><div className="progress"><i style={{ width: `${branch.total ? Math.round((branch.processed / branch.total) * 100) : 0}%` }} /></div></div>)}</div></div>}{job.account_blocked_until && <div className="info-panel"><strong>FloodWait</strong><span>Tài khoản đang tạm dừng theo yêu cầu Telegram đến {formatDate(job.account_blocked_until)}. Hệ thống sẽ không gửi yêu cầu mới trước thời điểm này.</span></div>}{job.next_retry_at && <div className="info-panel"><strong>Retry kế tiếp</strong><span>Item chưa hoàn tất sớm nhất sẽ được thử lại vào {formatDate(job.next_retry_at)}.</span></div>}{job.last_error_message && <div className="alert error"><strong>{job.last_error_type || "JOB_ERROR"}</strong> · {job.last_error_message}</div>}<div className="section-heading"><div><h3>Kết quả kiểm tra</h3><p>{totalItems.toLocaleString("vi-VN")} kết quả · trang {page}/{totalPages} · số điện thoại được che một phần</p></div><div className="filters"><input placeholder="Tìm số điện thoại…" value={query} onChange={(e) => { setQuery(e.target.value); setPage(1); }} /><select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}><option value="">Tất cả trạng thái</option><option value="PENDING">Chờ xử lý</option><option value="PROCESSING">Đang xử lý</option><option value="IN_FLIGHT_UNKNOWN">Chờ phục hồi</option><option value="FOUND">Tìm thấy</option><option value="NOT_DISCOVERABLE">Không tìm thấy</option><option value="RETRY_REQUIRED">Chờ thử lại</option><option value="TEMPORARY_ERROR">Lỗi tạm thời</option><option value="RATE_LIMITED">FloodWait</option><option value="PERMANENT_ERROR">Lỗi vĩnh viễn</option></select></div></div><div className="table-wrap"><table><thead><tr><th>Số điện thoại</th><th>Trạng thái</th><th>Tên tài khoản</th><th>Username</th><th>Lần thử</th></tr></thead><tbody>{items.length ? items.map((item) => <tr key={item.id}><td><strong>{item.masked_phone}</strong></td><td><Status status={item.status} /></td><td>{[item.first_name, item.last_name].filter(Boolean).join(" ") || "—"}</td><td className="mono">{item.username ? `@${item.username}` : "—"}</td><td>{item.attempt_count}</td></tr>) : <tr><td colSpan={5} className="table-empty">Chưa có kết quả phù hợp.</td></tr>}</tbody></table></div><div className="pagination"><button className="button ghost compact" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>← Trang trước</button><span>Trang {page} / {totalPages}</span><button className="button ghost compact" disabled={page >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>Trang sau →</button></div></section>;
}

function Metric({ label, value }: { label: string; value: number }) { return <div className="metric"><span>{label}</span><strong>{value.toLocaleString("vi-VN")}</strong></div>; }

function QrLoginModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [qr, setQr] = useState<QrLoginState | null>(null);
  const [image, setImage] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api.startQrLogin().then((v) => { if (active) setQr(v); }).catch((e) => setError(e instanceof Error ? e.message : "Không thể tạo QR."));
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!qr?.url) { setImage(""); return; }
    QRCode.toDataURL(qr.url, { width: 300, margin: 2, errorCorrectionLevel: "M" }).then(setImage).catch(() => setError("Không thể render mã QR."));
  }, [qr?.url]);
  useEffect(() => {
    if (!qr?.qr_id || !["WAIT_SCAN", "WAIT_2FA"].includes(qr.state)) return;
    const timer = window.setInterval(() => {
      api.qrLoginStatus(qr.qr_id).then((v) => { setQr((current: QrLoginState | null) => ({ ...(current || v), ...v })); if (v.state === "COMPLETED") onDone(); }).catch(() => {});
    }, 1500);
    return () => window.clearInterval(timer);
  }, [qr?.qr_id, qr?.state, onDone]);
  async function close() {
    if (qr?.qr_id && qr.state !== "COMPLETED") { try { await api.cancelQrLogin(qr.qr_id); } catch {} }
    onClose();
  }
  async function refresh() {
    if (!qr?.qr_id) return; setBusy(true); setError("");
    try { setQr(await api.refreshQrLogin(qr.qr_id)); } catch (e) { setError(e instanceof Error ? e.message : "Không thể làm mới QR."); } finally { setBusy(false); }
  }
  async function submit2fa(e: FormEvent) {
    e.preventDefault(); if (!qr?.qr_id || !password) return; setBusy(true); setError("");
    try { await api.submitQr2fa(qr.qr_id, password); setQr({ ...qr, state: "COMPLETED" }); onDone(); }
    catch (e) { setError(e instanceof Error ? e.message : "Mật khẩu 2FA không hợp lệ."); } finally { setBusy(false); }
  }
  return <div className="modal-backdrop"><div className="modal qr-modal"><div className="modal-head"><div><span className="eyebrow">QR LOGIN</span><h2>Đăng nhập bằng QR Telegram</h2></div><button className="close" onClick={close}>×</button></div>
    <p className="muted">Mở Telegram trên điện thoại → Cài đặt → Thiết bị → Liên kết thiết bị Desktop, sau đó quét mã bên dưới. QR token chỉ được render cục bộ trong trình duyệt.</p>
    {error && <div className="alert error">{error}</div>}
    {!qr && !error && <div className="qr-loading"><span className="spinner" /> Đang tạo QR…</div>}
    {image && qr?.state === "WAIT_SCAN" && <div className="qr-image-wrap"><img src={image} alt="Telegram QR login" /><strong>Đang chờ quét…</strong><small>{qr.expires_at ? `Hết hạn: ${formatDate(qr.expires_at)}` : "QR có thời hạn ngắn"}</small></div>}
    {qr?.state === "WAIT_2FA" && <form onSubmit={submit2fa}><div className="alert success">QR đã được quét. Telegram yêu cầu mật khẩu 2FA.</div><label>Mật khẩu 2FA<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus /></label><button className="button primary wide" disabled={busy || !password}>{busy ? "Đang xác thực…" : "Xác nhận 2FA"}</button></form>}
    {qr?.state === "EXPIRED" && <div className="qr-expired"><strong>QR đã hết hạn</strong><button className="button primary" disabled={busy} onClick={refresh}>Tạo QR mới</button></div>}
    {qr?.state === "FAILED" && <div className="alert error">{qr.error || "Đăng nhập QR thất bại."}</div>}
    {qr?.state === "COMPLETED" && <div className="alert success">Đã đăng nhập. Session đã được mã hóa và lưu PostgreSQL.</div>}
  </div></div>;
}

function AccountPage() {
  const [accounts, setAccounts] = useState<TelegramAccount[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [showSessionImport, setShowSessionImport] = useState(false);
  const [showQr, setShowQr] = useState(false);
  const [sessionFile, setSessionFile] = useState<File | null>(null);
  const [sessionLabel, setSessionLabel] = useState("");
  const [phone, setPhone] = useState("");
  const [label, setLabel] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = () => api.accounts().then((d) => setAccounts(d.items)).catch((err) => setError(err instanceof Error ? err.message : "Không thể tải danh sách tài khoản."));
  useEffect(() => {
    load();
    const events = new EventSource("/api/events");
    events.onmessage = (event) => { try { const p = JSON.parse(event.data); if (p.type === "account_login" || p.type === "job_update") load(); } catch {} };
    const timer = window.setInterval(load, 30000);
    return () => { events.close(); window.clearInterval(timer); };
  }, []);
  async function addAndLogin(e: FormEvent) {
    e.preventDefault(); setBusy("add"); setError("");
    try {
      const created = await api.addAccount({ phone, label: label || undefined });
      setPhone(""); setLabel(""); setShowAdd(false);
      await api.startAccountLogin(created.id); load();
    } catch (err) { setError(err instanceof Error ? err.message : "Không thể thêm tài khoản."); }
    finally { setBusy(""); }
  }
  async function startLogin(id: string) {
    setBusy(id); setError("");
    try { await api.startAccountLogin(id); load(); }
    catch (err) { setError(err instanceof Error ? err.message : "Không thể đăng nhập."); }
    finally { setBusy(""); }
  }
  async function migrateSql(id: string) {
    setBusy(id); setError("");
    try { await api.migrateLegacySession(id); load(); }
    catch (err) { setError(err instanceof Error ? err.message : "Không thể chuyển session legacy sang SQL."); }
    finally { setBusy(""); }
  }
  async function importSession(e: FormEvent) {
    e.preventDefault();
    if (!sessionFile) return setError("Hãy chọn file Telethon .session.");
    setBusy("session-import"); setError("");
    try {
      await api.importTelegramSession(sessionFile, sessionLabel || undefined);
      setSessionFile(null); setSessionLabel(""); setShowSessionImport(false); load();
    } catch (err) { setError(err instanceof Error ? err.message : "Không thể nhập file session."); }
    finally { setBusy(""); }
  }
  async function submit(account: TelegramAccount) {
    if (!account.login) return;
    setBusy(account.id); setError("");
    try {
      await api.submitLogin(account.login.state === "WAIT_2FA" ? { session_id: account.login.session_id, password } : { session_id: account.login.session_id, code });
      setCode(""); setPassword(""); load();
    } catch (err) { setError(err instanceof Error ? err.message : "Không thể xác thực."); }
    finally { setBusy(""); }
  }
  async function action(id: string, kind: "default" | "logout" | "delete") {
    setBusy(id); setError("");
    try {
      if (kind === "default") await api.setDefaultAccount(id);
      else if (kind === "logout") await api.logoutAccount(id);
      else await api.deleteAccount(id);
      load();
    } catch (err) { setError(err instanceof Error ? err.message : "Không thể cập nhật tài khoản."); }
    finally { setBusy(""); }
  }
  return <section>
    <div className="intro-row"><div><span className="eyebrow">TELEGRAM ACCOUNTS</span><h2>Quản lý <em>tài khoản.</em></h2><p>Đăng nhập bằng QR, OTP/2FA hoặc file Telethon .session. Session hợp lệ đều được chuyển thành encrypted StringSession trong PostgreSQL.</p></div><div className="top-actions"><button className="button ghost" onClick={() => { setShowQr(true); setShowAdd(false); setShowSessionImport(false); }}>▦ Đăng nhập QR</button><button className="button ghost" onClick={() => { setShowSessionImport(!showSessionImport); setShowAdd(false); }}>⇧ Nhập file session</button><button className="button primary" onClick={() => { setShowAdd(!showAdd); setShowSessionImport(false); }}>＋ Thêm bằng OTP</button></div></div>
    {error && <div className="alert error">{error}</div>}
    {showSessionImport && <form className="account-add-card" onSubmit={importSession}>
      <div><span className="eyebrow">SESSION LOGIN</span><h3>Đăng nhập bằng file Telethon .session</h3><p className="muted">File sẽ chỉ tồn tại tạm thời để xác minh, sau đó session được mã hóa vào PostgreSQL và file upload bị xóa.</p></div>
      <label>Tên hiển thị<input value={sessionLabel} onChange={(e) => setSessionLabel(e.target.value)} placeholder="Ví dụ: Account session 02" /></label>
      <label>File .session<input required type="file" accept=".session,application/x-sqlite3" onChange={(e) => setSessionFile(e.target.files?.[0] || null)} /><small>Chỉ dùng file Telethon .session của chính tài khoản bạn sở hữu.</small></label>
      <div className="modal-actions"><button type="button" className="button ghost" onClick={() => setShowSessionImport(false)}>Hủy</button><button className="button primary" disabled={busy === "session-import" || !sessionFile}>{busy === "session-import" ? "Đang xác minh…" : "Nhập & xác minh session →"}</button></div>
    </form>}
    {showAdd && <form className="account-add-card" onSubmit={addAndLogin}>
      <div><span className="eyebrow">NEW TELEGRAM ACCOUNT</span><h3>Thêm và đăng nhập tài khoản</h3></div>
      <label>Tên hiển thị<input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Ví dụ: Account bán hàng 01" /></label>
      <label>Số điện thoại<input required value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+84912345678" /></label>
      <div className="modal-actions"><button type="button" className="button ghost" onClick={() => setShowAdd(false)}>Hủy</button><button className="button primary" disabled={busy === "add"}>{busy === "add" ? "Đang gửi OTP…" : "Thêm & đăng nhập →"}</button></div>
    </form>}
    <div className="account-summary"><strong>{accounts.length}</strong><span>tài khoản đã lưu</span><b>{accounts.filter((a) => ["AUTHORIZED","IN_USE","FLOOD_WAIT"].includes(a.state)).length}</b><span>đang kết nối</span></div>
    <div className="account-list">{accounts.length === 0 ? <div className="empty-state large"><h3>Chưa có tài khoản Telegram</h3><p>Thêm tài khoản đầu tiên để bắt đầu.</p></div> : accounts.map((a) => <div className="account-list-card" key={a.id}>
      <div className={`account-symbol ${a.state.toLowerCase()}`}>{["AUTHORIZED","IN_USE"].includes(a.state) ? "✓" : a.state === "FLOOD_WAIT" ? "⏱" : a.state === "LOGIN_IN_PROGRESS" ? "…" : "○"}</div>
      <div className="account-copy"><div className="account-title"><h3>{a.label}</h3>{a.is_default && <span className="default-badge">Mặc định</span>}</div><p>{a.phone}</p><small>{a.state === "AUTHORIZED" ? "Đã đăng nhập" : a.state === "IN_USE" ? "Đang được job sử dụng" : a.state === "FLOOD_WAIT" ? `FloodWait đến ${a.blocked_until ? formatDate(a.blocked_until) : "khi Telegram cho phép"}` : a.state === "LOGIN_IN_PROGRESS" ? "Đang chờ xác thực" : "Chưa đăng nhập"}{a.session_backend ? ` · Session: ${a.session_backend === "SQL" ? "PostgreSQL" : a.session_backend === "LEGACY_FILE" ? "Local legacy" : "Chưa có"}` : ""}{a.last_login_at ? ` · Đăng nhập gần nhất ${formatDate(a.last_login_at)}` : ""}{a.last_rate_limit_at ? ` · Rate-limit gần nhất ${formatDate(a.last_rate_limit_at)}` : ""}</small></div>
      <div className="account-actions">
        {!a.is_default && <button className="button ghost compact" disabled={busy === a.id || a.state === "IN_USE"} onClick={() => action(a.id, "default")}>Đặt mặc định</button>}
        {a.state === "NOT_AUTHORIZED" && <button className="button primary compact" disabled={busy === a.id} onClick={() => startLogin(a.id)}>Đăng nhập</button>}
        {a.session_backend === "LEGACY_FILE" && a.state !== "IN_USE" && <button className="button ghost compact" disabled={busy === a.id} onClick={() => migrateSql(a.id)}>Chuyển session sang SQL</button>}
        {a.state === "AUTHORIZED" && <button className="button ghost compact" disabled={busy === a.id} onClick={() => action(a.id, "logout")}>Đăng xuất</button>}
        <button className="icon-button danger" title="Xóa tài khoản" disabled={busy === a.id || a.state === "IN_USE"} onClick={() => action(a.id, "delete")}>⌫</button>
      </div>
      {a.login && <div className="account-login-step">
        <div><strong>{a.login.state === "WAIT_2FA" ? "Nhập mật khẩu 2FA" : "Nhập mã OTP Telegram"}</strong><span>{a.login.state === "WAIT_2FA" ? "Telegram yêu cầu xác thực hai bước." : `Mã đã được gửi đến ${a.phone}.`}</span></div>
        <div className="inline-form"><input type={a.login.state === "WAIT_2FA" ? "password" : "text"} value={a.login.state === "WAIT_2FA" ? password : code} onChange={(e) => a.login?.state === "WAIT_2FA" ? setPassword(e.target.value) : setCode(e.target.value)} placeholder={a.login.state === "WAIT_2FA" ? "Mật khẩu 2FA" : "12345"} /><button className="button primary compact" disabled={busy === a.id} onClick={() => submit(a)}>Xác nhận</button><button className="button ghost compact" onClick={() => api.cancelLogin(a.login!.session_id).then(load)}>Hủy</button></div>
      </div>}
    </div>)}</div>
    {showQr && <QrLoginModal onClose={() => setShowQr(false)} onDone={() => { load(); window.setTimeout(() => setShowQr(false), 900); }} />}
    <div className="info-panel"><strong>Cách chọn account cho job</strong><span>Tài khoản mặc định được chọn tự động. Khi tạo/import job bạn có thể chọn account khác; job sẽ ghi nhớ account đó để pause/resume/crash-recovery luôn dùng đúng phiên.</span></div>
  </section>;
}

function SettingsPage() {
  const [data, setData] = useState<RuntimeConfig | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.config().then(setData).catch((err) => setError(err instanceof Error ? err.message : "Không thể tải cấu hình.")); }, []);
  return <section className="narrow"><div className="intro-row"><div><span className="eyebrow">SYSTEM CONFIGURATION</span><h2>Cấu hình <em>runtime.</em></h2><p>Thông tin đang hoạt động, các secret luôn được che.</p></div></div>{error && <div className="alert error">{error}</div>}{!data ? <div className="empty-state"><span className="spinner" /> Đang tải cấu hình…</div> : <div className="settings-grid"><SettingGroup title="Telegram"><Setting label="API ID" value={data.api_id_masked || "Chưa đặt"} /><Setting label="API Hash" value={data.api_hash_set ? "•••••••• Đã đặt" : "Chưa đặt"} /><Setting label="Số điện thoại" value={data.api_phone_number || "Chưa đặt"} /><Setting label="Proxy" value={data.proxy_set ? "Đã cấu hình" : "Không dùng"} /></SettingGroup><SettingGroup title="Engine"><Setting label="Khu vực mặc định" value={data.default_phone_region} /><Setting label="Số lần thử tối đa" value={String(data.max_attempts)} /><Setting label="Tự động tiếp tục" value={data.auto_resume ? "Bật" : "Tắt"} /><Setting label="Lease worker" value={`${data.worker_lease_seconds}s`} /></SettingGroup><SettingGroup title="Web UI"><Setting label="Username" value={data.web_ui.username} /><Setting label="Session" value={`${data.web_ui.session_hours} giờ`} /><Setting label="Telegram owner" value={data.web_ui.telegram_account_owned ? "Đã xác thực" : "Chưa xác thực"} /><Setting label="Database" value={data.database_backend} /></SettingGroup></div>}</section>;
}

function SettingGroup({ title, children }: { title: string; children: React.ReactNode }) { return <div className="setting-group"><h3>{title}</h3>{children}</div>; }
function Setting({ label, value }: { label: string; value: string }) { return <div className="setting-row"><span>{label}</span><strong>{value}</strong></div>; }
function formatDate(value: string) { if (!value) return "—"; try { return new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); } catch { return value; } }

export default App;