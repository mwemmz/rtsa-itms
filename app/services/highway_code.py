"""Search over the Zambian Highway Code.

The Code is split into numbered *rules* ("Rule 128 ...") grouped under sections.
``scripts/ingest_highway_code.py`` turns the official PDF into one JSON line per
rule (``HIGHWAY_CODE_PATH``); this module loads that file and finds the rules
that best match a question using BM25, a standard keyword-ranking method. It is
pure Python with no extra dependencies, so it runs on the free Render tier.

``parse_rules`` is the PDF-text parser the ingest script uses. It lives here so
the tests can exercise it without a PDF.
"""

from __future__ import annotations

import difflib
import json
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings

MAX_RULE_CHARS = 1500

_STOPWORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have how i if in into is it its
may me my not of on or our should so than that the their them then there these they this to us was we were
what when where which who why will with would you your must
""".split())


@dataclass
class Rule:
    number: int
    section: str
    text: str
    tokens: list[str] = field(default_factory=list, repr=False)

    def public(self) -> dict:
        return {"rule": self.number, "section": self.section, "text": self.text}


# People ask in everyday words; the Code uses its own. Both the rules and the question are
# passed through the same mapping, so "seatbelt", "highway" and "drunk" find the right rule.
_COMPOUNDS = (
    (re.compile(r"\bseat-?belts?\b"), "seat belt"),
    (re.compile(r"\b(?:cell|mobile|smart)-?phones?\b"), "phone"),
    (re.compile(r"\bzebra crossings?\b"), "pedestrian crossing"),
    (re.compile(r"\bround-?abouts?\b"), "roundabout"),
    (re.compile(r"\byield\b"), "give way"),
)
# Only applied to questions (never to the rule text): people ask how far to stay behind the car in
# front; the Code answers with "gap" and "two-second".
_QUERY_ONLY = (
    (re.compile(r"\b(?:stay(?:ing)? )?(?:behind|after) (?:the )?(?:car|vehicle)(?: in front)?\b|\b(?:car|vehicle) in front\b|\bstay(?:ing)? behind\b|\btailgat\w*|\bfollowing distance\b"), "gap two second distance"),
)
# Questions about a rear-end crash are about keeping a safe gap, not about reversing.
_QUERY_ONLY += (
    (re.compile(r"\b(?:hit|hits|hitting|crash(?:ed|es)? into|ran into|run into|bump(?:ed)? into|slam(?:med)? into)\b[^.?!]{0,40}?\b(?:from )?(?:behind|the back|the rear|rear)\b|\brear-?end\w*|\bhit (?:the )?back of\b"),
     "gap two second distance"),
)
# More everyday wordings that the Code expresses differently.
_QUERY_ONLY += (
    (re.compile(r"\b(?:have|had|having|was in|been in|involved in|cause[ds]?|caused) (?:an?|the) (?:accident|crash|collision)\b|\bafter (?:an?|the) (?:accident|crash|collision)\b|\b(?:accident|crash|collision) happen\w*"),
     "crash stop assistance casualties injured police report"),
    (re.compile(r"\b(?:double |single )?yellow lines?\b"), "yellow lines kerb park stand"),
    (re.compile(r"\bbrakes? (?:fail\w*|not working|stopped working|gone|give out)\b|\bno brakes\b"), "emergency brake immediately skid lock wheels"),
)
_QUERY_ONLY += (
    (re.compile(r"\b(?:break|breaks|broke|broken)(?:ing)? down\b|\bstalled\b|\bflat tyre\b|\bpuncture\w*"), "breakdown vehicle off road warn other traffic triangle"),
    (re.compile(r"\b(?:not allowed|allowed|can i|may i|where (?:can|may|must not)|where am i not)\b[^.?!]{0,15}\bpark\w*"), "do not park stand kerb signs"),
    (re.compile(r"\bwhich side\b[^.?!]{0,25}\bovertak\w*|\bovertak\w* on (?:the )?(?:left|right)\b"), "overtake only on the right"),
    (re.compile(r"\b(?:when|where)\b[^.?!]{0,20}\b(?:not|never|must not|should not|shouldn't)\b[^.?!]{0,10}\bovertak\w*"), "do not overtake junction bend crossing narrows brow"),
    (re.compile(r"\b(?:ambulance|fire engine|fire truck|siren|police car|emergency vehicle)s?\b"), "emergency vehicles ambulance give way"),
    (re.compile(r"\b(?:change|changing|switch|switching|merge|merging)\b[^.?!]{0,10}\blanes?\b"), "change lane mirrors blind spots signal"),
    (re.compile(r"\b(?:helmet|crash helmet)s?\b"), "motor cyclists protective clothing helmet"),
)
_QUERY_ONLY += (
    (re.compile(r"\b(?:around|near|behind|past|passing|next to|beside)\b[^.?!]{0,10}\b(?:cyclists?|bicycles?|bikes?)\b"), "overtaking cyclists do not drive too close leave room"),
)
_SYNONYMS = {
    "rest": "break", "resting": "break", "stopped": "park", "stopping": "park", "stops": "park",
    "dirty": "clean", "bicycle": "cycle", "bicycles": "cycle", "bike": "cycle", "bikes": "cycle", "cyclist": "cycle", "cyclists": "cycle",
    "motorbike": "motorcycle", "motorbikes": "motorcycle", "motorcyclist": "motorcycle", "motorcyclists": "motorcycle",
    "medication": "medicine", "medications": "medicine", "pills": "medicine", "prescription": "medicine",
    "rain": "wet weather", "raining": "wet weather", "rainy": "wet weather", "storm": "wet weather", "slippery": "wet weather",
    "ambulances": "ambulance",
    "highway": "road", "motorway": "road", "expressway": "road",
    "drunk": "alcohol", "drink": "alcohol", "drinking": "alcohol", "intoxicated": "alcohol", "beer": "alcohol", "liquor": "alcohol",
    "fast": "speed", "faster": "speed", "speeding": "speed", "kph": "speed", "kmh": "speed",
    "pass": "overtake", "passing": "overtake", "overtaking": "overtake",
    "stop": "park", "parking": "park", "parked": "park",
    "kid": "child", "kids": "child", "children": "child",
    "walker": "pedestrian", "pedestrians": "pedestrian",
    "licence": "license", "licenses": "license",
}


def tokenize(text: str, query: bool = False) -> list[str]:
    """Lower-case words, everyday wording mapped to the Code's, stopwords dropped, endings trimmed."""
    text = text.lower()
    for pattern, repl in _COMPOUNDS + (_QUERY_ONLY if query else ()):
        text = pattern.sub(repl, text)
    out = []
    for w in re.findall(r"[a-z0-9]+", text):
        w = _SYNONYMS.get(w, w)
        for part in w.split():
            if part in _STOPWORDS or len(part) < 2:
                continue
            for suffix in ("ing", "es", "ed", "s"):
                if part.endswith(suffix) and len(part) - len(suffix) >= 3:
                    part = part[: -len(suffix)]
                    break
            out.append(part)
    return out


