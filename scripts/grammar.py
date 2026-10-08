"""Grammar exercises for the English teacher, built on your own mistakes: multiple-choice questions (sent as Telegram quiz
polls) and rewrite exercises (free answer). Claude Code writes fresh ones from your latest corrections when connected;
otherwise they come from the bank below, which targets the mistakes in Daily Speaking Practice. Topics you miss come up
more often; above 80% right the level goes up (1-3), under 50% it goes down. Zero tokens without Claude Code."""
import json, random, re

# topic -> words in a correction that show you need it
TOPICS = {
    "explain to": r"\bexplain|\bdescrib",
    "question order": r"what does|how can they|embedded|statement order|word order",
    "present perfect": r"present perfect|\bsince\b|\bfor two|been here|ago",
    "verb tenses": r"tense|past|future|\bwas\b|\bhad\b|will",
    "prepositions": r"\bat\b|\bin the\b|\bon\b|preposition",
    "false friends": r"TPV|POS|false friend|commercial agent|emitting|meeting",
    "advanced structures": r"connector|however|as a result|on top of that",
}

# (topic, level, question, options, answer index, explanation)
CHOICE = [
    ("explain to", 1, "I spent the morning ___.", ["explaining her the app", "explaining the app to her", "explaining to her the app about", "explaining the app her"], 1,
     "Explain something TO someone. Never \"explain her\"."),
    ("explain to", 1, "Can you ___ how the pipeline works?", ["explain me", "explain to me", "explain for me", "explain myself"], 1,
     "Explain + to + person: \"explain to me how…\"."),
    ("explain to", 2, "She ___ her problem, so I could help.", ["described me", "described to me", "described for me", "described myself"], 1,
     "Describe works like explain: describe something TO someone."),
    ("question order", 1, "She needs to understand what ___.", ["does our app do", "our app does", "our app do", "do our app"], 1,
     "Inside a sentence, a question keeps statement order: what our app does."),
    ("question order", 1, "Do you know where ___?", ["is the meeting", "the meeting is", "is it the meeting", "the meeting it is"], 1,
     "Embedded question: where the meeting is (subject before verb)."),
    ("question order", 2, "I asked him how long ___.", ["did the migration take", "the migration took", "took the migration", "the migration did take"], 1,
     "Reported question: no auxiliary \"did\", statement order."),
    ("present perfect", 1, "I ___ in this city for two years.", ["live", "am living", "have lived", "lived"], 2,
     "Started in the past and still true: present perfect + for."),
    ("present perfect", 1, "I've worked here ___ 2024.", ["for", "since", "from", "during"], 1,
     "Since + a point in time (2024); for + a period (two years)."),
    ("present perfect", 2, "We ___ this pipeline since May.", ["maintain", "have been maintaining", "maintained", "are maintaining"], 1,
     "An activity from May until now: present perfect continuous."),
    ("present perfect", 2, "How long ___ your new teammate?", ["do you know", "have you known", "are you knowing", "did you know"], 1,
     "Duration up to now: present perfect. \"Know\" isn't used in the continuous."),
    ("verb tenses", 1, "I ___ the dashboard when the database went down.", ["fixed", "was fixing", "have fixed", "had fix"], 1,
     "An action in progress, interrupted: past continuous."),
    ("verb tenses", 1, "If the client ___ tomorrow, I'll show her the new screen.", ["calls", "will call", "called", "would call"], 0,
     "First conditional: if + present, will + verb."),
    ("verb tenses", 2, "When I joined, they ___ the old system for years.", ["used", "have used", "had been using", "were used"], 2,
     "Before another past moment, for a duration: past perfect continuous."),
    ("verb tenses", 2, "By the end of the quarter, we ___ the migration.", ["will finish", "will have finished", "finish", "are finishing"], 1,
     "Done before a future deadline: future perfect."),
    ("verb tenses", 3, "If we ___ the indexes earlier, the queries wouldn't have been slow.", ["reviewed", "had reviewed", "have reviewed", "would review"], 1,
     "Third conditional (past, unreal): if + past perfect, would have + participle."),
    ("verb tenses", 3, "I wish I ___ the release notes before the demo.", ["read", "would read", "had read", "have read"], 2,
     "Regret about the past: wish + past perfect."),
    ("prepositions", 1, "There are too many screens ___ the side menu.", ["at", "in", "on", "into"], 1,
     "Things are IN a menu, a list or a table."),
    ("prepositions", 1, "I work as a data engineer ___ a startup.", ["in", "at", "on", "by"], 1,
     "At for the place you work: at a startup, at a bank."),
    ("prepositions", 1, "The meeting is ___ Monday ___ 10 am.", ["in / at", "on / at", "at / on", "on / in"], 1,
     "On + day, at + clock time."),
    ("prepositions", 2, "I moved to Italy to ___ my citizenship.", ["do", "make", "get", "take"], 2,
     "You GET (or apply for) citizenship."),
    ("false friends", 1, "We had a long ___ with the client.", ["reunion", "meeting", "assembly", "encounter"], 1,
     "Reunión = meeting. A reunion is meeting again after a long time."),
    ("false friends", 1, "In an English-speaking store, the TPV is the ___.", ["POS", "TPV", "cashbox", "sale machine"], 0,
     "TPV is Spanish; in English it's the POS (point of sale)."),
    ("false friends", 2, "She joined as our ___: sales and client relationships.", ["commercial agent", "sales representative", "comercial", "seller agent"], 1,
     "Commercial agent is a legal role; for the job, say sales representative or account executive."),
    ("false friends", 2, "I'm ___ the data platform.", ["in charge of", "on charge of", "charged of", "at charge of"], 0,
     "Be in charge of something."),
    ("advanced structures", 2, "The pipeline, ___ runs every hour, promotes data to gold.", ["that", "which", "what", "who"], 1,
     "Non-defining clause (between commas): which, never that."),
    ("advanced structures", 3, "Not only ___ the queries, but we also cut costs.", ["we sped up", "did we speed up", "we did speed up", "sped we up"], 1,
     "After \"Not only\" at the start: inversion (did + subject + verb)."),
    ("advanced structures", 3, "I'd rather we ___ the release until Monday.", ["postpone", "postponed", "will postpone", "postponing"], 1,
     "I'd rather + someone + past simple (present meaning)."),
    ("advanced structures", 3, "It's high time we ___ the old screens.", ["merge", "merged", "will merge", "have merged"], 1,
     "It's (high) time + past simple."),
]

