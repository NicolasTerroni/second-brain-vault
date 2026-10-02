# Second Brain + LLM Wiki

An [Obsidian](https://obsidian.md) vault run as a **Second Brain** and maintained by AI agents as an **LLM Wiki**:

- Organized with Tiago Forte's [PARA](https://fortelabs.com/blog/para/) method, with notes flowing through his **CODE** workflow (Capture → Organize → Distill → Express).
- Kept up by agents following Andrej Karpathy's [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern. They file raw sources, write linked wiki notes, and keep an index and a log up to date.

This repository holds the **system**: agent instructions, capture and maintenance scripts, and Docker setup. The notes themselves are personal and are never committed (see [Privacy](#privacy)).

## How it works

```
 phone ──► Telegram bot ──► 00 - Inbox ──► agent "process the inbox" ──► raw source  → 03 - Resources/Sources/<type>/
 (text, voice, links,        (raw capture)                            └─► wiki note   → Projects / Areas / Resources
  photos, files)                                                        └─► index.md + log.md updated
                                                                        └─► Google Drive backup if 24 h have passed
```

| Folder | Holds |
|---|---|
| `00 - Inbox` | Raw captures. Temporary: processed, not stored. |
| `01 - Projects` | Efforts with a goal and an end state |
| `02 - Areas` | Ongoing responsibilities (training, English, software and AI, finance, ...) |
| `03 - Resources` | Reference topics, plus `Sources/`, the immutable raw sources |
| `04 - Archive` | Inactive items. Nothing is deleted. |

The three layers of the LLM Wiki:
1. **Raw sources** in `03 - Resources/Sources/`. Never edited.
2. **Wiki notes**, written by the agent, in the PARA folders.
3. **Schema**: [`AGENTS.md`](AGENTS.md), the rules any agent follows, and [`CLAUDE.md`](CLAUDE.md), Claude Code specifics.

## Features

- **Telegram capture bot** ([`scripts/telegram_bot.py`](scripts/telegram_bot.py)): text, links, voice notes and audio (transcribed locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper)), photos and files become notes in the Inbox.
  - Instagram Reels are transcribed without keeping the video.
  - YouTube links keep their metadata.
  - X posts keep their text.
- **Agent workflows** in [`AGENTS.md`](AGENTS.md): Inbox processing, ingest, query, lint, and rules for links, transcripts, sensitive data and language.
- **Zero-token helpers**:
  - [`scripts/lint.py`](scripts/lint.py) finds broken links, orphans, pages missing from the index, and missing frontmatter.
  - [`scripts/backup_if_due.py`](scripts/backup_if_due.py) runs the backup check at the end of every distill.
- **Google Drive backup** ([`scripts/backup.sh`](scripts/backup.sh), rclone):
  - Runs daily, with 90 days of history for changed or deleted files.
  - Mounts the vault read-only and uses the `drive.file` permission.
  - Alerts through Telegram if a backup fails.
- **Docker**: the same setup runs on Windows (Docker Desktop), Ubuntu or any Linux server.

## Quick start

Requirements: Docker with Compose, and a Telegram bot token from [@BotFather](https://t.me/BotFather).

```bash
git clone <this-repo> Vault && cd Vault/scripts
cp .env.example .env        # set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_IDS (your Telegram user id)
docker compose up -d --build
docker compose logs -f bot  # expect "Bot running"
```

Open the `Vault` folder in Obsidian, send your bot a message, and it appears in `00 - Inbox`. Then ask your agent (Claude Code, or any agent that reads `AGENTS.md`) to **"process the inbox"**.

Connect the Google Drive backup once by following [`scripts/DOCKER.md` → Backup to Google Drive](scripts/DOCKER.md#backup-to-google-drive). The same guide covers moving to a Linux server, everyday commands and troubleshooting.

## Repository layout

```
AGENTS.md            rules for agents (PARA, workflows, wiki operations)
CLAUDE.md            Claude Code specifics; imports AGENTS.md
README.md            this file
docs/BACKLOG.md      planned improvements
docs/CHANGELOG.md    system changes
scripts/
  telegram_bot.py    Telegram → Inbox capture
  lint.py            vault health check
  backup.sh          Google Drive backup loop (rclone container)
  backup_if_due.py   start a backup if 24 h have passed
  Dockerfile, compose.yaml, .dockerignore, requirements.txt
  DOCKER.md          setup on Windows and Linux, backup, troubleshooting
  .env.example       settings template
.obsidian/           shared Obsidian settings (workspace state excluded)
00 - Inbox/ … 04 - Archive/   PARA folders; only .gitkeep is committed
```

## Privacy

`.gitignore` excludes everything personal:

- **Notes:** the contents of the PARA folders, plus `index.md` and `log.md`, which list note titles and activity.
- **Secrets:** environment files (except `.env.example`), cookies, rclone configuration, OAuth/token/credential JSON files, private keys and certificates, and Obsidian plugin `data.json` settings.
- **Runtime and per-machine files:** bot offset, logs, `vendor/`, `.venv/`, Obsidian workspace state.

The notes are backed up to Google Drive instead of git.

## Roadmap

See [`docs/BACKLOG.md`](docs/BACKLOG.md). Next up:
- the bot saving Reel captions and full X posts itself;
- detecting music-only transcripts;
- an Inbox routing script.
