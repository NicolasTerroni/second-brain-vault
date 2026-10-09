#!/usr/bin/env python3
"""Personal trainer: a second Telegram bot (its own token) that plans the training week, asks when you'll train, pushes
you until you start, guides each session set by set, and writes it to the strength log. Brief and persona:
02 - Areas/Training/Coach.md.

The engine (plans, nags, logging, progression checks) is plain Python: zero tokens. Claude Code, when connected
(`claude` on PATH and CLAUDE_CODE_OAUTH_TOKEN set), only writes the coach's words and answers free questions; without
it every message falls back to a template.

Config (scripts/.env):
  TRAINER_BOT_TOKEN=...          from @BotFather (a second bot, not the capture bot)
  TELEGRAM_ALLOWED_IDS=...       shared with the capture bot; the first id gets the messages
  TRAINER_PLAN_TIME=Sun 20:00    weekly report + next week's plan
  TRAINER_ASK_TIME=21:30         the evening before a training day: what time tomorrow?
  TRAINER_MORNING_TIME=08:00     missed-day follow-up, and the time question if still unanswered
  TRAINER_QUIET=22:30-07:00      nothing is sent in this range unless you write first
  TRAINER_NAG_EVERY=20           minutes between nags once your planned time has passed (15 min grace)
  TRAINER_NAG_MAX=6              nags per day at most
  TRAINER_TARGET=3               sessions per week (default: the routine's target_per_week)
  TRAINER_AI=claude              "off" = templates only
  CLAUDE_CODE_OAUTH_TOKEN=...    from `claude setup-token` (your Claude subscription), used by the container's Claude Code
Runs inside the capture bot (telegram_bot.py starts it in a thread when TRAINER_BOT_TOKEN is set), so it shares its
container. Standalone for testing: python scripts/trainer.py
"""
from datetime import date, datetime, timedelta
import json, re
import coach
import companion
import habits
import team
from botkit import Bot, at, env, hhmm, in_range
from vault import ROOT, read

LOG = companion.STRENGTH_LOG
ROUTINE = companion.ROUTINE
CANVAS = companion.CANVAS
COACH_NOTE = ROOT / "02 - Areas" / "Training" / "Coach.md"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_NAMES = companion.DAY_NAMES

BOT = Bot("Coach", "TRAINER_BOT_TOKEN", COACH_NOTE, "trainer", agent="coach", writes=("02 - Areas/Training", "00 - Inbox"))
S = BOT.S
PLAN_TIME = env("TRAINER_PLAN_TIME", "Sun 20:00")
ASK_TIME = env("TRAINER_ASK_TIME", "21:30")
MORNING = env("TRAINER_MORNING_TIME", "08:00")
QUIET = env("TRAINER_QUIET", "22:30-07:00")
NAG_EVERY = int(env("TRAINER_NAG_EVERY", "20"))
NAG_MAX = int(env("TRAINER_NAG_MAX", "6"))
NAG_GRACE = 15
MOBILITY_TIME = env("TRAINER_MOBILITY_TIME", "09:00")   # the daily mobility nag starts here (or at the app's reminder, if earlier)
MOBILITY_EVERY = int(env("TRAINER_MOBILITY_EVERY", "30"))
MOBILITY_MAX = int(env("TRAINER_MOBILITY_MAX", "6"))
TASK_TIMES = ("10:00", "19:30")
PROTEIN_TIME = env("TRAINER_PROTEIN_TIME", "20:00")    # evening protein check (Protein Target note: 160 g a day)                          # related Pending Tasks, twice a day
MOBILITY_NOTE = ROOT / "02 - Areas" / "Training" / "mobility" / "Daily Mobility Routine (5 Minutes).md"
TIME_CHOICES = ["07:00", "08:00", "13:00", "18:00", "18:30", "19:00", "20:00"]


def now():
    return datetime.now()


def send(text, buttons=None, edit=None):
    return BOT.send(text, buttons, edit)


def send_routine(caption):
    BOT.send_file(ROUTINE, caption)


def claude(prompt, tools=False, model="haiku", timeout=90):
    return BOT.claude(prompt, tools, model, timeout)


def say(situation, facts, fallback):
    return BOT.say(situation, facts, fallback)


def save_state():
    BOT.save()


def once(key):
    return BOT.once(key, now())


def days():
    """Planned and past training days: iso date -> {time, status, ...}. status: planned, started, done, cancelled, missed."""
    return S.setdefault("days", {})


def quiet(t=None):
    return in_range(t or now(), QUIET)


def monday(d):
    return d - timedelta(days=d.weekday())


def target():
    if env("TRAINER_TARGET"):
        return int(env("TRAINER_TARGET"))
    return companion.training_status()["target"]


def logged():
    """Dates with a logged session (strength log) or a session finished with the coach."""
    out = {d for d, _ in companion.sessions()}
    out |= {date.fromisoformat(k) for k, v in days().items() if v.get("status") in ("done", "ticked")}
    return out


def week_done(d):
    m = monday(d)
    return sum(1 for x in logged() if m <= x <= m + timedelta(days=6))


def planned(d):
    """Planned (still to do) dates in d's week."""
    m = monday(d)
    return sorted(date.fromisoformat(k) for k, v in days().items()
                  if v.get("status") in ("planned", "started") and m <= date.fromisoformat(k) <= m + timedelta(days=6))


def propose(start, n):
    """n dates from `start` to that Sunday, never next to a logged or chosen day when avoidable."""
    end = monday(start) + timedelta(days=6)
    busy = set(logged()) | {date.fromisoformat(k) for k, v in days().items() if v.get("status") == "started"}
    span = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    chosen = []
    for d in span:
        if len(chosen) < n and not any(abs((d - b).days) <= 1 for b in busy | set(chosen)):
            chosen.append(d)
    for d in span:  # not enough room: accept back-to-back days
        if len(chosen) < n and d not in chosen and d not in busy:
            chosen.append(d)
    return sorted(chosen)


def set_week(m, chosen):
    """Make `chosen` the plan for the week of monday m (past days and finished sessions are left alone)."""
    today = now().date()
    for k, v in list(days().items()):
        d = date.fromisoformat(k)
        if m <= d <= m + timedelta(days=6) and d >= today and v.get("status") == "planned" and d not in chosen:
            del days()[k]
    for d in chosen:
        if d >= today:
            days().setdefault(d.isoformat(), {"time": None, "status": "planned"})
    S.setdefault("weeks", {})[m.isoformat()] = "confirmed"


def plan_text(m, chosen, header):
    names = " · ".join(f"{DAYS[d.weekday()]} {d.day}" for d in chosen) or "no days"
    return f"{header}\n📅 Week of {m:%a %d %b}: {names} ({len(chosen)}/{target()}).\nTap days to change them, then ✅ Confirm."


def plan_buttons(m, chosen):
    cells = []
    for i in range(7):
        d = m + timedelta(days=i)
        mark = "✅" if d in chosen else ("✔️" if d in logged() else "")
        cells.append((f"{mark}{DAYS[i]} {d.day}", f"pt:{d.isoformat()}"))
    return [cells[:4], cells[4:], [("✅ Confirm", f"pc:{m.isoformat()}")]]