# (topic, level, instruction, model answer, what it means in plain words, an example on another topic: before → after)
# The example is never about the exercise's own topic, so you see the pattern and apply it to your sentence yourself.
REWRITE = [
    ("present perfect", 1, "Rewrite with the present perfect and \"for\": \"I live in this city since two years ago.\"",
     "I've lived (or I've been living) in this city for two years.",
     "The present perfect is have/has + the past participle (lived, worked, played). Use it for something that started in "
     "the past and is still true now. \"For\" + how long: for two years, for a month.",
     "\"She plays the guitar since twenty years ago.\" → \"She has played the guitar for twenty years.\""),
    ("question order", 1, "Make it part of the sentence: \"What does our app do?\" → \"She needs to know …\"",
     "She needs to know what our app does.",
     "When a question goes inside another sentence (after I know, she needs to know, can you tell me), it stops being a "
     "question: no do/does, and the subject goes before the verb.",
     "\"Where does the train stop?\" → \"Can you tell me where the train stops?\""),
    ("explain to", 1, "Fix and join into one sentence: \"I explained her all. She understood what does the app do.\"",
     "I explained everything to her, and she understood what the app does.",
     "Explain (and describe) need \"to\" before the person: explain something TO someone. \"All\" alone as the thing "
     "explained becomes \"everything\". A question inside a sentence keeps normal order (no does).",
     "\"He explained me all. I learned how does the recipe work.\" → \"He explained everything to me, and I learned how the recipe works.\""),
    ("verb tenses", 2, "Join with \"when\" using the past perfect continuous: \"They used the old system for years. Then I joined.\"",
     "They had been using the old system for years when I joined.",
     "The past perfect continuous is had been + verb-ing. Use it for something that was going on for a while before "
     "another moment in the past.",
     "\"We waited for an hour. Then the bus came.\" → \"We had been waiting for an hour when the bus came.\""),
    ("verb tenses", 3, "Third conditional: \"We didn't review the indexes, so the queries were slow.\"",
     "If we had reviewed the indexes, the queries wouldn't have been slow.",
     "The third conditional imagines a different past: If + had + past participle, … would (not) have + past participle.",
     "\"I didn't take an umbrella, so I got wet.\" → \"If I had taken an umbrella, I wouldn't have got wet.\""),
    ("advanced structures", 3, "Start with \"Not only\": \"We fixed the slow queries and we reduced costs.\"",
     "Not only did we fix the slow queries, but we also reduced costs.",
     "Starting with \"Not only\" flips the order like a question (did/does/have + subject + verb), then \"but … also\".",
     "\"She sings and she plays the piano.\" → \"Not only does she sing, but she also plays the piano.\""),
    ("prepositions", 2, "Fix the prepositions: \"I work in a startup and the button is at the side menu since Monday.\"",
     "I work at a startup, and the button has been in the side menu since Monday.",
     "At = the place where you work or a point (at a startup, at the station). In = inside something (in a menu, in a box). "
     "Since + the moment something started, used with the present perfect (has been … since Monday).",
     "\"I study in a university and the keys are at the drawer since this morning.\" → \"I study at a university, and the keys have been in the drawer since this morning.\""),
]


