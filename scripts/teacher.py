#!/usr/bin/env python3
"""English teacher: a Telegram bot (its own token) in charge of your English. Every morning it drills one past
correction from Daily Speaking Practice (spaced repetition); on speaking days it asks for a voice note of at least
today's target, nags until you reach it, transcribes it (the capture bot's Whisper), corrects it with Claude Code,
writes the corrections into Daily Speaking Practice, saves the audio as a raw capture and ticks English in the habit
tracker. Every 3 days in a row on target, the target grows and the exercises get harder. Brief and persona:
02 - Areas/English/English Teacher.md.

Config (scripts/.env):
  TEACHER_BOT_TOKEN=...           from @BotFather (its own bot)
  TEACHER_DRILL_TIME=08:30        daily drill on a past correction
  TEACHER_GRAMMAR_TIME=13:00      daily grammar set: quiz polls + one rewrite, built on your mistakes (grammar.py)
  TEACHER_SPEAK_TIME=18:00        the speaking session on speaking days
  TEACHER_SPEAK_DAYS=Mon-Sun      speaking days: every day by default (e.g. Mon-Fri, or Mon,Wed,Fri)
  TEACHER_REVIEW_TIME=Sun 19:00   weekly report
  TEACHER_QUIET=22:30-07:00       nothing is sent in this range unless you write first
  TEACHER_NAG_EVERY=30            minutes between nags until today's target is reached
  TEACHER_NAG_MAX=6               nags per day at most
  TEACHER_START_TARGET=120        first daily target in seconds; +15 s every 3 days in a row on target, up to 360
  TEACHER_AI=claude               "off" = no corrections or answers from Claude Code (the agent corrects at organize)
Runs inside the capture bot (telegram_bot.py starts it in a thread when TEACHER_BOT_TOKEN is set).
"""
import hashlib
import re
import grammar
import workenglish
import team
from datetime import datetime, timedelta
import coach
import companion
import habits
from botkit import Bot, at, env, in_range
from pathlib import Path
from vault import ROOT, read

SPEAKING = companion.SPEAKING
NOTE = ROOT / "02 - Areas" / "English" / "English Teacher.md"
INBOX = ROOT / "00 - Inbox"
ATTACH = INBOX / "attachments"
BOOK = ROOT / "02 - Areas" / "English" / "Business English Workbook.md"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

BOT = Bot("Teacher", "TEACHER_BOT_TOKEN", NOTE, "teacher", agent="english-teacher",
          writes=("02 - Areas/English", "01 - Projects/English to C1", "00 - Inbox"))
S = BOT.S
DRILL_TIME = env("TEACHER_DRILL_TIME", "08:30")
GRAMMAR_TIME = env("TEACHER_GRAMMAR_TIME", "13:00")
SPEAK_TIME = env("TEACHER_SPEAK_TIME", "18:00")
SPEAK_DAYS = env("TEACHER_SPEAK_DAYS", "Mon-Sun")
TASK_TIMES = ("09:00", "20:00")   # related Pending Tasks, twice a day
NUDGE_EVERY_H, NUDGE_MAX = 2, 3   # an unanswered drill, rewrite or quiz: a reminder every 2 h, 3 a day at most
REVIEW_TIME = env("TEACHER_REVIEW_TIME", "Sun 19:00")
QUIET = env("TEACHER_QUIET", "22:30-07:00")
NAG_EVERY = int(env("TEACHER_NAG_EVERY", "30"))
NAG_MAX = int(env("TEACHER_NAG_MAX", "6"))
START_TARGET = int(env("TEACHER_START_TARGET", "120"))
STEP, MAX_TARGET, STREAK_STEP = 15, 360, 3
BOXES = [1, 3, 7, 14, 30]          # spaced repetition: days until the next drill of a correction, per box
TRANSCRIBE = None                  # the capture bot's Whisper, passed in by main()

TOPICS = [  # by level: the target decides the level (harder as it grows)
    ["Your workday: what you did, what blocked you, what's next.",
     "Explain one thing you learned today, as if to a colleague.",
     "Describe your plan for tomorrow and why that order."],
    ["Explain a technical idea from your work (bronze/silver/gold, dbt, indexes…) to a non-technical colleague.",
     "Tell the story of a problem you solved this week: the symptom, the cause, the fix.",
     "Summarize a DDIA idea you read recently and where it applies at your job."],
    ["Argue for or against a decision your team made: two reasons and one risk.",
     "A client says your app is too expensive. Answer the objection.",
     "Compare two tools you know (e.g. BigQuery vs PostgreSQL) for a concrete use case and recommend one."],
    ["Give a structured 3-minute talk: context, problem, options, recommendation. Use however, as a result, on top of that.",
     "Tell a story from your life in your city using past simple and present perfect correctly.",
     "Explain a trade-off you'd present to your CTO, then defend it against two objections."],
]


def now():
    return datetime.now()


def send(text, buttons=None, edit=None):
    return BOT.send(text, buttons, edit)


def once(key):
    return BOT.once(key, now())


def days():
    return S.setdefault("days", {})


def day(d):
    return days().setdefault(d.isoformat(), {"spoken": 0, "audios": 0, "nags": 0})


def level():
    return S.setdefault("level", {"target": START_TARGET, "streak": 0, "last_hit": None})


