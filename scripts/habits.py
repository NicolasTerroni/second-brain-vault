"""Habit consistency from the Everyday habit tracker app. Reads the app's read-only integration API, scores each habit
against its target (every day, or N times a week, from the 'Habits in the app' table in Discipline), and writes
02 - Areas/Discipline/Habit Consistency.md. Zero tokens, standard library only. Used by telegram_bot.py (brief, /habits,
weekly review); by hand: `python scripts/habits.py` (`--file response.json` scores a saved API response instead)."""
import argparse, json, os, re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from companion import plain
from vault import ROOT, frontmatter, read

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8: dates in the host's local time
    ZoneInfo = None

SCRIPTS = Path(__file__).resolve().parent
DISCIPLINE = ROOT / "02 - Areas" / "Discipline" / "Discipline.md"
REPORT = ROOT / "02 - Areas" / "Discipline" / "Habit Consistency.md"
LOG = ROOT / "02 - Areas" / "Discipline" / "Habit Log.md"
CACHE = SCRIPTS / "habits.cache.json"   # last API response, used when the app can't be reached (git-ignored)
TARGETS = "## Habits in the app"
HISTORY_DAYS = 364
SOLID, OK = 0.85, 0.6                   # ✅ at or above SOLID, 🟡 at or above OK, 🔴 below OK or missed twice in a row
WEEKS_SHOWN = 12


# ---------- data ----------
def env(key):
    if os.environ.get(key):
        return os.environ[key].strip()
    f = SCRIPTS / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == key and not line.lstrip().startswith("#"):
                return re.split(r"\s+#", v.strip(), maxsplit=1)[0].strip().strip("\"'")
    return ""


def configured():
    return bool(env("HABITS_API_URL") and env("HABITS_API_TOKEN"))


