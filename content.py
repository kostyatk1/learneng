"""Банк слов и фраз + проверка ответов."""
import re
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(__file__).parent


def _load_words():
    bank, topic = {}, None
    for line in (BASE / "words.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("#"):
            topic = line[1:].strip()
            bank[topic] = []
            continue
        en, ru, ex = (p.strip() for p in line.split("|", 2))
        bank[topic].append({"en": en, "ru": ru, "ex": ex, "topic": topic})
    return bank


def _load_phrases():
    out = []
    for line in (BASE / "phrases.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        ru, en = line.split("|", 1)
        out.append({"ru": ru.strip(), "en": [v.strip() for v in en.split("/") if v.strip()]})
    return out


BANK = _load_words()
TOPICS = list(BANK)                                   # порядок тем = индексы в настройках
ALL_WORDS = [w for t in TOPICS for w in BANK[t]]
BY_EN = {w["en"].lower(): w for w in ALL_WORDS}
PHRASES = _load_phrases()

_CYR = re.compile(r"[а-яё]", re.I)


def has_cyrillic(s: str) -> bool:
    return bool(_CYR.search(s))


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("`", "'")
    s = re.sub(r"[^\w\s']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def check_answer(user: str, variants: list[str]) -> str:
    """exact / close (мелкая опечатка) / wrong"""
    u = norm(user)
    best = max(SequenceMatcher(None, u, norm(v)).ratio() for v in variants)
    if best >= 0.999:
        return "exact"
    return "close" if best >= 0.88 else "wrong"
