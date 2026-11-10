"""Генератор русской транскрипции для everyday.txt → everyday_tr.json (запускается разработчиком, не ботом).
Нужен пакет cmudict (pip install cmudict). Транскрипция ПРИБЛИЗИТЕЛЬНАЯ: русскими буквами, ударение — знаком ́ ."""
import json
import re
import sys
from pathlib import Path

import cmudict

BASE = Path(__file__).resolve().parent.parent
D = cmudict.dict()
ACUTE = "\u0301"

VOW = {"AA": "а", "AE": "э", "AO": "о", "AW": "ау", "AY": "ай", "EH": "э", "EY": "эй", "IH": "и", "IY": "и",
       "OW": "оу", "OY": "ой", "UH": "у", "UW": "у"}
CON = {"B": "б", "CH": "ч", "D": "д", "DH": "з", "F": "ф", "G": "г", "HH": "х", "JH": "дж", "K": "к", "L": "л",
       "M": "м", "N": "н", "NG": "нг", "P": "п", "R": "р", "S": "с", "SH": "ш", "T": "т", "TH": "с", "V": "в",
       "W": "в", "Y": "й", "Z": "з", "ZH": "ж"}

# ручные транскрипции: сленг, сокращения, слова вне словаря (уже с ударением)
OVR = {"gonna": "га́на", "wanna": "ва́на", "gotta": "га́та", "kinda": "ка́йнда", "lemme": "ле́ми", "gimme": "ги́ми",
       "dunno": "дэна́у", "y'all": "йол", "'cause": "коз", "wi-fi": "уа́йфай", "facetime": "фэ́йстайм", "ac": "эй-си́",
       "mira": "ми́ра", "preheated": "прихи́тэд", "screenshot": "скри́ншот", "voicemail": "во́йсмэйл",
       "sketchy": "ске́чи", "bummed": "бамд", "muggy": "ма́ги", "goofy": "гу́фи", "clogged": "кло́гд", "faucet": "фо́сит",
       "congrats": "конгра́тс", "okay": "оукэ́й", "tonight": "тунайт", "lit": "лит", "sweetheart": "суи́тхарт",
       "binge-watch": "бинж-уо́ч", "rip-off": "рип-оф", "a.m": "эй-э́м"}


def word_ph(w: str):
    w = w.lower()
    if w in OVR:
        return OVR[w]
    prons = D.get(w)
    if not prons and "-" in w:
        return "-".join(word_ph(p) for p in w.split("-"))
    if not prons and "'" in w:                       # сокращения: what're, who's, ...
        for suf, ru in (("n't", "нт"), ("'ll", "л"), ("'re", "р"), ("'ve", "в"), ("'d", "д"), ("'m", "м"), ("'s", "з")):
            if w.endswith(suf) and (w[:-len(suf)] in D or w[:-len(suf)] in OVR):
                return word_ph(w[:-len(suf)]) + ru
    if not prons:
        return None
    ph = prons[0]
    nv = sum(1 for p in ph if p[-1].isdigit())
    out = []

    def stress(x: str) -> str:
        if "ё" in x:
            return x
        for k, ch in enumerate(x):
            if ch in "аэоуиюя":
                return x[:k + 1] + ACUTE + x[k + 1:]
        return x

    for i, p in enumerate(ph):
        base, st = (p[:-1], p[-1]) if p[-1].isdigit() else (p, "")
        nxt = ph[i + 1].rstrip("012") if i + 1 < len(ph) else ""
        if st != "":                                  # гласный
            if base == "AH":
                x = "а" if st in "12" else "э"
            elif base == "ER":
                x = "ёр" if st in "12" else "эр"
            else:
                x = VOW[base]
            if out and out[-1] == "й" and base == "UW":
                out.pop(); x = "ю"
            elif out and out[-1] == "й" and base == "AH" and st in "12":
                out.pop(); x = "я"
            out.append(stress(x) if (st == "1" and nv > 1) else x)
        elif base == "W":
            out.append("в" if nxt in ("IY", "IH") else "у")
        elif base == "NG" and nxt == "K":
            out.append("н")
        else:
            out.append(CON[base])
    return "".join(out)


def sentence_tr(s: str):
    toks = re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*", s.replace("’", "'"))
    res, missing = [], []
    for t in toks:
        if t.isdigit():
            res.append(t); continue
        r = word_ph(t.strip("."))
        if r is None:
            missing.append(t); r = "?"
        res.append(r)
    return " ".join(res), missing


if __name__ == "__main__":
    out, miss = {}, set()
    for line in (BASE / "everyday.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("//", "#")):
            continue
        en = line.split("|")[0]
        tr, m = sentence_tr(en)
        out[en] = tr
        miss.update(m)
    (BASE / "everyday_tr.json").write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(len(out), "транскрипций; нет в словаре:", sorted(miss))