def fetch(today):
    url, token = env("HABITS_API_URL"), env("HABITS_API_TOKEN")
    if not (url and token):
        raise RuntimeError("Set HABITS_API_URL and HABITS_API_TOKEN in scripts/.env")
    query = f"from={today - timedelta(days=HISTORY_DAYS)}&to={today}"
    request = Request(f"{url.rstrip('/')}/api/integration/habits?{query}",
                      headers={"Authorization": f"Bearer {token}", "User-Agent": "vault-bot"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def load(today):
    """(data, fetched_at, error): fresh from the API when possible, else the cached copy (or None)."""
    try:
        data = fetch(today)
        stamp = datetime.now().isoformat(timespec="minutes")
        CACHE.write_text(json.dumps({"fetched": stamp, "data": data}, ensure_ascii=False), encoding="utf-8")
        return data, stamp, None
    except Exception as e:  # network, auth or config: fall back to the last good copy
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
            return cached["data"], cached["fetched"], str(e)
        except (OSError, ValueError, KeyError):
            return None, None, str(e)


def tz_of(name):
    try:
        return ZoneInfo(name) if ZoneInfo else None
    except Exception:  # no tz database on this host
        return None


def local_day(timestamp, tz):
    moment = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    return (moment.astimezone(tz) if tz else moment.astimezone()).date()


# ---------- targets ----------
def key(name):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", plain(name))).strip().lower()


def parse_target(text):
    """'every day' -> 7, '3× a week' -> 3, 'workdays' -> 5; a date in the cell is when scoring starts."""
    t = plain(text).lower()
    m = re.search(r"(\d+)\s*(?:×|x\b|times)", t)
    per_week = int(m.group(1)) if m else 5 if "workday" in t else 7
    since = re.search(r"\d{4}-\d{2}-\d{2}", t)
    return max(1, min(per_week, 7)), date.fromisoformat(since.group(0)) if since else None


def default_goal(name):
    return {"per_week": 7, "since": None, "workdays": False, "tip": "", "goal": "", "aliases": [key(name)]}


def targets():
    """{habit key: {per_week, since, workdays, tip, goal, aliases}} from the 'Habits in the app' table in Discipline."""
    out, inside, header = {}, False, None
    for line in read(DISCIPLINE).splitlines() if DISCIPLINE.exists() else []:
        if line.startswith("## "):
            inside, header = line.startswith(TARGETS), None
            continue
        if inside and line.startswith("|") and not re.match(r"^\|[-\s|:]+\|$", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = [c.lower() for c in cells]
                continue
            row = dict(zip(header, cells))
            per_week, since = parse_target(row.get("target", ""))
            name = key(row.get("app habit", ""))
            said = [a.strip().lower() for a in plain(row.get("also called", "")).split(",") if a.strip() not in ("", "–", "-")]
            goal = row.get("goal", "").strip()
            out[name] = {"per_week": per_week, "since": since, "workdays": "workday" in row.get("target", "").lower(),
                         "tip": plain(row.get("when it slips, try", "")),
                         "goal": "" if goal in ("", "–", "-", "?") else goal, "aliases": [name, *said]}
    return out


# ---------- scoring ----------
def fmt(n):
    return f"{n:.0f}" if abs(n - round(n)) < 0.05 else f"{n:.1f}"


def pct(x):
    return f"{x * 100:.0f}%"


def monday(d):
    return d - timedelta(days=d.weekday())


def score_habit(habit, totals, goal, today):
    """Everything the report and the bot say about one habit."""
    start = max(habit["_created"], goal["since"]) if goal["since"] else habit["_created"]
    end = habit["_archived"] or today
    target = float(habit["targetValue"]) if habit.get("targetValue") not in (None, "") else None
    done = {d for d, v in totals.items() if (v >= target if target else v > 0)}
    per_week = goal["per_week"]
    active = lambda d: start <= d <= end  # noqa: E731
    yesterday = today - timedelta(days=1)
    s = {"id": habit["id"], "name": habit["name"], "icon": habit.get("icon") or "", "per_week": per_week,
         "workdays": goal["workdays"], "target": target, "unit": habit.get("unit") or "", "type": habit["type"],
         "tip": goal["tip"], "goal": goal["goal"], "aliases": goal["aliases"], "start": start,
         "active_today": active(today) and not habit["_archived"], "today_value": totals.get(today, 0),
         "typical": habit["_typical"], "reminder": habit["_reminder"],
         "ever": bool(totals), "done_today": today in done, "done_yesterday": yesterday in done,
         "active_yesterday": active(yesterday), "last_done": max((d for d in done if d >= start), default=None)}

    def daily_rate(days):
        window = [yesterday - timedelta(days=i) for i in range(days)]
        expected = [d for d in window if active(d)] + ([today] if today in done and active(today) else [])
        hits = sum(1 for d in expected if d in done)
        return hits, len(expected)

    if per_week == 7:
        s["d7"], s["d28"] = daily_rate(7), daily_rate(28)
        days_this_week = [monday(today) + timedelta(days=i) for i in range(today.weekday())]
        expected = [d for d in days_this_week if active(d)] + ([today] if s["done_today"] else [])
        s["week"] = (sum(1 for d in expected if d in done), len(expected))
        streak, d = 0, today if s["done_today"] else yesterday
        while d in done and active(d):
            streak, d = streak + 1, d - timedelta(days=1)
        s["streak"] = f"{streak} day{'s' if streak != 1 else ''}"
        s["missed_twice"] = all(active(d) and d not in done for d in (yesterday, yesterday - timedelta(days=1)))
        hits, n = s["d7"]
        s["rate"] = hits / n if n else None
        s["status"] = ("⚪" if s["rate"] is None else "🔴" if s["missed_twice"] or s["rate"] < OK
                       else "🟡" if s["rate"] < SOLID else "✅")
    else:
        def weekly(lo, hi):  # sessions in [lo, hi] against the target for the active part of that span
            span = [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]
            days = sum(1 for d in span if active(d))
            need = per_week * days / 7
            return sum(1 for d in span if d in done and active(d)), need
        s["d7"], s["d28"] = weekly(today - timedelta(days=6), today), weekly(today - timedelta(days=27), today)
        count = sum(1 for i in range(today.weekday() + 1) if monday(today) + timedelta(days=i) in done)
        days_left = 7 - today.weekday()
        s["week"], s["days_left"] = (count, per_week), days_left
        s["at_risk"] = per_week - count > 0 and per_week - count >= days_left and active(today)
        past = []  # complete weeks, newest first: did they reach the target?
        w = monday(today) - timedelta(days=7)
        while w + timedelta(days=6) >= start and len(past) < 52:
            hits, need = weekly(w, w + timedelta(days=6))
            past.append(hits >= min(per_week, max(1, round(need))))
            w -= timedelta(days=7)
        streak = 0
        for ok in ([count >= per_week] if count >= per_week else []) + past:
            if not ok:
                break
            streak += 1
        s["streak"] = f"{streak} week{'s' if streak != 1 else ''}"
        s["missed_twice"] = len(past) >= 2 and not past[0] and not past[1]
        hits, need = s["d28"]
        s["rate"] = min(1.0, hits / need) if need >= 1 else None
        s["status"] = ("⚪" if s["rate"] is None and not s["at_risk"] else
                       "🔴" if s["at_risk"] or s["missed_twice"] else
                       "🟡" if (past and not past[0]) or (s["rate"] is not None and s["rate"] < SOLID) else "✅")

    if target and s["type"] != "boolean":
        days = [yesterday - timedelta(days=i) for i in range(7)]
        days = [d for d in days if active(d)]
        s["avg"] = sum(totals.get(d, 0) for d in days) / len(days) if days else None
    else:
        s["avg"] = None
    s["history"] = {}
    w = monday(start)
    while w <= today:
        span = [w + timedelta(days=i) for i in range(7)]
        if per_week == 7:
            expected = [d for d in span if active(d) and (d < today or d in done)]
            s["history"][w] = (sum(1 for d in expected if d in done), len(expected)) if expected else None
        else:
            days = [d for d in span if active(d)]
            need = per_week if w == monday(today) else min(per_week, max(1, round(per_week * len(days) / 7)))
            s["history"][w] = (sum(1 for d in days if d in done), need) if days else None
        w += timedelta(days=7)
    s["why"] = why(s)
    return s


def target_text(s):
    amount = f"{fmt(s['target'])} {s['unit']}".strip() if s["target"] else ""
    every = "every day" if s["per_week"] == 7 else f"{s['per_week']}× a week"
    return f"{amount} {every}".strip()


def why(s):
    if not s["ever"]:
        return "never logged in the app yet"
    parts = []
    if s["per_week"] == 7:
        hits, n = s["d7"]
        if s["missed_twice"] and hits:
            parts.append("missed yesterday and the day before")
        if n:
            parts.append(f"{hits} of {n} days in the last week" + (f" at {fmt(s['target'])} {s['unit']}" if s["avg"] is not None else ""))
    else:
        count, need = s["week"]
        if s.get("at_risk"):
            left = s["days_left"]
            parts.append(f"{count}/{need} this week with {'only today' if left == 1 else f'{left} days'} left")
        if s["missed_twice"]:
            parts.append(f"under {need}× in each of the last two weeks")
        if not parts:
            parts.append(f"{count}/{need} this week")
    if s["avg"] is not None:
        parts.append(f"{fmt(s['avg'])} {s['unit']} a day on average")
    return "; ".join(parts)


def report(data, today=None):
    """Score every active habit. Returns {today, habits: [score, ...]} in the app's order."""
    tz = tz_of(data.get("timezone") or "Europe/Rome")
    today = today or (datetime.now(tz).date() if tz else date.today())
    goals = targets()
    totals, amounts, notes = {}, {}, []
    for e in data.get("entries", []):
        day = local_day(e["timestamp"], tz)
        per = totals.setdefault(e["habitId"], {})
        per[day] = per.get(day, 0) + float(e["value"])
        amounts.setdefault(e["habitId"], []).append(float(e["value"]))
        if e.get("note"):
            notes.append((day, e["habitId"], e["note"]))
    first_reminder = {}
    for r in data.get("reminders", []):
        if r.get("enabled") and r.get("startTime"):
            first_reminder[r["habitId"]] = min(first_reminder.get(r["habitId"], "99:99"), r["startTime"][:5])
    scored = []
    for h in sorted(data.get("habits", []), key=lambda h: (h.get("sortOrder") or 0, h.get("createdAt") or "")):
        values = amounts.get(h["id"], [])
        h = dict(h, _created=local_day(h["createdAt"], tz),
                 _archived=local_day(h["archivedAt"], tz) if h.get("archivedAt") else None,
                 _typical=max(set(values), key=values.count) if values else None,  # the amount usually logged
                 _reminder=first_reminder.get(h["id"]))
        if h["_archived"] and h["_archived"] < today - timedelta(days=28):
            continue
        goal = goals.get(key(h["name"])) or default_goal(h["name"])
        scored.append(score_habit(h, totals.get(h["id"], {}), goal, today))
    return {"today": today, "habits": [s for s in scored if s["start"] <= today],
            "totals": totals, "notes": notes, "tz": tz}


def attention(rep, limit=None):
    """The habits doing badly, worst first."""
    bad = [s for s in rep["habits"] if s["status"] == "🔴"]
    bad.sort(key=lambda s: (not s["missed_twice"] and not s.get("at_risk"), s["rate"] if s["rate"] is not None else 0))
    return bad[:limit] if limit else bad


# ---------- the notes ----------
def cell(s, period):
    hits, n = s[period]
    if s["per_week"] == 7:
        return f"{hits}/{n} days ({pct(hits / n)})" if n else "–"
    return f"{hits} of {fmt(n)} ({pct(min(1, hits / n))})" if n >= 1 else f"{hits}"


def _created(path, today):
    if path.exists():
        fm, _ = frontmatter(read(path))
        return str((fm or {}).get("created") or today)[:10]
    return today.isoformat()


def _header(path, today, summary):
    return ["---", f"created: {_created(path, today)}", "type: note", "status: active", "tags: [area/discipline, habit]",
            "source: Everyday habit tracker app, generated by scripts/habits.py", "---", summary, ""]


def write_note(rep, fetched=None, error=None):
    today = rep["today"]
    lines = _header(REPORT, today, "How consistent you are with each habit in your habit tracker app, scored against the targets "
                    "in [[Discipline#Habits in the app]]. Generated by `scripts/habits.py` whenever the bot reads the app; don't edit it by hand.")
    lines += [f"Updated {datetime.now():%Y-%m-%d %H:%M}" + (f" · ⚠️ the app couldn't be reached, data from {fetched}" if error else "")
              + f" · ✅ {pct(SOLID)} or more · 🟡 {pct(OK)}–{pct(SOLID)} · 🔴 under {pct(OK)}, or missed twice in a row "
                "(for weekly habits: under target two weeks running, or this week can't be reached without training every day left). "
                "Every day's values and notes: [[Habit Log]]. Why you skip: [[Habit Reasons]].",
              "", f"## This week ({monday(today):%a %d %b} – {today:%a %d %b})",
              "| Habit | Goal | Target | This week | Last 7 days | Last 28 days | Streak | Status |", "|---|---|---|---|---|---|---|---|"]
    for s in rep["habits"]:
        week = s["week"]
        this_week = (f"{week[0]}/{week[1]} days" if s["per_week"] == 7 else f"{week[0]}/{week[1]}") if week[1] else "–"
        avg = f" · avg {fmt(s['avg'])} {s['unit']}" if s["avg"] is not None else ""
        lines.append(f"| {s['icon']} {s['name']} | {s['goal'].replace('|', chr(92) + '|') or '❔ none'} | {target_text(s)} | {this_week} "
                     f"| {cell(s, 'd7')}{avg} | {cell(s, 'd28')} | {s['streak']} | {s['status']} |")
    lines += ["", "## Needs attention"]
    bad = attention(rep)
    if bad:
        for s in bad:
            lines.append(f"- 🔴 **{s['icon']} {s['name']}**: {s['why']}." + (f" Try: {s['tip']}" if s["tip"] else
                         " No fix written yet: add one in [[Discipline#Habits in the app]]."))
    else:
        lines.append("- Nothing is slipping. Keep the minimums small.")
    weeks = sorted({w for s in rep["habits"] for w in s["history"]}, reverse=True)[:WEEKS_SHOWN]
    lines += ["", "## Weekly history",
              "Days done out of days expected (for weekly habits, sessions out of the target). Newest first.", "",
              "| Week | " + " | ".join(f"{s['icon']} {s['name']}" for s in rep["habits"]) + " |",
              "|---|" + "---|" * len(rep["habits"])]
    for w in weeks:
        row = []
        for s in rep["habits"]:
            v = s["history"].get(w)
            row.append("–" if not v or not v[1] else f"{v[0]}/{v[1]} ({pct(min(1, v[0] / v[1]))})")
        label = f"{w:%d %b}" + (" (so far)" if w == monday(today) else "")
        lines.append(f"| {label} | " + " | ".join(row) + " |")
    lines += ["", "Related: [[Discipline]], [[habit-tracker (V1 done)]]."]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_log(rep):
    """Habit Log: every day's values from the app, newest first, with the notes you wrote on entries."""
    today, habits = rep["today"], rep["habits"]
    lines = _header(LOG, today, "Every day in your habit tracker app: what you logged for each habit, and the notes you wrote "
                    "on entries. Generated by `scripts/habits.py`; don't edit it by hand. Scores: [[Habit Consistency]].")
    first = min((s["start"] for s in habits), default=today)
    notes = {}
    names = {s["id"]: s["name"] for s in habits}
    for day, habit_id, note in rep["notes"]:
        notes.setdefault(day, []).append(f"{names.get(habit_id, 'archived habit')}: {note}")
    lines += ["✅ done · a number is the amount logged (bold when it met the target) · · not done · blank: the habit didn't exist yet", "",
              "| Date | " + " | ".join(f"{s['icon']} {s['name']}" for s in habits) + " | Notes |",
              "|---|" + "---|" * len(habits) + "---|"]
    day = today
    while day >= first:
        row = []
        for s in habits:
            value = rep["totals"].get(s["id"], {}).get(day, 0)
            if day < s["start"]:
                row.append("")
            elif s["type"] == "boolean":
                row.append("✅" if value else "·")
            elif value:
                row.append(f"**{fmt(value)}**" if s["target"] and value >= s["target"] else fmt(value))
            else:
                row.append("·")
        text = "; ".join(notes.get(day, [])).replace("|", "/").replace("\n", " ")
        lines.append(f"| {day:%a %d %b %Y} | " + " | ".join(row) + f" | {text} |")
        day -= timedelta(days=1)
    lines += ["", "Related: [[Discipline]], [[Habit Reasons]]."]
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


# ---------- today, for the bot's check-ins ----------
def remaining(s):
    """What's left today: (value to log for 'done', label)."""
    if s["type"] == "boolean" or not s["target"]:
        return 1, ""
    left = max(s["target"] - s["today_value"], 0)
    if s["type"] == "quantity":
        step = s["typical"] or s["target"] / 4
        return min(step, left) or step, f"{fmt(s['today_value'])}/{fmt(s['target'])} {s['unit']}"
    return left or s["target"], f"{fmt(s['today_value'])}/{fmt(s['target'])} {s['unit']}"


def is_open(s, today):
    """Still to do today. Weekly habits only when the week needs it: at risk, or 2+ days since the last session."""
    if not s["active_today"] or s["done_today"]:
        return False
    if s["per_week"] == 7:
        return True
    if s["workdays"]:
        return today.weekday() < 5
    count, need = s["week"]
    if count >= need:
        return False
    return s.get("at_risk") or not s["last_done"] or (today - s["last_done"]).days >= 2


def open_today(rep):
    return [s for s in rep["habits"] if is_open(s, rep["today"])]


def item_line(s):
    _, label = remaining(s)
    extra = label or (f"{s['week'][0]}/{s['week'][1]} this week" if s["per_week"] < 7 and not s["workdays"] else "")
    alarm = " 🔴 missed twice, don't make it three" if s["missed_twice"] else " ⚠️ week at risk" if s.get("at_risk") else ""
    return f"{s['icon']} {s['name']}" + (f" · {extra}" if extra else "") + alarm


def done_button(s):
    value, _ = remaining(s)
    amount = f" {fmt(value)} {s['unit']}" if s["type"] != "boolean" and s["unit"] else ""
    return f"✅ {s['name'][:18]}{amount}", value


# ---------- writes (the bot logs entries) ----------
def _call(method, path, body=None):
    url, token = env("HABITS_API_URL"), env("HABITS_API_TOKEN")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(f"{url.rstrip('/')}{path}", data=data, method=method,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "vault-bot"})
    with urlopen(request, timeout=20) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else None


def log_entry(habit_id, value=1, note=None):
    """Log an entry in the app. Returns the entry, with created=False when a yes/no habit was already done today."""
    body = {"habitId": habit_id, "value": value}
    if note:
        body["note"] = note[:1000]
    return _call("POST", "/api/integration/entries", body)


def undo_entry(entry_id):
    _call("DELETE", f"/api/integration/entries?id={entry_id}")


# ---------- focus lock (the app decides; an iPhone Shortcut enforces it) ----------
def lock_status():
    """The app's focus lock right now (GET /api/integration/lock), or None when it's off or the app can't be reached."""
    try:
        status = _call("GET", "/api/integration/lock")
    except Exception as e:
        print("lock status failed:", e)
        return None
    return status if status and status.get("rule") != "off" else None


def lock_line(status):
    if status["locked"]:
        until = f" until {status['focusHours']['end']}" if status.get("focusHours") else ""
        return f"🔒 Apps locked{until}: {status['message'].removeprefix('Locked: ')}."
    if status.get("goalMet"):
        return "🔓 Apps unlocked: you've earned your free time today."
    return "🔓 Apps open now (outside your focus hours)."


# ---------- chat text (telegram_bot.py) ----------
def refresh(today=None):
    """Fetch, score and write the notes. (report, error) with report None when there is no data at all."""
    data, fetched, error = load(today or date.today())
    if data is None:
        return None, error
    rep = report(data, today)
    rep["fetched"], rep["error"] = fetched, error
    write_note(rep, fetched, error)
    write_log(rep)
    return rep, error


def focus(rep):
    """The one habit to win today: the worst slipping one, else the weakest open one."""
    bad = attention(rep)
    if bad:
        return bad[0]
    weak = sorted(open_today(rep), key=lambda s: s["rate"] if s["rate"] is not None else 1)
    return weak[0] if weak else None


def brief_lines(rep):
    daily = [s for s in rep["habits"] if s["per_week"] == 7 and s["active_yesterday"]]
    missed = [s["name"] for s in daily if not s["done_yesterday"]]
    lines = [f"📊 Yesterday: {len(daily) - len(missed)}/{len(daily)}" + (f" (missed: {', '.join(missed)})" if missed else " 🎉")]
    f = focus(rep)
    if f:
        lines.append(f"🎯 Today's focus: {f['icon']} {f['name']}. {f['why'][:1].upper() + f['why'][1:]}." if f["status"] == "🔴"
                     else f"🎯 Today's focus: {f['icon']} {f['name']}.")
        if f["tip"]:
            lines.append(f"👉 {f['tip']}")
    others = [s["name"] for s in attention(rep) if s is not f]
    if others:
        lines.append("Also slipping: " + ", ".join(others))
    if rep.get("error"):
        lines.append(f"⚠️ The habit app couldn't be reached; this is data from {rep['fetched']}.")
    return lines


def week_text(rep):
    lines = [f"📊 This week ({monday(rep['today']):%a %d} – {rep['today']:%a %d %b})"]
    for s in rep["habits"]:
        week = s["week"]
        done = f"{week[0]}/{week[1]}" + (" days" if s["per_week"] == 7 else "") if week[1] else "not done today yet"
        lines.append(f"{s['status']} {s['icon']} {s['name']}: {done} · 28 days {pct(s['rate']) if s['rate'] is not None else '–'}")
    lines.append("\nDetails: Habit Consistency and Habit Log in your Discipline area.")
    return "\n".join(lines)


def by_goal(rep):
    """[(goal, [scores])] in first-seen order; habits without a goal last, under ''."""
    groups = {}
    for s in rep["habits"]:
        groups.setdefault(plain(s["goal"]), []).append(s)
    return sorted(groups.items(), key=lambda g: g[0] == "")


def review_lines(rep):
    lines = ["\n📊 This week, by goal (from the app):"]
    for goal, items in by_goal(rep):
        counts = ", ".join(f"{s['status']} {s['name']}" + (f" {s['week'][0]}/{s['week'][1]}" if s["week"][1] else "")
                           + (" days" if s["per_week"] == 7 and s["week"][1] else "") for s in items)
        lines.append(f"🎯 {goal}: {counts}" if goal else f"❔ No goal: {counts}. Keep them, or drop them?")
    return lines


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--file", help="score a saved API response instead of calling the app")
    parser.add_argument("--today", help="YYYY-MM-DD, for testing")
    args = parser.parse_args()
    today = date.fromisoformat(args.today) if args.today else None
    if args.file:
        rep = report(json.loads(Path(args.file).read_text(encoding="utf-8")), today)
        rep["fetched"], rep["error"] = None, None
        write_note(rep)
        write_log(rep)
    else:
        rep, error = refresh(today)
        if rep is None:
            raise SystemExit(f"No habit data: {error}")
    print(week_text(rep))
    print("\n" + "\n".join(brief_lines(rep)))
    print("\n".join(review_lines(rep)))
    print("\nOpen today: " + ", ".join(item_line(s) for s in open_today(rep)))
    print(f"\nWrote {REPORT.relative_to(ROOT)} and {LOG.relative_to(ROOT)}")
