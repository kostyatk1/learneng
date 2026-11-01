"""Раздел «Неправильные глаголы»: группы, предложения с V2 (в обе стороны), тренировка, прогресс.

Роутер подключается в main.py ПЕРЕД основным (чтобы кнопка «📘 Глаголы» не ушла в переводчик).
"""
import random
import re
from difflib import SequenceMatcher
from collections import defaultdict, deque

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import content
import handlers as h
import motivation as mot
from storage import LEARNED_BOX, srs_review

router = Router()
V = content.VERBS
G = content.VERB_GROUPS
PAGE = 6                                                  # глаголов на странице с предложениями

RECENT_V: dict[int, deque] = defaultdict(lambda: deque(maxlen=5))
VGROUP: dict[int, int | None] = {}                        # какую группу сейчас тренирует пользователь
VMODE: dict[int, str | None] = {}                         # None — вперемешку, "forms" — только «русский → 3 формы»


# ============================ вспомогательное ============================
def v2_forms(v: dict) -> list[str]:
    return [f.strip() for f in v["v2"].split("/")]


def v2_regex(v: dict):
    return re.compile(r"\b(" + "|".join(re.escape(f) for f in v2_forms(v)) + r")\b", re.I)


def bold_v2(sentence: str, v: dict) -> str:
    return v2_regex(v).sub(lambda m: f"<b>{m.group(0)}</b>", h.esc(sentence))


def group_size(g: int) -> int:
    return sum(1 for v in V if v["g"] == g)


def verb_line(v: dict) -> str:
    return f"{h.esc(v['v1'])} – <b>{h.esc(v['v2'])}</b> – {h.esc(v['v3'])} <i>[{h.esc(v['t2'])}]</i> · {h.esc(v['ru'])}"


def full_card(v: dict) -> str:
    sent = "\n".join(f"{bold_v2(e, v)} — <i>{h.esc(r)}</i>" for e, r in zip(v["en"], v["sru"]))
    return (f"📘 {h.esc(v['v1'])} – <b>{h.esc(v['v2'])}</b> – {h.esc(v['v3'])}\n"
            f"<i>[{h.esc(v['t1'])} – {h.esc(v['t2'])} – {h.esc(v['t3'])}]</i> · {h.esc(v['ru'])}\n\n{sent}")


def speech_of(v: dict) -> str:
    return f"{v['v1']}, {v['v2']}, {v['v3']}. " + " ".join(v["en"])


# ============================ меню и группы ============================
def menu_text() -> str:
    return ("📘 <b>Неправильные глаголы</b> · 100 штук в 10 группах\n\n"
            "<b>V1</b> — начальная форма (go), <b>V2</b> — Past Simple, прошедшее время (went), "
            "<b>V3</b> — причастие для have/был (gone).\n\n"
            "Здесь упор на <b>V2</b>: у каждого глагола есть предложение с переводом, а тренировка идёт "
            "в обе стороны (EN→RU и RU→EN), с выбором формы и вставкой пропущенного слова.\n\n"
            "Выбери группу или сразу тренировку 👇")


def menu_kb():
    rows = [[(f"{G[i]} · {group_size(i)}", f"vg:{i}")] for i in range(len(G))]
    rows += [[("🧩 Русский → 3 формы (V1–V2–V3)", "vf:all")], [("🎯 Тренировка вперемешку", "vt:all")],
             [("📊 Мой прогресс по глаголам", "vp")]]
    return h.kb(*rows)


@router.message(Command("verbs"))
@router.message(F.text == h.MENU["verbs"])
async def cmd_verbs(m: Message):
    h.get_user(m.from_user)
    h.S.pop(m.from_user.id, None)
    await m.answer(menu_text(), reply_markup=menu_kb())


@router.callback_query(F.data == "vm")
async def on_menu(cb: CallbackQuery):
    h.S.pop(cb.from_user.id, None)
    await h.safe_edit(cb, menu_text(), menu_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("vg:"))
