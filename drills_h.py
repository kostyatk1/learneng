"""Игры и упражнения: диктант, напиши слово, составь предложение, найди ошибку, соедини пары, квиз по теме."""
import random
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import content
import extras_h
import handlers as h
from storage import srs_review

router = Router()           # подключается в main.py раньше основного роутера
RECENT_G: dict[int, list] = {}


def _recent(uid: int, key: str, n: int = 8) -> list:
    lst = RECENT_G.setdefault(uid, [])
    lst.append(key)
    del lst[:-n]
    return lst


# ============================ меню ============================
def menu_kb():
    return h.kb([("🎧 Диктант (напиши, что слышишь)", "gm:dict")],
                [("🔤 Напиши слово по-английски", "gm:spell")],
                [("🧩 Составь предложение", "gm:order")],
                [("🔍 Найди ошибку", "gm:err")],
                [("🔗 Соедини пары", "gm:match")],
                [("🎯 Квиз по теме", "gm:topic")])


MENU_TEXT = ("🎮 <b>Упражнения и игры</b>\n\n"
             "🎧 <b>Диктант</b> — слушаешь и пишешь слово или фразу\n"
             "🔤 <b>Напиши слово</b> — видишь перевод, пишешь по-английски\n"
             "🧩 <b>Составь предложение</b> — собираешь фразу из перемешанных слов\n"
             "🔍 <b>Найди ошибку</b> — верное предложение или нет?\n"
             "🔗 <b>Соедини пары</b> — английские слова и их переводы\n"
             "🎯 <b>Квиз по теме</b> — слова из выбранной темы\n\nВыбирай 👇")


@router.message(Command("games"))
@router.message(F.text == h.MENU["games"])
async def cmd_games(m: Message):
    h.get_user(m.from_user)
    h.S.pop(m.from_user.id, None)
    await m.answer(MENU_TEXT, reply_markup=menu_kb())


@router.callback_query(F.data == "gmenu")
async def on_gmenu(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer(MENU_TEXT, reply_markup=menu_kb())


@router.callback_query(F.data.startswith("gm:"))
async def on_gm(cb: CallbackQuery, bot: Bot):
    kind = cb.data[3:]
    u = h.get_user(cb.from_user)
    await cb.answer()
    if kind in ("dict", "spell"):
        await ask_typed(bot, u, kind)
    elif kind == "order":
        await ask_order(bot, u)
    elif kind == "err":
        await ask_err(bot, u)
    elif kind == "match":
        await ask_match(bot, u)
    elif kind == "topic":
        rows = [[(t, f"tqs:{i}")] for i, t in enumerate(content.TOPICS)]
        await cb.message.answer("🎯 Выбери тему для квиза:", reply_markup=h.kb(*rows))


# ============================ общий разбор слова ============================
def pool_words(u: dict, n_min: int = 1):
    mine = [w for w in h.store.words_of(u["uid"]) if w["kind"] != "slang"]
    return mine if len(mine) >= n_min else content.ALL_WORDS


# ============================ 🎧 диктант и 🔤 напиши слово ============================
async def ask_typed(bot: Bot, u: dict, mode: str):
    uid = u["uid"]
    pool = pool_words(u, 3)
    recent = RECENT_G.get(uid, [])
    cands = [w for w in pool if w["en"] not in recent] or pool
    w = random.choice(cands)
    _recent(uid, w["en"])
    en = w["en"]
    answers = [en] + ([en[3:]] if en.startswith("to ") else [])
    hint = f"Первая буква: {en[0]}…  (букв: {len(en.replace(' ', ''))})"
    h.set_session(uid, type="typed", mode=mode, answers=answers, word=en.lower(), hint=hint)
    h.SPEECH[uid] = en
    if mode == "dict":
        await h.speak(bot, uid, en)
        text = "🎧 <b>Диктант</b>: послушай и напиши по-английски, что услышала 👇"
        rows = [[("🔁 Послушать ещё раз", "say"), ("💡 Подсказка", "xh")], [("🤷 Не знаю", "xk"), ("🚪 Выйти", "exit")]]
    else:
        text = f"🔤 <b>Напиши по-английски:</b>\n\n🇷🇺 <b>{h.esc(w['ru'])}</b>"
        rows = [[("💡 Подсказка", "xh"), ("🤷 Не знаю", "xk")], [("🚪 Выйти", "exit")]]
    await bot.send_message(uid, text, reply_markup=h.kb(*rows))


def _finish_typed(u: dict, s: dict, ok: bool, base: int):
    w = h.store.words.get((u["uid"], s.get("word", "")))
    if w:
        srs_review(w, ok, h.today_of(u))
        h.store.mark("words")
    lines, gain = h.register_answer(u, ok, base, "word")
    return w, lines, gain


NEXT = {"dict": "gm:dict", "spell": "gm:spell", "order": "gm:order"}


async def check_typed(m: Message, u: dict, s: dict, text: str):
    h.S.pop(u["uid"], None)
    verdict = content.check_answer(text, s["answers"])
    ok = verdict != "wrong"
    w, lines, gain = _finish_typed(u, s, ok, 12 if verdict == "exact" else 9)
    shown = s["answers"][0]
    h.SPEECH[u["uid"]] = shown
    head = (f"✅ <b>{h.mot.praise(True)}</b> +{gain} XP" if verdict == "exact"
            else f"✅ <b>Почти!</b> Правильное написание: <b>{h.esc(shown)}</b> +{gain} XP" if ok
            else f"❌ <b>{h.mot.praise(False)}</b> Правильно: <b>{h.esc(shown)}</b>")
    body = head + (("\n\n" + h.card_text(w)) if w else "")
    if lines:
        body += "\n\n" + "\n".join(lines)
    await m.answer(body, reply_markup=h.kb([("▶️ Дальше", NEXT[s["mode"]]), ("🔊", "say")], [("🎮 Упражнения", "gmenu")]))


h.TEXT_HANDLERS["typed"] = check_typed


@router.callback_query(F.data == "xh")
async def on_hint(cb: CallbackQuery):
    s = h.get_session(cb.from_user.id, {"typed", "order"})
    if not s:
        await cb.answer("Это задание уже неактуально")
        return
    await cb.answer(s["hint"], show_alert=True)


@router.callback_query(F.data == "xk")
async def on_skip(cb: CallbackQuery):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"typed", "order"})
    if not s:
        await cb.answer("Это задание уже неактуально")
        return
    h.S.pop(u["uid"], None)
    if s["type"] == "typed":
        _finish_typed(u, s, False, 9)
    else:
        h.register_answer(u, False, 12, "phrase")
    shown = s["answers"][0]
    h.SPEECH[u["uid"]] = shown
    await h.safe_edit(cb, f"Ничего страшного! Правильно:\n\n<b>{h.esc(shown)}</b>",
                      h.kb([("▶️ Дальше", NEXT[s["mode"]]), ("🔊", "say")], [("🎮 Упражнения", "gmenu")]))
    await cb.answer()