def offer_plan(m, header):
    today = now().date()
    start = max(m, today)
    keep = [d for d in planned(start) if d >= start]
    need = max(0, target() - week_done(start))
    chosen = keep if len(keep) >= need else sorted(set(keep) | set(propose(start, need - len(keep))) )
    S["draft"] = {"monday": m.isoformat(), "days": [d.isoformat() for d in chosen], "made": today.isoformat()}
    S["draft"]["msg"] = send(plan_text(m, chosen, header), plan_buttons(m, chosen))
    if not S["draft"]["msg"]:  # not delivered (e.g. the chat isn't started yet): offer it again later
        S.pop("draft")
        S.get("sent", {}).pop(f"autoplan:{m}", None)


def confirm_plan(reason=None):
    draft = S.pop("draft", None)
    if not draft:
        return
    m = date.fromisoformat(draft["monday"])
    chosen = sorted(date.fromisoformat(x) for x in draft["days"])
    short = target() - week_done(max(m, now().date())) - len([d for d in chosen if d >= now().date()])
    if short > 0 and reason is None:
        S["draft"] = draft
        S["pending"] = {"kind": "reason", "why": "plan"}
        send(say("the athlete planned fewer sessions than the weekly target",
                 f"planned {len(chosen)}, target {target()}",
                 f"That's {len(chosen)} of {target()} this week. What's the reason?"))
        return
    set_week(m, chosen)
    if reason:
        strength_reason(f"planned {len(chosen)} of {target()}: {reason}", "skipped")
    lines = [f"{DAYS[d.weekday()]} {d:%d %b}" for d in chosen]
    send("✅ Plan locked: " + (", ".join(lines) or "no sessions") + ". I'll ask for the time the evening before.",
         edit=draft.get("msg"))


def shift_after(d):
    """Keep planned days after d at least one day apart; drop the ones that no longer fit in the week. Returns dropped."""
    dropped, prev = [], d
    for x in [p for p in planned(d) if p > d]:
        if (x - prev).days <= 1:
            entry = days().pop(x.isoformat())
            new = prev + timedelta(days=2)
            if monday(new) == monday(d) and new.isoformat() not in days():
                days()[new.isoformat()] = {**entry, "time": None}
                prev = new
            else:
                dropped.append(x)
        else:
            prev = x
    return dropped


def make_training_day(d):
    if d.isoformat() not in days() or days()[d.isoformat()]["status"] not in ("planned", "started", "done"):
        days()[d.isoformat()] = {"time": None, "status": "planned"}
    return shift_after(d)


# ---------- the routine and the log ----------
def norm(s):
    s = re.sub(r"\(.*?\)", " ", s.lower()).replace("→", " to ").replace("+", " ")
    return set(re.findall(r"[a-z_]+", s)) - {"the", "a", "with", "to", "and", "evgeniy", "s"}


def level_rows():
    """Current level and next target: [{name, day, variation, last, next, line}]."""
    rows, inside = [], False
    for i, line in enumerate(read(LOG).splitlines() if LOG.exists() else []):
        if line.startswith("## "):
            inside = line.startswith("## Current level")
        elif inside and line.startswith("| ") and not line.startswith(("| Exercise", "|---")):
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) >= 5:
                rows.append({"name": c[0], "day": c[1], "variation": c[2], "last": c[3], "next": c[4], "line": i})
    return rows


def routine_order(day):
    """Exercise names of a day in routine order, from the canvas cards (dead hang first)."""
    try:
        nodes = {n["id"]: n for n in json.loads(CANVAS.read_text(encoding="utf-8")).get("nodes", []) if n.get("type") == "text"}
    except (OSError, ValueError):
        nodes = {}
    blocks = ("upper", "legs", "core", "flow") if day == 1 else ("skill", "main", "legs", "core")
    names = ["Dead hang"]
    for b in blocks:
        names += re.findall(r"\*\*([^*]+)\*\*", nodes.get(f"d{day}-{b}", {}).get("text", ""))
    return names


def parse_target(text):
    """(sets, low, high, unit) from a target like '3 × 10-12', '2 × 15-20 s', '2-3 min'."""
    t = text.replace("*", "")
    m = re.search(r"(\d+)(?:-\d+)?\s*(?:rounds?\s*)?×\s*(\d+)(?:\s*-\s*(\d+))?\s*(s|min)?\b", t)
    if m:  # the unit is the one right after the range: "3 × 30-40 s" is seconds; "(5 s down)" elsewhere is not
        lo = int(m.group(2))
        return int(m.group(1)), lo, int(m.group(3) or lo), m.group(4) or "reps"
    m = re.search(r"(\d+)(?:\s*-\s*(\d+))?\s*(min|s)\b", t)
    if m:
        lo = int(m.group(1))
        return 1, lo, int(m.group(2) or lo), m.group(3)
    return 3, 0, 0, "reps"


def best_of(text):
    nums = [int(x) for x in re.findall(r"\b(\d{1,3})\b", re.sub(r"\(?\d{4}-\d{2}-\d{2}\)?", "", text))]
    return max(nums) if nums else None


def exercises(day, minimum=False):
    rows = level_rows()
    out, used = [], set()
    for name in routine_order(day):
        want = norm(name)
        best = max(rows, key=lambda r: len(want & norm(r["name"])) / max(1, len(want | norm(r["name"]))), default=None)
        if not best or best["name"] in used or not (want & norm(best["name"])):
            continue
        if len(want & norm(best["name"])) / max(1, len(want)) < 0.5:
            continue
        used.add(best["name"])
        sets, lo, hi, unit = parse_target(best["next"])
        out.append({"name": best["name"], "target": companion.plain(best["next"]), "last": companion.plain(best["last"]),
                    "sets": sets, "lo": lo, "hi": hi, "unit": unit, "best": best_of(best["last"])})
    if minimum:  # 15-minute version: hang, the first two upper-body moves, 2 sets each
        upper = [e for e in out if e["name"] != "Dead hang"][:2]
        out = [e for e in out if e["name"] == "Dead hang"][:1] + upper
        for e in out:
            e["sets"] = min(e["sets"], 2)
    return out


COLUMNS = {"hang": "Dead hang", "legs": ("squat", "lunge", "bridge", "calf"),
           "core": ("leg raise", "hollow", "cobra", "animal flow", "run_parents_run")}


def column(name):
    n = name.lower()
    if "hang" in n and "leg raise" not in n:
        return 2
    if any(k in n for k in COLUMNS["legs"]) and "deep squat" not in n:
        return 4
    if any(k in n for k in COLUMNS["core"]):
        return 5
    return 3


def fmt(e, vals):
    unit = {"s": " s", "min": " min"}.get(e["unit"], "")
    return ", ".join(str(v) for v in vals) + unit


