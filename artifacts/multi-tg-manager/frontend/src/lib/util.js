export function initials(first, last) {
  const a = (first || '').trim()[0] || ''
  const b = (last  || '').trim()[0] || ''
  return (a + b).toUpperCase() || '?'
}

export function fmtTime(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', month: 'short', day: '2-digit' })
  } catch { return '' }
}

export function colorForString(s) {
  let h = 0
  for (const c of s || '') h = (h * 31 + c.charCodeAt(0)) % 360
  return `hsl(${h} 70% 60%)`
}

export function ensureNotificationPermission() {
  if (!('Notification' in window)) return Promise.resolve('unsupported')
  if (Notification.permission === 'default') return Notification.requestPermission()
  return Promise.resolve(Notification.permission)
}

export function desktopNotify(title, body) {
  try {
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification(title, { body })
    }
  } catch {}
}