# ============================ 🧩 составь предложение ============================
def order_sources():
    out = [(p["en"], p["ru"]) for p in content.PHRASES if 3 <= len(p["en"][0].split()) <= 9]
    for d in extras_h.DIALOGS:
        for st in d["steps"]:
            if st["you"] and 3 <= len(st["you"]["ok"][0].split()) <= 9:
                out.append((st["you"]["ok"], st["you"]["ru"]))
    return out


async def ask_order(bot: Bot, u: dict):
    uid = u["uid"]
    src = order_sources()
    recent = RECENT_G.get(uid, [])
    cands = [x for x in src if x[0][0] not in recent] or src
    variants, ru = random.choice(cands)
    _recent(uid, variants[0], 12)
    words = re.findall(r"[A-Za-z0-9$%'’-]+", variants[0])
    shuffled = words[:]
    for _ in range(5):
        random.shuffle(shuffled)
        if shuffled != words:
            break
    chips = "  ".join(f"<code>{h.esc(w.lower() if i else w)}</code>" for i, w in enumerate(shuffled))
    h.set_session(uid, type="order", mode="order", answers=variants, word="",
                  hint=f"Начни со слова: {words[0]}…  (слов: {len(words)})")
    h.SPEECH[uid] = variants[0]
    await bot.send_message(uid, f"🧩 <b>Составь предложение</b> из слов:\n\n{chips}\n\n🇷🇺 <i>{h.esc(ru)}</i>\n\n"
                                "<i>Напиши предложение целиком сообщением.</i>",
                           reply_markup=h.kb([("💡 Подсказка", "xh"), ("🤷 Не знаю", "xk")], [("🚪 Выйти", "exit")]))


async def check_order(m: Message, u: dict, s: dict, text: str):
    h.S.pop(u["uid"], None)
    verdict = content.check_answer(text, s["answers"])
    ok = verdict != "wrong"
    lines, gain = h.register_answer(u, ok, 14 if verdict == "exact" else 10, "phrase")
    h.SPEECH[u["uid"]] = s["answers"][0]
    head = (f"✅ <b>{h.mot.praise(True)}</b> +{gain} XP" if verdict == "exact"
            else f"✅ <b>Почти!</b> Правильно: <b>{h.esc(s['answers'][0])}</b> +{gain} XP" if ok
            else f"❌ <b>{h.mot.praise(False)}</b> Правильно: <b>{h.esc(s['answers'][0])}</b>")
    others = [v for v in s["answers"][1:] if v != s["answers"][0]]
    body = head + (("\n\nЕщё варианты:\n" + "\n".join(f"• {h.esc(v)}" for v in others[:2])) if others and not ok else "")
    if lines:
        body += "\n\n" + "\n".join(lines)
    await m.answer(body, reply_markup=h.kb([("▶️ Дальше", "gm:order"), ("🔊", "say")], [("🎮 Упражнения", "gmenu")]))


