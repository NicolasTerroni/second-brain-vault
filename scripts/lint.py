#!/usr/bin/env python3
"""Zero-token vault lint: broken wikilinks, orphans, missing frontmatter, index gaps.
Run: python scripts/lint.py   (from vault root). Judgement checks (contradictions, stale claims) stay with the LLM."""
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {".obsidian", "scripts", ".git", "docs"}
LINK = re.compile(r"\[\[([^\]|#\\]+)(?:#[^\]|]*)?(?:\\?\|[^\]]*)?\]\]")  # \| = alias escaped inside a table
REQ = ("created", "type", "status", "tags")

notes = {p.stem: p for p in ROOT.rglob("*.md") if not SKIP & set(p.relative_to(ROOT).parts)}
inbound = {n: 0 for n in notes}
broken, nofm, missing = [], [], []

for name, p in notes.items():
    text = p.read_text(encoding="utf-8", errors="ignore")
    rel = p.relative_to(ROOT)
    if name not in ("AGENTS", "CLAUDE", "README") and "Sources" not in rel.parts:
        m = re.match(r"---\n(.*?)\n---", text, re.S)
        if not m:
            nofm.append(str(rel))
        else:
            miss = [k for k in REQ if not re.search(rf"^{k}:", m.group(1), re.M)]
            if miss:
                missing.append(f"{rel} (missing: {', '.join(miss)})")
    if name in ("AGENTS", "CLAUDE"):
        continue
    for t in set(LINK.findall(text)):
        t = t.strip()
        if t in notes:
            if t != name:
                inbound[t] += 1
        else:
            broken.append(f"{rel} -> [[{t}]]")

index = (ROOT / "index.md").read_text(encoding="utf-8") if (ROOT / "index.md").exists() else ""
indexed = set(LINK.findall(index))
orphans = [str(notes[n].relative_to(ROOT)) for n, c in inbound.items() if c == 0 and n not in ("AGENTS", "CLAUDE", "README", "index", "log")]
unindexed = [str(p.relative_to(ROOT)) for n, p in notes.items() if n not in indexed and n not in ("AGENTS", "CLAUDE", "README", "index", "log") and "00 - Inbox" not in p.parts]

def show(title, items):
    print(f"\n## {title} ({len(items)})")
    for i in sorted(items): print(" -", i)

show("Broken wikilinks", broken)
show("Orphan notes (no inbound links)", orphans)
show("Notes not in index.md", unindexed)
show("No frontmatter", nofm)
show("Incomplete frontmatter", missing)
print(f"\n{len(notes)} notes scanned.")
sys.exit(1 if broken else 0)