def clock(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def speak_days():
    """Weekday numbers of speaking days, from "Mon-Fri" or "Mon,Wed,Fri"."""
    spec = SPEAK_DAYS.replace(" ", "")
    if "-" in spec:
        a, b = (DAYS.index(x[:3].title()) for x in spec.split("-"))
        return set(range(a, b + 1))
    return {DAYS.index(x[:3].title()) for x in spec.split(",") if x}


def is_speak_day(d):
    return d.weekday() in speak_days()


def previous_speak_day(d):
    x = d - timedelta(days=1)
    while not is_speak_day(x):
        x -= timedelta(days=1)
    return x


def tier():
    t = level()["target"]
    return 0 if t < 150 else 1 if t < 210 else 2 if t < 270 else 3


def topic_for(d, shuffle=0):
    """Speaking days start with the standup; 🎲 moves through the current level's exercises."""
    if shuffle == 0 and is_speak_day(d) and tier() < 2:
        return TOPICS[0][0]
    bank = TOPICS[tier()]
    return bank[(d.toordinal() + shuffle) % len(bank)]


def target_today(d):
    return day(d).get("target_override") or level()["target"]


# ---------- corrections and drills (Daily Speaking Practice) ----------
def corrections():
    """[(key, said, better)] from the '## Notes from' bullets written as "said" → "better"."""
    out, inside = [], False
    for line in (read(SPEAKING).splitlines() if SPEAKING.exists() else []):
        if line.startswith("## "):
            inside = line.startswith("## Notes from")
        elif inside and line.startswith("- ") and "→" in line:
            said, better = line[2:].split("→", 1)
            said = companion.plain(said).strip().strip('"“”')
            if said:
                out.append((hashlib.sha1(said.encode()).hexdigest()[:10], said, companion.plain(better).strip()))
    return out


def next_drill(today):
    """A due correction (missed ones first), else the newest one never drilled."""
    srs = S.setdefault("srs", {})
    items = corrections()
    due = sorted((srs[k]["due"], k, s, b) for k, s, b in items if k in srs and srs[k]["due"] <= today.isoformat())
    if due:
        _, k, s, b = due[0]
        return k, s, b
    fresh = [(k, s, b) for k, s, b in items if k not in srs]
    return fresh[0] if fresh else None


IDK = re.compile(r"^\s*(i\s*)?(don'?t|do not|dont)\s+know|^\s*idk\b|^\s*no\s+s[eé]\b|^\s*ni\s+idea|^\s*no\s+idea|"
                 r"^\s*(teach|show)\s+me|^\s*no\s+lo\s+s[eé]", re.I)


def lesson(better):
    """("the better version", "why") from a correction's right-hand side: the quoted part(s), then the explanation."""
    m = re.match(r'\s*((?:["“][^"”]+["”]\s*(?:/\s*)?)+)(.*)', better, re.S)
    if not m:
        return better.strip(), ""
    return m[1].strip().rstrip("/").strip(), m[2].strip().lstrip(".,;: ").strip()


def bank_rule(topic):
    r = next((r for r in grammar.REWRITE if r[0] == topic), None)
    return (r[4], r[5]) if r else None


# a mistake's pattern -> (the rule in plain words, an example on another topic); grammar's bank where it has one
RULES = [
    (r"\b(explain|describ)\w* (her|him|me|them|us|you)\b", lambda: bank_rule("explain to")),
    (r"\b(what|how|where|why|when|who) (does|do|did|is|are|can) \w+", lambda: bank_rule("question order")),
    (r"\bsince\b.*\bago\b|\bsince (two|three|four|\d+) ", lambda: bank_rule("present perfect")),
    (r"\b(I|we|you|they|what I) done\b", lambda: (
        "\"Done\" is the past participle: it needs have. A finished time (yesterday, this morning) → I did it. "
        "No time, or until now → I've done it.",
        "\"I done my homework.\" → \"I did my homework yesterday.\" / \"I've done my homework.\"")),
    (r"\b(she|he|it) (click|get|want|need|go|have|do|make|work|use|say|see|think|know|run|take)\b", lambda: (
        "With he, she or it, a present-simple verb takes -s: she works, he gets, it runs (have → has, do → does, go → goes).",
        "\"My sister live in Madrid and she work in a bank.\" → \"My sister lives in Madrid and she works in a bank.\"")),
    (r"\b(at|in) the (side )?(menu|sidebar|page|screen)\b|\bin a startup\b", lambda: bank_rule("prepositions")),
]


def rule_for(said):
    for rx, rule in RULES:
        if re.search(rx, said, re.I):
            return rule()
    return None


def lesson_text(said, better, head="📘 Lesson from your own English"):
    good, why = lesson(better)
    rule = rule_for(said)
    return (f"{head}\nYou said: “{said}”\nBetter: {good}\n" + (f"Why: {why}\n" if why else "")
            + (f"The rule: {rule[0]}\nExample on another topic: {rule[1]}\n" if rule else "")
            + "\nNow use it: write (or say) one new sentence about your work (a pipeline, a client, a dashboard, your team) "
              "with the same pattern. Reply to this message.")


def send_drill(today):
    item = next_drill(today)
    if not item:
        send("🔁 No corrections to drill yet. Send me a voice note and we'll build them.")
        return
    k, said, better = item
    if k not in S.get("srs", {}):  # never practised: teach it first, then you use it
        msg = send(lesson_text(said, better))
        mode = "lesson"
    else:  # practised before: try to remember, with help one tap away
        msg = send(f"🔁 Do you remember this one? You once said:\n“{said}”\nHow would you say it better? Reply by text or a "
                   "short voice note. Not sure? Tap 💡 and I'll teach it again.", [[("💡 Teach me", f"dt:{k}")]])
        mode = "recall"
    S["pending"] = {"kind": "drill", "key": k, "msg": msg, "at": now().isoformat(timespec="seconds"), "mode": mode}


def teach(key):
    """The lesson for a correction, then you write your own work sentence with it."""
    item = next((c for c in corrections() if c[0] == key), None)
    if not item:
        send("That correction isn't in your notes anymore. /drill for another one.")
        return
    _, said, better = item
    msg = send(lesson_text(said, better, "📘 Here's how"))
    S["pending"] = {"kind": "drill", "key": key, "msg": msg, "at": now().isoformat(timespec="seconds"), "mode": "lesson",
                    "taught": True}


def send_phrase(today):
    """💼 One professional phrase a day for a data engineer (workenglish.PHRASES), in order."""
    n = S.get("phrase_n", 0)
    S["phrase_n"] = n + 1
    S["phrase_today"] = {"date": today.isoformat(), "n": n}
    send(workenglish.message(n))


def phrase_today(today):
    p = S.get("phrase_today") or {}
    return workenglish.today_phrase(p["n"])[1] if p.get("date") == today.isoformat() else None


def is_drill_answer(msg, seconds=None):
    """A reply to the open drill or rewrite, or a short message soon after it (a sentence that isn't a question, or a voice
    note under 30 s). Anything else is a speaking session or a question, even with an exercise open."""
    p = S.get("pending") or {}
    if p.get("kind") not in ("drill", "rewrite"):
        return False
    if p.get("msg") and msg.get("reply_to_message", {}).get("message_id") == p["msg"]:
        return True
    sent = datetime.fromisoformat(p.get("at") or "2000-01-01T00:00")
    fresh = now() - sent <= timedelta(hours=1)
    if seconds is not None:
        return fresh and seconds <= 30
    text = msg.get("text", "")
    if IDK.search(text):
        return True
    # a text answer counts the same day (the morning lesson answered after work); questions stay questions
    return sent.date() == now().date() and not text.rstrip().endswith("?")


def grade(key, ok):
    srs = S.setdefault("srs", {})
    box = min(srs.get(key, {}).get("box", -1) + 1, len(BOXES) - 1) if ok else 0
    due = now().date() + timedelta(days=BOXES[box] if ok else 1)
    srs[key] = {"box": box, "due": due.isoformat()}
    S.setdefault("drills", []).append({"date": now().date().isoformat(), "ok": ok})
    S["drills"] = S["drills"][-200:]


def drill_answer(answer):
    if (S.get("pending") or {}).get("kind") == "rewrite":
        rewrite_answer(answer)
        return
    pending = S.pop("pending", {})
    item = next((c for c in corrections() if c[0] == pending.get("key")), None)
    if not item:
        send("That correction isn't in your notes anymore. /drill for another one.")
        return
    k, said, better = item
    if IDK.search(answer):
        if not pending.get("taught"):
            grade(k, False)  # comes back tomorrow
        teach(k)
        return
    good, _ = lesson(better)
    lesson_mode = pending.get("mode") == "lesson"
    task = ("The student wrote a NEW sentence about their work using the corrected pattern" if lesson_mode
            else "The student tried to say it better")
    verdict = BOT.claude(f"Drill. The student once said: \"{said}\". A better version: {better}\n{task}: \"{answer}\"\n"
                         "Reply with CORRECT or WRONG as the first word, then teach in under 70 words: what's right or the fix, the "
                         "rule in plain words with a short example on another topic, and how a senior data engineer would say it "
                         "at work. Accept any natural, correct alternative.", model="haiku")
    if verdict:
        ok = verdict.strip().upper().startswith("CORRECT")
        if not pending.get("taught"):
            grade(k, ok)
        send(("✅ " if ok else "❌ ") + verdict.split(None, 1)[1] if " " in verdict else verdict)
    elif lesson_mode:
        p = "dk" if pending.get("taught") else "dg"
        send(f"Check your sentence against the pattern: {good}\nSame structure? Read yours out loud once.",
             [[("✅ Yes, same pattern", f"{p}:{k}:1"), ("❌ Not quite", f"{p}:{k}:0")]])
    else:
        why = lesson(better)[1]
        send(f"Model answer: {good}" + (f"\nWhy: {why}" if why else "") + "\nDid you have it?",
             [[("✅ I had it", f"dg:{k}:1"), ("❌ Not quite", f"dg:{k}:0")]])


# ---------- grammar (quiz polls + a rewrite, built on your mistakes) ----------
def gstats():
    return S.setdefault("grammar", {"level": 1, "recent": [], "topics": {}, "seen": [], "log": []})


def record_grammar(topic, ok):
    g = gstats()
    right, total = g["topics"].get(topic, [0, 0])
    g["topics"][topic] = [right + int(ok), total + 1]
    g["recent"] = (g["recent"] + [int(ok)])[-20:]
    g["log"] = (g["log"] + [{"date": now().date().isoformat(), "topic": topic, "ok": ok}])[-300:]


def send_grammar(today):
    g = gstats()
    items = corrections()
    parsed = grammar.from_claude(BOT.claude(grammar.claude_prompt(items, g), model="sonnet", timeout=150)) if BOT.ai_on() else None
    choices, rewrite = parsed or grammar.pick(items, g, seen=set(g["seen"][-30:]))
    topics = sorted({q["topic"] for q in choices} | {rewrite["topic"]})
    send(f"🧩 Grammar, level {g['level']}/3: {len(choices)} quick questions and one rewrite, built on your mistakes "
         f"({', '.join(topics)}). Tap an answer; then reply to the rewrite.")
    ids = []
    for q in choices:
        pid = BOT.send_poll(q["question"], q["options"], q["answer"], q.get("explanation", ""))
        if pid:
            S.setdefault("polls", {})[pid] = {"topic": q["topic"], "answer": q["answer"], "q": q["question"]}
            ids.append(pid)
            g["seen"] = (g["seen"] + [q["question"]])[-60:]
    for k in list(S.get("polls", {}))[:-60]:
        del S["polls"][k]
    msg = send(rewrite_text(rewrite))
    S["pending"] = {"kind": "rewrite", "msg": msg, "at": now().isoformat(timespec="seconds"), **rewrite}
    S["gset"] = {"date": today.isoformat(), "ids": ids, "answered": 0, "right": 0, "rewrite": False}


def rewrite_text(r):
    """The task, what its grammar means in plain words, and an example on another topic."""
    return (f"✍️ Rewrite: {r['prompt']}"
            + (f"\n\n💡 What it means: {r['hint']}" if r.get("hint") else "")
            + (f"\n\nExample (another topic):\n{r['example']}" if r.get("example") else "")
            + "\n\nNow do yours: reply to this message.")


def poll_answer(pa):
    info = S.get("polls", {}).pop(pa.get("poll_id"), None)
    if not info or not pa.get("option_ids"):
        return
    ok = pa["option_ids"][0] == info["answer"]
    record_grammar(info["topic"], ok)
    gs = S.get("gset") or {}
    if pa.get("poll_id") in gs.get("ids", []):
        gs["answered"] += 1
        gs["right"] += int(ok)
        if gs["answered"] == len(gs["ids"]):
            grammar_summary(gs)


def rewrite_answer(answer):
    p = S.pop("pending", {})
    if IDK.search(answer):
        rewrite_done(p, False)
        send(f"Here's how: {p.get('answer')}\nRead it out loud twice. Then write one sentence about your work (a pipeline, "
             "a client, your team) with the same pattern, and send it to me.")
        return
    verdict = BOT.claude(f"Grammar rewrite. Task: {p.get('prompt')}\nModel answer: {p.get('answer')}\nMy answer: \"{answer}\"\n"
                         "Reply with CORRECT or WRONG as the first word, then one short sentence: what's right, or the fix. "
                         "Accept any correct, natural alternative.", model="haiku")
    if verdict:
        ok = verdict.strip().upper().startswith("CORRECT")
        rewrite_done(p, ok)
        send(("✅ " if ok else "❌ ") + (verdict.split(None, 1)[1] if " " in verdict.strip() else ""))
    else:
        S["rewrite_check"] = {"topic": p.get("topic")}
        send(f"Model answer: {p.get('answer')}\nDid you have it?", [[("✅ I had it", "rg:1"), ("❌ Not quite", "rg:0")]])


def rewrite_done(p, ok):
    record_grammar(p.get("topic", "verb tenses"), ok)
    gs = S.get("gset") or {}
    gs["rewrite"] = True
    gs["right"] = gs.get("right", 0) + int(ok)
    if gs.get("answered") == len(gs.get("ids", [])):
        grammar_summary(gs)


def grammar_summary(gs):
    if not gs.get("rewrite") or gs.get("summarized"):
        return
    gs["summarized"] = True
    g = gstats()
    old = g["level"]
    new = grammar.level(g)
    total = len(gs["ids"]) + 1
    weak = sorted(((t, r / n) for t, (r, n) in g["topics"].items() if n >= 3), key=lambda x: x[1])[:1]
    msg = f"🧩 {gs['right']}/{total} today." + (f" Weakest so far: {weak[0][0]} ({round(weak[0][1] * 100)}%)." if weak else "")
    if new > old:
        msg += f" ⬆️ Grammar level {new}/3 from tomorrow: harder tenses and structures."
    elif new < old:
        msg += f" Back to level {new}/3 for a few days to lock it in."
    send(msg)


# ---------- speaking ----------
def speak_prompt(d, shuffle=0, edit=None):
    e = day(d)
    e["topic"] = topic_for(d, shuffle)
    e["shuffle"] = shuffle
    left = max(0, target_today(d) - e["spoken"])
    head = f"🎙 Speaking time. Today's target: {clock(target_today(d))}" + (f" ({clock(left)} left)" if e["spoken"] else "")
    phrase = phrase_today(d)
    use = f"\n💼 Use today's phrase: “{phrase}”" if phrase else ""
    send(BOT.say("ask for today's speaking session", f"target {clock(target_today(d))}, level {tier() + 1}/4, topic: {e['topic']}"
                 + (f"; ask them to use today's work phrase: {phrase}" if phrase else ""),
                 f"{head}.\nTopic: {e['topic']}{use}\nSend me a voice note; several add up."),
         [[("🎲 Another topic", f"tp:{shuffle + 1}")], [("⏰ +30 min", "sz"), ("🤕 Can't today", "cx")]], edit=edit)


def english_habit():
    rep = coach.report() if habits.configured() else None
    return coach.by_alias("#english-practice", rep) if rep else None


def write_capture(d, f, transcript, topic, corrected):
    INBOX.mkdir(exist_ok=True)
    stamp = now()
    path = INBOX / f"{stamp:%Y-%m-%d %H%M} English practice {d.isoformat()}.md"
    n = 2
    while path.exists():
        path = INBOX / f"{stamp:%Y-%m-%d %H%M} English practice {d.isoformat()} {n}.md"
        n += 1
    tags = ["telegram", "raw", "voice", "english-practice", "teacher"] + (["standup"] if topic == TOPICS[0][0] else []) \
        + (["corrected"] if corrected else [])
    body = (f"---\ncreated: {d.isoformat()}\ntype: source\nstatus: inbox\ntags: [{', '.join(tags)}]\nsource: \"telegram\"\n---\n"
            "Raw Telegram capture awaiting classification; the captured content below is preserved.\n\n"
            f"Voice note to the English teacher. Topic: {topic}\n"
            + ("Corrections already written to [[Daily Speaking Practice]] by the teacher.\n" if corrected
               else "English corrections come at the next organize.\n")
            + f"\n![[{f.name}]]\n\n## Transcript\n{transcript or '_Not transcribed._'}\n")
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def add_corrections(d, capture, bullets, origin="your voice note to the English teacher"):
    """Bullets into Daily Speaking Practice under '## Notes from <day>' (newest section first)."""
    lines = read(SPEAKING).splitlines()
    heading = f"## Notes from {d.isoformat()}"
    if heading in lines:
        i = lines.index(heading) + 1
        while i < len(lines) and not lines[i].startswith("## "):
            i += 1
        while i > 0 and not lines[i - 1].strip():
            i -= 1
        lines[i:i] = [f"From [[{capture.stem}]] (English teacher)."] + bullets
    else:
        first = next((i for i, l in enumerate(lines) if l.startswith("## Notes from")), len(lines))
        lines[first:first] = [heading, f"From {origin} ([[{capture.stem}]]).", *bullets, ""]
    SPEAKING.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def correct(transcript, topic):
    """(bullets, next) from Claude Code, or (None, None)."""
    out = BOT.claude(
        f"Transcript of my English voice note (topic: {topic}):\n\"\"\"{transcript}\"\"\"\n"
        "Give 3 to 5 corrections, one per line, exactly in this format:\n"
        "- \"what I said\" → \"a more natural way\". One short reason.\n"
        "Only real mistakes or clearly unnatural phrasing; keep my meaning; ignore speech-recognition errors on names. "
        "If there are fewer real mistakes, give fewer. Where the English is correct but sounds casual for work, give the "
        "version a senior data engineer or tech lead would say (same format). Every reason in plain words; if you name a "
        "grammar point, add a short example on another topic. Then one last line starting with \"Next:\" with one concrete "
        "thing to practise tomorrow.", model="sonnet", timeout=150)
    if not out:
        return None, None
    bullets = [l.strip() for l in out.splitlines() if l.strip().startswith("- ") and "→" in l]
    nxt = next((l.strip() for l in out.splitlines() if l.strip().lower().startswith("next:")), None)
    return bullets, nxt


def voice(msg):
    kind = next(k for k in ("voice", "audio", "video_note") if k in msg)
    m = msg[kind]
    today = now().date()
    stamp = now().strftime("%Y%m%d-%H%M%S")
    seconds = int(m.get("duration", 0))
    status = send("⏳ Listening…")
    try:
        f = BOT.download(m["file_id"], ATTACH / f"tg-teacher-{stamp}")
    except Exception as e:
        send(f"⚠️ I couldn't download it ({e}). Send it again?", edit=status)
        return
    transcript = None
    if TRANSCRIBE:
        transcript, _ = TRANSCRIBE(f, allowed_languages=("en",))
    answering = is_drill_answer(msg, seconds)
    if answering and transcript:
        send(f"🗣 “{transcript}”", edit=status)
        drill_answer(transcript)
        status = None
    e = day(today)
    e["spoken"] += seconds
    e["audios"] += 1
    topic = e.get("topic") or topic_for(today)
    bullets, nxt = correct(transcript, topic) if transcript and not answering else (None, None)
    capture = write_capture(today, f, transcript, topic, bool(bullets))
    if bullets:
        add_corrections(today, capture, bullets)
    target = target_today(today)
    progress = f"✅ {clock(seconds)} received · today {clock(e['spoken'])} of {clock(target)}."
    if bullets:
        text = progress + "\n\n" + "\n".join(bullets) + (f"\n\n{nxt}" if nxt else "")
    elif answering:
        text = progress
    else:
        text = progress + ("\nCorrections come at the next organize." if transcript else "\n(No transcript: Whisper isn't available.)")
    send(text, edit=status)
    if e["spoken"] >= target and not e.get("hit"):
        hit(today, e)
    elif e["spoken"] < target and is_speak_day(today):
        send(f"{clock(target - e['spoken'])} to go. Keep talking: same topic, or what's next tomorrow.")


# ---------- the Business English exercise book: photos of your answers, corrected ----------
def book_photo(msg):
    """A photo (or an image file) of book pages. Albums arrive as several messages: they're grouped and corrected together."""
    fid = msg["photo"][-1]["file_id"] if "photo" in msg else msg["document"]["file_id"]
    key = str(msg.get("media_group_id") or msg.get("message_id") or now().timestamp())
    batch = S.setdefault("book", {}).setdefault(key, {"files": [], "caption": ""})
    stamp = now().strftime("%Y%m%d-%H%M%S")
    try:
        f = BOT.download(fid, ATTACH / f"tg-teacher-book-{stamp}-{len(batch['files']) + 1}")
    except Exception as e:
        send(f"⚠️ I couldn't download the photo ({e}). Send it again?")
        return
    batch["files"].append(f.relative_to(ROOT).as_posix())
    if msg.get("caption"):
        batch["caption"] = msg["caption"].strip()
    if len(batch["files"]) == 1:
        batch["status"] = send("📸 Got it. Reading your answers…")


def book_feedback(files, caption):
    """(exercise, score, bullets, next, unreadable) from Claude Code, or None."""
    out = BOT.claude(
        "These are photos of my Business English exercise book with my own answers on them: "
        + ", ".join(f'"{p}"' for p in files) + ". Read each image." + (f"\nMy note: {caption}" if caption else "")
        + "\nCheck every answer I wrote. Don't edit any file: only answer, exactly in this format:\n"
        "Exercise: <book, unit, page and exercise number if visible; else a short description>\n"
        "Score: <right>/<answered>\n"
        "- \"my answer\" → \"the right answer\". One short reason in plain words; if you name a grammar point, explain it with "
        "a short example on another topic.\n"
        "(one line per wrong or clearly unnatural answer; no lines if all are right)\n"
        "Next: one concrete thing to practise.\n"
        "If you can't read my answers, write only: UNREADABLE: <why, and how to retake the photo>.",
        tools=True, model="sonnet", timeout=300)
    if not out:
        return None
    if "UNREADABLE:" in out:
        return None, None, [], None, out.split("UNREADABLE:", 1)[1].strip()
    def field(name):
        return next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.strip().lower().startswith(name.lower() + ":")), None)
    bullets = [l.strip() for l in out.splitlines() if l.strip().startswith("- ") and "→" in l]
    return field("Exercise") or "Book exercise", field("Score"), bullets, field("Next"), None


