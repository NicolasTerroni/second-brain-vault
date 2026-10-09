# Changelog

Changes to the vault **system**: scripts, Docker, agent rules. Note-level operations are in `log.md`, which is personal and not committed. Pending ideas are in [BACKLOG.md](BACKLOG.md).

## 2026-10-09

### The agents learn from feedback; the Teacher teaches before testing
- Agent definitions (`.claude/agents/coach.md`, `english-teacher.md`) rewritten: a professional profile, a personality, and *Keep improving*: the owner's corrections go to a dated *Learned from your feedback* section in each brief, read before everything else.
- `botkit`: `brief()` includes *Learned from your feedback*; `/feedback` (both bots) and phrases like "from now on…", "don't…", "a partir de ahora…" (with a confirm button, `fb:y`/`fb:n`) add a dated line marked *to apply*. AGENTS.md: the Assistant implements *to apply* lines at every organize.
- Teacher: a new correction comes as a lesson (better version, why, and for common patterns a plain-words rule with an off-topic example: `teacher.RULES`, partly from `grammar.REWRITE`), then you write a sentence about your work; known ones are recall with 💡 Teach me (`dt:`); "I don't know"/"no sé"/"idk" teaches (drills and rewrites). Text answers count all day. Without AI, the lesson's self-check after being taught doesn't regrade (`dk:`).
- `workenglish.py`: 45 professional phrases for a data engineer (casual → professional, key words explained); one a day with the drill, and the speaking prompt asks you to use it. Reference note generated: `Professional English for Data Engineers.md`. With Claude, corrections also give the professional version of casual-but-correct English.

### The Coach logs other sports; the Teacher corrects book photos
- Coach (`trainer.py`): "played football yesterday, 90 min" (English or Spanish) or `/activity` logs a sport. `parse_activity` reads the sport, the day (yesterday, a weekday) and the duration; buttons ask for what's missing (how long, how hard). Rows go to `02 - Areas/Training/Activity Log.md`. A strength day spent on a sport records the reason in Habit Reasons and skips the next morning's "what got in the way?"; a hard sport on a training day offers to move the session (`ax:`) or the 15-minute version. The weekly report lists them. Questions and plans ("can I play tomorrow?") still go to Claude.
- Teacher (`teacher.py`): photos (or image files, albums grouped by `media_group_id`) of the Business English book are saved to `00 - Inbox/attachments`, read by Claude Code (`Read` on the images), and answered with a score and corrections; corrections go to Daily Speaking Practice (so drills and grammar sets use them) and a row to `02 - Areas/English/Business English Workbook.md`. Raw capture tagged `business-english-book` (+ `corrected`). Answered even in quiet hours. Without Claude Code the capture waits for the english-teacher agent at organize.

## 2026-10-08

### The Coach and the English Teacher become agents
- `.claude/agents/assistant.md`, `coach.md`, `english-teacher.md`: agent definitions (who each is, what it owns, its writing rules). `botkit.Bot(agent=..., writes=...)` runs Claude Code with `--agent` and, for questions, read access to the whole vault plus `Edit(<folder>/**)` permission rules limited to the bot's own folders (Coach: Training; Teacher: English and the C1 project; Assistant: read-only `/ask`). Questions can now ask the Coach or the Teacher to record something in their notes. `stdin` is closed so Claude Code doesn't wait 3 s.
- `AGENTS.md` *The agent team*: ownership table and how organize hands Training and English captures to the coach and english-teacher agents.
- Coach: evening protein check (`TRAINER_PROTEIN_TIME`, 20:00) with +20/+30/+40 g buttons; `team.OWNED_ALIASES` gives it `protein`. ✅ on a quantity habit logs exactly what's left today (`hq:` quick amounts in `common_button`).
- `vault.SKIP` ignores `.claude/` (the agent files share names with the Coach and Assistant notes).

### /todo shows titles only
- `/todo` lists every open task as a one-line title (`companion.task_title`), grouped by section, instead of this week's full descriptions; "Done" replies and the /done choices use titles too.

