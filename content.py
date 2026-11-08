"""Банки слов, сленга, фраз и неправильных глаголов + проверка ответов + локальный словарь."""
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(__file__).parent
_CYR = re.compile(r"[а-яё]", re.I)


def has_cyrillic(s: str) -> bool:
    return bool(_CYR.search(s))


def _lines(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            yield line


def _load_bank(files, kind: str, seen: set):
    bank: dict[str, list] = {}
    topic = None
    for f in files:
        for line in _lines(f):
            if line.startswith("#"):
                topic = line[1:].strip()
                bank.setdefault(topic, [])
                continue
            p = [x.strip() for x in line.split("|")]
            en, ru, ex = p[0], p[1], p[2] if len(p) > 2 else ""
            extra = p[3] if len(p) > 3 else ""
            if en.lower() in seen:
                continue
            seen.add(en.lower())
            bank[topic].append({"en": en, "ru": ru, "ex": ex, "topic": topic, "kind": kind,
                                "tr": extra if kind == "" else "", "note": extra if kind == "slang" else ""})
    return {t: w for t, w in bank.items() if w}


_seen: set = set()
BANK = _load_bank(sorted(BASE.glob("words*.txt"), key=lambda p: (p.name != "words.txt", p.name)), "", _seen)
SLANG_BANK = _load_bank([BASE / "slang.txt"], "slang", _seen)   # (темы в одном файле slang.txt)
TOPICS = list(BANK)
SLANG_TOPICS = list(SLANG_BANK)
ALL_WORDS = [w for t in TOPICS for w in BANK[t]]
ALL_SLANG = [w for t in SLANG_TOPICS for w in SLANG_BANK[t]]
BY_EN = {w["en"].lower(): w for w in ALL_WORDS + ALL_SLANG}


def _load_phrases():
    cats, items, seen = [], [], set()
    files = sorted(BASE.glob("phrases*.txt"), key=lambda p: (p.name != "phrases.txt", p.name))
    for f in files:
        cat = None
        for line in _lines(f):
            if line.startswith("#"):
                cat = line[1:].strip()
                if cat not in cats:
                    cats.append(cat)
                continue
            ru, en = line.split("|", 1)
            if ru in seen:
                continue
            seen.add(ru)
            items.append({"ru": ru.strip(), "en": [v.strip() for v in en.split("/") if v.strip()],
                          "cat": cats.index(cat)})
    return cats, items


PHRASE_CATS, PHRASES = _load_phrases()

_V = json.loads((BASE / "verbs.json").read_text(encoding="utf-8"))
VERB_GROUPS: list[str] = _V["groups"]
VERBS: list[dict] = _V["verbs"]
VERB_BY_FORM: dict[str, dict] = {}
for _v in VERBS:
    for _f in (_v["v1"], _v["v3"], *[x.strip() for x in _v["v2"].split("/")]):
        VERB_BY_FORM.setdefault(_f.lower(), _v)


# ---------------- проверка ответов ----------------
_CONTR = [(r"\bcan't\b", "cannot"), (r"\bwon't\b", "will not"), (r"\blet's\b", "let us"),
          (r"n't\b", " not"), (r"'m\b", " am"), (r"'re\b", " are"), (r"'ve\b", " have"),
          (r"'ll\b", " will"), (r"'d\b", " would"),
          (r"\b(it|he|she|that|what|where|there|here|who|how|when)'s\b", r"\1 is")]


def canon(s: str) -> str:
    """Приводит фразу к единому виду: don't = do not, I'm = I am, без знаков препинания."""
    s = s.lower().replace("’", "'").replace("`", "'")
    for pat, rep in _CONTR:
        s = re.sub(pat, rep, s)
    s = s.replace("can not", "cannot")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def check_answer(user: str, variants: list[str]) -> str:
    """exact / close (мелкая опечатка) / wrong"""
    u = canon(user)
    best = max(SequenceMatcher(None, u, canon(v)).ratio() for v in variants)
    if best >= 0.999:
        return "exact"
    return "close" if best >= 0.88 else "wrong"


def best_ratio(user: str, variants: list[str]) -> float:
    u = canon(user)
    return max(SequenceMatcher(None, u, canon(v)).ratio() for v in variants)


# ---------------- локальный словарь (мгновенный перевод без интернета) ----------------
def _build_index():
    en2ru, ru2en = {}, {}
    for w in ALL_WORDS + ALL_SLANG:
        en2ru.setdefault(canon(w["en"]), w)
        ru2en.setdefault(w["ru"].lower().strip(), w)
        for part in re.split(r"[;,/]|\s\(", re.sub(r"\)", "", w["ru"].lower())):   # «пол; этаж» → «пол», «этаж»
            part = part.strip()
            if part and not part.startswith("(") and len(part) > 2:
                ru2en.setdefault(part, w)
    for p in PHRASES:
        for v in p["en"]:
            en2ru.setdefault(canon(v), {"en": v, "ru": p["ru"], "ex": "", "tr": "", "note": ""})
        ru2en.setdefault(p["ru"].lower().strip(" .?!"), {"en": p["en"][0], "ru": p["ru"], "ex": "", "tr": "", "note": ""})
    return en2ru, ru2en


_EN2RU, _RU2EN = _build_index()


def lookup(text: str):
    """Ищет слово/фразу в наших базах. Возвращает dict(en, ru, ex, tr, note) / dict(verb=...) / None."""
    t = text.strip()
    if has_cyrillic(t):
        return _RU2EN.get(t.lower().strip(" .?!"))
    c = canon(t)
    if not c:
        return None
    for key in (c, "to " + c, c[3:] if c.startswith("to ") else None):
        if key and key in _EN2RU:
            return _EN2RU[key]
    v = VERB_BY_FORM.get(c)
    return {"verb": v} if v else None
