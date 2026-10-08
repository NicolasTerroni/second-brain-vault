"""Vault side of the bot's companion features: pending tasks, questions about you, the morning brief and the
Sunday review. Reads (and for tasks, edits) notes in the vault; no Telegram and no LLM here, so it costs no tokens
and can be tested on any host. Used by telegram_bot.py."""
import json, re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from vault import ROOT, frontmatter, read, vault_files

SCRIPTS = Path(__file__).resolve().parent
STATE_FILE = SCRIPTS / "telegram.state.json"          # reply map, last brief/review sent (git-ignored)
BACKUP_REQUEST = SCRIPTS / "rclone" / "backup_requested"  # picked up by backup.sh within a minute
LAST_BACKUP = SCRIPTS / "rclone" / "last_backup"
LAST_FAILURE = SCRIPTS / "rclone" / "last_failure"
INBOX = ROOT / "00 - Inbox"
ABOUT = ROOT / "02 - Areas" / "About Me"
PENDING_TASKS = ABOUT / "Pending Tasks.md"
QUESTIONS = ABOUT / "Questions About Me.md"
DISCIPLINE = ROOT / "02 - Areas" / "Discipline" / "Discipline.md"
SPEAKING = ROOT / "02 - Areas" / "English" / "Daily Speaking Practice.md"
PROJECTS = ROOT / "01 - Projects"
ROUTINE = ROOT / "02 - Areas" / "Training" / "home-training" / "Full-Body Strength + Mobility Routine (Home).md"
STRENGTH_LOG = ROOT / "02 - Areas" / "Training" / "home-training" / "Full-Body Strength Log.md"
CANVAS = ROOT / "02 - Areas" / "Training" / "Full Training Routine.canvas"
DAY_NAMES = {1: "Day 1 · Basics", 2: "Day 2 · Strength + mobility"}
ADDED = "## 📥 Added from Telegram"
THIS_WEEK = "## 🔥 This week"
TASK_RE = re.compile(r"^- \[( |x)\] (.+)$")
QUESTION_RE = re.compile(r"^- \[( |x)\] \*\*(\d+)\.\*\* (.+)$")


# ---------- state ----------
def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


# ---------- helpers ----------
def plain(text):
    """Markdown -> chat text: wikilinks to their alias or name, no bold/italics/code marks, no '→ links' tails."""
    text = re.sub(r"\s*→\s*(?:\[\[|`|system backlog|scripts/).*$", "", text)  # "→ [[Note]]" pointers, not "A → B" paths
    text = re.sub(r"\[\[([^\]|]+)\\?\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]#]+)(?:#[^\]]*)?\]\]", r"\1", text)
    return re.sub(r"\*\*|\*|`", "", text).strip()


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _lines(path):
    return read(path).splitlines() if path.exists() else []


# ---------- pending tasks ----------
def tasks():
    """[(line_no, section, text, done)] from Pending Tasks."""
    out, section = [], ""
    for i, line in enumerate(_lines(PENDING_TASKS)):
        if line.startswith("## "):
            section = line
        m = TASK_RE.match(line)
        if m:
            out.append((i, section, m.group(2), m.group(1) == "x"))
    return out


def open_tasks(sections=None):
    return [t for t in tasks() if not t[3] and (sections is None or t[1] in sections)]


def this_week():
    return open_tasks({ADDED, THIS_WEEK})


def add_task(text):
    """Append to the '📥 Added from Telegram' section (created above '🔥 This week'); the agent sorts it later."""
    lines = _lines(PENDING_TASKS)
    if not lines:
        return False
    item = f"- [ ] {text.strip()} *(added {date.today().isoformat()})*"
    if ADDED not in lines:
        at = lines.index(THIS_WEEK) if THIS_WEEK in lines else len(lines)
        lines[at:at] = [ADDED, "_Added with /add; the agent files them into the right section at the next organize._", ""]
    i = lines.index(ADDED) + 1
    while i < len(lines) and not lines[i].startswith("## "):
        i += 1
    while lines[i - 1] == "":
        i -= 1
    lines.insert(i, item)
    _write(PENDING_TASKS, lines)
    return True


def find_tasks(query):
    q = query.lower().strip()
    return [t for t in open_tasks() if q in plain(t[2]).lower()]


