"""The team: your three bots and what each one knows about the others. Each bot owns its part:
  Assistant (telegram_bot.py): captures, goals, projects, pending tasks, the daily habits, the weekly review, /ask.
  Coach (trainer.py): training: the week plan, session times, nags, set-by-set logging.
  English Teacher (teacher.py): English: drills, grammar, speaking sessions, corrections.
Their Telegram usernames come from scripts/.env (ASSISTANT_BOT_USERNAME, TRAINER_BOT_USERNAME, TEACHER_BOT_USERNAME).
They share the vault, the habit tracker, and each other's state files (read-only here), so they don't talk over each other
and can point you to the next teammate. Zero tokens."""
import json, re
from datetime import date, timedelta
import companion
import habits

SCRIPTS = companion.SCRIPTS
MEMBERS = {
    "Assistant": ("ASSISTANT_BOT_USERNAME", "your general assistant: captures, goals, projects, pending tasks, the daily habits, the weekly review, questions about the vault"),
    "Coach": ("TRAINER_BOT_USERNAME", "your personal trainer: plans the training week, asks the time, pushes until you train, logs every set"),
    "Teacher": ("TEACHER_BOT_USERNAME", "your English teacher: drills on past mistakes, grammar sets, speaking sessions, corrections"),
}
OWNED_ALIASES = {"Coach": {"workout", "mobility"}, "Teacher": {"#english-practice"}}  # habits a companion bot pushes itself
# Pending tasks belong to the bot whose notes they link to; the Assistant keeps the rest.
TASK_LINKS = {"Coach": ("[[Coach", "[[Full-Body", "[[Training", "[[Daily Mobility", "[[Home Training", "[[Técnica de dominadas",
                        "[[Animal Flow", "[[Push-up Rotation"),
              "Teacher": ("[[English", "[[Daily Speaking", "[[Grammar")}


def handle(member):
    """The bot's @username from scripts/.env, or its role name when it isn't set."""
    name = habits.env(MEMBERS[member][0]).lstrip("@")
    return f"@{name}" if name else f"your {member}"


def on(member):
    return member == "Assistant" or bool(habits.env({"Coach": "TRAINER_BOT_TOKEN", "Teacher": "TEACHER_BOT_TOKEN"}[member]))


def owned_aliases():
    """Habit aliases the Assistant leaves to the companion bots that are on."""
    return {a for m, aliases in OWNED_ALIASES.items() if on(m) for a in aliases}


def related_tasks(member):
    """Open Pending Tasks that link to this bot's notes: [(line_no, text)]. member "Assistant" = the ones no teammate owns."""
    out, soon = [], date.today() + timedelta(days=2)
    for line, section, text, _ in companion.open_tasks():
        dates = [date.fromisoformat(d) for d in re.findall(r"20\d\d-\d\d-\d\d", text)]
        if dates and min(dates) > soon:  # dated later (e.g. a retest in November): not yet
            continue
        owners = [m for m, keys in TASK_LINKS.items() if on(m) and any(k in text for k in keys)]
        if member in owners or (member == "Assistant" and not owners and section in (companion.ADDED, companion.THIS_WEEK)):
            out.append((line, text))
    return out


def state(prefix):
    try:
        return json.loads((SCRIPTS / f"{prefix}.state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def clock(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


# ---------- what each teammate is doing today ----------
def training(today=None):
    """{'status': planned/started/done/ticked/cancelled/missed/rest, 'time', 'week', 'target', 'running'}"""
    today = today or date.today()
    st = state("trainer")
    entry = st.get("days", {}).get(today.isoformat())
    ts = companion.training_status(today)
    return {"status": entry["status"] if entry else "rest", "time": (entry or {}).get("time"),
            "week": ts["this_week"], "target": ts["target"],
            "running": bool(st.get("session")) and st["session"].get("date") == today.isoformat()}


def english(today=None):
    """{'speak_day', 'spoken', 'target', 'hit', 'skipped', 'streak', 'grammar_left'}"""
    today = today or date.today()
    st = state("teacher")
    e = st.get("days", {}).get(today.isoformat(), {})
    level = st.get("level", {"target": 120, "streak": 0})
    gs = st.get("gset") or {}
    left = (len(gs.get("ids", [])) - gs.get("answered", 0) + (0 if gs.get("rewrite") else 1)) if gs.get("date") == today.isoformat() else None
    spec = habits.env("TEACHER_SPEAK_DAYS") or "Mon-Sun"
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    if "-" in spec:
        a, b = (days.index(x.strip()[:3].title()) for x in spec.split("-"))
        speak = a <= today.weekday() <= b
    else:
        speak = days[today.weekday()] in {x.strip()[:3].title() for x in spec.split(",")}
    return {"speak_day": speak, "spoken": e.get("spoken", 0), "target": e.get("target_override") or level.get("target", 120),
            "hit": bool(e.get("hit")), "skipped": e.get("skipped"), "streak": level.get("streak", 0), "grammar_left": left}


def training_line(today=None):
    t = training(today)
    week = f"{t['week']}/{t['target']} this week"
    if t["running"]:
        return f"🏋️ Coach: session in progress · {week}"
    if t["status"] in ("done", "ticked"):
        return f"🏋️ Coach: trained today ✅ · {week}"
    if t["status"] in ("planned", "started"):
        return f"🏋️ Coach: training today{' at ' + t['time'] if t['time'] else ', time not set yet'} · {week}"
    if t["status"] == "cancelled":
        return f"🏋️ Coach: today's session moved · {week}"
    return f"🏋️ Coach: rest day · {week}"


def english_line(today=None):
    e = english(today)
    if e["hit"]:
        main = f"spoken {clock(e['spoken'])} ✅"
    elif e["skipped"]:
        main = "skipped today"
    elif e["speak_day"]:
        main = f"{clock(e['spoken'])} of {clock(e['target'])} spoken"
    else:
        main = "drills only today"
    grammar = f" · grammar: {e['grammar_left']} left" if e["grammar_left"] else ""
    return f"🗣 Teacher: {main}{grammar} · streak {e['streak']}"


def lines(today=None):
    out = []
    if on("Coach"):
        out.append(training_line(today))
    if on("Teacher"):
        out.append(english_line(today))
    return out


def context(me, today=None):
    """For a bot's Claude prompt: who the team is, who you are in it, and what the others report today."""
    parts = [f"You are the {me} in a team of three bots that help the same person; each owns its part:"]
    for name, (_, role) in MEMBERS.items():
        if on(name):
            parts.append(f"- {name} ({handle(name)}): {role}{' (you)' if name == me else ''}.")
    parts.append("Stay in your lane: point to the right teammate by name instead of doing their job. Today, the others report:")
    parts += [f"- {line}" for line in lines(today)]
    return "\n".join(parts)


# ---------- handoffs between teammates ----------
def after_training(today=None):
    """A line for the Coach to add after a session: what the Teacher still expects today."""
    if not on("Teacher"):
        return ""
    e = english(today)
    if e["speak_day"] and not e["hit"] and not e["skipped"]:
        return f"\n🗣 Next: your English Teacher still expects {clock(max(0, e['target'] - e['spoken']))} of speaking today."
    return ""


def after_english(today=None):
    """A line for the Teacher to add after the speaking target: what the Coach still expects today."""
    if not on("Coach"):
        return ""
    t = training(today)
    if t["status"] in ("planned", "started") and not t["running"]:
        return f"\n🏋️ Next: your Coach has training for you today{' at ' + t['time'] if t['time'] else ''}."
    return ""


def training_running():
    return on("Coach") and training()["running"]
