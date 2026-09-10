export function confirmVi(message, { title = 'Xác nhận thao tác' } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div')
    overlay.className = 'fixed inset-0 z-[120] bg-black/50 flex items-center justify-center p-4'

    const card = document.createElement('div')
    card.className = 'nb-card p-5 w-full max-w-lg'

    const heading = document.createElement('h2')
    heading.className = 'font-extrabold uppercase tracking-tight mb-3'
    heading.textContent = title

    const body = document.createElement('div')
    body.className = 'text-sm whitespace-pre-wrap leading-relaxed'
    body.textContent = String(message || '')

    const actions = document.createElement('div')
    actions.className = 'flex justify-end gap-2 mt-5'

    const cancel = document.createElement('button')
    cancel.className = 'nb-btn'
    cancel.type = 'button'
    cancel.textContent = 'Hủy'

    const ok = document.createElement('button')
    ok.className = 'nb-btn-pri'
    ok.type = 'button'
    ok.textContent = 'Xác nhận'

    const finish = (value) => {
      document.removeEventListener('keydown', onKeyDown)
      overlay.remove()
      resolve(value)
    }
    const onKeyDown = (event) => {
      if (event.key === 'Escape') finish(false)
      if (event.key === 'Enter') finish(true)
    }

    cancel.addEventListener('click', () => finish(false))
    ok.addEventListener('click', () => finish(true))
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) finish(false)
    })
    document.addEventListener('keydown', onKeyDown)

    actions.append(cancel, ok)
    card.append(heading, body, actions)
    overlay.append(card)
    document.body.append(overlay)
    requestAnimationFrame(() => ok.focus())
  })
}
