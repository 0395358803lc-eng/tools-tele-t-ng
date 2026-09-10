export default function ConfirmModal({
  title = 'Bạn có chắc chắn?',
  message,
  confirmLabel = 'Có',
  cancelLabel = 'Không',
  danger = false,
  onConfirm,
  onCancel,
}) {
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onCancel}>
      <div className="nb-card p-6 w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
        <h2 className="font-extrabold uppercase tracking-tight mb-2">{title}</h2>
        {message && <div className="text-sm opacity-80 mb-5 whitespace-pre-wrap">{message}</div>}
        <div className="flex gap-2 justify-end">
          <button className="nb-btn" onClick={onCancel}>{cancelLabel}</button>
          <button
            className={'nb-btn ' + (danger ? 'bg-brand-err text-black' : 'nb-btn-pri')}
            onClick={onConfirm}
            autoFocus
          >{confirmLabel}</button>
        </div>
      </div>
    </div>
  )
}