_EXCLUDED = re.compile(r"\b(?:outside|except|apart from|other than)\s+(?:of\s+)?(?:a|an|the)?\s*[\w-]+(?:\s+[\w-]+){0,1}", re.IGNORECASE)


def rule_tokens(section: str, text: str) -> list[str]:
    """Index tokens for a rule. A place the rule excludes ("outside a built-up area") is left out,
    so a question about built-up areas does not land on the rule for open roads."""
    return tokenize(section + " " + _EXCLUDED.sub(" ", text))


# ---------------------------------------------------------------- parsing

_RULE_START = re.compile(r"^\s*(\d{1,3})\.\s+(\S.*)$")
_SECTION = re.compile(r"^\s*(SECTION\s+\d+\s*[:\-–.]?\s*.*)$", re.IGNORECASE)


_LIGATURES = str.maketrans({"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl", "\uf001": "fi", "\uf002": "fl", "\u0178": "-"})
_PAGE_FOOTER = re.compile(r"(?:\s+[A-Z][A-Z ,&()/-]{7,})?\s*ZAMBIAN HIGHWAY CODE\s*\d*")


# A chapter heading the PDF repeats on every page ends up glued to the end of the last rule on the page.
_RUNNING_HEAD = re.compile(
    r"(?:\s+(?:USING THE ROAD|LINES AND LANES|SPEED LIMITS|VEHICLE LIGHTS|USE OF THE HORN|WAITING AND PARKING|"
    r"BRAKING|VEHICLE MARKINGS NIGHT CONDITION|WHEELBARROWS AND HUMAN DRAWN CARTS|"
    r"PARENTS AND OTHERS IN CHARGE OF CHILDREN))+\s*$"
)


def _strip_trailing_heading(text: str) -> str:
    """Drop an ALL-CAPS page heading left after the last sentence of a rule ("... see you. RULES FOR PASSENGERS")."""
    words = text.split()
    i = len(words)
    while i > 0 and re.fullmatch(r"[A-Z0-9(),&'/:-]+", words[i - 1]) and not words[i - 1].isdigit():
        i -= 1
    tail = words[i:]
    # Only a real heading: at least two capitalised words, and the text before it ends a sentence.
    if i > 0 and words[i - 1][-1] in ".!?)\"”" and (len(tail) >= 2 or len(tail[0]) >= 5 if tail else False):
        return " ".join(words[:i])
    return text


