"""Shared plumbing for the companion bots (trainer.py, teacher.py): one Telegram bot each, run as threads of the capture
bot. Telegram calls, a JSON state file, "send once" keys, quiet hours, the poll loop, and Claude Code as an optional
voice that reads the bot's brief (Persona and Profile sections of its vault note). Zero tokens unless Claude Code is
connected (`claude` on PATH and CLAUDE_CODE_OAUTH_TOKEN set)."""
import json, os, re, secrets, shutil, subprocess, time, urllib.parse, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
import coach
import companion
import habits
import team
from vault import ROOT, read

SCRIPTS = Path(__file__).resolve().parent


def env(key, default=""):
    return habits.env(key) or os.environ.get(key, "") or default


def owner():
    ids = [int(x) for x in env("TELEGRAM_ALLOWED_IDS").replace(" ", "").split(",") if x]
    return ids, (ids[0] if ids else None)


def hhmm(s):
    h, m = map(int, s.split(":"))
    return h, m


def at(d, s):
    h, m = hhmm(s)
    return datetime(d.year, d.month, d.day, h, m)


def in_range(t, span):
    """True when t falls in "HH:MM-HH:MM" (may cross midnight)."""
    start, end = span.split("-")
    a, b = at(t.date(), start), at(t.date(), end)
    return t >= a or t < b if a > b else a <= t < b


