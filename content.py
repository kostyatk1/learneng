"""Лексикон (существительные / глаголы / прилагательные), неправильные глаголы, проверка ответов, локальный перевод."""
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(__file__).parent
_CYR = re.compile(r"[а-яё]", re.I)

GROUPS = {"N": "Существительные", "V": "Глаголы", "A": "Прилагательные"}
GROUP_NOTE = {"N": "сущ., местоимения, предлоги, союзы", "V": "глаголы, фразовые глаголы",
              "A": "прил., причастия, наречия от прилагательных"}


def has_cyrillic(s: str) -> bool:
    return bool(_CYR.search(s))


def norm_key(en: str) -> str:
    e = en.lower().strip()
    return e[3:] if e.startswith("to ") else e


def _load_lexicon():
    out, seen, g = [], set(), None
    for f in ("lexicon_a2.txt", "lexicon_b1.txt"):
        for line in (BASE / f).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("# "):
                g = line[2]
                continue
            p = [x.strip() for x in line.split("|")]
            en, label, ru, ex, note, level = p[:6]
            if norm_key(en) in seen:
                continue
            seen.add(norm_key(en))
            out.append({"en": en, "label": label, "ru": ru, "ex": ex, "note": note, "level": level, "group": g})
    return out


LEX = _load_lexicon()
BY_GROUP = {g: [w for w in LEX if w["group"] == g] for g in GROUPS}
BY_EN = {w["en"].lower(): w for w in LEX}
BY_KEY = {norm_key(w["en"]): w for w in LEX}

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


# ---------------- локальный словарь (мгновенный перевод без интернета) ----------------
def _build_index():
    en2ru, ru2en = {}, {}
    for w in LEX:
        en2ru.setdefault(canon(norm_key(w["en"])), w)
        en2ru.setdefault(canon(w["en"]), w)
        for part in re.split(r"[;,/]", re.sub(r"\(.*?\)", "", w["ru"].lower())):
            part = part.strip()
            if part and len(part) > 2:
                ru2en.setdefault(part, w)
    return en2ru, ru2en


_EN2RU, _RU2EN = _build_index()


def lookup(text: str):
    """Ищет слово в лексиконе: dict слова / {"verb": ...} (форма неправильного глагола) / None."""
    t = text.strip()
    if has_cyrillic(t):
        return _RU2EN.get(t.lower().strip(" .?!"))
    c = canon(t)
    if not c:
        return None
    for key in (c, "to " + c):
        if key in _EN2RU:
            return _EN2RU[key]
    v = VERB_BY_FORM.get(c)
    return {"verb": v} if v else None