def tick_task(line_no, text):
    """Mark one task done, if that line still holds the same task."""
    lines = _lines(PENDING_TASKS)
    if line_no >= len(lines) or lines[line_no] != f"- [ ] {text}":
        return False
    lines[line_no] = f"- [x] {text} ✓ {date.today().isoformat()}"
    _write(PENDING_TASKS, lines)
    return True


# ---------- questions ----------
def questions():
    """[(number, text, answered)] in file order (starred first)."""
    out = []
    for line in _lines(QUESTIONS):
        m = QUESTION_RE.match(line)
        if m:
            out.append((int(m.group(2)), plain(m.group(3)), m.group(1) == "x"))
    return out


def answered_in_inbox():
    """Question numbers already answered by a capture still waiting in the Inbox."""
    done = set()
    for p in INBOX.glob("*.md"):
        fm, _ = frontmatter(read(p))
        for tag in (fm or {}).get("tags", []) if isinstance((fm or {}).get("tags"), list) else []:
            m = re.fullmatch(r"question-(\d+)", tag)
            if m:
                done.add(int(m.group(1)))
    return done


def next_question(after=None):
    """The next unanswered question; after a number, the one following it (wrapping around)."""
    waiting = answered_in_inbox()
    open_q = [q for q in questions() if not q[2] and q[0] not in waiting]
    if not open_q:
        return None
    if after is not None:
        numbers = [q[0] for q in open_q]
        if after in numbers:
            return open_q[(numbers.index(after) + 1) % len(open_q)]
    return open_q[0]


