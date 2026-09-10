#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is required}"
root=$(cd "$(dirname "$0")/.." && pwd)
sessions="$root/backend/sessions"
out_dir="${1:-$root/backups}"
mkdir -p "$out_dir"
ts=$(date -u +%Y%m%dT%H%M%SZ)
out="$out_dir/sessions-$ts.tar.gz.enc"
tmp="$out.tmp"
trap 'rm -f "$tmp"' EXIT
mkdir -p "$sessions"
tar -C "$root/backend" -czf - sessions | \
  openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -out "$tmp"
mv "$tmp" "$out"
chmod 600 "$out"
echo "$out"