async def on_group(cb: CallbackQuery):
    g = int(cb.data[3:])
    vs = [v for v in V if v["g"] == g]
    text = (f"<b>{h.esc(G[g])}</b> · {len(vs)} глаголов\n"
            "<i>V1 – <b>V2</b> – V3 [транскрипция V2] · перевод</i>\n\n" + "\n".join(verb_line(v) for v in vs))
    await h.safe_edit(cb, text, h.kb([("📖 Предложения с V2 (EN ↔ RU)", f"vs:{g}:0")],
                                     [("🧩 Русский → 3 формы", f"vf:{g}")],
                                     [("🧠 Тренировка вперемешку", f"vt:{g}")],
                                     [("🔊 Озвучить формы", f"vsay:{g}")],
                                     [("⬅️ Все группы", "vm")]))
    await cb.answer()


@router.callback_query(F.data.startswith("vsay:"))
async def on_group_say(cb: CallbackQuery, bot: Bot):
    g = int(cb.data[5:])
    await cb.answer()
    text = ". ".join(f"{v['v1']}, {v['v2']}, {v['v3']}" for v in V if v["g"] == g)
    await h.speak(bot, cb.from_user.id, text)


# ============================ предложения с V2 ============================
@router.callback_query(F.data.startswith("vs:"))
async def on_sentences(cb: CallbackQuery):
    _, g, page = cb.data.split(":")
    g, page = int(g), int(page)
    vs = [v for v in V if v["g"] == g]
    pages = (len(vs) + PAGE - 1) // PAGE
    page = max(0, min(page, pages - 1))
    chunk = vs[page * PAGE:(page + 1) * PAGE]
    lines = []
    for i, v in enumerate(chunk, page * PAGE + 1):
        lines.append(f"<b>{i}.</b> {h.esc(v['v1'])} → <b>{h.esc(v['v2'])}</b>")
        for e, r in zip(v["en"], v["sru"]):
            lines.append(f"   {bold_v2(e, v)}\n   <i>{h.esc(r)}</i>")
        lines.append("")
    h.SPEECH[cb.from_user.id] = " ".join(e for v in chunk for e in v["en"])
    nav = []
    if page > 0:
        nav.append(("⬅️", f"vs:{g}:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(("➡️", f"vs:{g}:{page + 1}"))
    text = f"📖 <b>{h.esc(G[g])}</b>\n<i>Предложения в Past Simple (V2 выделена)</i>\n\n" + "\n".join(lines)
    await h.safe_edit(cb, text, h.kb(nav, [("🔊 Озвучить страницу", "say")], [("🧠 Тренировка группы", f"vt:{g}")],
                                      [("⬅️ К группе", f"vg:{g}")]))
    await cb.answer()


# ============================ тренировка ============================
def pick_verb(u: dict, group: int | None) -> dict:
    uid, today = u["uid"], h.today_of(u).isoformat()
    cands = [v for v in V if group is None or v["g"] == group]
    recs = {v["v1"]: h.store.verbs.get((uid, v["v1"].lower())) for v in cands}
    recent = RECENT_V[uid]
    due = [v for v in cands if recs[v["v1"]] and recs[v["v1"]]["next_review"] <= today and v["v1"] not in recent]
    new = [v for v in cands if not recs[v["v1"]] and v["v1"] not in recent]
    if due and (not new or random.random() < 0.7):
        due.sort(key=lambda v: recs[v["v1"]]["box"])
        return random.choice(due[:5])
    if new:
        return random.choice(new)
    rest = [v for v in cands if v["v1"] not in recent] or cands
    return random.choice(rest)


def _other_sentences(v: dict, n: int, key_en: bool):
    others = [x for x in V if x is not v]
    same = [x for x in others if x["g"] == v["g"]]
    random.shuffle(same)
    random.shuffle(others)
    out = []
    for x in same + others:
        s = x["en"][0] if key_en else x["sru"][0]
        if s not in out and s not in (v["en"] if key_en else v["sru"]):
            out.append(s)
        if len(out) == n:
            break
    return out


async def ask_verb(bot: Bot, u: dict):
    uid = u["uid"]
    group = VGROUP.get(uid)
    v = pick_verb(u, group)
    RECENT_V[uid].append(v["v1"])
    h.SPEECH[uid] = speech_of(v)
    si = random.randrange(len(v["en"]))
    en, ru = v["en"][si], v["sru"][si]
    qtype = "vforms" if VMODE.get(uid) == "forms" else \
        random.choices(["ven", "vru", "vform", "vgap", "vforms"], weights=[25, 25, 15, 15, 20])[0]
    head = f"📘 <i>{h.esc(G[group]) if group is not None else 'Все глаголы'}</i>\n\n"

    if qtype == "vforms":                                  # русский глагол → назвать V1, V2, V3 по-английски
        h.set_session(uid, type="vforms", v1=v["v1"])
        await bot.send_message(
            uid, f"{head}🧩 Назови <b>все три формы</b> этого глагола по-английски (V1 – V2 – V3):\n\n"
                 f"🇷🇺 <b>{h.esc(v['ru'])}</b>\n\n<i>Напиши через пробел или дефис, например: go went gone</i>",
            reply_markup=h.kb([("💡 Подсказка", "vh"), ("🤷 Не знаю", "vkk")], [("🚪 Выйти", "exit")]))
        return

    if qtype == "vgap":
        mt = v2_regex(v).search(en)
        if not mt:
            qtype = "vform"
        else:
            gap = en[:mt.start()] + "_____" + en[mt.end():]
            h.set_session(uid, type="vgap", v1=v["v1"], answers=[mt.group(0)] + v2_forms(v))
            await bot.send_message(
                uid, f"{head}✍️ Вставь форму <b>Past Simple (V2)</b> глагола <b>{h.esc(v['v1'])}</b> "
                     f"({h.esc(v['ru'])}):\n\n<b>{h.esc(gap)}</b>\n\n🇷🇺 <i>{h.esc(ru)}</i>",
                reply_markup=h.kb([("🤷 Не знаю", "vk"), ("🚪 Выйти", "exit")]))
            return

    if qtype == "ven":
        correct, opts = ru, _other_sentences(v, 3, False)
        text = f"{head}🇺🇸➡️🇷🇺 Выбери верный перевод:\n\n{bold_v2(en, v)}"
    elif qtype == "vru":
        correct, opts = en, _other_sentences(v, 3, True)
        text = f"{head}🇷🇺➡️🇺🇸 Выбери верный перевод:\n\n<b>{h.esc(ru)}</b>"
    else:                                                  # vform: выбрать V2
        correct = v["v2"]
        pool = {v["v1"], v["v3"]} - {correct}              # ловушки: другие формы этого же глагола
        pool |= {x["v2"] for x in random.sample(V, 8)} - {correct}
        trap = [f for f in (v["v3"], v["v1"]) if f in pool]
        rest = [f for f in pool if f not in trap]
        random.shuffle(rest)
        opts = (trap + rest)[:3]
        text = (f"{head}🧩 Какая форма — <b>Past Simple (V2)</b> глагола <b>{h.esc(v['v1'])}</b> "
                f"({h.esc(v['ru'])})?")
    options = opts + [correct]
    random.shuffle(options)
    h.set_session(uid, type="vq", v1=v["v1"], opts=options, ans=options.index(correct), si=si)
    rows = [[(o if len(o) <= 60 else o[:57] + "…", f"va:{i}")] for i, o in enumerate(options)]
    rows.append([("🔊", "say"), ("🚪 Выйти", "exit")])
    await bot.send_message(uid, text, reply_markup=h.kb(*rows))


def _finish(u: dict, v1: str, ok: bool):
    """Учёт ответа по глаголу: интервальное повторение + мотивация. Возвращает (карточка, строки мотивации, XP)."""
    v = next(x for x in V if x["v1"] == v1)
    today = h.today_of(u)
    rec = h.store.verb_rec(u["uid"], v1, today)
    srs_review(rec, ok, today)
    h.store.mark("verbs")
    lines, gain = h.register_answer(u, ok, 12, "verb")
    h.SPEECH[u["uid"]] = speech_of(v)
    return v, lines, gain


def _feedback(v: dict, ok: bool, gain: int, lines: list, extra: str = "") -> str:
    head = f"✅ <b>{mot.praise(True)}</b> +{gain} XP" if ok else f"❌ <b>{mot.praise(False)}</b>"
    text = f"{head}{extra}\n\n{full_card(v)}"
    if lines:
        text += "\n\n" + "\n".join(lines)
    return text


NEXT_KB = lambda: h.kb([("▶️ Дальше", "vn"), ("🔊", "say")], [("📘 К группам", "vm")])


@router.callback_query(F.data.startswith("va:"))
async def on_v_answer(cb: CallbackQuery):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"vq"})
    if not s:
        await cb.answer("Этот вопрос уже неактуален — жми «Дальше»")
        return
    h.S.pop(u["uid"], None)
    ok = int(cb.data[3:]) == s["ans"]
    v, lines, gain = _finish(u, s["v1"], ok)
    extra = "" if ok else f"\nПравильный ответ: <b>{h.esc(s['opts'][s['ans']])}</b>"
    await h.safe_edit(cb, _feedback(v, ok, gain, lines, extra), NEXT_KB())
    await cb.answer()


