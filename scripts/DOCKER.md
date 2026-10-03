# Running the vault scripts in Docker (Windows, Ubuntu, any Linux server)

The container runs `telegram_bot.py` (Telegram → `00 - Inbox`) and can run `lint.py`.
The image holds only Python and its dependencies (`requirements.txt`). The vault is mounted at `/vault`, and the code runs from `/vault/scripts`, so the same files work on every host.

Obsidian and the agent workflows in `AGENTS.md` stay on whichever machine you use to edit the vault. Only the scripts run in Docker.

## Files

| File | Role |
|---|---|
| `Dockerfile` | `python:3.12-slim` + `pip install -r requirements.txt` |
| `.dockerignore` | Sends only `requirements.txt` to the build |
| `compose.yaml` | Bot service: vault mount, model cache, restart policy, log rotation |
| `.env` | Bot settings (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_IDS`, ...) plus optional `PUID`, `PGID`, `TZ` for compose |
| `vendor/` | Windows-only packages for running the bot natively on Windows. Ignored on Linux and in Docker. |

## Important: one bot per token

Telegram allows only one process to poll a bot token. Two running copies (Windows + Linux, or native + Docker) fight over updates and log `409 Conflict`. **Stop the old bot before you start a new one.**

## Quick start (any host with Docker)

```bash
cd "<vault>/scripts"
cp .env.example .env          # first time only; fill in the token and your Telegram id
docker compose up -d --build
docker compose logs -f        # expect "Bot running. ... Allowed ids: {...}"
```

Optional entries in `scripts/.env` (compose reads them too):

```
PUID=1000                      # Linux: your `id -u`; files in the vault are written as this user
PGID=1000                      # Linux: your `id -g`
TZ=America/Argentina/Buenos_Aires   # timezone for note timestamps (default UTC)
YTDLP_COOKIES=/vault/scripts/cookies.txt   # optional; path *inside* the container
```

## Moving to Ubuntu / a Linux server

1. **Install Docker** (Ubuntu 22.04+):
   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-v2
   sudo usermod -aG docker $USER   # then log out and back in
   ```
2. **Stop the Windows bot**, then wait for it to exit, so that `telegram.offset` is final.
3. **Copy the vault**. On Windows, from the folder that contains `Vault`:
   ```powershell
   tar -czf vault.tgz --exclude=Vault/scripts/vendor --exclude=__pycache__ --exclude="*.log" Vault
   scp vault.tgz user@server:~
   ```
   On the server: `tar -xzf vault.tgz`. Keep `scripts/.env` (secrets) and `scripts/telegram.offset` (stops old messages from being imported twice).
4. **Set your user and timezone**: add `PUID`, `PGID` (from `id`) and `TZ` to `scripts/.env`.
5. **Start it**:
   ```bash
   cd ~/Vault/scripts && docker compose up -d --build
   ```
   `restart: unless-stopped` brings it back after reboots (Docker runs as a system service on Ubuntu).

The first voice note or Reel downloads the Whisper model (`small` is about 500 MB) into the `whisper-cache` volume. Later runs reuse it.

Telegram voice, audio, video-note and directly sent video transcripts are restricted to English or Spanish. The bot compares Whisper's language probabilities for those two languages and forces transcription in the stronger match. Instagram Reel and TikTok transcripts keep automatic language detection.

## Everyday commands (run in `scripts/`)

| Task | Command |
|---|---|
| Status / logs | `docker compose ps` · `docker compose logs -f` |
| Restart after editing a script | `docker compose restart` |
| Stop | `docker compose down` |
| Update yt-dlp (Instagram breaks often) | `docker compose build --pull --no-cache && docker compose up -d` |
| Run the vault lint | `docker compose run --rm bot python lint.py` |

## Keeping the vault in sync across machines

The bot writes into the vault on the server, so the server needs to be where the vault lives, or be synced with the place you edit it:

- **Syncthing** (free, works peer-to-peer) or **Obsidian Sync** keeps the server copy and your desktop copy in step.
- Run the bot on **one** machine only, and never sync `scripts/telegram.offset` to a machine where a second bot is running.
- Exclude `scripts/vendor/`, `__pycache__/` and `*.log` from sync. They are machine-specific.

## Backup to Google Drive

The `backup` service (rclone) copies the vault to your Google Drive every 24 h:

- `Vault-backup/current`: a mirror of the vault
- `Vault-backup/history/<date>`: every file that a run changed or deleted, kept for 90 days. This is your undo.
- Not uploaded: `scripts/.env`, `scripts/cookies.txt`, `scripts/rclone/` (the Drive token), `vendor/`, logs, `__pycache__`, `.obsidian/workspace*.json`
- The vault is mounted **read-only**, so the backup can't change your notes.
- It uses the `drive.file` permission, so rclone only sees the files it creates, not the rest of your Drive.

### One-time connection (needs a browser, so do it on Windows)

1. Install rclone, then open a **new** terminal:
   ```powershell
   winget install Rclone.Rclone
   ```
2. From `scripts/`, create the connection. A browser opens: sign in to Google and click **Allow**.
   ```powershell
   rclone config create gdrive drive scope=drive.file --config rclone/rclone.conf
   ```
3. Start the backup. The first run starts immediately.
   ```powershell
   docker compose up -d backup
   docker compose logs backup     # expect "backup done"
   ```

On a Linux server, copy `scripts/rclone/rclone.conf` from Windows (together with `scripts/.env`) and run `docker compose up -d`. You don't need to log in again.

Settings in `scripts/.env` (optional): `BACKUP_INTERVAL_HOURS=24`, `BACKUP_KEEP_DAYS=90`, `BACKUP_FOLDER=Vault-backup`.

| Task | Command |
|---|---|
| Back up now | `docker compose restart backup` |
| See what's in Drive | `docker compose run --rm --entrypoint rclone backup lsd gdrive:Vault-backup` |
| Restore everything to a folder | `docker compose run --rm --entrypoint rclone -v ~/restore:/restore backup copy gdrive:Vault-backup/current /restore` |

To restore one file, open `Vault-backup/current` (or `history/<date>`) at drive.google.com and download it.

## Without Docker (native Linux, optional)

```bash
cd ~/Vault/scripts
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python telegram_bot.py
```

To keep it running, use a systemd service with `ExecStart=/home/<you>/Vault/scripts/.venv/bin/python telegram_bot.py`, `WorkingDirectory=/home/<you>/Vault/scripts` and `Restart=always`. Docker is simpler, though.

## Troubleshooting

- **`409 Conflict` in logs**: another bot is polling the same token. Stop it.
- **Permission denied writing to `00 - Inbox`**: `PUID`/`PGID` don't match the owner of the vault folder (`ls -ln ~/Vault`).
- **Wrong timestamps on notes**: set `TZ` in `scripts/.env`, then `docker compose up -d`.
- **Instagram downloads fail**: update yt-dlp (see above), or export cookies to `scripts/cookies.txt` and set `YTDLP_COOKIES`.
- **Windows host**: Docker Desktop must be running. Leave `PUID`/`PGID` unset.