def write_book_capture(d, files, caption, corrected):
    INBOX.mkdir(exist_ok=True)
    stamp = now()
    path = INBOX / f"{stamp:%Y-%m-%d %H%M} Business English book {d.isoformat()}.md"
    n = 2
    while path.exists():
        path = INBOX / f"{stamp:%Y-%m-%d %H%M} Business English book {d.isoformat()} {n}.md"
        n += 1
    tags = ["telegram", "raw", "image", "english-practice", "teacher", "business-english-book"] + (["corrected"] if corrected else [])
    body = (f"---\ncreated: {d.isoformat()}\ntype: source\nstatus: inbox\ntags: [{', '.join(tags)}]\nsource: \"telegram\"\n---\n"
            "Raw Telegram capture awaiting classification; the captured content below is preserved.\n\n"
            "Photos of the Business English exercise book sent to the English teacher."
            + (f" Note: {caption}" if caption else "") + "\n"
            + ("Corrections already written to [[Daily Speaking Practice]] and [[Business English Workbook]] by the teacher.\n"
               if corrected else "Corrections come at the next organize (the english-teacher agent reads the photos).\n")
            + "\n" + "\n".join(f"![[{Path(f).name}]]" for f in files) + "\n")
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def add_book_entry(d, exercise, score, capture, n):
    """A row in Business English Workbook's table (newest first)."""
    if not BOOK.exists():
        BOOK.write_text(
            f"---\ncreated: {d.isoformat()}\ntype: note\nstatus: active\ntags: [area/english, english-practice, business-english]\n"
            "source: own idea\n---\nYour Business English exercise book, done on paper and corrected by your [[English Teacher]]: "
            "send it photos of the pages you've done (several at once is fine; a caption like \"unit 3, p. 24\" helps). Each wrong "
            "answer becomes a correction in [[Daily Speaking Practice]], so it comes back in your drills and grammar sets. "
            "Part of [[English to C1]].\n\n## Exercises (newest first)\n| Date | Exercise | Score | Corrections | Photos |\n"
            "|---|---|---|---|---|\n", encoding="utf-8", newline="\n")
    lines = read(BOOK).splitlines()
    sep = next((i for i, l in enumerate(lines) if l.startswith("|---")), None)
    row = f"| {d.isoformat()} | {exercise.replace('|', '/')} | {score or '–'} | {n} | [[{capture.stem}]] |"
    if sep is None:
        lines += ["", "## Exercises (newest first)", "| Date | Exercise | Score | Corrections | Photos |", "|---|---|---|---|---|", row]
    else:
        lines.insert(sep + 1, row)
    BOOK.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def correct_book():
    """Correct the photos received since the last tick (one album = one batch)."""
    for key in list(S.get("book", {})):
        b = S["book"].pop(key)
        today = now().date()
        result = book_feedback(b["files"], b["caption"]) if BOT.ai_on() else None
        if result and result[4]:  # unreadable: keep nothing half-done, ask for a better photo
            send(f"📸 I can't read your answers: {result[4]}", edit=b.get("status"))
            continue
        exercise, score, bullets, nxt, _ = result or (None, None, [], None, None)
        capture = write_book_capture(today, b["files"], b["caption"], bool(result))
        if not result:
            send(f"📘 Saved ({len(b['files'])} photo{'s' if len(b['files']) != 1 else ''}). I correct it once Claude Code is "
                 "connected; until then I correct it when you organize the vault.", edit=b.get("status"))
            continue
        if bullets:
            add_corrections(today, capture, bullets, "the Business English book")
        add_book_entry(today, exercise, score, capture, len(bullets))
        text = f"📘 {exercise}" + (f"\nScore: {score}" if score else "") + "\n\n" \
            + ("\n".join(bullets) + "\n\nThese join your drills." if bullets else "All correct. 💪") + (f"\n\nNext: {nxt}" if nxt else "")
        send(text, edit=b.get("status"))


