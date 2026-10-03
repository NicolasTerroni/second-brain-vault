#!/usr/bin/env python3
"""Per-type catalogs of the raw sources, generated (zero tokens). Run after every ingest or Inbox processing.

For each folder `03 - Resources/Sources/<type>/` with sources, writes `Sources index - <type>.md`: one table row per
source (captured date, kind, creator, what it is about, wiki notes built from it), grouped by month, newest first.
Also refreshes the list of these indexes in `index.md` between the sources-index markers.
The files are overwritten on every run: never edit them by hand.

Run: python scripts/source_index.py           write the indexes
     python scripts/source_index.py --check   only report indexes that are out of date (exit 1)
"""
import re, sys
from collections import defaultdict
from datetime import date, datetime
from vault import ROOT, SOURCES, NAV, Resolver, frontmatter, in_sources, links, read, vault_files

PREFIX = "Sources index - "
START, END = "<!-- sources-index:start -->", "<!-- sources-index:end -->"
DESCRIPTION = {
    "articles": "web pages and articles",
    "captures": "personal text and voice captures",
    "social": "Instagram Reels, TikToks, X posts and their fetched captions",
    "videos": "YouTube videos (metadata, no transcripts)",
    "papers": "papers",
    "pdfs": "PDF documents",
}
KINDS = ["instagram", "tiktok", "youtube", "x", "voice", "video_note", "audio", "video", "photo", "document"]
URL_KINDS = [("instagram", r"instagram\.com|instagr\.am"), ("tiktok", r"tiktok\.com"),
             ("youtube", r"youtube\.com|youtu\.be"), ("x", r"//(?:www\.|mobile\.)?(?:x|twitter)\.com")]
GENERIC = re.compile(r"^(?:Video by |TikTok (?:by|video)|Instagram$|Instagram Reel$|Voice note$|Note$)", re.I)


def cell(text, n=None):
    text = " ".join(str(text).split())
    if n and len(text) > n:
        text = text[:n].rsplit(" ", 1)[0] + "…"
    return text.replace("|", "\\|").replace("$", "\\$")  # "$$" would render as math


def plain(text):
    """Markdown summary line -> plain text: wikilinks to their alias or name, no bold or code marks."""
    text = re.sub(r"\[\[([^\]|]+)\\?\|([^\]]+)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\]#]+)(?:#[^\]]*)?\]\]", r"\1", text)
    return re.sub(r"\*\*|`", "", text)


def summary(body):
    return next((line.strip() for line in body.splitlines() if line.strip() and not line.startswith(("#", "!", ">"))), "")


def tags_of(fm):
    tags = (fm or {}).get("tags") or []
    return [tags] if isinstance(tags, str) else tags


def captured(path, fm):
    for key in ("captured", "created"):
        value = str((fm or {}).get(key, ""))
        if re.match(r"\d{4}-\d{2}-\d{2}", value):
            return value[:10]
    m = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
    return m.group(1) if m else datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def kind(fm):
    tags = tags_of(fm)
    for k in KINDS:
        if k in tags:
            return k.replace("_", " ")
    url = str((fm or {}).get("source", ""))
    for k, rx in URL_KINDS:
        if re.search(rx, url, re.I):
            return k
    return "link" if url.startswith("http") else "text"


def creator(path, body):
    m = re.search(r"\*\*(?:Creator|Author|Uploader|Handle):\*\*\s*([^\n·]+)", body)
    if m:
        handle = re.search(r"\(@?([\w.\-]+)\)\s*$", m.group(1).strip()) or re.fullmatch(r"@([\w.\-]+)", m.group(1).strip())
        return f"@{handle.group(1)}" if handle else m.group(1).strip()
    m = re.search(r"Video by ([\w.\-]+)$", path.stem) or re.match(r"([\w.\-]+) (?:Reel caption|X post|YouTube) ", path.stem)
    return f"@{m.group(1)}" if m else ""


SKIP_LINE = re.compile(r"^(?:#|\*\*\w[\w ]*:\*\*|!\[\[|>|_|-{3}|https?://\S+$)|^Raw Telegram capture awaiting|fetched verbatim")


def own_title(path, body):
    """What the source says it is: metadata title, else its first line of real content (caption, post, text), else its name."""
    m = re.search(r"\*\*Title:\*\*\s*(.+)", body)
    if m:
        return m.group(1)
    line = next((l.strip() for l in body.splitlines() if l.strip() and not SKIP_LINE.search(l.strip())), "")
    if line:
        return line
    name = re.sub(r"^\d{4}-\d{2}-\d{2}(?: \d{4})? ", "", path.stem)
    return "" if GENERIC.match(name) else name


