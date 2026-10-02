#!/usr/bin/env python3
"""Start a Google Drive backup in the background if the last one is older than BACKUP_INTERVAL_HOURS (default 24).
Agents run this at the end of every distill (see AGENTS.md). Zero tokens; prints one line and returns at once.
Run: python scripts/backup_if_due.py [--force]
Last backup time: scripts/rclone/last_backup ("<epoch> <UTC time>", written by backup.sh)."""
import os, shutil, subprocess, sys, time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
LAST = SCRIPTS / "rclone" / "last_backup"
FAIL = SCRIPTS / "rclone" / "last_failure"  # written by backup.sh while backups fail
HOURS = float(os.environ.get("BACKUP_INTERVAL_HOURS", "24"))
DETACH = {"creationflags": 0x08000000 | 0x00000008} if os.name == "nt" else {"start_new_session": True}  # no window / own session


def last_backup():
    try:
        return int(LAST.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def start(cmd, env=None):
    subprocess.Popen(cmd, cwd=SCRIPTS, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **DETACH)


def warn(last, age_h, when):
    """Print a warning when backups are failing or overdue (2x the interval)."""
    fail = FAIL.read_text(encoding="utf-8").split() if FAIL.is_file() else []
    if fail and (not last or int(fail[0]) > last):
        print(f"WARNING: BACKUP FAILED (latest failure {fail[-1]}); last good backup: {when}. "
              "Check `docker compose logs backup`. If the error mentions client_id, Google retired rclone's shared client.")
    elif last and age_h >= 2 * HOURS:
        print(f"WARNING: BACKUP OVERDUE: last good backup {when} ({age_h:.0f} h ago). Is the backup container running?")


def main():
    last = last_backup()
    age_h = (time.time() - last) / 3600 if last else None
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "never"
    warn(last, age_h, when)
    if "--force" not in sys.argv and age_h is not None and age_h < HOURS:
        print(f"Backup not due: last backup {when} ({age_h:.1f} h ago).")
        return
    if not (SCRIPTS / "rclone" / "rclone.conf").is_file():
        print("Backup due, but Google Drive is not connected (scripts/DOCKER.md, 'Backup to Google Drive').")
        return
    if shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
        start(["docker", "compose", "run", "--rm", "--no-deps", "backup", "--once"])
        how = "Docker"
    elif shutil.which("rclone") and shutil.which("sh"):
        start(["sh", str(SCRIPTS / "backup.sh"), "--once"],
              env={**os.environ, "VAULT_DIR": str(SCRIPTS.parent), "RCLONE_CONFIG_DIR": str(SCRIPTS / "rclone")})
        how = "local rclone"
    else:
        print("Backup due, but neither Docker nor rclone is available on this machine.")
        return
    print(f"Backup started in the background ({how}); last backup: {when}.")


if __name__ == "__main__":
    main()
