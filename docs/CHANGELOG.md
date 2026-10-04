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

### Agent rules: personal context
- `AGENTS.md` has a new **Personal context** section. `02 - Areas/About Me/About Me.md` is the hub agents read for personal queries and update whenever a capture reveals something about the user; contact details and sensitive data stay out. Open questions live in `Questions About Me.md`, and answers arrive as voice notes starting with "Question N".
- `AGENTS.md`: `Pending Tasks.md` (in About Me) is the user's task list. Agents add the tasks only the user can do, with a link to their note, and tick them when reported done.

### Bot: companion features
- **Replies add to a capture**: replying to the "✅ Saved" message appends a `## Added <time>` section (text, voice transcript, photo) to that note while it's in the Inbox. Once it's organized, the reply becomes a new capture linked to it. The Saved message explains this.
- **Area buttons** after each save add `area/<slug>` tags as classification hints.
- **Commands** (`setMyCommands`, `/help`): `/todo`, `/add`, `/done` (edit `Pending Tasks.md`), `/q` (next open question; the reply is saved as `#question-N`, with a "standup instead" button), `/standup` (reply saved as `#standup`), `/brief`, `/review`, `/inbox`, `/status`, `/backup`. `/ask`, `/organize` and `/did` reply with a "coming soon" description.
- **Scheduled messages**: a morning brief at `BRIEF_TIME` (minimums from Discipline with staged start dates, the English voice-note reminder with yesterday's check-in, project `deadline:` countdowns, a past correction to review, the top task) and a weekly review prompt at `REVIEW_TIME` (reply saved as `#weekly-review`).
- New `scripts/companion.py` (stdlib, no tokens) holds the vault logic. Bot state is in `scripts/telegram.state.json` (git-ignored).
- **Backup on request**: the backup loop checks every minute for `scripts/rclone/backup_requested` (written by `/backup`) and confirms in Telegram; scheduled backups still run every `BACKUP_INTERVAL_HOURS`.
- `AGENTS.md`: how to organize `#question-N`, `#standup` (corrections into Daily Speaking Practice), `#weekly-review`, `area/` hints, `## Added` sections, and tasks added from Telegram.

## 2026-10-04

### Time zone
- The bot and backup run on `Europe/Madrid` (`TZ` in `scripts/.env`), so note names, the morning brief and the weekly review follow Barcelona time. Docs and examples now use `Europe/Madrid`.
- Telegram task text keeps arrows that are part of the text (e.g. "Settings → Time & language"); only `→ [[link]]` pointers are stripped.

### Docker Desktop on Windows
- After the reboot on 2026-10-04, Docker Desktop crashed again on stale AF_UNIX socket files (`Docker\run\dockerInference`, `docker-secrets-engine\engine.sock`, Windows error 1920). New `scripts/windows/start-docker.ps1` moves those folders aside and starts Docker. `DOCKER.md` troubleshooting covers it and the sign-in start not firing.

### Agent rules: workout logging
- `AGENTS.md`: a capture starting with "Workout" is logged in `Full-Body Strength Log`, and the agent updates each exercise's next target using the log's progression rules.

### Seeing video content
- New `scripts/reel_frames.py` (run in the bot container) downloads a Reel/TikTok/Short temporarily and writes contact sheets of about 24 timestamped frames, then deletes the video. Agents view them to identify moves that the audio doesn't name.
- `AGENTS.md`: the user approved this on 2026-10-04 for videos whose transcript and caption don't name the content. Sheets are deleted after viewing, and nothing visual is stored in the vault.
- `reel_frames.py --frames N` for dense sampling (e.g. 60 frames over 16 s). The AGENTS.md rule notes `MSYS_NO_PATHCONV=1` for the cleanup, which Git Bash had silently skipped.
- `reel_frames.py --segment label:start-end --size N`: one collage per time range (e.g. per exercise) from a single download. `AGENTS.md` exception: frames may be stored in a note's `Attachments/` when the user asks for them.