async def check_gap(m: Message, u: dict, s: dict, text: str):
    h.S.pop(u["uid"], None)
    verdict = content.check_answer(text, s["answers"])
    ok = verdict != "wrong"
    v, lines, gain = _finish(u, s["v1"], ok)
    extra = "" if verdict == "exact" else (f"\nПочти! Правильно: <b>{h.esc(s['answers'][0])}</b>" if ok
                                           else f"\nПравильно: <b>{h.esc(s['answers'][0])}</b>")
    await m.answer(_feedback(v, ok, gain, lines, extra), reply_markup=NEXT_KB())


h.TEXT_HANDLERS["vgap"] = check_gap


@router.callback_query(F.data == "vk")
async def on_gap_skip(cb: CallbackQuery):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"vgap"})
    if not s:
        await cb.answer("Это упражнение уже неактуально")
        return
    h.S.pop(u["uid"], None)
    v, lines, gain = _finish(u, s["v1"], False)
    await h.safe_edit(cb, _feedback(v, False, gain, lines, f"\nПравильно: <b>{h.esc(s['answers'][0])}</b>"), NEXT_KB())
    await cb.answer()


def _eq(a: str, b: str) -> bool:
    return a == b or (len(b) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.88)


def _score_forms(c: dict, toks: list[str]):
    """Сколько форм угадано у кандидата c: (V1, V2, V3). Для be допускаем «be was were been»."""
    if len(toks) == 4 and toks[2] in ("was", "were"):
        toks = [toks[0], toks[1], toks[3]]
    t1, t2, t3 = toks[0], toks[1], toks[-1]
    return (_eq(t1, c["v1"].lower()), any(_eq(t2, f.lower()) for f in v2_forms(c)), _eq(t3, c["v3"].lower()))