def hit(today, e):
    e["hit"] = True
    lv = level()
    lv["streak"] = lv["streak"] + 1 if lv.get("last_hit") == previous_speak_day(today).isoformat() else 1
    lv["last_hit"] = today.isoformat()
    s = english_habit()
    if s and not s["done_today"]:
        try:
            coach.log(s, 1, "English voice note to the teacher")
        except Exception as ex:
            print("Teacher: app tick failed:", ex)
    msg = f"🎯 Target reached: {clock(e['spoken'])}. English ticked. Streak: {lv['streak']} day{'s' if lv['streak'] != 1 else ''}."
    if lv["streak"] % STREAK_STEP == 0 and lv["target"] < MAX_TARGET:
        old_tier = tier()
        lv["target"] = min(MAX_TARGET, lv["target"] + STEP)
        msg += f"\n⬆️ Level up: from tomorrow your target is {clock(lv['target'])}."
        if tier() > old_tier:
            msg += f" The exercises get harder too (level {tier() + 1}/4)."
    send(BOT.say("the student reached today's speaking target: celebrate briefly with their numbers", msg, msg) + team.after_english(today))


def skip_today(reason):
    today = now().date()
    e = day(today)
    s = english_habit()
    try:
        coach.add_reason([s["name"] if s else "✍️ English"], reason, "skipped")
    except Exception as ex:
        print("Teacher: reason not saved:", ex)
    if reason.lower().startswith("tired"):
        e["target_override"] = 60
        send(BOT.say("the student is tired: lower today's target to one minute instead of skipping", reason,
                     "Tired is fine. Then just 1 minute today: one voice note, anything about your day. Go."))
        return
    e["skipped"] = reason
    send(BOT.say("the student can't do today's speaking session: accept, no lecture, tomorrow counts double in spirit", reason,
                 "OK, noted. Tomorrow we go again: never skip twice."))