# ---------- brief and review ----------
def minimums():
    """Rows of the Discipline standard table: [(habit, minimum, when, starts)]."""
    rows, in_table, header = [], False, None
    for line in _lines(DISCIPLINE):
        if line.startswith("## Standard to maintain"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if in_table and line.startswith("|") and not re.match(r"^\|[-\s|]+\|$", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if header is None:
                header = [c.lower() for c in cells]
                continue
            row = dict(zip(header, cells))
            rows.append((plain(row.get("habit", "")), plain(row.get("minimum", "")), plain(row.get("when", "")), plain(row.get("starts", ""))))
    return rows


def deadlines(today):
    """Projects with a `deadline:` in their frontmatter: [(name, days_left)]."""
    out = []
    for p in PROJECTS.rglob("*.md"):
        fm, _ = frontmatter(read(p))
        d = str((fm or {}).get("deadline", ""))
        if re.match(r"\d{4}-\d{2}-\d{2}$", d) and (fm or {}).get("status") != "done":
            out.append((p.stem, (date.fromisoformat(d) - today).days))
    return sorted(out, key=lambda x: x[1])


def english_checkins(start, end):
    """Dates (start..end inclusive) with an `english-practice` capture anywhere in the vault."""
    days = set()
    for p in vault_files():
        if p.suffix != ".md":
            continue
        fm, _ = frontmatter(read(p))
        tags = (fm or {}).get("tags")
        if isinstance(tags, list) and "english-practice" in tags:
            d = str(fm.get("created") or fm.get("captured") or "")[:10]
            if re.match(r"\d{4}-\d{2}-\d{2}$", d) and start <= date.fromisoformat(d) <= end:
                days.add(d)
    return days


def corrections():
    """Bullets under '## Notes from …' headings in Daily Speaking Practice (written there at each organize)."""
    out, inside = [], False
    for line in _lines(SPEAKING):
        if line.startswith("## "):
            inside = line.startswith("## Notes from")
        elif inside and line.startswith("- "):
            out.append(plain(line[2:]))
    return out


def correction_of_the_day(today):
    items = corrections()
    return items[today.toordinal() % len(items)] if items else None


# ---------- training ----------
def sessions():
    """[(date, day)] from the Session log table of the strength log; day is 1 or 2 (tests and other rows are skipped)."""
    out, inside = [], False
    for line in _lines(STRENGTH_LOG):
        if line.startswith("## "):
            inside = line.startswith("## Session log")
        elif inside and line.startswith("| 20"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            m = re.search(r"\d", cells[1]) if len(cells) > 1 else None
            if m and m.group(0) in "12":
                try:
                    out.append((date.fromisoformat(cells[0][:10]), int(m.group(0))))
                except ValueError:
                    pass
    return sorted(out)


def training_status(today=None):
    """Which session is next (alternating from the last logged one), and how many this week vs. the target."""
    today = today or date.today()
    fm, _ = frontmatter(read(ROUTINE)) if ROUTINE.exists() else (None, "")
    fm = fm or {}
    target = int(str(fm.get("target_per_week", "3")) or 3)
    try:
        start = date.fromisoformat(str(fm.get("start", ""))[:10])
    except ValueError:
        start = None
    done = sessions()
    last = done[-1] if done else None
    next_day = 2 if last and last[1] == 1 else 1
    monday = today - timedelta(days=today.weekday())
    this_week = sum(1 for d, _ in done if monday <= d <= today)
    return {"start": start, "target": target, "last": last, "next": next_day, "this_week": this_week,
            "days_since": (today - last[0]).days if last else None}


def training_line(today=None):
    today = today or date.today()
    s = training_status(today)
    if s["start"] and today < s["start"]:
        return f"🏋️ Training starts {s['start']:%a %d %b} with {DAY_NAMES[1]}."
    text = f"🏋️ Next session: {DAY_NAMES[s['next']]} (~40 min). This week: {s['this_week']}/{s['target']}."
    if s["last"]:
        text += f" Last: Day {s['last'][1]} on {s['last'][0]:%a %d %b}."
        if s["days_since"] and s["days_since"] >= 3:
            text += " ⚠️ 3+ days without training: today is a good day."
    return text + " /train shows it."


def session_plan(day):
    """The text cards of one day from the routine canvas (Day 1 = d1-*, Day 2 = d2-*), plus the shared start."""
    try:
        nodes = json.loads(CANVAS.read_text(encoding="utf-8")).get("nodes", [])
    except (OSError, ValueError):
        return None
    cards = {n["id"]: n for n in nodes if n.get("type") == "text"}
    blocks = ("upper", "legs", "core", "flow") if day == 1 else ("skill", "main", "legs", "core")
    order = ["hang"] + [f"d{day}-{block}" for block in blocks]
    parts = ["Warm-up: Trevor Shan's 5-min routine"]
    for node_id in order:
        if node_id in cards:
            parts.append(plain(cards[node_id]["text"]).replace("### ", "").strip())
    return "\n\n".join(parts)


def training_caption(day=None, today=None):
    """Short Telegram caption for the actual Obsidian routine note attachment."""
    status = training_status(today)
    day = day or status["next"]
    if not session_plan(day):
        return None
    note_path = quote(ROUTINE.relative_to(ROOT).with_suffix("").as_posix(), safe="/")
    obsidian_uri = f"obsidian://open?vault=Vault&file={note_path}"
    return (f"🏋️ {DAY_NAMES[day]} (~40 min) · {status['this_week']}/{status['target']} this week.\n"
            f"Full Obsidian routine note attached, with both days and exercise links.\n{obsidian_uri}")


ABOUT_ME = ABOUT / "About Me.md"


def task_title(text):
    """A task's bold title ("**Do X**: details" -> "Do X"), or its first 90 characters."""
    m = re.search(r"\*\*(.+?)\*\*", text)
    return plain(m.group(1)) if m else plain(text)[:90]


def goals():
    """[(goal, where)] from the "What you're working on" table in About Me."""
    out, inside = [], False
    for line in _lines(ABOUT_ME):
        if line.startswith("## "):
            inside = line.startswith("## What you're working on")
        elif inside and line.startswith("| ") and not line.startswith(("| Goal", "|---")):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2:
                out.append((plain(cells[0]), plain(cells[1])))
    return out


def goals_text():
    rows = goals()
    if not rows:
        return "🎯 No goals table in About Me yet."
    return "🎯 Your goals\n" + "\n".join(f"{i}. {g}\n   ↳ {w}" for i, (g, w) in enumerate(rows, 1))


def brief_text(today=None, habit_lines=None, team_lines=None):
    """habit_lines: from habits.brief_lines (the habit tracker app). With them the brief is short: the day's focus
    first, then training, English and one correction; tasks and the rest are behind the brief's buttons.
    team_lines: the Coach's and the Teacher's status (team.py); they replace the training and English lines."""
    today = today or date.today()
    weekday = today.weekday() < 5
    lines = [f"☀️ Good morning! {today:%A %d %B}"]
    if habit_lines and team_lines:
        lines += ["", *habit_lines]
        rows = goals()
        if rows:
            goal, where = rows[today.toordinal() % len(rows)]
            lines.append(f"\n🎯 Goal in focus: {goal}\n   ↳ {where}")
        week = this_week()
        if week:
            lines.append(f"📌 This week: {task_title(week[0][2])}" + (f"  (+{len(week) - 1} more: /todo)" if len(week) > 1 else ""))
        for name, days in deadlines(today):
            if days >= 0:
                lines.append(f"⏳ {name}: {days} days left")
        lines += ["\n👥 Your team today", *team_lines]
        return "\n".join(lines)
    if habit_lines:
        lines += ["", *habit_lines]
        if ROUTINE.exists():
            lines.append("\n" + training_line(today).replace(" /train shows it.", ""))
        if weekday:
            lines.append("🎙 English: one voice note about your workday, after work.")
        tip = correction_of_the_day(today)
        if tip:
            lines.append(f"🔁 {tip}")
        for name, days in deadlines(today):
            if days >= 0:
                lines.append(f"⏳ {name}: {days} days left")
        return "\n".join(lines)
    rows = minimums()
    fm, _ = frontmatter(read(DISCIPLINE)) if DISCIPLINE.exists() else (None, "")
    try:
        plan_start = date.fromisoformat(str((fm or {}).get("created", ""))[:10])
    except ValueError:
        plan_start = today
    if rows:
        lines.append("\nToday's minimums:")
        later = []
        for habit, minimum, when, starts in rows:
            m = re.search(r"week (\d+)", starts.lower())
            begins = plan_start + timedelta(weeks=int(m.group(1)) - 1) if m else today
            if begins > today:
                later.append(f"{habit} (from {begins:%d %b})")
            else:
                lines.append(f"• {habit}: {minimum}" + (f" ({when})" if when else ""))
        if later:
            lines.append("Coming up: " + ", ".join(later))
    yesterday = today - timedelta(days=1)
    did = yesterday.isoformat() in english_checkins(yesterday, yesterday)
    lines.append("\n🎙 English: " + ("send today's voice note about your workday. /standup or just a voice note."
                                      if weekday else "weekend, so the voice note is optional today.")
                 + f" Yesterday: {'✅' if did else '—'}")
    for name, days in deadlines(today):
        if days >= 0:
            lines.append(f"⏳ {name}: {days} days left")
    if ROUTINE.exists():
        lines.append("\n" + training_line(today))
    tip = correction_of_the_day(today)
    if tip:
        lines.append(f"\n🔁 Review this correction:\n{tip}")
    week = this_week()
    if week:
        lines.append(f"\n📌 Pending: {plain(week[0][2])}" + (f"  (+{len(week) - 1} more: /todo)" if len(week) > 1 else ""))
    return "\n".join(lines)


def review_text(today=None, habit_lines=None):
    """habit_lines: from habits.review_lines (this week's counts in the habit tracker app)."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    done = english_checkins(monday, today)
    workdays = min(5, (today - monday).days + 1)
    lines = [f"🗓 Weekly review · week {today.isocalendar()[1]}",
             f"\n🎙 English voice notes this week: {len(done)} of {workdays} workdays"]
    if habit_lines:
        lines += habit_lines
        questions = ["1. What made the ✅ habits easy, and what got in the way of the 🔴 ones?",
                     "2. The ONE thing you'll change next week (smaller minimum, better time, or drop it)."]
    else:
        questions = ["1. How many days did you keep each minimum?",
                     "2. What made it easy, and what got in the way?",
                     "3. The ONE thing you'll change next week (smaller minimum, better time, or drop it)."]
    lines += ["\nAnswer in one reply to this message (voice or text):", *questions,
              "\nThe agent adds it to your Discipline review log at the next organize."]
    return "\n".join(lines)


# ---------- inbox and backup ----------
def inbox_titles():
    return sorted(re.sub(r"^\d{4}-\d{2}-\d{2} \d{4} ", "", p.stem) for p in INBOX.glob("*.md"))


def backup_status():
    try:
        epoch, when = LAST_BACKUP.read_text(encoding="utf-8").split()[:2]
        age = (datetime.now().timestamp() - int(epoch)) / 3600
        text = f"last backup {datetime.fromtimestamp(int(epoch)):%Y-%m-%d %H:%M} ({age:.0f} h ago)"
    except (OSError, ValueError):
        text = "no backup recorded yet"
    if LAST_FAILURE.exists():
        text += " · ⚠️ the latest backup attempt FAILED"
    return text


def request_backup():
    if not BACKUP_REQUEST.parent.is_dir():
        return False
    BACKUP_REQUEST.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return True