def write_log(sess, notes):
    """Add the session row and update each exercise's Last session cell."""
    lines = read(LOG).splitlines()
    rows = {r["name"]: r for r in level_rows()}
    day = sess["date"]
    for e in sess["ex"]:
        vals = sess["results"].get(e["name"])
        r = rows.get(e["name"])
        if vals and r:
            c = lines[r["line"]].strip().strip("|").split("|")
            c[3] = f" {fmt(e, vals)} ({day}) "
            lines[r["line"]] = "|" + "|".join(c) + "|"
    cells = ["", "", "", ""]
    for e in sess["ex"]:
        vals = sess["results"].get(e["name"])
        if vals:
            i = column(e["name"]) - 2
            cells[i] = (cells[i] + " · " if cells[i] else "") + (fmt(e, vals) if i == 0 else f"{e['name']} {fmt(e, vals)}")
    minutes = max(1, round((now() - datetime.fromisoformat(sess["start"])).total_seconds() / 60))
    duration = f"{minutes} min" if any(sess["results"].values()) else "–"
    row = f"| {day} | {sess['day']} | {cells[0] or '–'} | {cells[1] or '–'} | {cells[2] or '–'} | {cells[3] or '–'} | {duration} | {notes} |"
    inside, last = False, None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            inside = line.startswith("## Session log")
        elif inside and line.startswith("| 20"):
            last = i
    if last is None:
        raise RuntimeError("Session log table not found in the strength log")
    lines.insert(last + 1, row)
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


# ---------- the habit tracker ----------
def strength():
    rep = coach.report() if habits.configured() else None
    return coach.by_alias("workout", rep) if rep else None


def strength_reason(text, kind, day=None):
    s = strength()
    try:
        coach.add_reason([s["name"] if s else "🏋️ Strength"], text, kind, day)
    except Exception as e:
        print("reason not saved:", e)


def tick_app(note):
    s = strength()
    if s and not s["done_today"]:
        try:
            coach.log(s, 1, note)
        except Exception as e:
            print("app tick failed:", e)


def sync_lock(today):
    """Strength is non-negotiable in the app on training days only (the app has no weekly habits)."""
    entry = days().get(today.isoformat())
    want = bool(entry and entry["status"] in ("planned", "started", "done"))
    if S.get("lock") == [today.isoformat(), want] or not habits.configured():
        return
    s = strength()
    if not s:
        return
    try:
        habits._call("PATCH", f"/api/integration/habits/{s['id']}", {"nonNegotiable": want})
        S["lock"] = [today.isoformat(), want]
    except Exception as e:
        print("lock sync failed:", e)


# ---------- the day ----------
def next_day_number():
    return companion.training_status()["next"]


def ask_time(d, edit=None):
    label = "today" if d == now().date() else f"tomorrow ({DAYS[d.weekday()]})" if d == now().date() + timedelta(days=1) else f"{d:%a %d}"
    rows = [[(t, f"tm:{d.isoformat()}:{t}") for t in TIME_CHOICES[:4]], [(t, f"tm:{d.isoformat()}:{t}") for t in TIME_CHOICES[4:]],
            [("✍️ Other time", f"to:{d.isoformat()}"), ("🤕 Can't", f"cx:{d.isoformat()}")]]
    days()[d.isoformat()]["asked"] = now().isoformat(timespec="minutes")
    days()[d.isoformat()]["asks"] = days()[d.isoformat()].get("asks", 0) + 1
    n = next_day_number()
    send(say(f"ask what time they will train {label}", f"session: {DAY_NAMES[n]} (~40 min); this week {week_done(d)}/{target()}",
             f"🏋️ {DAY_NAMES[n]} is {label}. What time will you train?"), rows, edit=edit)


def go_buttons(d, entry):
    rows = [[("▶️ Started", f"st:{d.isoformat()}")]]
    second = [("⚡ 15-min version", f"mn:{d.isoformat()}")] if entry.get("nags", 0) >= 2 else []
    if entry.get("snoozes", 0) < 2:
        second.append(("⏰ +30 min", f"sz:{d.isoformat()}"))
    second.append(("🤕 Can't today", f"cx:{d.isoformat()}"))
    return rows + [second]


NAG_TONE = ["a first reminder: friendly", "second reminder: firmer, offer the 15-minute version",
            "third reminder: direct, no excuses, the 15-minute version still beats nothing",
            "fourth: blunt, remind them that missing twice is how habits die",
            "fifth: very short and insistent", "last call of the day: the 15-minute version, now"]