# ---------- schedule ----------
def nudge_unanswered(t):
    """The open drill or rewrite and today's quizzes, if you haven't answered: every 2 h, 3 times a day at most."""
    today = t.date()
    p = S.get("pending") or {}
    gs = S.get("gset") or {}
    quizzes = len(gs.get("ids", [])) - gs.get("answered", 0) if gs.get("date") == today.isoformat() else 0
    exercise = p if p.get("kind") in ("drill", "rewrite") and p.get("at", "")[:10] == today.isoformat() else None
    if not exercise and quizzes <= 0:
        return
    since = datetime.fromisoformat(exercise["at"]) if exercise else at(today, GRAMMAR_TIME)
    st = S.setdefault("nudges", {})
    for k in [k for k in st if k != today.isoformat()]:
        del st[k]
    n = st.get(today.isoformat(), 0)
    if n >= NUDGE_MAX or t < since + timedelta(hours=NUDGE_EVERY_H * (n + 1)):
        return
    st[today.isoformat()] = n + 1
    parts = []
    if exercise and exercise["kind"] == "drill":
        item = next((c for c in corrections() if c[0] == exercise.get("key")), None)
        if item:
            parts.append(f"🔁 Your drill is still open: how would you say “{item[1]}” better?")
    elif exercise:
        parts.append(f"✍️ Your rewrite is still open: {exercise.get('prompt')}")
    if quizzes > 0:
        parts.append(f"🧩 {quizzes} grammar quiz{'zes' if quizzes != 1 else ''} still unanswered above.")
    if parts:
        send("\n".join(parts) + ("\nReply to the exercise message; it takes two minutes." if exercise else ""))



