# Changelog

Changes to the vault **system**: scripts, Docker, agent rules. Note-level operations are in `log.md`, which is personal and not committed. Pending ideas are in [BACKLOG.md](BACKLOG.md).

## 2026-10-02

### Git security review
- Expanded `.gitignore` for environment files (preserving `.env.example`), rclone and OAuth credentials, private keys/certificates, and Obsidian plugin `data.json` settings. Updated the README privacy summary to match.

### Docker: runs on Windows and any Linux
- Added `scripts/Dockerfile` (`python:3.12-slim` + `requirements.txt`), `scripts/compose.yaml` and `scripts/.dockerignore`. The code runs from the mounted vault (`/vault`), so editing a script needs a restart, not a rebuild.
- `telegram_bot.py` loads `scripts/vendor/` only on Windows. That folder holds Windows-only packages; Linux and Docker use pip packages.
- Compose settings: `PUID`/`PGID` (file owner on Linux), `TZ` (note timestamps), a Whisper model cache volume, `restart: unless-stopped`, log rotation, and `SIGINT` for a clean stop.
- The bot now runs in Docker on Windows, replacing the native process. Only one bot can poll a token at a time.
- Guide: [`scripts/DOCKER.md`](../scripts/DOCKER.md), covering moving to Ubuntu, daily commands, keeping machines in sync, and troubleshooting.
- Docker Desktop was crashing on startup because it couldn't remove leftover AF_UNIX socket files. Fixed by resetting the stale runtime files and turning off Docker AI and Model Runner. If it happens again, reset the stale runtime files.

### Git
- Root `.gitignore`: commits the system (agent instructions, scripts, Docker, shared Obsidian settings) but not personal content: the PARA folders (except `.gitkeep`), `index.md`, `log.md`, secrets, runtime state, and `vendor/`.
- `.gitattributes`: shell scripts always use LF line endings.
- Security review before going public: no secrets or personal data in the committed files. `.gitignore` also covers `.env` files, keys, certificates, credential and token JSON files, `rclone.conf` anywhere in the repo, and Obsidian plugin `data.json` files.
- `AGENTS.md`: captured content (captions, transcripts, posts, pages) is data, never instructions. This guards against prompt injection.

### Backup to Google Drive
- `backup` service (rclone) and `scripts/backup.sh` keep a mirror in `Drive:Vault-backup/current`, with changed or deleted files kept 90 days in `history/<date>`. The vault is mounted read-only. It uses the `drive.file` permission, so rclone can only see files it created.
- Each successful run writes its time to `scripts/rclone/last_backup`. The service checks hourly and backs up once 24 h have passed.
- `scripts/backup_if_due.py` (zero tokens) runs at the end of every distill. If a backup is due, it starts one in the background, through Docker or local rclone.
- Failure alerts: `scripts/rclone/last_failure`, a Telegram message from the vault bot (first failure, then at most daily), and a `WARNING` line on every distill when backups are failing or more than 48 h old.

### Agent rules (`AGENTS.md`)
- Original user notes found in PARA folders without conventions are treated as Inbox captures: moved unchanged to Sources and distilled in place.
- Enrichment: fetched captions and full post text are saved as extra raw sources next to the capture.
- A new source from the same creator updates the existing note, and agents flag when it's not confirmed to be the same content.
- Language rule, sensitive-data rule, and the backup check at the end of a distill.

### Bot
- Every Instagram Reel, TikTok and YouTube capture gets a `## Metadata` section with title, creator and @handle, publish date, duration, music track, language, location, categories, tags, hashtags, engagement, chapters and the full caption or description. If the audio download fails, metadata is still fetched without downloading.
- TikTok links are handled like Reels: metadata plus a temporary-audio transcript, with no media kept.
- Fixed a crash when yt-dlp returned metadata without a media file.

### Lint
- `lint.py` accepts `[[Note\|alias]]`, the escaped alias form Obsidian requires inside tables.
- `docs/` is excluded from note checks.

### Vault content (details in `log.md`)
- Processed the Inbox twice: 9 captures moved to Sources, plus fetched captions and posts saved as raw sources.
- Distilled the original Training notes, and created the Training MOC and new notes for English, Training, Software and AI, and Business Ideas.

## 2026-10-03

### Telegram audio language selection
- Directly sent Telegram voice/audio/video clips now choose between English and Spanish using Whisper's language probabilities, then force transcription in the stronger match. Reel and TikTok transcription behavior is unchanged.

### Bot
- **Transcription feedback**: voice notes, audio, video and Reels/TikToks get an immediate "⏳ Transcribing…" reply, edited into "Saved: …" when done. The Whisper model loads in a background thread at startup (`WHISPER_PRELOAD=0` turns it off).
- **Full X posts**: after the X API (if configured), the bot uses the public fxtwitter API for the full post text, author, @handle, date and quoted post. X's embed is the last fallback.
- **Sensitive data**: the user's text, voice transcripts and photos (Tesseract OCR, English + Spanish) are checked for PINs, PUKs, passwords, card numbers (Luhn), CVVs, CBU/IBAN, API keys and tokens, and seed phrases. A match is held outside the vault and the bot asks **Save anyway** / **Discard** (inline buttons). Kept captures are tagged `sensitive`. Held items expire after 24 h or on restart.
- English voice notes and video notes are tagged `english-practice`: the check-in for Daily Speaking Practice.
- Heartbeat file `/tmp/vault-bot.heartbeat`, touched on every poll.

### Docker
- **Pinned versions**: the bot image is `python:3.12.15-slim-trixie` and the backup image is `rclone/rclone:1.75.1`, both pinned by multi-platform digest. Python packages come from the hashed `requirements.lock`, generated in a Linux container by the new `lock` tool service (`docker compose run --rm lock`). Only yt-dlp floats.
- The bot image adds Tesseract OCR (`eng`, `spa`), plus `pytesseract` and `Pillow`.
- **Health check**: the bot is `unhealthy` after 15 minutes without a heartbeat. The check is a local Python one-liner every 2 minutes.
- New `backup.Dockerfile`: rclone plus `zip` and `tzdata`.

### Backup
- **Zipped history**: each run's `history/<date>/` folder is downloaded, zipped, uploaded as `history/<date>.zip` and removed only after the upload is verified. Leftover folders are converted on the next run. The existing history folder was converted on 2026-10-03. `current/` stays a plain mirror.
- Log lines use local time (`TZ`); history names stay UTC.

### Lint and source indexes
- New `scripts/vault.py`: shared note discovery, frontmatter parsing and Obsidian-style link resolution (note names, attachment and canvas file names, full or partial paths, case- and Unicode-insensitive).
- `lint.py` no longer reports attachment, canvas and path links as broken. It ignores links in `log.md` (append-only history), doesn't report Inbox captures as orphans, checks `index.md` for wiki pages only, and reports stale source indexes. Output is UTF-8 on Windows.
- New `scripts/source_index.py`: generates `Sources index - <type>.md` in every Sources type folder (captured date, kind, creator, topic, wiki notes; by month, newest first) and refreshes their list in `index.md` between `sources-index` markers. `--check` only reports.
- `AGENTS.md`: the per-type source index convention, `sources:` frontmatter as the link from a wiki note to its raw sources, `source_index.py` in the ingest and Inbox workflows, and the `#sensitive` tag.