### Each bot nags about what it owns
- `botkit.py`: `nag_habit` (an app habit, every N min from a start time until ticked, with ✅ Done / ⏰ 1 h), `remind_tasks` (open Pending Tasks that link to the bot's notes, twice a day, ✅ per task) and `common_button`. `team.py`: `OWNED_ALIASES` (Coach: workout + mobility; Teacher: English), `related_tasks` (by wikilinks; dated tasks wait until 2 days before; the Assistant keeps this week's unowned tasks).
- Coach: daily mobility nags (09:00 or the app reminder, every 30 min, 6 max, 📄 sends the routine), training tasks at 10:00 and 19:30, a rest-day note on the routine (Sunday: tonight's planning).
- Teacher: English every day (`TEACHER_SPEAK_DAYS` default Mon-Sun), English tasks at 09:00 and 20:00, reminders for an unanswered drill, rewrite or quizzes every 2 h (3 a day), replacing the single grammar nudge.
- Assistant: its nags skip every habit a teammate owns (`coach.open_today` uses `team.owned_aliases`); this week's other tasks at 13:00 and 19:00.

### The capture bot becomes the Assistant; the three bots work as a team
- `scripts/team.py`: each bot reads the others' state (training today and this week, English spoken vs target, grammar left, a session running). `botkit.brief()` adds the team (members, roles, today's reports, "stay in your lane") to every Claude prompt.
- Handoffs: the Teacher doesn't nag during a Coach session; after a session the Coach adds what English is still open; after the speaking target the Teacher adds training still waiting.
- `telegram_bot.py` (Assistant, persona in `02 - Areas/About Me/Assistant.md`): the brief shows the daily habits it owns (Strength and English filtered out when their bots are on), a goal in focus (About Me table), this week's top task (bold title), deadlines and a "Your team today" block; no routine attachment when the Coach is on. New `/goals`, `/team`, and a working `/ask` (Claude Code reads the vault read-only, in a thread). `/train` and `/standup` point to the Coach and the Teacher. The trainer's bot name is now "Coach".

### English teacher: grammar sets
- `scripts/grammar.py` + `teacher.py`: every day at `TEACHER_GRAMMAR_TIME` (13:00) a set of 4 Telegram quiz polls and 1 rewrite exercise built on the mistakes in Daily Speaking Practice (topics weighted by how often they appear there and by your error rate). Levels 1-3 by accuracy over the last 10 answers. Claude Code writes fresh sets from your corrections when connected (JSON, validated; falls back to the bank). One nudge 4 h later if unanswered; score per set, per topic and in the Sunday report; `/grammar` on demand.
- `botkit.py`: `send_poll` (quiz polls) and `poll_answer` updates.
- Rewrites explain their grammar term in plain words and show a worked example on a different topic; Claude-written sets without both are rejected, and the teacher's persona never uses a grammar term unexplained (your feedback).

### English teacher (third Telegram bot, same container) and a shared base
- `scripts/botkit.py`: Telegram, state, send-once keys, quiet hours, poll loop and the Claude Code voice, shared by the companion bots. `trainer.py` now runs on it (behaviour unchanged, same tests).
- `scripts/teacher.py`: every morning a drill on one past correction from Daily Speaking Practice (spaced repetition: 1, 3, 7, 14, 30 days; missed ones come back tomorrow). Speaking days (Mon-Fri): a voice-note session at 18:00 with a topic (standup, then harder exercises by level), nags every 30 min until the day's target, never during quiet hours. Voice notes are transcribed with the capture bot's Whisper, saved as raw captures (`teacher`, `english-practice`, `corrected` when Claude Code corrected them) and corrected into Daily Speaking Practice; reaching the target ticks English in the habit tracker. Every 3 days in a row on target: +15 s (2:00 → 6:00) and harder exercises. Tired = 1 minute today; skip reasons go to Habit Reasons; Sunday report; questions answered from the notes. A message counts as a drill answer only as a reply to the drill, or when short and soon after it.
- Both bots ask about the session when the habit is ticked in the app without them (the coach stops nagging and can log the sets afterwards); the capture bot no longer nags about English while the teacher is on.
- `AGENTS.md`: teacher captures tagged `corrected` aren't corrected again at organize.

## 2026-10-07

### Personal trainer (second Telegram bot, same container)
- `scripts/trainer.py`: plans the week (Sunday, 3 days, never back to back, auto-confirmed Monday morning; fewer than the target asks for a reason), asks the training time the evening before and in the morning, sends a heads-up with the routine note, "Go", then nags every 20 min (6 max, quiet 22:30-07:00, 15-minute version from the 2nd nag). Guided session: each exercise of the day in canvas order with its target and last result, one tap per set; on finish it adds the session row, updates *Last session*, flags records and top-of-range exercises, ticks Strength in the app. Missed days are followed up the next morning and today becomes a training day; "can't today" moves the session (tired: 15-minute version first; sick or pain: rest plus a morning check-in). Reasons go to Habit Reasons. Toggles Strength's non-negotiable flag per day in the app.
- Runs as a thread of `telegram_bot.py` when `TRAINER_BOT_TOKEN` is set; state in `scripts/trainer.state.json` (git-ignored). The capture bot's coach stops nagging about Strength while it's on (`coach.open_today`).
- Optional voice: Claude Code (pinned 2.1.293, now in the image with Node.js) in print mode with the persona from `02 - Areas/Training/Coach.md`; read-only tools for free questions. Falls back to templates without `CLAUDE_CODE_OAUTH_TOKEN`.
- Rule in `AGENTS.md`: coach-logged sessions are finished at organize (next targets, variation step-ups), never duplicated by a Workout capture.