def tick():
    t = now()
    today = t.date()
    if BOT.owner and S.get("book"):
        correct_book()
    if not BOT.owner or in_range(t, QUIET):
        return
    if t >= at(today, DRILL_TIME) and once(f"drill:{today}"):
        send_drill(today)
        send_phrase(today)
    if t >= at(today, GRAMMAR_TIME) and once(f"grammar:{today}"):
        send_grammar(today)
    nudge_unanswered(t)
    BOT.remind_tasks("Teacher", t, TASK_TIMES)
    rd, rt = REVIEW_TIME.split()
    if DAYS.index(rd[:3].title()) == today.weekday() and t >= at(today, rt) and once(f"week:{today}"):
        weekly_report(today)
    e = day(today)
    if e.get("hit") or e.get("skipped"):
        return
    s = english_habit()
    if s and s["done_today"] and e["audios"] == 0 and once(f"ticked:{today}"):
        send(BOT.say("the student ticked English in the habit tracker but sent you no audio: ask for it, you want to hear them",
                     f"target {clock(target_today(today))}", "✍️ You ticked English in the app. Send me the audio: I want to hear you."))
    if not is_speak_day(today):
        return
    start = at(today, e.get("snooze") or SPEAK_TIME)
    if t >= start and once(f"speak:{today}:{e.get('snooze') or SPEAK_TIME}"):
        speak_prompt(today)
        e["last_nag"] = t.isoformat()
        return
    last = datetime.fromisoformat(e.get("last_nag") or start.isoformat())
    if team.training_running():  # never interrupt a workout: the nags wait until the session ends
        return
    if t >= start and e["nags"] < NAG_MAX and t - last >= timedelta(minutes=NAG_EVERY):
        e["nags"] += 1
        e["last_nag"] = t.isoformat()
        left = target_today(today) - e["spoken"]
        tone = ["friendly", "firmer", "direct: two minutes is nothing", "blunt: missing twice is how habits die",
                "very short", "last call of the day"][min(e["nags"] - 1, 5)]
        send(BOT.say(f"the student hasn't finished today's speaking; reminder {e['nags']} of {NAG_MAX}, tone: {tone}",
                     f"{clock(left)} left of {clock(target_today(today))}; topic: {e.get('topic')}",
                     ["🎙 {l} to go. Your topic is waiting.", "🎙 Still {l}. Phone in hand: talk.", "🎙 {l}. That's less than a song.",
                      "🎙 Missing twice is how habits die. {l}.", "🎙 {l}.", "🚨 Last call today: {l}, now."][min(e["nags"] - 1, 5)].format(l=clock(left))),
             [[("🎲 Another topic", f"tp:{e.get('shuffle', 0) + 1}"), ("🤕 Can't today", "cx")]])


