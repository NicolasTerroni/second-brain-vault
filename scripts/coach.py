"""The bot's habit coach, on top of habits.py: nags about habits still open today (after the app's own reminder had its
chance), the evening check-in, logging habits from plain messages ("water 500", "did mobility"), and the reasons you
give when you skip. No Telegram calls here: telegram_bot.py sends what these functions return. Zero tokens.

Settings in scripts/.env: CHECKIN_TIME (21:00), NAG_EVERY minutes (60, 0 = off), NAG_GRACE minutes after the app's
reminder before the bot starts (30), NAG_UNTIL (23:00)."""
import re, time
from datetime import date, datetime, timedelta
import habits
from companion import plain
from vault import ROOT, read

REASONS = ROOT / "02 - Areas" / "Discipline" / "Habit Reasons.md"
DISCIPLINE = habits.DISCIPLINE
CHECKIN_TIME = habits.env("CHECKIN_TIME") or "21:00"
NAG_EVERY = int(habits.env("NAG_EVERY") or 60)
NAG_GRACE = int(habits.env("NAG_GRACE") or 30)
NAG_UNTIL = habits.env("NAG_UNTIL") or "23:00"
SNOOZE_HOURS = 2
DONE_WORDS = r"(?:\bdid\b|\bdone\b|\bfinished\b|\bcompleted\b|\bdrank\b|\bhecho\b|\bhice\b|\blisto\b|\bok\b|✅)"
UNITS = {"l": 1000, "lt": 1000, "liter": 1000, "liters": 1000, "litro": 1000, "litros": 1000,
         "h": 60, "hour": 60, "hours": 60, "hora": 60, "horas": 60}

_cache = {"rep": None, "at": 0.0, "tried": 0.0}


# ---------- data ----------
def report(max_age=300):
    """The latest habit report, refreshed from the app when older than max_age seconds (at most once a minute)."""
    now = time.time()
    if _cache["rep"] is None or now - _cache["at"] > max_age:
        if now - _cache["tried"] >= 60:
            _cache["tried"] = now
            rep, error = habits.refresh()
            if rep is not None and not error:
                _cache.update(rep=rep, at=now)
            elif rep is not None and _cache["rep"] is None:
                _cache["rep"] = rep  # cached copy from disk: better than nothing
    rep = _cache["rep"]
    return rep if rep and rep["today"] == date.today() else None


def find(habit_id):
    rep = _cache["rep"]
    return next((s for s in rep["habits"] if s["id"] == habit_id), None) if rep else None


def log(s, value, note=None):
    """Log in the app and update the cached day. Returns (entry, created)."""
    entry = habits.log_entry(s["id"], value, note)
    created = bool(entry and entry.get("created", True))
    if created:
        s["today_value"] += value
        target = s["target"]
        s["done_today"] = s["today_value"] >= target if target else True
        if s["done_today"] and s["per_week"] < 7:
            s["week"] = (s["week"][0] + 1, s["week"][1])
    return entry, created


def logged_text(s, value, created):
    if not created:
        return f"👍 {s['icon']} {s['name']} was already done today."
    if s["type"] == "boolean" or not s["target"]:
        text = f"✅ {s['icon']} {s['name']} done."
    else:
        text = f"✅ {s['icon']} {s['name']} +{habits.fmt(value)} {s['unit']} → {habits.fmt(s['today_value'])}/{habits.fmt(s['target'])} today."
    if s["per_week"] < 7 and not s["workdays"]:
        text += f" {s['week'][0]}/{s['week'][1]} this week."
    return text


# ---------- logging from plain messages ----------
def parse(text, rep):
    """'water 500', 'drank 1 l of water', 'did mobility', 'read 20 min' -> (habit, value), else None (it's a capture)."""
    t = " ".join(text.lower().split())
    if not t or len(t) > 80 or len(t.split()) > 8 or "http" in t or "\n" in text.strip() or t.startswith("workout"):
        return None
    hits = [s for s in rep["habits"] if s["active_today"]
            and any(not a.startswith("#") and re.search(rf"(?<!\w){re.escape(a)}(?!\w)", t) for a in s["aliases"])]
    if len(hits) != 1:
        return None
    s = hits[0]
    number = re.search(r"(\d+(?:[.,]\d+)?)\s*([a-z]+)?", t)
    named_alone = t.strip(" .!✅") in s["aliases"]
    if number and s["type"] != "boolean":
        value = float(number.group(1).replace(",", "."))
        value *= UNITS.get(number.group(2) or "", 1)
    elif number or named_alone or re.search(DONE_WORDS, t):
        value, _ = habits.remaining(s)
    else:
        return None
    return (s, value) if value > 0 else None


def by_alias(alias, rep):
    """The habit tied to a special alias, e.g. 'workout' (Workout notes) or '#english-practice' (English voice notes)."""
    return next((s for s in (rep or {}).get("habits", []) if alias in s["aliases"] and s["active_today"]), None)


# ---------- nags and the evening check-in ----------
def _at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m)


def due_at(s, day):
    """When the bot starts pushing: the app's reminder plus a grace period, or the evening check-in."""
    if s["reminder"]:
        return _at(day, s["reminder"]) + timedelta(minutes=NAG_GRACE)
    return _at(day, CHECKIN_TIME)


def day_state(state, today):
    st = state.get("coach")
    if not st or st.get("day") != today.isoformat():
        st = state["coach"] = {"day": today.isoformat(), "skipped": [], "nagged": {}, "snooze": 0, "checkin": False}
    return st


