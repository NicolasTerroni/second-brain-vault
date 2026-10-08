# Running the vault scripts in Docker (Windows, Ubuntu, any Linux server)

The container runs `telegram_bot.py` (Telegram → `00 - Inbox`) and can run `lint.py` and `source_index.py`.
The image holds only Python, Tesseract OCR and the Python dependencies. The vault is mounted at `/vault`, and the code runs from `/vault/scripts`, so the same files work on every host (Windows with Docker Desktop, Ubuntu, any Linux server, x86 or ARM).

Obsidian and the agent workflows in `AGENTS.md` stay on whichever machine you use to edit the vault. Only the scripts run in Docker.

## Files

| File | Role |
|---|---|
| `Dockerfile` | Bot image: `python:3.12.15-slim-trixie` pinned by digest, Tesseract (English + Spanish), `requirements.lock`, latest yt-dlp |
| `backup.Dockerfile` | Backup image: `rclone/rclone:1.75.1` pinned by digest, plus `zip` and `tzdata` |
| `requirements.txt` | Direct dependencies (edit this one) |
| `requirements.lock` | Generated: every package pinned with hashes, except yt-dlp. Never edit by hand. |
| `.dockerignore` | Sends only `requirements.lock` to the build |
| `compose.yaml` | `bot` and `backup` services (vault mount, model cache, health check, restart policy, log rotation) and the `lock` tool |
| `.env` | Bot settings (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_IDS`, ...) plus optional `PUID`, `PGID`, `TZ` for compose |
| `vendor/` | Windows-only packages for running the bot natively on Windows. Ignored on Linux and in Docker. |

## Pinned versions

A rebuild gives the same image except for yt-dlp, which you want fresh because Instagram and TikTok break old versions.

- **Base images**: pinned by tag and digest in `Dockerfile` and `backup.Dockerfile`. The digests point to multi-platform images, so they work on x86 and ARM. To upgrade, change the tag, then run `docker pull <image>:<tag>` and copy the new digest from `docker image inspect <image>:<tag> --format "{{index .RepoDigests 0}}"`.
- **Python packages**: `requirements.lock` is generated from `requirements.txt` inside a Linux container, so it matches the image on every host:
  ```bash
  docker compose run --rm lock          # after editing requirements.txt, or to take upgrades on purpose
  docker compose build bot && docker compose up -d bot
  ```
- **System packages** (Tesseract, zip, tzdata) come from the pinned OS release and only receive its security updates.

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
TZ=Europe/Madrid   # timezone for note timestamps, the morning brief and the weekly review (default UTC)
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

On first start, the bot downloads the Whisper model (`small` is about 500 MB) into the `whisper-cache` volume. Later starts load it from the cache in a few seconds, before the first voice note arrives. Set `WHISPER_PRELOAD=0` to load it on first use instead, which saves about 500 MB of RAM while the bot is idle. Voice notes and Reels get an immediate "⏳ Transcribing…" reply, which becomes "Saved: …" when done.

Messages that look like they hold secrets (a PIN, PUK, password, card number, CVV, CBU/IBAN, token or seed phrase in your text, your voice note's transcript, or a photo read by Tesseract OCR) are not saved straight away. The bot asks **Save anyway** / **Discard**. Until you answer, the media waits outside the vault in the container's `/tmp`. Held captures are dropped after 24 h or when the bot restarts. A capture you save anyway is tagged `#sensitive`.

Telegram voice, audio, video-note and directly sent video transcripts are restricted to English or Spanish. The bot compares Whisper's language probabilities for those two languages and forces transcription in the stronger match. Instagram Reel and TikTok transcripts keep automatic language detection.

## Talking to the bot

Send anything and it becomes a note in `00 - Inbox`. **Reply** to the bot's "✅ Saved" message to add more to that same note. Only replies are merged, so a new message never gets attached by accident. After each save, area buttons (Training, English, Career…) add an `area/…` tag that guides the next organize.

Commands (`/help` in Telegram shows the same list):

| Command | What it does |
|---|---|
| `/todo` · `/add <task>` · `/done <words>` | Show this week's Pending Tasks, add one, tick one off (edits `Pending Tasks.md`) |
| `/q` | Next unanswered question from `Questions About Me`. A reply is saved as the answer (`#question-N`) |
| `/standup` | Prompt for today's workday in English. The reply is saved as `#standup`; corrections are added at the next organize |
| `/train` · `/train 1` · `/train 2` | Your next training session, exercise by exercise, read from the routine canvas. "Next" alternates from the last session logged in the strength log |
| `/brief` · `/review` | The morning brief or the weekly review, on demand |
| `/inbox` · `/status` · `/backup` | Inbox contents; bot health and last backup; back up now (the backup service starts within a minute and confirms in Telegram) |
| `/ask` · `/organize` · `/did` | Coming soon: shows what each will do |

Scheduled messages, in the bot's `TZ`: the **morning brief** at `BRIEF_TIME` (default `08:00`) and the **weekly review** at `REVIEW_TIME` (default `Sun 18:00`). Set either to `off` in `scripts/.env`. The brief shows today's minimums from Discipline, the English voice-note reminder (and whether yesterday's was sent), project deadlines, the next training session and this week's count ("This week: n/3"), one past English correction to review, and the top pending task. All of this is read straight from the notes, with no tokens. Reply-tracking and send times live in `scripts/telegram.state.json` (git-ignored).

## Everyday commands (run in `scripts/`)

| Task | Command |
|---|---|
| Status / logs | `docker compose ps` (bot shows `healthy` / `unhealthy`) · `docker compose logs -f` |
| Restart after editing a script | `docker compose restart` |
| Stop | `docker compose down` |
| Update yt-dlp (Instagram breaks often) | `docker compose build --no-cache bot && docker compose up -d bot` |
| Regenerate the lock file | `docker compose run --rm lock` |
| Run the vault lint | `docker compose run --rm bot python lint.py` |
| Rebuild the source indexes | `docker compose run --rm bot python source_index.py` |

**Health check.** The bot touches `/tmp/vault-bot.heartbeat` on every Telegram poll, at most 50 s apart. If 15 minutes pass without a touch (polling stalled, or a transcription stuck), Docker marks the container `unhealthy`. The check is a one-line local Python command every 2 minutes, with no network calls and no tokens. Fix it with `docker compose restart bot`.

## Keeping the vault in sync across machines

The bot writes into the vault on the server, so the server needs to be where the vault lives, or be synced with the place you edit it:

- **Syncthing** (free, works peer-to-peer) or **Obsidian Sync** keeps the server copy and your desktop copy in step.
- Run the bot on **one** machine only, and never sync `scripts/telegram.offset` to a machine where a second bot is running.
- Exclude `scripts/vendor/`, `__pycache__/` and `*.log` from sync. They are machine-specific.

## Backup to Google Drive

The `backup` service (rclone) copies the vault to your Google Drive every 24 h:

- `Vault-backup/current`: a mirror of the vault (plain files, not zipped)
- `Vault-backup/history/<date>.zip`: every file that a run changed or deleted, zipped with its folder paths, kept for 90 days. This is your undo. `<date>` is in UTC (`2026-10-03_1754Z`), while log lines use your `TZ`.
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

To restore one file, open `Vault-backup/current` at drive.google.com and download it. For an older version, download `history/<date>.zip` and open it; it holds only the files that run replaced or deleted.

How the zipping works: rclone can only move replaced files to a folder on the same remote, so each run first writes `history/<date>/`, then downloads that folder, zips it, uploads `<date>.zip`, and removes the folder only after the upload is verified. A folder left by a failed run is zipped on the next one.

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
- **Wrong timestamps, or the brief arrives at the wrong hour**: set `TZ` in `scripts/.env` (e.g. `Europe/Madrid`), then `docker compose up -d`. A plain restart does not pick up a new `TZ`.
- **Instagram downloads fail**: update yt-dlp (see above), or export cookies to `scripts/cookies.txt` and set `YTDLP_COOKIES`.
- **Windows host**: Docker Desktop must be running. Leave `PUID`/`PGID` unset. To survive a reboot, turn on Docker Desktop → Settings → General → **Start Docker Desktop when you sign in**. The containers then come back on their own (`restart: unless-stopped`).
- **Docker Desktop won't start ("An unexpected error occurred … listening on unix://… The file cannot be accessed by the system")**: a crash or unclean shutdown left stale socket files (`AppData\Local\Docker\run\…`, `AppData\Local\docker-secrets-engine\engine.sock`) that Windows can't remove. Quit the error dialog, then run `powershell -ExecutionPolicy Bypass -File scripts\windows\start-docker.ps1`. It moves those folders aside (renamed `*.stale-<date>`, nothing deleted) and starts Docker. For automatic recovery at sign-in, put a shortcut to that command in `shell:startup` (the current Windows setup uses `Start Docker for Vault.lnk`).
- **Docker didn't start at sign-in although "Start Docker Desktop when you sign in" is on**: check Task Manager → Startup apps → Docker Desktop is *Enabled*. Turning the Docker setting off, applying, then on again rewrites the entry.
- **`unhealthy` bot**: `docker compose logs --tail 50 bot` to see where it stopped, then `docker compose restart bot`.

## Personal trainer (optional)
A second Telegram bot that plans your training week, asks when you'll train, nags until you start, logs every set and writes the strength log (`trainer.py`; what it does and its persona: `02 - Areas/Training/Coach.md`). It runs **inside the bot container** as a thread, so there is nothing else to start.
1. In Telegram, message **@BotFather** → `/newbot` → name it (e.g. "Coach"). Copy the token into `scripts/.env` as `TRAINER_BOT_TOKEN=...`. It must be a different bot from the capture bot.
2. Optional, for the coach's own words and free questions: on your PC run `claude setup-token` (it uses your Claude subscription) and put the result in `scripts/.env` as `CLAUDE_CODE_OAUTH_TOKEN=...`. The image already includes a pinned Claude Code. Without the token the coach uses fixed messages.
3. `docker compose restart bot`. The logs show `Trainer running. AI: Claude Code` (or `templates`). Open your new bot in Telegram and send `/start`.

While the trainer is on, the capture bot no longer nags about the Strength habit. Timing settings (`TRAINER_*`) are listed in `.env.example`.

## English teacher (optional)
A third Telegram bot, also a thread of the bot container: a daily drill on your past English mistakes, a speaking session on workdays with nags until you reach the target, Whisper transcription, corrections into Daily Speaking Practice and the English habit ticked (`teacher.py`; brief: `02 - Areas/English/English Teacher.md`). Create a bot with @BotFather, put its token in `scripts/.env` as `TEACHER_BOT_TOKEN=...`, press Start in its chat, then `docker compose restart bot`. Corrections and answers need `CLAUDE_CODE_OAUTH_TOKEN` (see above). Shared code for both companion bots: `botkit.py`.

## Habit tracker connection (optional)

Set `HABITS_API_URL` and `HABITS_API_TOKEN` in `scripts/.env` to connect the bot to the Everyday habit tracker app (see the app's README, *Integration endpoints*). Then the bot writes `Habit Consistency` and `Habit Log` in the Discipline area and nags about open habits. The morning brief, Strength reminders and `/train` send the actual routine `.md` note as a Telegram document, preserving its Obsidian wikilinks; the caption also includes an `obsidian://` link to open the synced note. Tune reminders with `CHECKIN_TIME`, `NAG_EVERY`, `NAG_GRACE` and `NAG_UNTIL` (`scripts/.env.example`), then `docker compose restart bot`.