### Bot: focus lock from the habit tracker
- `habits.lock_status()` reads the app's new `GET /api/integration/lock` (same token as the other integration routes); `habits.lock_line()` turns it into one line.
- `/lock` shows whether distracting apps are locked right now and what unlocks them. The morning brief adds that line when the lock is on.
- After a habit is logged from Telegram (a plain message, or a Workout/English note), the bot says "apps unlocked" once a day when that log met the unlock rule.

## 2026-10-06

### Send the Obsidian routine note in training reminders
- The morning brief, Strength coach reminders and `/train` now send the original routine `.md` as a Telegram document, with a caption naming the upcoming day and an Obsidian URI for the synced note.
- The routine canvas continues to choose Day 1 or Day 2 from the training log; the Markdown attachment preserves its wikilinks and exercise references.

## 2026-10-05

### Habit coach: the bot pushes, logs and asks why
- App: `POST`/`DELETE /api/integration/entries` (same token) so the bot can log and undo entries.
- `scripts/habits.py` also writes `Habit Log` (every day's values and entry notes), and reads two new columns of *Habits in the app* in `Discipline`: **Goal** (the review groups habits by goal and questions habits without one) and **Also called** (words that log the habit from a message).
- New `scripts/coach.py`: after each app reminder (plus `NAG_GRACE`, 30 min) the bot nags every `NAG_EVERY` (60) minutes about habits still open until `NAG_UNTIL` (23:00), with ✅ / ⏭ Skip / 😴 Snooze buttons; an evening check-in at `CHECKIN_TIME` (21:00); a last call before the day ends. Weekly habits are only pushed when the week needs it (at risk, or 2+ days since the last session).
- Plain messages like "water 500" or "did mobility" log the habit (with Undo / "It was a note"); a "Workout…" note ticks strength and an English voice note ticks English.
- Skipping asks "what got in the way?"; replies (voice too) go to `Habit Reasons`. The morning brief asks the same for yesterday's misses.
- Morning brief: one focus first, short, with buttons (training, habits, tasks, a question). Sunday review: counts by goal, your reasons, and one proposed change to approve, which is written to Discipline's review log.
- Command menu cut to /brief, /standup, /review, /help; the other commands still work when typed.

### Habit consistency from the habit tracker app
- New `scripts/habits.py` (standard library, zero tokens). It reads the habit tracker's read-only endpoint (`GET /api/integration/habits`, bearer token in `HABITS_API_TOKEN`, URL in `HABITS_API_URL`), scores each habit against its target, and writes `02 - Areas/Discipline/Habit Consistency.md`. That note shows this week, the last 7 and 28 days, streaks, a weekly history, and the habits that need attention, each with its fix. Targets and fixes come from a new *Habits in the app* table in `Discipline`: "every day", "workdays" or "N× a week", with an optional start date, so weekly habits such as Strength aren't counted as missed on rest days. 🔴 means under 60%, or missed twice in a row. The last good response is cached in `scripts/habits.cache.json` (git-ignored), which is used when the app can't be reached.
- Bot: the morning brief adds yesterday's habits and up to two slipping habits with their fix; the Sunday review adds this week's counts per habit and the one most worth fixing; new `/habits` command. Without the two env vars, all of this stays off.
- `AGENTS.md`: Habit Consistency is generated; at each organize and weekly review the agent rewrites the fix of a habit that stays 🔴.

### Windows bot startup
- Added `Start Docker for Vault.lnk` to this Windows user's Startup folder to run `scripts/windows/start-docker.ps1` at sign-in; no Docker startup entry was present. Docker Desktop exited during verification, so container startup remains to be confirmed.

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

### Bot: training
- `/train` (`/train 1`, `/train 2`): the next training session, exercise by exercise, read from the routine canvas's Day 1 / Day 2 cards. "Next" is the opposite of the last session in the strength log's session table.
- The morning brief adds a training line: the next session, this week's sessions out of the routine's `target_per_week`, the last session, a nudge after 3+ days without one, or the start date before `start:`.