def message(items, header):
    lines = [header, *(habits.item_line(s) for s in items), "", "Tap ✅ when it's done, or ⏭ to skip it with a reason."]
    rows = [[(habits.done_button(s)[0], f"hd:{s['id']}"), ("⏭ Skip", f"hs:{s['id']}")] for s in items[:8]]
    rows.append([("😴 Snooze 2 h", "hz:")])
    return "\n".join(lines), rows


def tick(state, now=None):
    """Messages to send now: [(text, buttons)]. Call it on every poll; it only calls the app when something is due."""
    if NAG_EVERY <= 0 or not habits.configured():
        return []
    now = now or datetime.now()
    today = now.date()
    st = day_state(state, today)
    until = _at(today, NAG_UNTIL)
    if now >= until or now.timestamp() < st["snooze"]:
        return []
    checkin = not st["checkin"] and now >= _at(today, CHECKIN_TIME)
    rep = report(max_age=3600)
    if rep is None:
        return []

    def candidates(r):
        return [s for s in habits.open_today(r) if s["id"] not in st["skipped"] and now >= due_at(s, today)]

    expired = [s for s in candidates(rep) if now.timestamp() - st["nagged"].get(s["id"], 0) >= NAG_EVERY * 60]
    if not expired and not checkin:
        return []
    rep = report(max_age=120)  # confirm with fresh data before pushing
    if rep is None:
        return []
    if checkin:
        st["checkin"] = True
        items = [s for s in habits.open_today(rep) if s["id"] not in st["skipped"]]
        header = "🌙 Evening check-in. Still open today:" if items else None
        if not items:
            return [("🌙 Evening check-in: everything's done today. 🎉", None)]
    else:
        items = candidates(rep)
        if not items:
            return []
        last_call = now + timedelta(minutes=NAG_EVERY) >= until
        header = ("🚨 Last call before the day ends. Do the 2-minute version:" if last_call
                  else "⏰ Still open (your app already reminded you):")
    for s in items:
        st["nagged"][s["id"]] = now.timestamp()
    return [message(items, header)]


def skip(state, habit_id):
    st = day_state(state, date.today())
    if habit_id not in st["skipped"]:
        st["skipped"].append(habit_id)


def snooze(state):
    day_state(state, date.today())["snooze"] = time.time() + SNOOZE_HOURS * 3600


# ---------- reasons ----------
def add_reason(names, text, kind="skipped", day=None):
    """Append your reason under each habit's heading in Habit Reasons (created on first use)."""
    day = day or date.today()
    if REASONS.exists():
        lines = read(REASONS).splitlines()
    else:
        lines = ["---", f"created: {date.today().isoformat()}", "type: note", "status: active", "tags: [area/discipline, habit]",
                 "source: own words, collected by the Telegram bot", "---",
                 "Why habits get skipped or missed, in your own words, collected by the bot when you skip a habit or answer "
                 "\"what got in the way?\". The agent reads it at each organize and weekly review to find patterns and rewrite "
                 "the fixes in [[Discipline#Habits in the app]]. Scores: [[Habit Consistency]].", ""]
    text = " ".join(text.split())
    for name in names:
        heading = f"## {name}"
        if heading not in lines:
            lines += ["", heading] if lines[-1] != "" else [heading]
        i = lines.index(heading) + 1
        while i < len(lines) and not lines[i].startswith("## "):
            i += 1
        while lines[i - 1] == "":
            i -= 1
        lines.insert(i, f"- {day.isoformat()} ({kind}): {text}")
    REASONS.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def reasons_since(start):
    """{habit heading: [reason text]} written on or after `start`."""
    out, heading = {}, None
    for line in read(REASONS).splitlines() if REASONS.exists() else []:
        if line.startswith("## "):
            heading = line[3:].strip()
        m = re.match(r"- (\d{4}-\d{2}-\d{2}) \((\w+)\): (.+)", line)
        if heading and m and date.fromisoformat(m.group(1)) >= start:
            out.setdefault(heading, []).append(m.group(3))
    return out


# ---------- the Sunday review ----------
def review_lines(rep):
    """Counts by goal, this week's reasons, and one proposed change: (lines, proposal or None)."""
    lines = habits.review_lines(rep)
    monday = rep["today"] - timedelta(days=rep["today"].weekday())
    said = reasons_since(monday)
    if said:
        lines.append("\n🗣 What got in the way, in your words:")
        for name, items in said.items():
            lines.append(f"• {name}: " + "; ".join(items[-3:]))
    worst = habits.attention(rep, 1)
    proposal = None
    if worst and worst[0]["tip"]:
        s = worst[0]
        proposal = {"habit": s["name"], "tip": s["tip"]}
        lines.append(f"\n🔧 Proposed change for next week: {s['icon']} {s['name']} ({s['why']}).\n👉 {s['tip']}")
    return lines, proposal


def approve(proposal):
    """Record the approved change in Discipline's review log."""
    lines = read(DISCIPLINE).splitlines()
    entry = f"- {date.today().isoformat()}: next week's one change → {proposal['habit']}: {plain(proposal['tip'])} (approved in Telegram)"
    if "## Review log" not in lines:
        lines += ["", "## Review log"]
    i = lines.index("## Review log") + 1
    while i < len(lines) and not lines[i].startswith("## "):
        i += 1
    while lines[i - 1] == "":
        i -= 1
    lines.insert(i, entry)
    DISCIPLINE.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
