# CLAUDE.md

@AGENTS.md

## Claude Code specifics

- The vault root is the repository root. It may run on Windows or Linux, so check the platform before using shell-specific syntax. Quote paths: folder names contain spaces (`00 - Inbox`).
- Use `Write` to create new notes in `00 - Inbox`; use `Bash` `mv` (or PowerShell `Move-Item`) to relocate notes when processing the Inbox.
- Use `Glob`/`Grep` to explore the vault instead of reading every file; read whole files only for notes being processed.
- Before moving or renaming notes, Grep for `[[Note Name]]` references so links stay valid.
- Git tracks the system only, never the notes. Notes are backed up daily to Google Drive with 90 days of history (`Drive:Vault-backup/history`). There's still no local undo, so confirm before bulk moves or rewrites and report exactly what changed.
- Scripts run in Docker (`scripts/compose.yaml`: services `bot` and `backup`). Run `docker compose` commands from `scripts/`. In Git Bash, set `MSYS_NO_PATHCONV=1` before a `docker run` with container paths, or Git Bash rewrites `/vault/...`.
- Python on the host is only for zero-token helpers (`scripts/lint.py`, `scripts/backup_if_due.py`). Run fetches that need yt-dlp inside the bot container.