def build():
    """{index path: content} for every Sources type folder that holds sources."""
    files = list(vault_files())
    resolver = Resolver(files)
    notes = [p for p in files if p.suffix == ".md"]
    citing = defaultdict(list)         # source path -> wiki notes linking to it (body or `sources:` frontmatter)
    source_links = defaultdict(int)    # wiki note -> how many sources it links (fewer = more specific)
    linked_sources = defaultdict(set)  # source path -> other sources it links (an enrichment source -> its capture)
    info = {}
    for p in notes:
        if p.stem in NAV or p.parent == ROOT or "00 - Inbox" in p.parts or p.name.startswith(PREFIX):
            continue
        text = read(p)
        targets = [q for q in (resolver.resolve(t) for t in links(text)) if q is not None and in_sources(q) and q != p]
        if in_sources(p):
            linked_sources[p].update(targets)
            continue
        fm, body = frontmatter(text)
        info[p] = ((fm or {}).get("type"), summary(body))
        for q in dict.fromkeys(targets):
            citing[q].append(p)
            source_links[p] += 1
    for p, others in linked_sources.items():   # an enrichment source shares the wiki notes of the capture it belongs to
        if not citing.get(p):
            citing[p] = list(dict.fromkeys(n for q in sorted(others) for n in citing.get(q, [])))

    out = {}
    for folder in sorted(d for d in SOURCES.iterdir() if d.is_dir()):
        index_path = folder / f"{PREFIX}{folder.name}.md"
        rows = []
        for p in folder.rglob("*.md"):
            if p == index_path:
                continue
            fm, body = frontmatter(read(p))
            wiki = sorted(citing.get(p, []), key=lambda n: (info[n][0] == "moc", source_links[n], n.stem.lower()))
            about = next((info[n][1] for n in wiki if info[n][0] != "moc" and info[n][1]), "") or own_title(p, body)
            display = re.sub(r"^\d{4}-\d{2}-\d{2} \d{4} ", "", p.stem)
            source = f"[[{p.stem}\\|{display}]]" if display != p.stem else f"[[{p.stem}]]"
            wiki_cell = ", ".join(f"[[{n.stem}]]" for n in wiki[:4]) + (f" +{len(wiki) - 4} more" if len(wiki) > 4 else "")
            rows.append((captured(p, fm), p.stem.lower(),
                         f"| {captured(p, fm)} | {kind(fm)} | {source} | {cell(creator(p, body))} | {cell(plain(about), 140)} | {wiki_cell or '—'} |"))
        if not rows:
            continue
        rows.sort(reverse=True)
        old_fm, _ = frontmatter(read(index_path)) if index_path.exists() else (None, "")
        created = (old_fm or {}).get("created") or date.today().isoformat()
        lines = [
            "---", f"created: {created}", "type: moc", "status: active", "tags: [sources-index, llm-wiki]",
            "generated: scripts/source_index.py", "---",
            f"Generated catalog of the {len(rows)} raw sources in `Sources/{folder.name}` ({DESCRIPTION.get(folder.name, folder.name)}), "
            "newest first, with the wiki notes built from each.",
            "",
            "> [!info] Generated by `python scripts/source_index.py` on every ingest. Hand edits are overwritten.",
            "> Search: Ctrl+F on this page, or `path:\"Sources/" + folder.name + "\" <words>` in Obsidian search.",
        ]
        month = None
        for day, _, row in rows:
            if day[:7] != month:
                month = day[:7]
                lines += ["", f"## {month}", "", "| Captured | Kind | Source | Creator | About | Wiki notes |", "|---|---|---|---|---|---|"]
            lines.append(row)
        out[index_path] = "\n".join(lines) + "\n"

    index_md = ROOT / "index.md"
    if index_md.exists():
        text = read(index_md)
        if START in text and END in text:
            items = []
            for path, content in sorted(out.items()):
                n = re.search(r"catalog of the (\d+) raw sources", content).group(1)
                items.append(f"- [[{path.stem}]] — {n} {DESCRIPTION.get(path.parent.name, path.parent.name)}")
            block = START + "\n" + "\n".join(items) + "\n" + END
            out[index_md] = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
        else:
            print(f"index.md has no {START} … {END} markers; add them where the Sources list belongs.")
    return out


def stale(out=None):
    out = out if out is not None else build()
    return [p for p, content in out.items() if not p.exists() or read(p) != content]


def main():
    out = build()
    changed = stale(out)
    if "--check" in sys.argv:
        for p in changed:
            print("out of date:", p.relative_to(ROOT).as_posix())
        sys.exit(1 if changed else 0)
    for p in changed:
        p.write_text(out[p], encoding="utf-8", newline="\n")
    total = sum(int(re.search(r"catalog of the (\d+)", c).group(1)) for p, c in out.items() if p.name.startswith(PREFIX))
    print(f"Source indexes: {len(changed)} updated, {sum(p.name.startswith(PREFIX) for p in out)} total, {total} sources.")


if __name__ == "__main__":
    main()