async def check_forms(m: Message, u: dict, s: dict, text: str):
    toks = re.findall(r"[a-z']+", text.lower())
    if len(toks) < 3:
        await m.answer("Нужно написать <b>три формы</b> через пробел или дефис, например: <code>go went gone</code>. "
                       "Если не знаешь — нажми «🤷 Не знаю».")
        return
    v = next(x for x in V if x["v1"] == s["v1"])
    cands = [x for x in V if x["ru"] == v["ru"]]          # «делать» — это и make, и do: принимаем любой подходящий
    best = max(cands, key=lambda c: sum(_score_forms(c, toks)))
    res = _score_forms(best, toks)
    ok = all(res)
    h.S.pop(u["uid"], None)
    shown = best if ok else v
    mark = lambda r: "✅" if r else "❌"
    extra = (f"\nV1 {mark(res[0])}  V2 {mark(res[1])}  V3 {mark(res[2])}" +
             (f"\n<i>Тоже верно: {h.esc(best['v1'])} – {h.esc(best['v2'])} – {h.esc(best['v3'])}</i>" if ok and best is not v else "") +
             ("" if ok else f"\nПравильно: <b>{h.esc(v['v1'])} – {h.esc(v['v2'])} – {h.esc(v['v3'])}</b>"))
    v_done, lines, gain = _finish(u, s["v1"], ok)
    await m.answer(_feedback(v_done, ok, gain, lines, extra), reply_markup=NEXT_KB())


