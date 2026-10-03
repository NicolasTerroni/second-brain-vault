"""Shared helpers for the zero-token vault scripts (lint.py, source_index.py): note discovery, frontmatter, link resolution.
Standard library only, so the scripts run on any host Python 3.8+ or in the Docker image."""
import os, re, sys, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "03 - Resources" / "Sources"
SKIP = {".obsidian", "scripts", ".git", "docs", ".trash", "node_modules"}
# [[target]], [[target#heading]], [[target|alias]], [[target\|alias]] (escaped alias inside a table)
LINK = re.compile(r"\[\[([^\]|#\\]+)(?:#[^\]|]*)?(?:\\?\|[^\]]*)?\]\]")
NAV = {"index", "log"}                   # navigation files at the vault root
SYSTEM = {"AGENTS", "CLAUDE", "README"}  # instructions, not notes

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252 and garble accents


def nfc(s):
    return unicodedata.normalize("NFC", s)


def vault_files():
    """Every file in the vault outside system folders."""
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for name in filenames:
            yield Path(dirpath) / name


def rel(path):
    return path.relative_to(ROOT).as_posix()


def in_sources(path):
    return SOURCES in path.parents


def read(path):
    return path.read_text(encoding="utf-8", errors="ignore")


def frontmatter(text):
    """(frontmatter dict of top-level scalar/inline-list keys, or None if absent; body)."""
    m = re.match(r"---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if not m:
        return None, text
    fm = {}
    for line in m.group(1).splitlines():
        km = re.match(r"([A-Za-z_][\w-]*):\s*(.*)$", line)
        if km:
            key, value = km.group(1), km.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                value = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
            else:
                value = value.strip("\"'")
            fm[key] = value
    return fm, text[m.end():]


class Resolver:
    """Resolves a wikilink target the way Obsidian does: note name, file name with extension
    (attachments, canvases), or a full or partial path from the vault root. Case-insensitive."""

    def __init__(self, files):
        self.stems, self.names, self.paths = {}, {}, {}
        for p in files:
            r = nfc(rel(p)).lower()
            self.paths[r] = p
            self.names.setdefault(nfc(p.name).lower(), p)
            if p.suffix == ".md":
                self.stems.setdefault(nfc(p.stem).lower(), p)

    def resolve(self, target):
        t = nfc(target.strip()).replace("\\", "/").strip("/").lower()
        if not t:
            return None
        if "/" in t:
            for cand in (t, t + ".md"):
                if cand in self.paths:
                    return self.paths[cand]
                for r, p in self.paths.items():
                    if r.endswith("/" + cand):
                        return p
            return None
        return self.stems.get(t) or self.names.get(t)


def links(text):
    return {t.strip() for t in LINK.findall(text)}