def _tidy(text: str) -> str:
    """Undo PDF quirks: ligature glyphs (traffic written as one 'ffi' character) and page footers."""
    text = text.translate(_LIGATURES)
    text = re.sub(r"\b([Tt]raf)f?\s?ic", r"\1fic", text)  # "Traff ic" -> "Traffic"
    text = re.sub(r"Road Traffic ?Signs and Markings", " ", text)
    text = _PAGE_FOOTER.sub(" ", text)
    text = re.sub(r"\s*\(RULE \d+(?: TO \d+)?\)", "", text, flags=re.IGNORECASE)  # picture-caption markers
    text = _RUNNING_HEAD.sub("", text)
    text = _strip_trailing_heading(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_rules(text: str) -> list[dict]:
    """Split Highway Code text into ``{"rule", "section", "text"}`` dicts.

    A rule starts on a line beginning with its number and a full stop and runs
    to the next rule. Rule numbers are expected to rise; a number that jumps
    backwards or far ahead is treated as a list item inside the current rule
    (the Code uses "1." style sub-points in places).
    """
    rules: list[dict] = []
    section = ""
    current: dict | None = None
    last = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        sec = _SECTION.match(line)
        if sec and len(line) < 120:
            section = re.sub(r"\s+", " ", sec.group(1)).strip()
            continue
        m = _RULE_START.match(line)
        if m:
            n = int(m.group(1))
            if last < n <= last + 5:  # the next rule, allowing for a few gaps
                if current:
                    rules.append(current)
                current = {"rule": n, "section": section, "text": m.group(2).strip()}
                last = n
                continue
        if current:
            current["text"] += " " + line
    if current:
        rules.append(current)
    for r in rules:
        r["text"] = _tidy(r["text"])[:MAX_RULE_CHARS]
    return rules


# ---------------------------------------------------------------- index

_lock = threading.Lock()
_cache: dict = {"path": None, "mtime": None, "index": None}


_SENTENCE = re.compile(r"(?<=[.;:!?])\s+(?=[A-Z(\[])")


def split_sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENTENCE.split(text) if p.strip()]


class Index:
    def __init__(self, rules: list[Rule]):
        self.rules = rules
        self.by_number = {r.number: r for r in rules}
        self.n = len(rules)
        self.avg_len = (sum(len(r.tokens) for r in rules) / self.n) if self.n else 0.0
        df: Counter = Counter()
        for r in rules:
            df.update(set(r.tokens))
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.vocab = list(df)
        self._bigrams = {r.number: set(zip(r.tokens, r.tokens[1:])) for r in rules}

    def expand(self, query: str) -> list[str]:
        """Query tokens, with a misspelt word replaced by the closest word the Code actually uses."""
        out = []
        for t in tokenize(query, query=True):
            if t not in self.idf and len(t) >= 4:
                close = difflib.get_close_matches(t, self.vocab, n=1, cutoff=0.82)
                if close:
                    t = close[0]
            out.append(t)
        return out

    def search(self, query: str, k: int = 4) -> list[tuple[Rule, float]]:
        q = self.expand(query)
        if not q or not self.n:
            return []
        k1, b = 1.5, 0.75
        qpairs = set(zip(q, q[1:]))
        scored = []
        for r in self.rules:
            tf = Counter(r.tokens)
            dl = len(r.tokens) or 1
            s = 0.0
            for t in set(q):
                f = tf.get(t, 0)
                if f:
                    s += self.idf.get(t, 0.0) * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / (self.avg_len or 1)))
            if s > 0:
                # words that sit side by side in both the question and the rule are a much stronger sign
                s *= 1 + 0.25 * len(qpairs & self._bigrams[r.number])
                scored.append((r, s))
        scored.sort(key=lambda x: -x[1])
        if not scored:
            return []
        # keep the rules that are close to the best one; a long tail of weak matches is noise
        top = scored[0][1]
        return [x for x in scored if x[1] >= 0.45 * top][:k]

    def best_sentences(self, rule: Rule, query: str, n: int = 2) -> list[str]:
        """The sentences of a rule that answer the question best, in their original order."""
        sents = split_sentences(rule.text)
        if len(sents) <= 1:
            return sents
        q = set(self.expand(query))
        scored = []
        for i, sent in enumerate(sents):
            toks = set(tokenize(sent))
            scored.append((sum(self.idf.get(t, 0.0) for t in toks & q), i))
        keep = sorted(i for sc, i in sorted(scored, reverse=True)[:n] if sc > 0)
        return [sents[i] for i in keep] or sents[:1]

    def near_topics(self, query: str, k: int = 3) -> list[str]:
        """Section names of the rules that share at least a word with the question (for 'did you mean')."""
        q = set(self.expand(query))
        seen, out = set(), []
        ranked = sorted(self.rules, key=lambda r: -sum(self.idf.get(t, 0.0) for t in set(r.tokens) & q))
        for r in ranked:
            if not (set(r.tokens) & q):
                break
            sec = r.section.strip()
            if sec and sec not in seen:
                seen.add(sec)
                out.append(sec)
            if len(out) >= k:
                break
        return out


def load_index(path: str | None = None) -> Index | None:
    """The loaded Code, or None when the data file hasn't been created yet."""
    p = Path(path or settings.HIGHWAY_CODE_PATH)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    with _lock:
        if _cache["path"] == str(p) and _cache["mtime"] == mtime and _cache["index"] is not None:
            return _cache["index"]
        rules = []
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                r = Rule(number=int(d["rule"]), section=d.get("section", ""), text=d["text"])
                r.tokens = rule_tokens(r.section, r.text)
                rules.append(r)
        idx = Index(rules)
        _cache.update(path=str(p), mtime=mtime, index=idx)
        return idx