h.TEXT_HANDLERS["order"] = check_order


# ============================ 🔍 найди ошибку ============================
async def ask_err(bot: Bot, u: dict):
    uid = u["uid"]
    qs = [(g["title"], q) for g in extras_h.GRAMMAR for q in g["qs"]]
    recent = RECENT_G.get(uid, [])
    cands = [x for x in qs if x[1]["q"] not in recent] or qs
    title, q = random.choice(cands)
    _recent(uid, q["q"], 20)
    correct = random.random() < 0.5
    word = q["ok"] if correct else random.choice(q["bad"])
    sentence = q["q"].replace("___", word)
    h.set_session(uid, type="err", correct=correct, q=q, sentence=sentence)
    await bot.send_message(uid, f"🔍 <b>Верное предложение или с ошибкой?</b>\n\n<b>{h.esc(sentence)}</b>",
                           reply_markup=h.kb([("✅ Верно", "eg:1"), ("❌ Ошибка", "eg:0")], [("🚪 Выйти", "exit")]))


@router.callback_query(F.data.startswith("eg:"))
async def on_err_answer(cb: CallbackQuery):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"err"})
    if not s:
        await cb.answer("Это задание уже неактуально")
        return
    h.S.pop(u["uid"], None)
    ok = (cb.data[3:] == "1") == s["correct"]
    lines, gain = h.register_answer(u, ok, 8, "phrase")
    q = s["q"]
    fixed = q["q"].replace("___", q["ok"])
    head = f"✅ <b>{h.mot.praise(True)}</b> +{gain} XP" if ok else f"❌ <b>{h.mot.praise(False)}</b>"
    verdict = "Предложение было <b>верным</b>." if s["correct"] else f"В предложении была ошибка. Правильно: <b>{h.esc(fixed)}</b>"
    body = f"{head}\n\n{verdict}\n💡 <i>{h.esc(q['why'])}</i>" + ("\n\n" + "\n".join(lines) if lines else "")
    h.SPEECH[u["uid"]] = fixed
    await h.safe_edit(cb, body, h.kb([("▶️ Дальше", "gm:err"), ("🔊", "say")], [("🎮 Упражнения", "gmenu")]))
    await cb.answer()


# ============================ 🔗 соедини пары ============================
async def _edit_kb(cb: CallbackQuery, s: dict):
    try:
        await cb.message.edit_reply_markup(reply_markup=match_kb(s))
    except TelegramBadRequest:
        pass



def match_kb(s: dict):
    left = []
    for i, w in enumerate(s["words"]):
        if i in s["done"]:
            left.append((f"✅ {w['en']}", "noop"))
        else:
            left.append((("👉 " if s["sel"] == i else "") + w["en"], f"mp:L:{i}"))
    right = []
    for j in s["order"]:
        if j in s["done"]:
            right.append((f"✅ {s['words'][j]['ru'][:28]}", "noop"))
        else:
            right.append((s["words"][j]["ru"][:28], f"mp:R:{j}"))
    rows = [[left[k], right[k]] for k in range(len(left))]
    return h.kb(*rows, [("🚪 Выйти", "exit")])


async def ask_match(bot: Bot, u: dict):
    uid = u["uid"]
    pool = pool_words(u, 5)
    words = random.sample(pool, 5)
    for _ in range(20):                                    # переводы в паре должны отличаться друг от друга
        if len({w['ru'] for w in words}) == 5:
            break
        words = random.sample(pool, 5)
    order = list(range(5))
    random.shuffle(order)
    h.set_session(uid, type="match", words=words, order=order, done=set(), sel=None, errors=0)
    await bot.send_message(uid, "🔗 <b>Соедини пары</b>: нажми английское слово слева, потом его перевод справа.",
                           reply_markup=match_kb(h.S[uid]))


