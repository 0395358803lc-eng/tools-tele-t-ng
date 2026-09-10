#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is required}"
root=$(cd "$(dirname "$0")/.." && pwd)
backup="${1:?usage: restore_sessions.sh BACKUP.tar.gz.enc}"
test -f "$backup"
mkdir -p "$root/backend/sessions"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in "$backup" | \
  tar -C "$root/backend" -xzf -
chmod 700 "$root/backend/sessions"
find "$root/backend/sessions" -maxdepth 1 -type f -exec chmod 600 {} +
echo "Session restore completed"