class Bot:
    def __init__(self, name, token_key, note, prefix, agent=None, writes=()):
        self.name = name
        self.agent = agent       # its definition in .claude/agents/<agent>.md: who it is, what it owns, the vault rules
        self.writes = writes     # the only vault folders it may edit (Claude Code permission rules enforce it)
        self.token = env(token_key)
        self.allowed, self.owner = owner()
        self.note = note                                    # the vault note with ## Persona and the profile section
        self.state_file = SCRIPTS / f"{prefix}.state.json"
        self.offset_file = SCRIPTS / f"{prefix}.offset"
        self.ai = env(f"{prefix.upper()}_AI", "claude").lower()
        self.S = self.load()

    # ---------- state ----------
    def load(self):
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.S, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_file)

    def once(self, key, now):
        """True the first time a key is seen: a scheduled message is never sent twice."""
        sent = self.S.setdefault("sent", {})
        if key in sent:
            return False
        sent[key] = now.isoformat(timespec="minutes")
        for k in list(sent)[:-400]:
            del sent[k]
        return True

    # ---------- Telegram ----------
    def api(self, method, **params):
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}", data=data)
        with urllib.request.urlopen(req, timeout=70) as r:
            return json.load(r)["result"]

    def send(self, text, buttons=None, edit=None):
        """Send (or edit `edit` in place). buttons: rows of (label, callback data). Returns the message id or None."""
        params = {"chat_id": self.owner, "text": text}
        if buttons:
            rows = buttons if isinstance(buttons[0], list) else [buttons]
            params["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]})
        try:
            if edit:
                self.api("editMessageText", message_id=edit, **params)
                return edit
            return self.api("sendMessage", **params)["message_id"]
        except Exception as e:
            if edit and "not modified" in str(e):
                return edit
            print(f"{self.name}: send failed:", e)
            return None

    def send_poll(self, question, options, correct, explanation=""):
        """A Telegram quiz (one tap answers; Telegram shows right/wrong and the explanation). Returns the poll id or None."""
        try:
            r = self.api("sendPoll", chat_id=self.owner, question=question[:300], type="quiz", is_anonymous="false",
                         options=json.dumps([{"text": o[:100]} for o in options]), correct_option_id=correct,
                         explanation=explanation[:200])
            return r["poll"]["id"]
        except Exception as e:
            print(f"{self.name}: poll failed:", e)
            return None

    def send_file(self, path, caption):
        """A vault note as a document (Markdown and wikilinks preserved)."""
        boundary = "----Bot" + secrets.token_hex(12)
        body = bytearray()
        for name, value in (("chat_id", str(self.owner)), ("caption", caption)):
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{Path(path).name}\"\r\n"
                    f"Content-Type: text/markdown\r\n\r\n".encode())
        body.extend(Path(path).read_bytes())
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendDocument", data=bytes(body), method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=70):
                pass
        except Exception as e:
            print(f"{self.name}: file send failed:", e)

    def download(self, file_id, dest):
        """A file sent to this bot (voice note…) saved at dest + the file's extension. Returns the path."""
        info = self.api("getFile", file_id=file_id)
        dest = Path(f"{dest}{Path(info['file_path']).suffix}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}", dest)
        return dest

    # ---------- the voice (Claude Code, optional) ----------
    def brief(self):
        """## Persona plus the profile section (## Athlete / ## Student / ## Owner) of the bot's note, and the team: its system prompt."""
        text = read(self.note) if self.note.exists() else ""
        parts = re.findall(r"^## (Persona|Athlete|Student|Owner)\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
        own = "\n\n".join(f"{h}:\n{companion.plain(b).strip()}" for h, b in parts) or "You are a demanding, caring coach."
        try:
            return own + "\n\nTeam:\n" + team.context(self.name)
        except Exception as e:  # never let a teammate's state break this bot
            print(f"{self.name}: team context failed:", e)
            return own

    def ai_on(self):
        return self.ai != "off" and bool(shutil.which("claude")) and bool(env("CLAUDE_CODE_OAUTH_TOKEN"))

    def claude(self, prompt, tools=False, model="haiku", timeout=90):
        """Claude Code in print mode, or None when it isn't connected or fails (callers fall back to templates)."""
        if not self.ai_on():
            return None
        cmd = [shutil.which("claude"), "-p", prompt, "--model", model, "--output-format", "text", "--append-system-prompt", self.brief()]
        if self.agent and (ROOT / ".claude" / "agents" / f"{self.agent}.md").exists():
            cmd += ["--agent", self.agent]
        if tools:  # read the whole vault; write only in its own folders (when it has any)
            allowed = ["Read", "Grep", "Glob"] + [f"Edit({path}/**)" for path in self.writes]  # Edit(path) covers every file write
            cmd += ["--allowedTools", ",".join(allowed), "--max-turns", "20"]
            cmd += ["--disallowedTools", "Bash,NotebookEdit,WebFetch,WebSearch,Task"]
        else:
            cmd += ["--max-turns", "1", "--disallowedTools", "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Task"]
        try:
            out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                                 env={**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": env("CLAUDE_CODE_OAUTH_TOKEN")})
            text = out.stdout.strip()
            return text if out.returncode == 0 and text else None
        except Exception as e:
            print(f"{self.name}: claude failed:", e)
            return None

    def say(self, situation, facts, fallback):
        text = self.claude(f"Write your next Telegram message to me. Situation: {situation}\nFacts: {facts}\n"
                           "Only the message text: one to three short sentences.")
        return text or fallback

    # ---------- nagging until it's done: habits from the app, related tasks ----------
    def app_habit(self, alias):
        rep = coach.report() if habits.configured() else None
        return coach.by_alias(alias, rep) if rep else None

    def nag_habit(self, key, alias, start, now, every, max_n, text, buttons=()):
        """Nag about one app habit from `start`, every `every` minutes, at most `max_n` a day, until it's ticked.
        text(habit, n) -> message. A ✅ button ticks it in the app; ⏰ snoozes an hour."""
        if now < start:
            return
        s = self.app_habit(alias)
        if not s or s["done_today"]:
            return
        nags = self.S.setdefault("habit_nags", {})
        for k in [k for k in nags if not k.endswith(now.date().isoformat())]:
            del nags[k]
        st = nags.setdefault(f"{key}:{now.date().isoformat()}", {"n": 0, "last": None, "snooze": None})
        if st["n"] >= max_n or (st["snooze"] and now < datetime.fromisoformat(st["snooze"])):
            return
        if st["last"] and now - datetime.fromisoformat(st["last"]) < timedelta(minutes=every):
            return
        st["n"] += 1
        st["last"] = now.isoformat(timespec="minutes")
        self.send(text(s, st["n"]), [[("✅ Done", f"hd:{alias}"), ("⏰ 1 h", f"hz:{key}")], *buttons])

    def remind_tasks(self, member, now, times):
        """At each of `times` ("HH:MM"), the open Pending Tasks that belong to this bot, with a ✅ per task."""
        for t in times:
            if now >= at(now.date(), t) and self.once(f"tasks:{now.date()}:{t}", now):
                items = team.related_tasks(member)[:5]
                if items:
                    self.S["task_choices"] = {str(line): text for line, text in items}
                    self.send("📌 Still pending on your list:\n" + "\n".join(f"• {companion.task_title(x)}" for _, x in items)
                              + "\nTap the ones you've done.",
                              [[("✅ " + companion.task_title(x)[:40], f"td:{line}")] for line, x in items])
                return

    def common_button(self, data, now):
        """✅ habit, ⏰ snooze, ✅ task. True when handled."""
        if data.startswith("hq:"):  # a quick amount for a quantity habit, e.g. +30 g of protein
            alias, _, value = data[3:].rpartition(":")
            s = self.app_habit(alias)
            if s:
                try:
                    coach.log(s, float(value), f"logged from the {self.name}")
                    unit = s.get("unit") or ""
                    self.send(f"✅ +{value} {unit}: {habits.fmt(s['today_value'])}/{habits.fmt(s['target'])} {unit} today."
                              + (" Target reached. 💪" if s["done_today"] else ""))
                except Exception as e:
                    self.send(f"⚠️ Couldn't log it in the app ({e}).")
            return True
        if data.startswith("hd:"):
            s = self.app_habit(data[3:])
            if s and not s["done_today"]:
                try:
                    # yes/no habits: 1; amounts ("✅ I reached it"): exactly what's left today
                    value = 1 if s["type"] == "boolean" or not s.get("target") else max(s["target"] - s["today_value"], 0) or 1
                    coach.log(s, value, f"ticked from the {self.name}")
                    self.send(f"✅ {s['name']} ticked. Good.")
                except Exception as e:
                    self.send(f"⚠️ Couldn't tick it in the app ({e}).")
            else:
                self.send("Already done today. 👍")
            return True
        if data.startswith("hz:"):
            st = self.S.setdefault("habit_nags", {}).setdefault(f"{data[3:]}:{now.date().isoformat()}", {"n": 0, "last": None})
            st["snooze"] = (now + timedelta(hours=1)).isoformat(timespec="minutes")
            self.send("⏰ One hour. Then I'm back.")
            return True
        if data.startswith("td:"):
            text = self.S.get("task_choices", {}).get(data[3:])
            ok = bool(text) and companion.tick_task(int(data[3:]), text)
            self.send(f"✅ Done: {companion.task_title(text)}" if ok else "That task changed or is already done.")
            return True
        return False

    # ---------- loop ----------
    def run(self, tick, handle, commands):
        if not self.token or not self.owner:
            print(f"{self.name} off: set its token and TELEGRAM_ALLOWED_IDS in scripts/.env")
            return
        try:
            self.api("setMyCommands", commands=json.dumps([{"command": c, "description": d} for c, d in commands]))
        except Exception as e:
            print(f"{self.name}: setMyCommands failed:", e)
        print(f"{self.name} running. AI:", "Claude Code" if self.ai_on() else "templates")
        try:
            offset = int(self.offset_file.read_text().strip())
        except (OSError, ValueError):
            offset = None
        while True:
            try:
                tick()
                self.save()
                params = {"timeout": 30, "allowed_updates": json.dumps(["message", "callback_query", "poll_answer"])}
                if offset:
                    params["offset"] = offset
                for u in self.api("getUpdates", **params):
                    try:
                        sender = ((u.get("callback_query") or u.get("message") or {}).get("from", {}).get("id")
                                  or u.get("poll_answer", {}).get("user", {}).get("id"))
                        if sender in self.allowed:
                            if "callback_query" in u:
                                try:
                                    self.api("answerCallbackQuery", callback_query_id=u["callback_query"]["id"])
                                except Exception:
                                    pass
                            handle(u)
                            self.save()
                    except Exception as e:
                        print(f"{self.name}: update error:", e)
                    offset = u["update_id"] + 1
                    self.offset_file.write_text(str(offset))
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"{self.name}: poll error:", e)
                time.sleep(5)
