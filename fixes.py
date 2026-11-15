"""Word corrections for subtitle files: replace words in an existing .srt, and remember corrections for future files."""
import json, re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORDS_FILE = ROOT / "my_words.json"          # the user's saved corrections; never overwritten by updates


# ---------------------------------------------------------------- saved word list

def load_words():
    try:
        data = json.loads(WORDS_FILE.read_text(encoding="utf-8"))
        return clean_pairs(data)
    except (OSError, ValueError):
        return []

def save_words(pairs):
    pairs = clean_pairs(pairs)
    WORDS_FILE.write_text(json.dumps(pairs, ensure_ascii=False, indent=1), encoding="utf-8")
    return pairs

def clean_pairs(pairs):
    """Drop empty or no-op rows and duplicates (the last entry for a word wins)."""
    seen = {}
    for p in pairs or []:
        w, r = str(p.get("wrong", "")).strip(), str(p.get("right", "")).strip()
        if w and w != r:
            seen[w.lower()] = {"wrong": w, "right": r}
    return list(seen.values())


# ---------------------------------------------------------------- replacing

def make_replacer(pairs, whole_words=True, ignore_case=True):
    """Returns f(text) -> (new_text, Counter of how often each 'wrong' was replaced). All pairs are applied in one pass,
    longest first, so one correction never re-edits the result of another."""
    pairs = clean_pairs(pairs)
    if not pairs:
        return lambda t: (t, Counter())
    table = {(p["wrong"].lower() if ignore_case else p["wrong"]): p for p in pairs}
    alts = "|".join(re.escape(p["wrong"]) for p in sorted(pairs, key=lambda p: -len(p["wrong"])))
    pat = (r"(?<![A-Za-z0-9])(?:" + alts + r")(?![A-Za-z0-9])") if whole_words else "(?:" + alts + ")"
    rx = re.compile(pat, re.I if ignore_case else 0)

    def run(text):
        counts = Counter()
        def sub(m):
            found = m.group(0)
            p = table.get(found.lower() if ignore_case else found)
            if p is None:
                return found
            counts[p["wrong"]] += 1
            right = p["right"]
            if found[:1].isupper() and right[:1].islower() and p["wrong"][:1].islower():
                right = right[:1].upper() + right[1:]          # keep a sentence-initial capital
            return right
        return rx.sub(sub, text), counts
    return run


# ---------------------------------------------------------------- .srt files

def parse_srt(text):
    """-> list of {"num": str, "timing": str, "lines": [str]} (timing lines are kept exactly as they are)."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.split("\n")]
        i = next((k for k, l in enumerate(lines) if "-->" in l), None)
        if i is None:
            continue
        cues.append({"num": lines[i - 1].strip() if i > 0 else str(len(cues) + 1), "timing": lines[i].strip(),
                     "lines": [l.strip() for l in lines[i + 1:] if l.strip()]})
    return cues

def write_srt(cues):
    return "".join(f"{c['num']}\n{c['timing']}\n" + "\n".join(c["lines"]) + "\n\n" for c in cues)

def fix_srt(text, pairs, whole_words=True, ignore_case=True, wrap=None):
    """Apply corrections to an .srt. Timings are untouched; changed cues are re-wrapped to fit two lines.
    Returns (new_text, changes, counts) where changes = [{"time", "before", "after"}]."""
    cues = parse_srt(text)
    if not cues:
        raise ValueError("This does not look like an .srt subtitle file.")
    rep = make_replacer(pairs, whole_words, ignore_case)
    total, changes = Counter(), []
    for c in cues:
        dialogue = len(c["lines"]) > 1 and all(l.startswith("-") for l in c["lines"])
        before = "\n".join(c["lines"]) if dialogue else " ".join(c["lines"])
        after, n = rep(before)
        if not n:
            continue
        total.update(n)
        c["lines"] = after.split("\n") if dialogue else (wrap(after).split("\n") if wrap else [after])
        changes.append({"time": c["timing"][:8], "before": before.replace("\n", " "), "after": after.replace("\n", " ")})
    return write_srt(cues), changes, dict(total)

_STARTERS = {"The", "A", "An", "I", "It", "He", "She", "We", "They", "You", "This", "That", "These", "Those", "And", "But", "So",
             "If", "When", "What", "Where", "Why", "How", "Who", "Yes", "No", "Oh", "Ah", "Well", "Is", "Are", "Was", "Were", "Do",
             "Did", "In", "On", "At", "To", "For", "With", "As", "Now", "Then", "There", "Here", "Please", "Thank", "Sorry", "Mr",
             "Mrs", "Ms", "My", "Our", "His", "Her", "Their", "Your", "Not", "Let", "Can", "Could", "Would", "Will", "Just",
             "Hello", "Okay", "OK", "After", "Before", "Because", "Also", "Even", "Every", "All", "Some", "One", "Two"}

def names_in(text):
    """Capitalised words and phrases that look like names, most frequent first: a quick way to spot spellings to fix."""
    body = " \n ".join(" ".join(c["lines"]) for c in parse_srt(text))
    found = Counter()
    for m in re.finditer(r"[A-Z][a-zA-Z'’]*[a-z](?:[ -][A-Z][a-zA-Z]*[a-z])*", body):
        words = m.group(0).split(" ")
        while words and words[0] in _STARTERS:
            words = words[1:]
        name = " ".join(words).rstrip("'’s") if words and words[-1].endswith(("'s", "’s")) else " ".join(words)
        if name and name not in _STARTERS and len(name) > 2:
            found[name] += 1
    return [{"name": n, "count": c} for n, c in found.most_common(40)]