def tick():
    t = now()
    today = t.date()
    if not BOT.owner:
        return
    sync_lock(today)
    # a session started and never finished is logged with the sets you recorded
    if S.get("session") and S["session"]["date"] < today.isoformat():
        finish_session()
    # sessions left open from earlier days become missed
    for k, v in list(days().items()):
        if date.fromisoformat(k) < today and v["status"] in ("planned", "started"):
            v["status"] = "missed"
            S.setdefault("to_report", []).append(k)
    if quiet(t):
        return
    m = monday(today)
    # ticked in the habit tracker without a session here: stop pushing, ask how it went
    s = strength() if habits.configured() else None
    if s and s["done_today"] and today not in logged() and not S.get("session") and once(f"ticked:{today}"):
        days().setdefault(today.isoformat(), {"time": None, "status": "planned"})["status"] = "ticked"
        send(say("the athlete ticked Strength in the habit tracker but didn't log the session with you: ask how it went",
                 f"{DAY_NAMES[next_day_number()]}; this week {week_done(today)}/{target()}",
                 "💪 You ticked Strength in the app. How did it go? Log your sets so I can track your progress."),
             [[("📝 Log my sets now", f"st:{today.isoformat()}")], [("🗣 Just tell you how it went", "tk:")]])
    # weekly report and plan (Sunday night), automatic confirm (Monday morning)
    pd, pt = PLAN_TIME.split()
    if DAYS.index(pd[:3].title()) == today.weekday() and t >= at(today, pt) and once(f"plan:{today}"):
        weekly_report(today)
        offer_plan(m + timedelta(days=7), "Next week:")
    if S.get("weeks", {}).get(m.isoformat()) != "confirmed" and t >= at(today, MORNING):
        draft = S.get("draft")
        if draft and draft["monday"] == m.isoformat() and draft.get("made", "") < today.isoformat():  # Sunday's plan, unanswered
            confirm_plan(reason="unconfirmed plan, confirmed automatically")
        elif once(f"autoplan:{m}"):
            offer_plan(m, "Let's plan this week.")
    # missed days: ask why, then make today count
    if S.get("to_report") and t >= at(today, MORNING):
        for k in S.pop("to_report"):
            report_missed(date.fromisoformat(k), today)
    # sick or in pain: a check-in every morning; no pushing until you're better
    sick = S.get("sick")
    if sick:
        if t >= at(today, MORNING) and sick.get("asked") != today.isoformat():
            sick["asked"] = today.isoformat()
            send(say("morning check-in after the athlete reported being sick or in pain", f"since {sick['since']}: {sick['reason']}",
                     "How do you feel today?"), [[("💪 Better, let's train", "sk:ok"), ("🤒 Still not OK", "sk:no")]])
        return
    # every day: mobility until it's ticked, and the training tasks on your list
    s_mob = BOT.app_habit("mobility")
    start = at(today, MOBILITY_TIME)
    if s_mob and s_mob.get("reminder"):
        start = min(start, at(today, s_mob["reminder"]))
    BOT.nag_habit("mobility", "mobility", start, t, MOBILITY_EVERY, MOBILITY_MAX,
                  lambda s, n: say(f"daily mobility isn't done yet; reminder {n} of {MOBILITY_MAX}: 5 minutes, no excuses",
                                   f"habit {s['name']}", ["🚶 5 minutes of mobility today. Now is good.",
                                                          "🚶 Still no mobility. 5 minutes, then you're free.",
                                                          "🚶 Mobility keeps the strength work healthy. 5 minutes.",
                                                          "🚶 Missing twice is how habits die. 5 minutes.",
                                                          "🚶 Mobility.", "🚨 Last call: 5 minutes of mobility, now."][min(n - 1, 5)]),
                  [[("📄 The routine", "mob:")]])
    BOT.nag_habit("protein", "protein", at(today, PROTEIN_TIME), t, 60, 3,
                  lambda s, n: say("evening protein check: how far from today's protein target; suggest one easy food to close it",
                                   f"{habits.fmt(s['today_value'])} of {habits.fmt(s['target'])} g today",
                                   f"🥩 Protein: {habits.fmt(s['today_value'])}/{habits.fmt(s['target'])} g today. "
                                   "Close the gap: Greek yogurt, a can of tuna, 3 eggs or a shake."),
                  [[("+20 g", "hq:protein:20"), ("+30 g", "hq:protein:30"), ("+40 g", "hq:protein:40")]])
    BOT.remind_tasks("Coach", t, TASK_TIMES)
    # rest days: one note on the routine (Sunday: tonight's planning)
    if not days().get(today.isoformat()) and t >= at(today, MORNING) and once(f"rest:{today}"):
        rest_note(today)
    # tomorrow's time, the evening before
    tomorrow = today + timedelta(days=1)
    te = days().get(tomorrow.isoformat())
    if te and te["status"] == "planned" and not te.get("time") and t >= at(today, ASK_TIME) and once(f"ask:{tomorrow}"):
        ask_time(tomorrow)
    entry = days().get(today.isoformat())
    if not entry or entry["status"] != "planned" or week_done(today) >= target():
        return
    if not entry.get("time"):
        last = entry.get("asked")
        if t >= at(today, MORNING) and (not last or t - datetime.fromisoformat(last) >= timedelta(hours=2)) and entry.get("asks", 0) < 4:
            ask_time(today)
        return
    start = at(today, entry["time"])
    if start - timedelta(minutes=30) <= t < start and once(f"heads:{today}:{entry['time']}"):
        n = next_day_number()
        send(say("heads-up 30 minutes before the session", f"{DAY_NAMES[n]} at {entry['time']}",
                 f"⏳ {DAY_NAMES[n]} in 30 min. Water, mat, phone away."))
        send_routine(f"🏋️ {DAY_NAMES[n]} at {entry['time']}")
    if t >= start and once(f"go:{today}:{entry['time']}"):
        send(say("it's time to start the session", f"{DAY_NAMES[next_day_number()]}, planned {entry['time']}",
                 f"🟢 {entry['time']}. Time to train. Warm-up first, then tap Started."), go_buttons(today, entry))
        entry["last_nag"] = t.isoformat()
        return
    last = datetime.fromisoformat(entry.get("last_nag") or start.isoformat())
    if (t >= start + timedelta(minutes=NAG_GRACE) and entry.get("nags", 0) < NAG_MAX
            and t - last >= timedelta(minutes=NAG_EVERY)):
        n = entry.get("nags", 0)
        entry["nags"] = n + 1
        entry["last_nag"] = t.isoformat()
        late = int((t - start).total_seconds() // 60)
        send(say(f"the athlete hasn't started; reminder {n + 1} of {NAG_MAX}: {NAG_TONE[min(n, len(NAG_TONE) - 1)]}",
                 f"planned {entry['time']}, {late} min late; this week {week_done(today)}/{target()}",
                 ["⏰ You planned {t}. Still time: start now.", "⏰ {late} min late. Can't do 40? Do the 15-minute version.",
                  "⏰ No excuses today. 15 minutes counts.", "⏰ Missing twice is how habits die. Start.",
                  "⏰ Still waiting.", "🚨 Last call today: the 15-minute version, now."][min(n, 5)].format(t=entry["time"], late=late)),
             go_buttons(today, entry))


def rest_note(today):
    """A rest day: what's next, and one exercise to think about (rotating through your current targets)."""
    nxt = [d for d in planned(today) if d > today]
    rows = [r for r in level_rows() if r["next"]]
    focus = rows[today.toordinal() % len(rows)] if rows else None
    parts = [f"😌 Rest day. This week: {week_done(today)}/{target()}."]
    if nxt:
        e = days()[nxt[0].isoformat()]
        parts.append(f"Next: {DAY_NAMES[next_day_number()]} on {DAYS[nxt[0].weekday()]}" + (f" at {e['time']}" if e.get("time") else "") + ".")
    if focus:
        parts.append(f"Think about your {focus['name']} today: next target {companion.plain(focus['next'])} "
                     f"(last: {companion.plain(focus['last']) or '–'}).")
    if today.weekday() == 6:
        parts.append(f"Tonight at {PLAN_TIME.split()[-1]} we plan next week.")
    parts.append("Mobility still counts today.")
    send(say("rest day: no training; one short note on the routine and what's next", " ".join(parts), " ".join(parts)))


def report_missed(d, today):
    if days().get(d.isoformat(), {}).get("reason"):  # already explained (e.g. a logged football match)
        return
    s_left = target() - week_done(today) - len([p for p in planned(today) if p >= today])
    send(say("yesterday's planned session didn't happen; ask what got in the way, no scolding",
             f"missed {DAYS[d.weekday()]} {d:%d %b}; this week {week_done(today)}/{target()}",
             f"{DAYS[d.weekday()]}'s session didn't happen. What got in the way?"),
         [[("💼 Work", "mr:Work"), ("😴 Tired", "mr:Tired"), ("📱 Distracted", "mr:Distracted")],
          [("🤒 Sick or pain", "mr:Sick or pain"), ("✍️ Other", "mr:")]])
    S["pending"] = {"kind": "reason", "why": "missed", "day": d.isoformat()}
    if s_left > 0 and today.isoformat() not in days() and monday(today) == monday(d):
        dropped = make_training_day(today)
        msg = "Today becomes a training day. I'll ask for the time."
        if dropped:
            msg += f" The week can't fit everything now: {target() - len(dropped)} is this week's new max. Keep the habit alive."
        send(msg)


def weekly_report(today):
    m = monday(today)
    done = week_done(today)
    missed = [k for k, v in days().items() if v["status"] == "missed" and m <= date.fromisoformat(k) <= today]
    prs = [p for p in S.get("prs", []) if p["date"] >= m.isoformat()]
    other = ", ".join(f"{a['name']} {dur(a['minutes'])}" for a in activities(m))
    facts = (f"sessions {done}/{target()}; missed {len(missed)}; records: "
             + (", ".join(f"{p['name']} {p['value']}" for p in prs) or "none") + (f"; other sports: {other}" if other else ""))
    fallback = f"📊 This week: {done}/{target()} sessions." + (f" Records: {', '.join(p['name'] + ' ' + str(p['value']) for p in prs)}." if prs else "") \
        + (f" Also: {other}." if other else "") + (" Every session counted. 💪" if done >= target() else " Next week we get all of them.")
    send(say("Sunday weekly report: sessions, records, one thing to improve next week", facts, fallback))


# ---------- the guided session ----------
def start_session(d, day=None, minimum=False):
    day = day or next_day_number()
    entry = days().setdefault(d.isoformat(), {"time": now().strftime("%H:%M"), "status": "planned"})
    entry["status"] = "started"
    ex = exercises(day, minimum)
    if not ex:
        send("I couldn't read today's exercises from the routine canvas and the strength log. Log it with the capture bot.")
        return
    S["session"] = {"date": d.isoformat(), "day": day, "start": now().isoformat(timespec="seconds"), "minimum": minimum,
                    "ex": ex, "i": 0, "results": {}}
    send(say("the athlete just started the session", f"{DAY_NAMES[day]}{', 15-minute version' if minimum else ''}; {len(ex)} exercises",
             f"💪 {DAY_NAMES[day]}{' (15-min version)' if minimum else ''}. Warm-up first (5 min), then log every set here."))
    prompt_set()


def value_buttons(e):
    if e["unit"] == "s":
        values = [10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 75, 90]
    elif e["unit"] == "min":
        values = [1, 2, 3, 4, 5]
    else:
        top = max(e["hi"], e["best"] or 0) + 3
        values = list(range(0, min(top, 25) + 1))
    rows = [[(str(v), f"r:{v}") for v in values[i:i + 6]] for i in range(0, len(values), 6)]
    return rows + [[("↩️ Undo set", "ru"), ("⏭ Skip exercise", "rs"), ("🏁 Finish", "rf")]]


def prompt_set(edit=None):
    sess = S["session"]
    if sess["i"] >= len(sess["ex"]):
        finish_session()
        return
    e = sess["ex"][sess["i"]]
    vals = sess["results"].get(e["name"], [])
    unit = {"s": "seconds", "min": "minutes"}.get(e["unit"], "reps")
    done = f"\nSo far: {fmt(e, vals)}" if vals else ""
    text = (f"{sess['i'] + 1}/{len(sess['ex'])} · {e['name']}\nTarget: {e['target']}\nLast: {e['last'] or '–'}{done}\n\n"
            f"Set {len(vals) + 1} of {max(e['sets'], len(vals) + 1)}: how many {unit}?")
    buttons = value_buttons(e)
    if len(vals) >= e["sets"]:
        nxt = sess["ex"][sess["i"] + 1]["name"] if sess["i"] + 1 < len(sess["ex"]) else "finish"
        text = f"{sess['i'] + 1}/{len(sess['ex'])} · {e['name']} ✅ {fmt(e, vals)}\n" + exercise_verdict(e, vals)
        buttons = [[(f"➡️ Next: {nxt}", "rn")], [("➕ One more set", "rm"), ("↩️ Undo set", "ru")], [("🏁 Finish", "rf")]]
    sess["msg"] = send(text, buttons, edit=edit)
    S["pending"] = {"kind": "reps"}


def exercise_verdict(e, vals):
    notes = []
    if e["best"] is not None and max(vals) > e["best"]:
        notes.append(f"🏆 New best: {max(vals)} (was {e['best']}).")
    if e["hi"] and len(vals) >= e["sets"] and all(v >= e["hi"] for v in vals[:e["sets"]]):
        notes.append("🎯 Top of the range in every set: next time, the harder version.")
    return " ".join(notes) or "Logged."


def record(v):
    sess = S.get("session")
    if not sess:
        return
    e = sess["ex"][sess["i"]]
    sess["results"].setdefault(e["name"], []).append(v)
    prompt_set(edit=sess.get("msg"))


def session_button(data):
    sess = S.get("session")
    if not sess:
        send("No session running. /train starts one.")
        return
    e = sess["ex"][min(sess["i"], len(sess["ex"]) - 1)]
    if data.startswith("r:"):
        record(int(data[2:]))
    elif data == "ru":
        vals = sess["results"].get(e["name"])
        if vals:
            vals.pop()
        elif sess["i"] > 0:
            sess["i"] -= 1
        prompt_set(edit=sess.get("msg"))
    elif data == "rm":
        e["sets"] = len(sess["results"].get(e["name"], [])) + 1
        prompt_set(edit=sess.get("msg"))
    elif data in ("rn", "rs"):
        sess["i"] += 1
        prompt_set()
    elif data == "rf":
        finish_session()


def finish_session():
    sess = S.pop("session", None)
    S.pop("pending", None)
    if not sess:
        return
    done = {k: v for k, v in sess["results"].items() if v}
    if not done:
        days().get(sess["date"], {})["status"] = "planned"
        send("Nothing logged, so the session is still open. /train to start again.")
        return
    prs, tops = [], []
    for e in sess["ex"]:
        vals = done.get(e["name"])
        if not vals:
            continue
        if e["best"] is not None and max(vals) > e["best"]:
            prs.append({"date": sess["date"], "name": e["name"], "value": max(vals)})
        if e["hi"] and len(vals) >= e["sets"] and all(v >= e["hi"] for v in vals[:e["sets"]]):
            tops.append(e["name"])
    S.setdefault("prs", []).extend(prs)
    S["prs"] = S["prs"][-100:]
    notes = "Logged by the coach, set by set." + (" 15-minute version." if sess["minimum"] else "")
    if tops:
        notes += f" Top of range: {', '.join(tops)} (step up the variation)."
    if prs:
        notes += " Records: " + ", ".join(f"{p['name']} {p['value']}" for p in prs) + "."
    try:
        write_log(sess, notes)
    except Exception as e:
        send(f"⚠️ I couldn't write the strength log ({e}). Your sets: " +
             "; ".join(f"{k} {', '.join(map(str, v))}" for k, v in done.items()))
    days().setdefault(sess["date"], {})["status"] = "done"
    tick_app(f"{DAY_NAMES[sess['day']]}, logged with the coach")
    today = date.fromisoformat(sess["date"])
    facts = (f"{DAY_NAMES[sess['day']]} done; {len(done)} exercises; this week {week_done(today)}/{target()}; "
             f"records: {', '.join(p['name'] + ' ' + str(p['value']) for p in prs) or 'none'}; "
             f"top of range: {', '.join(tops) or 'none'}")
    fallback = (f"✅ {DAY_NAMES[sess['day']]} done. This week: {week_done(today)}/{target()}."
                + (f" 🏆 {', '.join(p['name'] + ' ' + str(p['value']) for p in prs)}." if prs else "")
                + (f" 🎯 Ready to step up: {', '.join(tops)}." if tops else ""))
    send(say("the session is finished: congratulate with their numbers, name records and what steps up next", facts, fallback)
         + team.after_training(today))


# ---------- other sports and activities (football, padel, runs…) ----------
ACTIVITY_LOG = ROOT / "02 - Areas" / "Training" / "Activity Log.md"
SPORTS = [  # (name, emoji, hard on the legs, words that name it)
    ("Football", "⚽", True, ("football", "fútbol", "futbol", "soccer", "futsal", "fulbito", "partido")),
    ("Padel", "🎾", True, ("padel", "pádel")),
    ("Tennis", "🎾", True, ("tennis", "tenis")),
    ("Basketball", "🏀", True, ("basketball", "basket", "básquet", "basquet")),
    ("Volleyball", "🏐", True, ("volleyball", "voley", "vóley")),
    ("Running", "🏃", True, ("running", "run", "ran", "jog", "jogging", "jogged", "correr", "corrí", "trote")),
    ("Cycling", "🚴", True, ("cycling", "cycled", "bike", "biked", "biking", "bici", "bicicleta")),
    ("Swimming", "🏊", False, ("swim", "swam", "swimming", "natación", "nadé", "nadar")),
    ("Hiking", "🥾", True, ("hike", "hiked", "hiking", "trekking", "senderismo")),
    ("Climbing", "🧗", False, ("climbing", "climbed", "escalada", "bouldering")),
    ("Yoga", "🧘", False, ("yoga",)),
    ("Combat sports", "🥊", False, ("boxing", "boxeo", "kickboxing", "muay thai", "bjj", "jiu jitsu", "judo")),
    ("Surf", "🏄", False, ("surf", "surfing", "surfed")),
    ("Long walk", "🚶", True, ("long walk", "caminata")),
    ("Dance", "💃", True, ("dance", "danced", "dancing", "bailé", "bailar")),
]
WEEKDAYS = {"monday": 0, "lunes": 0, "tuesday": 1, "martes": 1, "wednesday": 2, "miércoles": 2, "miercoles": 2,
            "thursday": 3, "jueves": 3, "friday": 4, "viernes": 4, "saturday": 5, "sábado": 5, "sabado": 5,
            "sunday": 6, "domingo": 6}
EFFORTS = {"easy": "😌 Easy", "medium": "😤 Medium", "hard": "🥵 Hard"}


def has_word(text, word):
    return re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", text) is not None


def parse_minutes(t):
    t = t.lower()
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:h|hs|hr|hrs|hour|hours|hora|horas)(?![a-z])(?:\s*(?:and|y)?\s*(\d+)\s*(?:m|min|mins|minutes|minutos)?(?![a-z]))?", t)
    if m:
        return round(float(m[1].replace(",", ".")) * 60) + int(m[2] or 0)
    m = re.search(r"(\d+)\s*(?:'|m|min|mins|minute|minutes|minuto|minutos)\b", t)
    if m:
        return int(m[1])
    if re.search(r"\b(an?|one|una) (hour|hora) (and a half|y media)\b|\bhour and a half\b", t):
        return 90
    if re.search(r"\bhalf an hour\b|\bmedia hora\b", t):
        return 30
    if re.search(r"\b(two|dos) (hours|horas)\b", t):
        return 120
    if re.search(r"\b(an?|one|una) (hour|hora)\b", t):
        return 60
    if re.fullmatch(r"\s*\d{2,3}\s*", t):  # a bare number answering "how long?"
        return int(t)
    return None


