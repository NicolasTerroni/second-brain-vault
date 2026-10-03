#!/bin/sh
# Vault -> Google Drive backup (runs in the rclone container, see compose.yaml and DOCKER.md).
#   Drive:Vault-backup/current             mirror of the vault (plain files)
#   Drive:Vault-backup/history/<date>.zip  files changed or deleted by each run, zipped, kept $BACKUP_KEEP_DAYS days
# History names use UTC (<date> = YYYY-MM-DD_HHMMZ); log lines use local time (TZ).
# Each successful run writes "<epoch> <UTC time>" to scripts/rclone/last_backup.
#   sh backup.sh          loop: back up whenever BACKUP_INTERVAL_HOURS have passed since the last backup
#   sh backup.sh --once   back up now and exit (used by backup_if_due.py)
VAULT="${VAULT_DIR:-/vault}"
CONF_DIR="${RCLONE_CONFIG_DIR:-/config/rclone}"
export RCLONE_CONFIG="$CONF_DIR/rclone.conf"
LAST="$CONF_DIR/last_backup"
FAIL="$CONF_DIR/last_failure"   # present only while backups are failing
DEST="gdrive:${BACKUP_FOLDER:-Vault-backup}"
INTERVAL="${BACKUP_INTERVAL_HOURS:-24}"
KEEP="${BACKUP_KEEP_DAYS:-90}"

now() { date '+%Y-%m-%d %H:%M %Z'; }

# rclone can only move replaced files to a folder on the same remote (--backup-dir), so each run's
# history/<date>/ folder is zipped afterwards: download it, zip it, upload <date>.zip, then remove the folder.
# Any folder left by an older run or a failed attempt is converted on the next run.
zip_history() {
  rclone mkdir "$DEST/history" || return 1
  for dir in $(rclone lsf "$DEST/history" --dirs-only); do
    name="${dir%/}"
    tmp=$(mktemp -d) || return 1
    mkdir -p "$tmp/$name"
    rclone copy "$DEST/history/$name" "$tmp/$name" && {
      if [ -z "$(ls -A "$tmp/$name")" ]; then
        rclone purge "$DEST/history/$name"          # empty folder, nothing to keep
      else
        (cd "$tmp/$name" && zip -qr -X "$tmp/$name.zip" .) \
          && rclone copyto "$tmp/$name.zip" "$DEST/history/$name.zip" \
          && rclone purge "$DEST/history/$name" \
          && echo "[$(now)] history: $name -> $name.zip"   # folder removed only after the zip is uploaded and verified
      fi
    }
    rc=$?
    rm -rf "$tmp"
    [ $rc -eq 0 ] || { echo "[$(now)] history: could not zip $name"; return 1; }
  done
}

backup() {
  stamp=$(date -u +%Y-%m-%d_%H%MZ)
  echo "[$(now)] backup start"
  if rclone sync "$VAULT" "$DEST/current" \
      --backup-dir "$DEST/history/$stamp" \
      --create-empty-src-dirs \
      --exclude "scripts/.env" --exclude "scripts/cookies.txt" --exclude "scripts/rclone/**" \
      --exclude "scripts/vendor/**" --exclude "scripts/.venv/**" --exclude "**/__pycache__/**" \
      --exclude "scripts/*.log" --exclude ".obsidian/workspace*.json" --exclude ".git/**" \
      --stats-one-line --stats 0 --log-level NOTICE \
    && zip_history \
    && rclone delete "$DEST/history" --min-age "${KEEP}d" \
    && rclone rmdirs "$DEST/history" --leave-root; then
    echo "$(date -u +%s) $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LAST"
    rm -f "$FAIL"
    echo "[$(now)] backup done"
  else
    echo "[$(now)] backup FAILED"
    prev=$(cut -d' ' -f1 "$FAIL" 2>/dev/null)
    echo "$(date -u +%s) $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$FAIL"
    # Alert on the first failure, then at most once a day while it keeps failing.
    if [ -z "$prev" ] || [ $(( $(date -u +%s) - prev )) -ge 86400 ]; then
      alert "⚠️ Vault backup to Google Drive FAILED ($(date -u +%Y-%m-%d\ %H:%MZ)). Last good backup: $(cut -d' ' -f2 "$LAST" 2>/dev/null || echo never). Check: docker compose logs backup"
    else
      # keep the time of the first alert so the daily limit holds
      echo "$prev $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$FAIL"
    fi
    return 1
  fi
}

# Telegram message through the vault bot (token and first allowed id from scripts/.env).
alert() {
  env="$VAULT/scripts/.env"
  token=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$env" 2>/dev/null | cut -d= -f2- | tr -d '"\r'"'")
  chat=$(grep -E '^TELEGRAM_ALLOWED_IDS=' "$env" 2>/dev/null | cut -d= -f2- | tr -d ' "\r'"'" | cut -d, -f1)
  [ -n "$token" ] && [ -n "$chat" ] || return 0
  wget -q -O /dev/null --post-data "chat_id=$chat&text=$1" "https://api.telegram.org/bot$token/sendMessage" \
    || echo "alert: Telegram message failed"
}

due() {
  last=$(cut -d' ' -f1 "$LAST" 2>/dev/null)
  [ -z "$last" ] || [ $(( $(date -u +%s) - last )) -ge $(( INTERVAL * 3600 )) ]
}

if [ ! -s "$RCLONE_CONFIG" ]; then
  [ "$1" = "--once" ] && { echo "No Google Drive connection: see DOCKER.md, 'Backup to Google Drive'."; exit 1; }
  while [ ! -s "$RCLONE_CONFIG" ]; do
    echo "No Google Drive connection yet: create scripts/rclone/rclone.conf (see DOCKER.md, 'Backup to Google Drive'). Checking again in 10 min."
    sleep 600
  done
fi

[ "$1" = "--once" ] && { backup; exit $?; }

while true; do
  due && backup
  sleep 3600
done
