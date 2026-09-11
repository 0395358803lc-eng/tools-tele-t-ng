# MERGE PLAN — Telegram Checker + Multi TG Manager

## Mục tiêu
- Một FastAPI process, một PostgreSQL, một Web UI.
- Giữ checker/job engine, worker lease, retry/FloodWait và encrypted SQL Telegram session hiện tại làm core.
- Chuyển toàn bộ shell/UI sang phong cách neo-brutalism của Multi TG Manager.
- Không dùng localStorage/sessionStorage/SQLite cho dữ liệu persistent production.

## Kiến trúc hợp nhất
1. Core hiện tại là nguồn chuẩn cho auth, account, Telegram session, jobs, results và settings.
2. Multi TG Manager được nhập theo capability, không chạy backend thứ hai.
3. Các bảng mới nếu cần phải dùng prefix `mtm_`; không được đụng tên `app_settings` hiện tại.
4. Các module quản trị Telegram phải lấy session từ `telegram_sessions` hiện tại.
5. Một navigation duy nhất: Tổng quan, Tài khoản, Hồ sơ, Bảo mật, Nhóm & Kênh, Tin nhắn, Kiểm tra, Hàng loạt, Cài đặt.

## Pha triển khai
- P1: Neo-brutalism shell + sidebar account + tab navigation, giữ nguyên API hiện có.
- P2: Adapter account/session thống nhất và API profile/security/groups/messaging.
- P3: Bulk actions + audit/history + UI hoàn chỉnh.
- P4: Full regression, security audit, production publish.

## Tiêu chí nghiệm thu
- Existing checker tests 100% pass.
- UI build pass, npm audit 0 critical/high.
- PostgreSQL là nguồn persistent duy nhất.
- Account/session hiện tại không mất sau restart.
- Không có backend/service thứ hai chạy song song.