def parse_day(t, today):
    t = t.lower()
    if re.search(r"day before yesterday|anteayer|antes de ayer", t):
        return today - timedelta(days=2)
    if re.search(r"\byesterday\b|\bayer\b|\blast night\b|\banoche\b", t):
        return today - timedelta(days=1)
    for word, wd in WEEKDAYS.items():
        if has_word(t, word):
            return today - timedelta(days=(today.weekday() - wd) % 7)
    return today


def parse_activity(text, today, loose=False):
    """{'name', 'emoji', 'legs', 'date', 'minutes', 'note'} for a sport you did, or None. Questions and plans aren't logs."""
    t = text.lower()
    if "?" in t or re.search(r"\b(tomorrow|mañana|going to|gonna|will|voy a|vamos a|should i|can i|puedo)\b", t):
        return None
    sport = next(((n, e, l) for n, e, l, words in SPORTS if any(has_word(t, w) for w in words)), None)
    if not sport:
        if not loose or not text.strip():
            return None
        name = re.sub(r"\b(yesterday|today|ayer|hoy|last night|anoche|i|did|played|went|some|\d+\s*(min|minutes|h|hours)?)\b", " ",
                      text, flags=re.I)
        name = re.sub(r"\s+", " ", name).strip(" .,") or text.strip()
        sport = (name[:40].capitalize(), "🏅", False)
    return {"name": sport[0], "emoji": sport[1], "legs": sport[2], "date": parse_day(t, today).isoformat(),
            "minutes": parse_minutes(t), "note": text.strip()[:200]}


