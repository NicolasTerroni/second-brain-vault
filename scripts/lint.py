#!/usr/bin/env python3
"""Zero-token vault lint: broken wikilinks, orphans, missing frontmatter, index gaps, stale source indexes.
Run: python scripts/lint.py   (from any folder). Judgement checks (contradictions, stale claims) stay with the LLM.

Links resolve like Obsidian: note names, attachments and canvases by file name (`[[clip.oga]]`), and full or
partial paths (`[[03 - Resources/Sources/x/Note]]`). `log.md` is append-only history, so its links to renamed
notes are not errors. Raw sources are catalogued in their per-type source index, not in index.md."""
import re, sys
from vault import ROOT, NAV, SYSTEM, Resolver, frontmatter, in_sources, links, read, rel, vault_files
import source_index

REQ = ("created", "type", "status", "tags")

files = list(vault_files())
resolver = Resolver(files)
notes = [p for p in files if p.suffix == ".md"]
inbound = {p: 0 for p in notes}
broken, nofm, missing = [], [], []

for p in notes:
    text = read(p)
    if p.stem not in SYSTEM and not in_sources(p):
        fm, _ = frontmatter(text)
        if fm is None:
            nofm.append(rel(p))
        else:
            miss = [k for k in REQ if k not in fm]
            if miss:
                missing.append(f"{rel(p)} (missing: {', '.join(miss)})")
    if p.stem in SYSTEM:
        continue
    for t in links(text):
        q = resolver.resolve(t)
        if q is None:
            if p.stem != "log":
                broken.append(f"{rel(p)} -> [[{t}]]")
        elif q != p and q in inbound:
            inbound[q] += 1

indexed = {resolver.resolve(t) for t in links(read(ROOT / "index.md"))} if (ROOT / "index.md").exists() else set()
skip = lambda p: p.stem in SYSTEM | NAV or "00 - Inbox" in p.parts  # Inbox captures are unprocessed by design
orphans = [rel(p) for p, c in inbound.items() if c == 0 and not skip(p)]
unindexed = [rel(p) for p in notes if p not in indexed and not skip(p) and not in_sources(p)]
stale = [rel(p) for p in source_index.stale()]


def show(title, items):
    print(f"\n## {title} ({len(items)})")
    for i in sorted(items):
        print(" -", i)


show("Broken wikilinks", broken)
show("Orphan notes (no inbound links)", orphans)
show("Notes not in index.md", unindexed)
show("Source indexes out of date (run: python scripts/source_index.py)", stale)
show("No frontmatter", nofm)
show("Incomplete frontmatter", missing)
print(f"\n{len(notes)} notes scanned.")
sys.exit(1 if broken else 0)