h.TEXT_HANDLERS["vforms"] = check_forms


@router.callback_query(F.data == "vh")
async def on_forms_hint(cb: CallbackQuery):
    s = h.get_session(cb.from_user.id, {"vforms"})
    if not s:
        await cb.answer("Это упражнение уже неактуально")
        return
    v = next(x for x in V if x["v1"] == s["v1"])
    await cb.answer(f"Подсказка: {v['v1'][0]}…  {v2_forms(v)[0][0]}…  {v['v3'][0]}…", show_alert=True)


@router.callback_query(F.data == "vkk")
async def on_forms_skip(cb: CallbackQuery):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"vforms"})
    if not s:
        await cb.answer("Это упражнение уже неактуально")
        return
    h.S.pop(u["uid"], None)
    v, lines, gain = _finish(u, s["v1"], False)
    await h.safe_edit(cb, _feedback(v, False, gain, lines, f"\nПравильно: <b>{h.esc(v['v1'])} – {h.esc(v['v2'])} – {h.esc(v['v3'])}</b>"), NEXT_KB())
    await cb.answer()


@router.callback_query(F.data.startswith("vf:"))
async def on_forms_train(cb: CallbackQuery, bot: Bot):
    arg = cb.data[3:]
    VGROUP[cb.from_user.id] = None if arg == "all" else int(arg)
    VMODE[cb.from_user.id] = "forms"
    await cb.answer()
    await ask_verb(bot, h.get_user(cb.from_user))


@router.callback_query(F.data.startswith("vt:"))
async def on_train(cb: CallbackQuery, bot: Bot):
    arg = cb.data[3:]
    VMODE[cb.from_user.id] = None
    VGROUP[cb.from_user.id] = None if arg == "all" else int(arg)
    await cb.answer()
    await ask_verb(bot, h.get_user(cb.from_user))


@router.callback_query(F.data == "vn")
async def on_train_next(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await ask_verb(bot, h.get_user(cb.from_user))


# ============================ прогресс ============================
@router.callback_query(F.data == "vp")
async def on_progress(cb: CallbackQuery):
    u = h.get_user(cb.from_user)
    recs = {v["v1"]: h.store.verbs.get((u["uid"], v["v1"].lower())) for v in V}
    lines, total_learned = [], 0
    for g in range(len(G)):
        vs = [v for v in V if v["g"] == g]
        learned = sum(1 for v in vs if recs[v["v1"]] and recs[v["v1"]]["box"] >= LEARNED_BOX)
        started = sum(1 for v in vs if recs[v["v1"]])
        total_learned += learned
        lines.append(f"{h.esc(G[g])}\n   {mot.bar(learned, len(vs))} выучено {learned}/{len(vs)} · начато {started}")
    text = (f"📊 <b>Прогресс по глаголам</b>: выучено <b>{total_learned}</b> из {len(V)}\n"
            "<i>«Выучено» — когда глагол верно отвечен несколько раз с интервалами.</i>\n\n" + "\n\n".join(lines))
    await h.safe_edit(cb, text, h.kb([("🎯 Тренировка", "vt:all")], [("⬅️ Все группы", "vm")]))
    await cb.answer()