def dur(minutes):
    h, m = divmod(int(minutes), 60)
    return f"{h} h {m:02d}" if h and m else f"{h} h" if h else f"{m} min"


def day_label(d):
    today = now().date()
    if d == today:
        return "today"
    if d == today - timedelta(days=1):
        return "yesterday"
    return f"{DAYS[d.weekday()]} {d.day}"


def ask_activity():
    """Fill in what's missing (how long, how hard), then save."""
    a = S["activity"]
    head = f"{a['emoji']} {a['name']}, {day_label(date.fromisoformat(a['date']))}"
    if not a.get("minutes"):
        send(f"{head}. How long?", [[("30 min", "am:30"), ("1 h", "am:60"), ("1 h 30", "am:90"), ("2 h", "am:120")]])
    elif not a.get("effort"):
        send(f"{head}, {dur(a['minutes'])}. How hard was it?", [[(v, f"ae:{k}") for k, v in EFFORTS.items()]])
    else:
        save_activity()


def activities(since):
    return [a for a in S.get("activities", []) if a["date"] >= since.isoformat()]


def write_activity(a):
    if not ACTIVITY_LOG.exists():
        ACTIVITY_LOG.write_text(
            f"---\ncreated: {now().date().isoformat()}\ntype: note\nstatus: active\ntags: [area/training, coach, activity-log]\n"
            "source: own idea\n---\nSports and activities outside the strength routine (football, padel, runs…), logged by your "
            "[[Coach]] when you tell it in Telegram. They don't replace strength sessions: the Coach uses them to plan around "
            "fatigue and counts them in the weekly report. Strength sessions are in [[Full-Body Strength Log]].\n\n"
            "## Log\n| Date | Activity | Duration | Effort | What you said |\n|---|---|---|---|---|\n",
            encoding="utf-8", newline="\n")
    lines = read(ACTIVITY_LOG).splitlines()
    said = a["note"].replace("|", "/").replace("\n", " ")
    row = f"| {a['date']} | {a['emoji']} {a['name']} | {dur(a['minutes'])} | {a['effort']} | {said} |"
    inside, last = False, None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            inside = line.startswith("## Log")
        elif inside and line.startswith("|"):
            last = i
    if last is None:
        lines += ["", "## Log", "| Date | Activity | Duration | Effort | What you said |", "|---|---|---|---|---|"]
        last = len(lines) - 1
    lines.insert(last + 1, row)
    ACTIVITY_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def save_activity():
    a = S.pop("activity")
    d, today = date.fromisoformat(a["date"]), now().date()
    try:
        write_activity(a)
    except Exception as e:
        send(f"⚠️ I couldn't write the Activity Log ({e}).")
        return
    S.setdefault("activities", []).append({k: a[k] for k in ("date", "name", "minutes", "effort", "legs")})
    S["activities"] = S["activities"][-60:]
    entry = days().get(a["date"])
    tomorrow = days().get((today + timedelta(days=1)).isoformat())
    facts = [f"✅ In your Activity Log: {a['emoji']} {a['name']}, {day_label(d)}, {dur(a['minutes'])}, {a['effort']}."]
    buttons = None
    if entry and d < today and entry["status"] in ("planned", "missed"):
        # a strength day that went to another sport: that's the reason, no need to ask
        if a["date"] in S.get("to_report", []):
            S["to_report"].remove(a["date"])
        if not entry.get("reason"):
            entry["reason"] = f"{a['name']} instead"
            strength_reason(f"Played {a['name']} ({dur(a['minutes'])}, {a['effort']}) instead", "missed", d)
        facts.append(f"That was a strength day, and {a['name']} doesn't replace the strength work. This week: {week_done(today)}/{target()}.")
    elif entry and d == today and entry["status"] == "planned":
        if a["effort"] == "hard":
            facts.append("Today is still a strength day. After a hard one: move it to tomorrow, or do the 15-minute version now.")
            buttons = [[("➡️ Move to tomorrow", "ax:"), ("⚡ 15-min version", f"mn:{today.isoformat()}")],
                       [("💪 I'll still train today", "ak:")]]
        else:
            facts.append("Today is still a strength day: the session stays.")
    elif tomorrow and tomorrow["status"] == "planned" and a["legs"] and a["effort"] == "hard" and d == today:
        facts.append("Tomorrow is a strength day: sleep, eat your protein, and if the squats feel heavy, slow them down instead of skipping.")
    else:
        facts.append(f"Good for conditioning and recovery; it doesn't count as a strength session. This week: {week_done(today)}/{target()}.")
    text = " ".join(facts)
    send(say("the athlete did a sport besides strength training: acknowledge it with their numbers and say how it fits this week",
             text, text), buttons)