def weekly_report(today):
    m = today - timedelta(days=today.weekday())
    week = [(d, days().get(d.isoformat(), {})) for d in (m + timedelta(days=i) for i in range(7)) if d <= today]
    spoken = sum(v.get("spoken", 0) for _, v in week)
    hits = sum(1 for _, v in week if v.get("hit"))
    planned = sum(1 for d, _ in week if is_speak_day(d))
    drills = [x for x in S.get("drills", []) if x["date"] >= m.isoformat()]
    gl = [x for x in gstats()["log"] if x["date"] >= m.isoformat()]
    missed = {}
    for x in gl:
        if not x["ok"]:
            missed[x["topic"]] = missed.get(x["topic"], 0) + 1
    facts = (f"spoken {clock(spoken)} this week; on target {hits}/{planned} speaking days; target now {clock(level()['target'])}, "
             f"level {tier() + 1}/4; drills {sum(x['ok'] for x in drills)}/{len(drills)} right; grammar "
             f"{sum(x['ok'] for x in gl)}/{len(gl)} right, level {gstats()['level']}/3"
             + (f", most missed: {max(missed, key=missed.get)}" if missed else ""))
    text = BOT.claude(f"Sunday report for my week of English. Facts: {facts}. Read this week's '## Notes from' sections in "
                      "02 - Areas/English/Daily Speaking Practice.md and name the 1-2 mistakes that keep coming back, then one "
                      "goal for next week. Under 90 words, plain text.", tools=True, model="sonnet", timeout=180)
    send(text or f"📊 Your week: {facts}. " + ("Every day on target. 💪" if hits >= planned else "Next week: every speaking day."))