def weights(corrections):
    """How much each topic appears in your corrections (1 = never seen)."""
    text = "\n".join(f"{s} → {b}" for _, s, b in corrections)
    return {t: 1 + len(re.findall(rx, text, flags=re.I)) for t, rx in TOPICS.items()}


def level(stats_history):
    """1-3 from the last 10 answers: up above 80% right, down under 50%."""
    lv = stats_history.get("level", 1)
    last = stats_history.get("recent", [])[-10:]
    if len(last) >= 10:
        rate = sum(last) / len(last)
        if rate >= 0.8 and lv < 3:
            lv += 1
            stats_history["recent"] = []
        elif rate < 0.5 and lv > 1:
            lv -= 1
            stats_history["recent"] = []
    stats_history["level"] = lv
    return lv


def pick(corrections, stats, n=4, seen=(), rng=random):
    """n multiple-choice and 1 rewrite from the bank, weighted to your mistakes and weak topics, at your level."""
    lv = level(stats)
    w = weights(corrections)
    for topic, (right, total) in stats.get("topics", {}).items():
        if total:
            w[topic] = w.get(topic, 1) + 3 * (1 - right / total)
    pool = [c for c in CHOICE if c[1] <= lv and c[2] not in seen] or [c for c in CHOICE if c[1] <= lv]
    chosen = []
    while pool and len(chosen) < n:
        c = rng.choices(pool, weights=[w.get(x[0], 1) * (1.5 if x[1] == lv else 1) for x in pool])[0]
        chosen.append(c)
        pool.remove(c)
    rewrites = [r for r in REWRITE if r[1] <= lv] or REWRITE
    rewrite = max(rewrites, key=lambda r: (w.get(r[0], 1) + rng.random(), r[1]))
    return ([{"type": "choice", "topic": c[0], "question": c[2], "options": c[3], "answer": c[4], "explanation": c[5]} for c in chosen],
            {"type": "rewrite", "topic": rewrite[0], "prompt": rewrite[2], "answer": rewrite[3], "hint": rewrite[4],
             "example": rewrite[5]})


def from_claude(text):
    """Parse Claude's JSON set; None when it isn't usable (the caller falls back to the bank)."""
    try:
        m = re.search(r"\{.*\}", text or "", flags=re.S)
        data = json.loads(m.group(0))
        choices = [q for q in data.get("choice", []) if len(q.get("options", [])) in (3, 4)
                   and 0 <= int(q.get("answer", -1)) < len(q["options"]) and len(q.get("question", "")) <= 290
                   and all(len(o) <= 100 for o in q["options"])]
        for q in choices:
            q.update(type="choice", answer=int(q["answer"]), explanation=str(q.get("explanation", ""))[:190],
                     topic=q.get("topic", "verb tenses"))
        r = data.get("rewrite") or {}
        rewrite = {"type": "rewrite", "topic": r.get("topic", "verb tenses"), "prompt": r["prompt"], "answer": r["answer"],
                   "hint": r.get("hint", ""), "example": r.get("example", "")} \
            if r.get("prompt") and r.get("answer") and r.get("hint") and r.get("example") else None
        return (choices, rewrite) if len(choices) >= 3 and rewrite else None
    except Exception:
        return None


def claude_prompt(corrections, stats, n=4):
    lv = stats.get("level", 1)
    weak = sorted(((t, r / tot) for t, (r, tot) in stats.get("topics", {}).items() if tot), key=lambda x: x[1])[:3]
    recent = "\n".join(f"- {s} → {b}" for _, s, b in corrections[:25])
    return ("Write a grammar set for me, built on my real mistakes below. Level " + str(lv) + "/3 (1 = B2 basics I get "
            "wrong, 3 = C1: mixed tenses, conditionals, inversion, reported speech). "
            + (f"My weakest topics so far: {', '.join(t for t, _ in weak)}. " if weak else "")
            + f"\nMy corrections:\n{recent}\n\n"
            f"Return only JSON: {{\"choice\": [{n} items of {{\"topic\", \"question\" (one gap ___, max 200 chars), \"options\" "
            "(4 short options, one correct), \"answer\" (index 0-3), \"explanation\" (one sentence, max 150 chars)}], "
            "\"rewrite\": {\"topic\", \"prompt\" (a rewrite task on one of my mistakes, e.g. change tense, join with a "
            "conditional, make an indirect question), \"answer\" (the model answer), \"hint\" (what the grammar term means "
            "in plain words, with its form, as for someone who doesn't know grammar names), \"example\" (one worked "
            "example ON A DIFFERENT TOPIC, as \\\"before\\\" → \\\"after\\\", so I apply the pattern myself)}}. Never use a grammar "
            "term without explaining it. Topics from: "
            + ", ".join(TOPICS) + ". Use my work and life context from my profile. Vary the tenses.")