def start_activity(text, today, loose=False):
    a = parse_activity(text, today, loose)
    if not a:
        return False
    S["activity"] = a
    ask_activity()
    return True


# ---------- buttons, messages, commands ----------
def cancel_day(d, reason):
    entry = days().setdefault(d.isoformat(), {"time": None, "status": "planned"})
    sick = reason.lower().startswith(("sick", "pain", "injur"))
    entry["status"] = "cancelled"
    strength_reason(reason, "skipped")
    if sick:
        S["sick"] = {"since": d.isoformat(), "reason": reason, "asked": d.isoformat()}
        send(say("the athlete is sick or in pain: rest, no training, mobility only if it doesn't hurt; check in tomorrow",
                 reason, "Rest today. Mobility only if it doesn't hurt. I'll check on you tomorrow morning."))
        return
    tomorrow = d + timedelta(days=1)
    if monday(tomorrow) == monday(d) and tomorrow.isoformat() not in days():
        dropped = make_training_day(tomorrow)
        days()[tomorrow.isoformat()]["time"] = entry.get("time")
        extra = f" This week's max is now {target() - len(dropped)}." if dropped else ""
        send(say("the session moves to tomorrow; never skip twice", f"reason: {reason}; tomorrow {entry.get('time') or 'time to set'}",
                 f"OK: tomorrow{' at ' + entry['time'] if entry.get('time') else ''}. Never skip twice.{extra}"))
    else:
        send("Noted. This week can't fit it anymore; we go again next week.")


def button(q):
    data = q.get("data", "")
    msg_id = q.get("message", {}).get("message_id")
    if BOT.common_button(data, now()):
        return
    if data == "mob:":
        BOT.send_file(MOBILITY_NOTE, "🚶 Your 5-minute mobility routine. Tap ✅ Done on my reminder when you finish.")
        return
    if data.startswith("pt:"):
        draft = S.get("draft")
        if draft:
            d = data[3:]
            draft["days"] = sorted(set(draft["days"]) ^ {d})
            m = date.fromisoformat(draft["monday"])
            chosen = [date.fromisoformat(x) for x in draft["days"]]
            send(plan_text(m, chosen, "Your plan:"), plan_buttons(m, chosen), edit=msg_id)
    elif data.startswith("pc:"):
        confirm_plan()
    elif data.startswith("tm:"):
        _, d, t = data.split(":", 2)
        set_time(date.fromisoformat(d), t, msg_id)
    elif data.startswith("to:"):
        S["pending"] = {"kind": "time", "day": data[3:]}
        send("Type the time, e.g. 18:45.")
    elif data.startswith("cx:"):
        d = data[3:]
        S["pending"] = {"kind": "reason", "why": "cancel", "day": d}
        send("What's stopping you?", [[("💼 Work", "cr:Work"), ("😴 Tired", "cr:Tired")],
                                       [("🤒 Sick or pain", "cr:Sick or pain"), ("✍️ Other", "cr:")]])
    elif data.startswith("cr:"):
        reason = data[3:]
        d = date.fromisoformat(S.get("pending", {}).get("day") or now().date().isoformat())
        if not reason:
            send("Tell me in a few words.")
            return
        if reason == "Tired":
            S["pending"] = {"kind": "reason", "why": "cancel", "day": d.isoformat()}
            send(say("the athlete is tired: offer the 15-minute version before moving the session",
                     "tired after work is their known obstacle", "Tired is exactly when the 15-minute version wins. Do that instead?"),
                 [[("⚡ 15-min version", f"mn:{d.isoformat()}"), ("➡️ Move to tomorrow", "cr:Tired, moved")]])
            return
        S.pop("pending", None)
        cancel_day(d, reason)
    elif data.startswith("mr:"):
        reason = data[3:]
        pending = S.get("pending", {})
        if not reason:
            send("Tell me in a few words.")
            return
        S.pop("pending", None)
        missed_day = date.fromisoformat(pending["day"]) if pending.get("day") else None
        strength_reason(reason, "missed", missed_day)
        if reason.lower().startswith("sick"):
            cancel_day(now().date(), reason)
        else:
            send("Noted in Habit Reasons. Today we make it count.")
    elif data == "sk:ok":
        S.pop("sick", None)
        today = now().date()
        entry = days().get(today.isoformat())
        if entry and entry["status"] == "cancelled":
            entry["status"] = "planned"
        send(say("the athlete feels better after being sick: welcome back, start gently today", "",
                 "Welcome back. Today we restart gently: same targets, no records needed."))
    elif data == "sk:no":
        today = now().date()
        entry = days().get(today.isoformat())
        if entry and entry["status"] in ("planned", "started"):
            entry["status"] = "cancelled"
        send("Rest again today. I'll ask tomorrow morning. If it lasts more than a few days, see a doctor.")
    elif data == "tk:":
        S["pending"] = {"kind": "session_note", "day": now().date().isoformat()}
        send("Tell me in a few words: which day, what felt strong, what was hard.")
    elif data.startswith("st:"):
        start_session(date.fromisoformat(data[3:]))
    elif data.startswith("mn:"):
        start_session(date.fromisoformat(data[3:]), minimum=True)
    elif data.startswith("sz:"):
        d = date.fromisoformat(data[3:])
        entry = days()[d.isoformat()]
        h, m = hhmm(entry["time"])
        new = (datetime(2000, 1, 1, h, m) + timedelta(minutes=30)).strftime("%H:%M")
        entry.update(time=new, snoozes=entry.get("snoozes", 0) + 1, nags=0, last_nag=None)
        send(f"⏰ {new} then. I'll be here.")
    elif data in ("ru", "rm", "rn", "rs", "rf") or data.startswith("r:"):
        session_button(data)
    elif data.startswith("am:") and S.get("activity"):
        S["activity"]["minutes"] = int(data[3:])
        ask_activity()
    elif data.startswith("ae:") and S.get("activity"):
        S["activity"]["effort"] = data[3:]
        ask_activity()
    elif data == "ax:":
        cancel_day(now().date(), "Played another sport today")
    elif data == "ak:":
        send("Good. Warm up longer than usual and keep the form clean.")
    save_state()


