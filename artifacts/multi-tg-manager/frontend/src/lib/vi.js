const STATUS = {
  connected: 'Đã kết nối',
  disconnected: 'Mất kết nối',
  banned: 'Bị cấm',
  removed: 'Đã xóa',
  deactivated: 'Đã vô hiệu hóa',
  ok: 'Thành công',
  success: 'Thành công',
  failed: 'Thất bại',
  skipped: 'Bỏ qua',
  pending: 'Đang chờ',
  partial: 'Một phần',
  waiting: 'Đang chờ',
  authorized: 'Đã xác thực',
  expired: 'Đã hết hạn',
  error: 'Lỗi',
  idle: 'Sẵn sàng',
  needs_2fa: 'Cần 2FA',
  current: 'Hiện tại',
}

const PEER_KIND = {
  user: 'Người dùng',
  bot: 'Bot Telegram',
  chat: 'Trò chuyện',
  group: 'Nhóm',
  channel: 'Kênh',
  megagroup: 'Siêu nhóm',
}
const SECURITY_TYPE = {
  login_code: 'Mã đăng nhập',
  new_login: 'Đăng nhập mới',
  '2fa_change': 'Thay đổi 2FA',
  account_deletion: 'Xóa tài khoản',
  unknown: 'Khác',
}

export function viStatus(value) {
  if (value == null) return ''
  return STATUS[String(value)] || String(value)
}

export function viPeerKind(value, isBot = false) {
  if (isBot) return PEER_KIND.bot
  if (value == null) return PEER_KIND.chat
  return PEER_KIND[String(value)] || 'Đối tượng Telegram'
}

export function viSecurityType(value) {
  if (value == null) return ''
  return SECURITY_TYPE[String(value)] || 'Cảnh báo Telegram'
}

export function viReason(value) {
  return viStatus(value)
}

const hasVietnamese = (text) => /[À-ỹĐđ]/.test(String(text || ''))
const EXACT = {
  'Cannot reach server': 'Không thể kết nối tới máy chủ.',
  'Not authenticated': 'Chưa đăng nhập hoặc phiên đăng nhập đã hết hạn.',
  'Wrong password': 'Mật khẩu không đúng.',
  'Account not found': 'Không tìm thấy tài khoản.',
  'Telegram returned an error': 'Telegram trả về lỗi.',
  'Could not read this chat': 'Không thể đọc thông tin của cuộc trò chuyện này.',
  'TG_API_HASH is required for the first configuration': 'Cần nhập TG_API_HASH khi cấu hình lần đầu.',
  'TG_API_HASH is required when TG_API_ID changes': 'Cần nhập TG_API_HASH mới khi thay đổi TG_API_ID.',
  'TG_API_HASH must be exactly 32 hexadecimal characters': 'TG_API_HASH phải có đúng 32 ký tự hệ thập lục phân.',
  'No application encryption key is configured': 'Chưa cấu hình khóa mã hóa của ứng dụng.',
  'Stored Telegram API credentials could not be decrypted': 'Không thể giải mã thông tin Telegram API đã lưu.',
}

const PATTERNS = [
  [/Too many attempts\. Try again in (\d+)s\.?/i, (m) => `Thử quá nhiều lần. Vui lòng thử lại sau ${m[1]} giây.`],
  [/PHONE_CODE_INVALID/i, () => 'Mã OTP không hợp lệ.'],
  [/PHONE_CODE_EXPIRED/i, () => 'Mã OTP đã hết hạn.'],
  [/SESSION_PASSWORD_NEEDED/i, () => 'Tài khoản yêu cầu mật khẩu 2FA.'],
  [/PASSWORD_HASH_INVALID/i, () => 'Mật khẩu 2FA không đúng.'],
  [/AUTH_KEY_UNREGISTERED|AUTH_KEY_INVALID/i, () => 'Phiên Telegram không còn hợp lệ.'],
  [/USER_DEACTIVATED|USER_DEACTIVATED_BAN/i, () => 'Tài khoản Telegram đã bị vô hiệu hóa hoặc bị cấm.'],
  [/FLOOD_WAIT|A wait of (\d+) seconds/i, (m) => m[1] ? `Telegram yêu cầu chờ ${m[1]} giây trước khi thử lại.` : 'Telegram đang giới hạn tần suất. Vui lòng thử lại sau.'],
  [/ChannelsTooMuch|CHANNELS_TOO_MUCH/i, () => 'Tài khoản đã đạt giới hạn số nhóm/kênh Telegram.'],
  [/USERNAME_OCCUPIED|already occupied/i, () => 'Tên người dùng này đã được sử dụng.'],
  [/USERNAME_INVALID/i, () => 'Tên người dùng không hợp lệ.'],
  [/PHONE_NOT_OCCUPIED/i, () => 'Số điện thoại này chưa được đăng ký Telegram hoặc không thể được tìm thấy.'],
  [/PHONE_NUMBER_INVALID/i, () => 'Số điện thoại không hợp lệ. Hãy dùng mã quốc gia quốc tế.'],
  [/PEER_FLOOD/i, () => 'Telegram đang giới hạn việc nhắn tin tới người dùng mới trên tài khoản này.'],
  [/USER_PRIVACY_RESTRICTED/i, () => 'Cài đặt quyền riêng tư của người nhận không cho phép thao tác này.'],
  [/YOU_BLOCKED_USER|USER_IS_BLOCKED/i, () => 'Tài khoản gửi đang chặn người nhận này.'],
]
export function viMessage(message, status = 0) {
  const text = String(message || '').trim()
  if (!text) return status >= 500 ? 'Máy chủ gặp lỗi nội bộ.' : 'Không thể hoàn tất thao tác.'
  if (hasVietnamese(text)) return text
  if (EXACT[text]) return EXACT[text]
  for (const [pattern, render] of PATTERNS) {
    const match = text.match(pattern)
    if (match) return render(match)
  }
  if (status === 400) return 'Yêu cầu không hợp lệ. Vui lòng kiểm tra dữ liệu đã nhập.'
  if (status === 401) return 'Chưa đăng nhập hoặc phiên đăng nhập đã hết hạn.'
  if (status === 403) return 'Bạn không có quyền thực hiện thao tác này.'
  if (status === 404) return 'Không tìm thấy dữ liệu hoặc chức năng được yêu cầu.'
  if (status === 409) return 'Dữ liệu đang xung đột với trạng thái hiện tại.'
  if (status === 413) return 'Tệp tải lên vượt quá dung lượng cho phép.'
  if (status === 422) return 'Dữ liệu nhập chưa hợp lệ.'
  if (status === 429) return 'Thao tác quá nhanh. Vui lòng thử lại sau.'
  if (status >= 500) return 'Máy chủ gặp lỗi nội bộ. Vui lòng thử lại.'
  return 'Không thể hoàn tất thao tác. Vui lòng kiểm tra lại.'
}

export function viDetail(message) {
  return viMessage(message, 0)
}
