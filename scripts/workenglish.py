"""Professional English for a data engineer and tech lead: what people often say (casual or Spanish-influenced) and how a
senior engineer says it at work, with the key words explained in plain English. The English teacher sends one a day with
the morning drill and asks you to use it in your voice note. Zero tokens. The reference note
02 - Areas/English/Professional English for Data Engineers.md lists them all."""

# (instead of, say, what it means in plain words)
PHRASES = [
    ("The pipeline broke.", "The pipeline failed overnight because of an upstream schema change.",
     "Upstream = the systems that send you data. Downstream = the ones that use yours (dashboards, reports)."),
    ("I'm fixing it.", "I'm looking into it and I'll share an update by noon.",
     "\"Look into\" = investigate. Giving a time for the next update sounds in control."),
    ("The data is wrong.", "We have a data quality issue in the orders table: duplicated rows since Monday.",
     "Name the table, the symptom and since when. \"Data quality issue\" is the standard term."),
    ("It's slow.", "This query is the bottleneck: it scans the whole table instead of using the index.",
     "Bottleneck = the one slow part that limits everything else."),
    ("We need to do it again for the old days.", "We need to backfill the last 30 days.",
     "Backfill = reprocess past data with the fixed logic."),
    ("I'll tell you.", "I'll keep you posted.",
     "= I'll send you updates as things change."),
    ("Can we talk?", "Do you have five minutes for a quick sync?",
     "Sync = a short meeting to align on something."),
    ("It depends.", "It depends on the data volume; let me run a quick benchmark before we commit to it.",
     "\"Commit to\" = decide for sure. A benchmark = a measured test of speed or cost."),
    ("It's done.", "It's deployed to production and we're monitoring it.",
     "Deployed = released. Saying it's monitored shows you own the result, not just the code."),
    ("I don't know.", "I'm not sure yet; let me double-check and get back to you.",
     "\"Get back to you\" = answer you later. Never just \"I don't know\" in a meeting."),
    ("The numbers don't match.", "The figures don't reconcile with Finance; I'm tracing where they diverge.",
     "Reconcile = make two sources agree. Diverge = start to be different."),
    ("We changed the table.", "We introduced a breaking change to the schema, so the downstream dashboards need updating.",
     "Breaking change = a change that makes things that depend on it fail."),
    ("It works on my computer.", "It works locally, but it fails in the staging environment.",
     "Staging = the copy of production where you test before releasing."),
    ("Let's see it later.", "Let's park it and revisit it next sprint.",
     "Park = put aside for now. Revisit = come back to."),
    ("Is it clear?", "Does that make sense? Happy to walk you through it.",
     "\"Walk someone through\" = explain step by step."),
    ("I finished the thing.", "I've wrapped up the migration; the next step is to decommission the old tables.",
     "Wrap up = finish. Decommission = switch off and remove something old."),
    ("We're late.", "We're behind schedule; the realistic ETA is Thursday.",
     "ETA = estimated time of arrival: when it will be ready."),
    ("Give me access.", "Could you grant me read access to the production database?",
     "\"Grant access\" is the formal verb. \"Could you…\" is the polite request."),
    ("The data arrives every hour.", "The data is ingested hourly in batches; we're moving to near real-time streaming.",
     "Ingest = bring data into your system. Batch = a group processed together."),
    ("We have a lot of data.", "The table holds around 200 million rows and grows by about 2 million a day.",
     "Professionals give orders of magnitude, not \"a lot\"."),
    ("This is better.", "This approach scales better and is cheaper to maintain.",
     "Scale = keep working well when the data or users grow. Say WHY it's better."),
    ("Check my code.", "Could you review my pull request when you have a moment?",
     "PR = pull request. \"When you have a moment\" is polite, not urgent."),
    ("I will do it.", "I'll take ownership of it.",
     "Take ownership = be the person responsible until it's done."),
    ("It's very important.", "This is a blocker for the release.",
     "Blocker = something that stops other work until it's solved."),
    ("Explain me the problem.", "Could you walk me through the issue?",
     "Never \"explain me\": explain something TO someone, or \"walk me through\"."),
    ("We made a test.", "We ran a proof of concept to validate the approach.",
     "Proof of concept (PoC) = a small build to check an idea works. You RUN a test, you don't make it."),
    ("The dashboard shows old things.", "The dashboard is reading from a stale table: the data is two days old.",
     "Stale = old, not updated."),
    ("We lose rows.", "We're dropping records during deduplication; I'm adding a test to catch it.",
     "Drop records = lose rows. Deduplication = removing duplicates."),
    ("If it runs two times there are duplicates.", "The job should be idempotent: running it twice must give the same result.",
     "Idempotent = safe to run again; same result every time. A favourite word in data interviews."),
    ("Everything is OK.", "All checks passed and there are no anomalies in the last 24 hours.",
     "Anomaly = something unusual in the data."),
    ("I agree.", "That makes sense to me; let's go with it.",
     "A natural way to agree and close a decision."),
    ("I don't agree.", "I see your point, but I have a concern about the cost.",
     "Disagree politely: acknowledge, then give one concrete concern."),
    ("Let's meet to talk about what they want.", "Let's set up a call to align on the requirements.",
     "Align = agree on the same view. Requirements = what the solution must do."),
    ("The client wants more things.", "The client has expanded the scope, so we should re-estimate.",
     "Scope = what's included in the work. Scope creep = when it keeps growing."),
    ("We put a cron.", "We scheduled it in the orchestrator, so it runs every night at 2 a.m.",
     "Orchestrator = the tool that schedules and chains jobs (Airflow, Dagster, dbt Cloud)."),
    ("The raw tables, the clean ones and the final ones.", "Raw data lands in bronze, gets cleaned in silver, and is modelled for reporting in gold.",
     "Land = arrive. Model = shape the data for its use. The medallion architecture."),
    ("It was a problem of the client.", "The root cause was on the client's side: they sent the file with a new column order.",
     "Root cause = the real origin of a problem, not the symptom."),
    ("We are going to do it like this.", "Here's the plan: first we migrate the history, then we switch over the daily loads.",
     "Switch over = move from the old system to the new one."),
    ("I have a doubt.", "I have a question about the requirements.",
     "Spanish \"duda\" is usually \"question\" in English. \"Doubt\" means you think something is probably not true."),
    ("We have to invest more time.", "We need to allocate more time to testing.",
     "Allocate = assign time, people or money to something. \"Invest time\" exists but sounds Spanish here."),
    ("The cost is very high.", "Our warehouse costs are up 40% this month, mostly from full-table scans.",
     "Give the number and the cause. \"Up 40%\" = increased by 40%."),
    ("Actually we use BigQuery.", "Currently we use BigQuery.",
     "False friend: \"actually\" = in fact (correcting something). \"Actualmente\" = currently."),
    ("I'm agree.", "I agree.",
     "Agree is a verb: I agree, she agrees. Never \"I'm agree\"."),
    ("We have to discuss about it.", "We need to discuss it.",
     "Discuss takes no \"about\": discuss the plan, discuss the costs."),
    ("Since two weeks I work on this.", "I've been working on this for two weeks.",
     "A duration until now: have been + -ing + FOR + period."),
]


def today_phrase(n):
    return PHRASES[n % len(PHRASES)]


def message(n):
    casual, pro, meaning = today_phrase(n)
    return (f"💼 Sound like a senior data engineer\nInstead of: “{casual}”\nSay: “{pro}”\n{meaning}\n"
            "Use it in today's voice note.")


def note():
    """The reference note's body (02 - Areas/English/Professional English for Data Engineers.md)."""
    rows = "\n".join(f"| {c} | {p} | {m} |" for c, p, m in PHRASES)
    return ("---\ncreated: 2026-10-09\ntype: note\nstatus: active\ntags: [area/english, english-practice, business-english, data-engineering]\n"
            "source: own idea\n---\nHow a senior data engineer and tech lead says common things at work, with the key words explained "
            "in plain English. Your [[English Teacher]] sends one a day with the morning drill; use it in your voice note. "
            "Generated from `scripts/workenglish.py` (edit the list there, then regenerate). Part of [[English to C1]].\n\n"
            "## Phrases\n| Instead of | Say | What it means |\n|---|---|---|\n" + rows + "\n")
