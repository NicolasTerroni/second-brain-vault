# Backlog

Proposed improvements to the vault system, most important first. Agents: when an item is done, tick it, add the date, and add an entry to [CHANGELOG.md](CHANGELOG.md).

## 1. Back up the notes
- [x] Daily Google Drive backup with 90 days of history (rclone container). Done 2026-10-02.
- [x] Backup at the end of each distill when 24 h have passed (`scripts/backup_if_due.py`). Done 2026-10-02.
- [x] Failure alerts: Telegram message, plus a warning on every distill. Done 2026-10-02.
- [ ] **Own Google OAuth client ID.** rclone's shared client is being retired during 2026, and backups will fail when that happens; the alert will report it. Creating your own client is free (Google Cloud Console → Drive API → Desktop OAuth client, then publish the app) and takes about 5 minutes. Deferred by the user on 2026-10-02.

## 2. Bot: capture what agents now fetch by hand
- [x] **Video metadata.** Done 2026-10-02: Reels, TikToks and YouTube get a full `## Metadata` section (creator, date, duration, music, tags, hashtags, chapters, full caption), and TikTok is handled like Reels.
- [ ] **Music detection.** Use faster-whisper's `no_speech_prob` and language probability to mark a transcript as "background music" instead of saving made-up text (one Reel produced invented Arabic).
- [ ] **Full X posts without an API key.** Fall back to the public fxtwitter API for the full text and any quoted post. Captures are currently cut off at "…".
- [ ] **Transcription feedback.** Reply "transcribing…" straight away, and load the Whisper model at startup. The first voice note took about 2 minutes with no feedback.
- [ ] **Sensitive data warning.** Warn before saving PINs, PUKs, passwords or card numbers in photos or text.

## 3. Routing script for the Inbox
- [ ] `scripts/route_inbox.py`: move each raw capture to `Sources/<type>` based on its tags, check for duplicate names, move attachments, and add index and log lines. The agent then only writes the distilled notes.

## 4. Lint and index
- [x] Lint accepts escaped table aliases (`[[Note\|alias]]`). Done 2026-10-02.
- [ ] Lint resolves attachment links (`.oga`, `.jpg`) and path-style links, which are currently false positives.
- [ ] Generate the Sources catalog in `index.md`, or give each source type its own index, so `index.md` lists only wiki pages.

## 5. Agent rules
- [x] Rules for: original notes in PARA without conventions, captions and full post text as extra raw sources, same-creator updates, language, sensitive data, backup check. Done 2026-10-02 (see `AGENTS.md`).

## 6. Make Areas actionable
- [ ] **Training:** a weekly plan in the Training MOC (mobility every morning; Cindy or the basic AMRAP 3 times a week). Turn "15 pull-ups" into a project with a deadline.
- [ ] **English:** make Daily Speaking Practice a habit in the habit tracker, with the daily voice note to the bot as the check-in.
- [ ] **Link-only notes:** distill or archive `sellable ideas` and `youtube channels`.

## 7. Docker polish
- [ ] Pin versions with a lock file, so a rebuild can only change yt-dlp, which you update on purpose.
- [ ] Health check for the bot, so `docker compose ps` shows "unhealthy" if polling stalls.
- [ ] Turn on Docker Desktop's "start when you sign in" on Windows, so the bot survives a reboot.
- [ ] The rclone image (Alpine) has no tzdata, so backup logs use UTC. Either install tzdata or keep the explicit `Z` timestamps.