@router.callback_query(F.data.startswith("mp:"))
async def on_match(cb: CallbackQuery):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"match"})
    if not s:
        await cb.answer("Это задание уже неактуально")
        return
    _, side, idx = cb.data.split(":")
    idx = int(idx)
    if side == "L":
        s["sel"] = idx
        await _edit_kb(cb, s)
        await cb.answer()
        return
    if s["sel"] is None:
        await cb.answer("Сначала выбери слово слева 👈")
        return
    if idx == s["sel"]:
        s["done"].add(idx)
        s["sel"] = None
        h.SPEECH[u["uid"]] = s["words"][idx]["en"]
        if len(s["done"]) == len(s["words"]):
            h.S.pop(u["uid"], None)
            ok = s["errors"] <= 1
            lines, gain = h.register_answer(u, ok, 15, "word")
            res = ("🏆 <b>Без ошибок!</b>" if s["errors"] == 0 else f"Ошибок: {s['errors']}") + f" +{gain} XP"
            allw = "\n".join("• " + h.card_text(w).split("\n")[0] for w in s["words"])
            await h.safe_edit(cb, f"🔗 <b>Все пары собраны!</b> {res}\n\n{allw}" + ("\n\n" + "\n".join(lines) if lines else ""),
                              h.kb([("▶️ Ещё раз", "gm:match"), ("🔊", "say")], [("🎮 Упражнения", "gmenu")]))
        else:
            await _edit_kb(cb, s)
        await cb.answer("✅ Верно!")
    else:
        s["errors"] += 1
        s["sel"] = None
        await _edit_kb(cb, s)
        await cb.answer("❌ Не совпало, попробуй ещё", show_alert=False)


# ============================ 🎯 квиз по теме ============================
async def ask_tq(bot: Bot, u: dict, ti: int):
    uid = u["uid"]
    bank = content.BANK[content.TOPICS[ti]]
    recent = RECENT_G.get(uid, [])
    cands = [w for w in bank if w["en"] not in recent] or bank
    w = random.choice(cands)
    _recent(uid, w["en"], max(3, min(8, len(bank) - 1)))
    rev = random.random() < 0.4
    correct = w["en"] if rev else w["ru"]
    pool = {(x["en"] if rev else x["ru"]) for x in bank + content.ALL_WORDS} - {correct}
    same = [(x["en"] if rev else x["ru"]) for x in bank if x is not w and (x["en"] if rev else x["ru"]) != correct]
    wrong = random.sample(same, min(3, len(same)))
    wrong += random.sample(sorted(pool - set(wrong)), 3 - len(wrong))
    opts = wrong + [correct]
    random.shuffle(opts)
    h.set_session(uid, type="tq", ti=ti, en=w["en"], opts=opts, ans=opts.index(correct))
    h.SPEECH[uid] = w["en"]
    prompt = (f"🇷🇺 <b>{h.esc(w['ru'])}</b>\nКак это по-английски?" if rev else f"🇺🇸 <b>{h.esc(w['en'])}</b>\nВыбери перевод")
    rows = [[(o, f"tq:{i}")] for i, o in enumerate(opts)] + [[("🔊", "say"), ("🚪 Выйти", "exit")]]
    await bot.send_message(uid, f"🎯 {h.esc(content.TOPICS[ti])}\n\n{prompt} 👇", reply_markup=h.kb(*rows))


@router.callback_query(F.data.startswith("tqs:"))
async def on_tq_start(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await ask_tq(bot, h.get_user(cb.from_user), int(cb.data[4:]))


@router.callback_query(F.data.startswith("tqn:"))
async def on_tq_next(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await ask_tq(bot, h.get_user(cb.from_user), int(cb.data[4:]))


@router.callback_query(F.data.startswith("tq:"))
async def on_tq_answer(cb: CallbackQuery):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"tq"})
    if not s:
        await cb.answer("Этот вопрос уже неактуален")
        return
    h.S.pop(u["uid"], None)
    ok = int(cb.data[3:]) == s["ans"]
    bank_w = next(w for w in content.BANK[content.TOPICS[s["ti"]]] if w["en"] == s["en"])
    h.store.add_word(u["uid"], bank_w["en"], bank_w["ru"], bank_w["ex"], bank_w["topic"], "topic", h.today_of(u))
    w = h.store.words.get((u["uid"], bank_w["en"].lower()))
    if w:
        srs_review(w, ok, h.today_of(u))
        h.store.mark("words")
    lines, gain = h.register_answer(u, ok, 10, "word")
    head = (f"✅ <b>{h.mot.praise(True)}</b> +{gain} XP" if ok
            else f"❌ <b>{h.mot.praise(False)}</b> Правильный ответ: <b>{h.esc(s['opts'][s['ans']])}</b>")
    body = f"{head}\n\n{h.card_text(bank_w)}" + ("\n\n" + "\n".join(lines) if lines else "")
    await h.safe_edit(cb, body, h.kb([("▶️ Дальше", f"tqn:{s['ti']}"), ("🔊", "say")], [("🎮 Упражнения", "gmenu")]))
    await cb.answer()