# ---------- chat ----------
HELP = ("🗣 I'm your English teacher. Every morning a drill on one of your past mistakes; on speaking days a voice-note "
        "session that grows with you. I correct you and write it all into Daily Speaking Practice.\n"
        "/today — today's target and progress\n/speak — a speaking session now\n/exercise — another topic\n"
        "/drill — practise another past correction\n/grammar — a grammar set now\n/skip — can't today\n/week — this week's report\n"
        "📸 Send photos of your Business English book (your answers on them) and I correct them.\n"
        "/feedback — tell me what to do differently (I learn it)\n"
        "Or ask me anything about English.")


def status_text():
    today = now().date()
    e = day(today)
    lv = level()
    parts = [f"🎙 Today: {clock(e['spoken'])} of {clock(target_today(today))}" + (" ✅" if e.get("hit") else "") + ".",
             f"Streak {lv['streak']} · level {tier() + 1}/4."]
    if not is_speak_day(today):
        parts.append("Not a speaking day: drills only (voice notes still count).")
    return " ".join(parts)


def command(text):
    cmd = text.split()[0].split("@")[0].lower()
    arg = text.partition(" ")[2]
    today = now().date()
    if cmd in ("/start", "/help"):
        send(HELP)
    elif cmd == "/today":
        send(status_text())
    elif cmd in ("/speak", "/standup"):
        speak_prompt(today)
    elif cmd == "/exercise":
        speak_prompt(today, day(today).get("shuffle", 0) + 1)
    elif cmd == "/grammar":
        send_grammar(today)
    elif cmd == "/feedback":
        BOT.feedback_command(arg, now())
    elif cmd == "/drill":
        send_drill(today)
    elif cmd == "/skip":
        button({"data": "cx"})
    elif cmd == "/week":
        weekly_report(today)
    else:
        send("Unknown command. /help lists them.")


def button(q):
    data = q.get("data", "")
    msg_id = q.get("message", {}).get("message_id")
    today = now().date()
    if BOT.common_button(data, now()):
        return
    if data.startswith("tp:"):
        speak_prompt(today, int(data[3:]), edit=msg_id)
    elif data == "sz":
        e = day(today)
        base = at(today, e.get("snooze") or SPEAK_TIME)
        e["snooze"] = (max(base, now()) + timedelta(minutes=30)).strftime("%H:%M")
        send(f"⏰ {e['snooze']} then. I'll be here.")
    elif data == "cx":
        send("What's stopping you?", [[("😴 Tired", "cr:Tired"), ("💼 Work", "cr:Work")],
                                       [("🤒 Sick or no voice", "cr:Sick or no voice"), ("✍️ Other", "cr:")]])
    elif data.startswith("cr:"):
        if data == "cr:":
            S["pending"] = {"kind": "skip"}
            send("Tell me in a few words.")
        else:
            skip_today(data[3:])
    elif data.startswith("rg:"):
        p = S.pop("rewrite_check", None) or {}
        rewrite_done(p, data == "rg:1")
        send("Good." if data == "rg:1" else "Read the model answer out loud twice. It'll come back.")
    elif data.startswith("dt:"):
        p = S.get("pending") or {}
        if p.get("kind") == "drill" and p.get("key") == data[3:] and not p.get("taught"):
            grade(data[3:], False)  # comes back tomorrow
        teach(data[3:])
    elif data.startswith("dk:"):  # your sentence after I taught it: it comes back tomorrow either way
        send("Good. It comes back tomorrow to stick." if data.endswith(":1")
             else "Read the pattern out loud twice, then try one more sentence. It comes back tomorrow.")
    elif data.startswith("dg:"):
        _, k, ok = data.split(":")
        grade(k, ok == "1")
        send("Good. It'll come back later to stick." if ok == "1" else "It'll come back tomorrow. Say the right version out loud twice now.")


def text_message(t, msg=None):
    t = t.strip()
    pending = S.get("pending") or {}
    if t.startswith("/"):
        command(t)
    elif BOT.feedback_text(t, now()) or (not IDK.search(t) and BOT.maybe_feedback(t)):
        pass
    elif is_drill_answer(msg or {"text": t}):
        drill_answer(t)
    elif pending.get("kind") == "skip":
        S.pop("pending", None)
        skip_today(t)
    else:
        answer = BOT.claude(f"I write: {t}\nAnswer as my English teacher in under 120 words, plain text. Use my notes when useful: "
                            "02 - Areas/English/Daily Speaking Practice.md, English Teacher.md, 01 - Projects/English to C1. If I ask "
                            "you to record or change something in your English notes, do it (your agent definition's writing rules) "
                            "and say what you changed.", tools=True, model="sonnet", timeout=240)
        send(answer or "I answer questions once Claude Code is connected. Meanwhile: /speak, /drill, /today")


def handle(update):
    if "callback_query" in update:
        button(update["callback_query"])
        return
    if "poll_answer" in update:
        poll_answer(update["poll_answer"])
        return
    msg = update.get("message", {})
    if any(k in msg for k in ("voice", "audio", "video_note")):
        voice(msg)
    elif "photo" in msg or msg.get("document", {}).get("mime_type", "").startswith("image/"):
        book_photo(msg)
    elif msg.get("text"):
        text_message(msg["text"], msg)


COMMANDS = [("today", "Today's target and progress"), ("speak", "A speaking session now"), ("exercise", "Another topic"),
            ("drill", "Practise a past correction"), ("grammar", "A grammar set now"), ("skip", "Can't today"), ("week", "This week's report"),
            ("feedback", "Tell me what to do differently"), ("help", "How the teacher works")]


def main(transcribe=None):
    global TRANSCRIBE
    TRANSCRIBE = transcribe
    BOT.run(tick, handle, COMMANDS)


if __name__ == "__main__":
    main()