def set_time(d, t, edit=None):
    entry = days().setdefault(d.isoformat(), {"time": None, "status": "planned"})
    entry.update(time=t, nags=0, last_nag=None)
    S.pop("pending", None)
    send(f"✅ {DAYS[d.weekday()]} at {t}. I'll remind you 30 min before.", edit=edit)


def text_message(text):
    pending = S.get("pending") or {}
    kind = pending.get("kind")
    t = text.strip()
    if kind == "reps" and re.fullmatch(r"\d{1,3}", t):
        record(int(t))
    elif kind == "time" and re.fullmatch(r"([01]?\d|2[0-3])[:.h]([0-5]\d)", t):
        h, m = re.split(r"[:.h]", t)
        set_time(date.fromisoformat(pending["day"]), f"{int(h):02d}:{m}")
    elif kind == "session_note":
        S.pop("pending", None)
        sess = {"date": pending["day"], "day": next_day_number(), "start": now().isoformat(timespec="seconds"), "ex": [], "results": {}}
        try:
            write_log(sess, f"Ticked in the habit tracker, sets not logged. How it went: {t}")
            send(say("the athlete told you how an unlogged session went: acknowledge, ask to log sets next time",
                     t, "Noted in your log. Next time log the sets with me: that's how I see you progress."))
        except Exception as e:
            send(f"⚠️ I couldn't write the strength log ({e}).")
    elif kind == "reason":
        S.pop("pending", None)
        if pending.get("why") == "plan":
            confirm_plan(reason=t)
        elif pending.get("why") == "cancel":
            cancel_day(date.fromisoformat(pending["day"]), t)
        else:
            strength_reason(t, "missed", date.fromisoformat(pending["day"]) if pending.get("day") else None)
            send("Noted in Habit Reasons. Today we make it count.")
    elif t.startswith("/"):
        command(t)
    elif BOT.feedback_text(t, now()) or BOT.maybe_feedback(t):
        pass
    elif S.get("activity") and not S["activity"].get("minutes") and parse_minutes(t):
        S["activity"]["minutes"] = parse_minutes(t)
        ask_activity()
    elif kind == "activity":
        S.pop("pending", None)
        if not start_activity(t, now().date(), loose=True):
            send("Tell me like this: football yesterday 90 min.")
    elif start_activity(t, now().date()):
        pass
    else:
        answer = claude(f"The athlete writes: {t}\nAnswer as their coach in under 120 words, plain text. Use the vault when "
                        "useful: index.md, Coach.md, the Training notes, Full-Body Strength Log, About Me. If they ask you to record "
                        "or change something in your Training notes, do it (your agent definition's writing rules) and say what "
                        "you changed.", tools=True, model="sonnet", timeout=240)
        send(answer or "I answer questions once Claude Code is connected (see Coach.md). Commands: /today /plan /train /time /skip /week")


def status_text():
    today = now().date()
    entry = days().get(today.isoformat())
    nxt = [d for d in planned(today) if d > today][:2]
    parts = [f"📊 This week: {week_done(today)}/{target()}."]
    if entry:
        parts.append(f"Today: {DAY_NAMES[next_day_number()]}, {entry['status']}" + (f" at {entry['time']}" if entry.get("time") else ", no time yet") + ".")
    else:
        parts.append("Today: rest day.")
    if nxt:
        parts.append("Next: " + ", ".join(f"{DAYS[d.weekday()]} {d.day}" for d in nxt) + ".")
    return " ".join(parts)


HELP = ("🏋️ I'm your coach: training and daily mobility. I plan your week on Sunday night, ask when you'll train, push you "
        "until you start and log every set with you. Every day I push your mobility until it's ticked.\n/today — today and this week\n/plan — change this week's days\n/train — start a session now (/train 1, /train 2)\n"
        "/time 18:30 — set today's time\n/skip — can't train today\n/activity — log another sport (or just write "
        "\"played football yesterday, 90 min\")\n/week — this week's report\n/feedback — tell me what to do differently (I learn it)\nOr just ask me anything.")


def command(text):
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    today = now().date()
    if cmd in ("/start", "/help"):
        send(HELP)
    elif cmd == "/today":
        send(status_text())
    elif cmd == "/plan":
        offer_plan(monday(today), "This week:")
    elif cmd == "/train":
        start_session(today, int(arg) if arg.strip() in ("1", "2") else None)
    elif cmd == "/time":
        if re.fullmatch(r"\s*([01]?\d|2[0-3]):[0-5]\d\s*", arg):
            h, m = arg.strip().split(":")
            set_time(today, f"{int(h):02d}:{m}")
        else:
            send("Usage: /time 18:30")
    elif cmd == "/skip":
        button({"id": "", "data": f"cx:{today.isoformat()}"})
    elif cmd == "/week":
        weekly_report(today)
    elif cmd == "/feedback":
        BOT.feedback_command(arg, now())
    elif cmd == "/activity":
        if not (arg.strip() and start_activity(arg, today, loose=True)):
            S["pending"] = {"kind": "activity"}
            send("What did you do? E.g. football yesterday 90 min, padel today 1 h, a 5 km run.")
    else:
        send("Unknown command. /help lists them.")


def handle(update):
    if "callback_query" in update:
        button(update["callback_query"])
    elif update.get("message", {}).get("text"):
        text_message(update["message"]["text"])


COMMANDS = [("today", "Today and this week"), ("plan", "Change this week's days"), ("train", "Start a session now"),
            ("time", "Set today's time, e.g. /time 18:30"), ("skip", "Can't train today"), ("activity", "Log another sport (football…)"),
            ("feedback", "Tell me what to do differently"),
            ("week", "This week's report"),
            ("help", "How the coach works")]


def main():
    BOT.run(tick, handle, COMMANDS)


if __name__ == "__main__":
    main()
