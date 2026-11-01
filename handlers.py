"""Основная логика бота: слова, квиз, фразы, сленг, настройки, мотивация, рассылка слов дня."""
import asyncio
import html
import io
import logging
import os
import random
import re
import time
from collections import defaultdict, deque
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup)

import content
import motivation as mot
import translator
from storage import DEFAULT_TZ, INTERVALS, LEARNED_BOX, Store, live_streak, srs_review

log = logging.getLogger("bot")
router = Router()

store: Store = None            # выставляется в main.py
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
BRAND = "Special FOR Ksen4ik"
SESSION_TTL = 600              # через 10 минут «ожидание ответа» сбрасывается

# ---------- временное состояние (в памяти; после перезапуска сервера упражнение просто «устаревает») ----------
S: dict[int, dict] = {}                                   # текущее упражнение
RECENT: dict[int, deque] = defaultdict(lambda: deque(maxlen=4))
RECENT_PH: dict[int, deque] = defaultdict(lambda: deque(maxlen=8))
SPEECH: dict[int, str] = {}                               # что озвучивает кнопка 🔊
PENDING: dict[int, dict] = {}                             # перевод, который можно добавить в словарь
COMBO: dict[int, int] = defaultdict(int)                  # ответов подряд без ошибок
LASTKIND: dict[int, str] = {}                             # какой квиз был последним ("" или "slang")
PH_CAT: dict[int, int | None] = {}                        # выбранная категория фраз
TEXT_HANDLERS: dict = {}                                  # тип упражнения -> функция проверки ответа текстом

MENU = {"quiz": "🧠 Квиз", "new": "🆕 Новые слова", "phrase": "✍️ Фразы", "verbs": "📘 Глаголы",
        "slang": "😎 Сленг", "words": "📚 Мои слова", "badges": "🏅 Достижения", "stats": "📊 Прогресс",
        "settings": "⚙️ Настройки"}

WELCOME = (
    f"💖 <b>{BRAND}</b> 💖\n\n"
    "Привет! 👋 Я помогу учить <b>американский английский</b> (уровень A2) маленькими порциями.\n\n"
    "<b>Что я умею</b>\n"
    "🆕 /new — новые слова с примерами и озвучкой (800+ слов, 20 тем)\n"
    "🧠 /quiz — повторение с умными интервалами\n"
    "✍️ /phrase — переведи фразу (160+ фраз по темам)\n"
    "📘 /verbs — неправильные глаголы: группы, предложения, тренировка\n"
    "😎 /slang — американский сленг\n"
    "📚 /words — твой словарь, ➕ /add — добавить своё слово\n"
    "💬 Просто напиши слово или фразу (по-русски или по-английски) — переведу и озвучу\n"
    "📊 /stats — прогресс · 🏅 /badges — значки и уровни\n"
    "⚙️ /settings — темы, цель дня, слово дня по утрам\n\n"
    "Начнём с выбора тем 👇"
)


# ============================ утилиты ============================
def esc(s) -> str:
    return html.escape(str(s))


def get_user(tg_user) -> dict:
    return store.ensure_user(tg_user.id, tg_user.first_name or "")


def tz_of(u: dict) -> ZoneInfo:
    try:
        return ZoneInfo(u["tz"])
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def now_of(u: dict) -> datetime:
    return datetime.now(tz_of(u))


def today_of(u: dict):
    return now_of(u).date()


def topics_of(u: dict) -> list[int]:
    return [int(x) for x in u["topics"].split(",") if x.strip().isdigit() and int(x) < len(content.TOPICS)]


def kb(*rows) -> InlineKeyboardMarkup:
    """kb([("текст", "callback")], [("текст2", "cb2"), ...])"""
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
                                                 for row in rows])


def main_kb() -> ReplyKeyboardMarkup:
    rows = [[MENU["quiz"], MENU["new"]], [MENU["phrase"], MENU["verbs"]], [MENU["slang"], MENU["words"]],
            [MENU["badges"], MENU["stats"]], [MENU["settings"]]]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True)


async def safe_edit(cb: CallbackQuery, text: str, markup=None):
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            log.warning("edit failed: %s", e)


async def send_chunks(bot: Bot, uid: int, header: str, lines: list[str], markup=None, limit: int = 3500):
    """Отправляет длинный список по частям (лимит Telegram — 4096 символов)."""
    parts, cur = [], header
    for ln in lines:
        if len(cur) + len(ln) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + ln
    parts.append(cur)
    for i, p in enumerate(parts):
        await bot.send_message(uid, p, reply_markup=markup if i == len(parts) - 1 else None)


# ---------- сессии упражнений ----------
def set_session(uid: int, **data):
    data["t"] = time.time()
    S[uid] = data


def get_session(uid: int, types=None):
    s = S.get(uid)
    if not s:
        return None
    if time.time() - s.get("t", 0) > SESSION_TTL:
        S.pop(uid, None)
        return None
    if types and s["type"] not in types:
        return None
    return s


# ---------- озвучка (американский акцент) ----------
def _tts(text: str) -> bytes:
    from gtts import gTTS
    last = None
    for tld in ("us", "com"):                              # сначала американский, потом запасной
        try:
            buf = io.BytesIO()
            gTTS(text=text, lang="en", tld=tld).write_to_fp(buf)
            return buf.getvalue()
        except Exception as e:
            last = e
    raise last


async def speak(bot: Bot, uid: int, text: str):
    try:
        data = await asyncio.to_thread(_tts, text)
        await bot.send_voice(uid, BufferedInputFile(data, "voice.mp3"))
    except Exception as e:
        log.exception("TTS failed")
        await bot.send_message(uid, f"Не получилось озвучить 😕 ({type(e).__name__}). Попробуй чуть позже.")


# ---------- перевод ----------
async def do_translate(text: str) -> dict:
    """Сначала ищем в своих базах (мгновенно), потом в онлайн-переводчиках с запасными вариантами."""
    hit = content.lookup(text)
    if hit:
        return hit
    if content.has_cyrillic(text):
        en, ru = await translator.translate_text(text, "ru", "en"), text
    else:
        en, ru = text, await translator.translate_text(text, "en", "ru")
    if len(en.split()) == 1:
        en = en.lower()
    return {"en": en, "ru": ru, "ex": "", "tr": "", "note": ""}


def card_text(w: dict) -> str:
    tr = f" <i>[{esc(w['tr'])}]</i>" if w.get("tr") else ""
    out = f"<b>{esc(w['en'])}</b>{tr} — {esc(w['ru'])}"
    if w.get("ex"):
        out += f"\n   <i>{esc(w['ex'])}</i>"
    if w.get("note"):
        out += f"\n   💬 {esc(w['note'])}"
    return out


def verb_card(v: dict) -> str:
    forms = f"{v['v1']} – {v['v2']} – {v['v3']}"
    tr = f"[{v['t1']} – {v['t2']} – {v['t3']}]"
    sent = "\n".join(f"<i>{esc(e)}</i> — {esc(r)}" for e, r in zip(v["en"], v["sru"]))
    return f"📘 <b>{esc(forms)}</b>\n<i>{esc(tr)}</i>\n🇷🇺 {esc(v['ru'])}\n\n{sent}"


# ============================ выдача слов ============================
def pick_new(u: dict, n: int, kind: str = "") -> list[dict]:
    have = {w["en"].lower() for w in store.words_of(u["uid"])}
    if kind == "slang":
        pool = [w for w in content.ALL_SLANG if w["en"].lower() not in have]
    else:
        idxs = topics_of(u) or range(len(content.TOPICS))
        pool = [w for i in idxs for w in content.BANK[content.TOPICS[i]] if w["en"].lower() not in have]
        if not pool:                                       # выбранные темы закончились — берём из остальных
            pool = [w for w in content.ALL_WORDS if w["en"].lower() not in have]
    random.shuffle(pool)
    return pool[:n]


async def deliver_words(bot: Bot, u: dict, n: int, header: str, note: str | None = None, kind: str = ""):
    uid = u["uid"]
    words = pick_new(u, n, kind)
    if not words:
        what = "сленга" if kind == "slang" else "слов"
        await bot.send_message(uid, f"🎉 Весь мой запас {what} уже в твоём словаре! "
                                    "Добавляй свои через /add или просто пиши мне любое слово.")
        return
    today = today_of(u)
    lines = []
    for w in words:
        store.add_word(uid, w["en"], w["ru"], w["ex"], w["topic"], "slang" if kind else "daily", today, kind)
        lines.append("• " + card_text(w))
    SPEECH[uid] = " ... ".join((w["ex"] and f"{w['en']}. {w['ex']}") or w["en"] for w in words)
    text = f"{header}\n\n" + "\n".join(lines)
    if note:
        text += f"\n\n💌 {esc(note)}"
    quiz_cb = "sl_quiz" if kind == "slang" else "goquiz"
    await bot.send_message(uid, text, reply_markup=kb([("🔊 Послушать", "say")], [("🧠 Закрепить квизом", quiz_cb)]))


# ============================ мотивация ============================
def build_ctx(u: dict) -> dict:
    uid = u["uid"]
    ws = store.words_of(uid)
    return {"words": sum(w["box"] >= LEARNED_BOX for w in ws if w["kind"] != "slang"),
            "slang": sum(w["box"] >= LEARNED_BOX for w in ws if w["kind"] == "slang"),
            "verbs": sum(v["box"] >= LEARNED_BOX for v in store.verbs_of(uid)), "total": len(ws)}


def register_answer(u: dict, ok: bool, base_xp: int, kind: str = "word"):
    """Учитывает ответ в статистике и мотивации. Возвращает (строки для сообщения, полученный XP)."""
    uid = u["uid"]
    COMBO[uid] = COMBO[uid] + 1 if ok else 0
    lines, gain = mot.record(u, build_ctx(u), ok, base_xp, today_of(u), COMBO[uid], kind)
    store.mark("users")
    return lines, gain


# ============================ квиз ============================
async def ask_question(bot: Bot, u: dict, kind: str | None = None):
    uid = u["uid"]
    kind = LASTKIND.get(uid, "") if kind is None else kind
    LASTKIND[uid] = kind
    today = today_of(u).isoformat()
    allw = store.words_of(uid)
    ws = [w for w in allw if w["kind"] == "slang"] if kind == "slang" else allw
    if not ws:
        msg = "Пока нет слов сленга в словаре. Открой «😎 Сленг» и добавь слова." if kind == "slang" \
            else "Словарь пока пуст. Жми «🆕 Новые слова» 🙂"
        await bot.send_message(uid, msg)
        return
    due = [w for w in ws if w["next_review"] <= today]
    if not due:
        nxt = min(w["next_review"] for w in ws)
        await bot.send_message(uid, f"🎉 На сегодня всё повторено! Следующее повторение: <b>{nxt}</b>.\n"
                                    "Хочешь ещё? Жми «🆕 Новые слова», «✍️ Фразы» или «📘 Глаголы».")
        return
    pool = [w for w in due if w["en"].lower() not in RECENT[uid]] or due
    low = min(w["box"] for w in pool)
    w = random.choice([x for x in pool if x["box"] <= low + 1])

    rev = random.random() < min(0.25 + 0.1 * w["box"], 0.8)          # чем лучше слово знаешь — тем чаще RU→EN
    correct = w["en"] if rev else w["ru"]
    prompt = w["ru"] if rev else w["en"]
    cands = {(x["en"] if rev else x["ru"]) for x in ws + content.ALL_WORDS + content.ALL_SLANG} - {correct}
    opts = random.sample(sorted(cands), 3) + [correct]
    random.shuffle(opts)

    set_session(uid, type="quiz", en=w["en"].lower(), opts=opts, ans=opts.index(correct))
    RECENT[uid].append(w["en"].lower())
    SPEECH[uid] = w["en"]
    tr = f" <i>[{esc(w['tr'])}]</i>" if (w.get("tr") and not rev) else ""
    head = (f"🇷🇺 <b>{esc(prompt)}</b>\nКак это по-английски? 👇" if rev
            else f"🇬🇧 <b>{esc(prompt)}</b>{tr}\nВыбери перевод 👇")
    rows = [[(o, f"a:{i}")] for i, o in enumerate(opts)] + [[("🔊", "say")]]
    await bot.send_message(uid, head, reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("a:"))
async def on_answer(cb: CallbackQuery):
    u = get_user(cb.from_user)
    uid = u["uid"]
    s = get_session(uid, {"quiz"})
    if not s:
        await cb.answer("Этот вопрос уже неактуален — жми «Квиз»")
        return
    S.pop(uid)
    ok = int(cb.data[2:]) == s["ans"]
    today = today_of(u)
    w = store.words.get((uid, s["en"]))
    extra = ""
    if w:
        srs_review(w, ok, today)
        store.mark("words")
        if ok and INTERVALS[w["box"]] > 0:
            extra = f"\n⏭ Повторим через {INTERVALS[w['box']]} дн."
    lines, gain = register_answer(u, ok, 10, "slang" if (w and w["kind"] == "slang") else "word")
    head = (f"✅ <b>{mot.praise(True)}</b> +{gain} XP" if ok
            else f"❌ <b>{mot.praise(False)}</b> Правильный ответ: <b>{esc(s['opts'][s['ans']])}</b>")
    text = head
    if w:
        text += "\n\n" + card_text(w)
    text += extra
    if lines:
        text += "\n\n" + "\n".join(lines)
    await safe_edit(cb, text, kb([("▶️ Дальше", "next"), ("🔊", "say")]))
    await cb.answer()


# ============================ фразы ============================
def phrases_cat_kb() -> InlineKeyboardMarkup:
    rows = [[(f"{c}", f"pc:{i}")] for i, c in enumerate(content.PHRASE_CATS)]
    rows.append([("🎲 Любая категория", "pc:all")])
    return kb(*rows)


async def ask_phrase(bot: Bot, u: dict):
    uid = u["uid"]
    cat = PH_CAT.get(uid)
    base = [p for p in content.PHRASES if cat is None or p["cat"] == cat]
    pool = [p for p in base if p["ru"] not in RECENT_PH[uid]] or base
    p = random.choice(pool)
    RECENT_PH[uid].append(p["ru"])
    set_session(uid, type="phrase", p=p)
    await bot.send_message(uid, f"✍️ Переведи на английский:\n\n<b>{esc(p['ru'])}</b>\n\n"
                                "<i>Напиши ответ сообщением (сокращения вроде don't тоже подойдут).</i>",
                           reply_markup=kb([("🤷 Не знаю", "pk"), ("🚪 Выйти", "exit")], [("🗂 Другая категория", "pcats")]))


async def check_phrase(m: Message, u: dict, s: dict, text: str):
    uid, p = u["uid"], s["p"]
    S.pop(uid, None)
    verdict = content.check_answer(text, p["en"])
    ok = verdict != "wrong"
    SPEECH[uid] = p["en"][0]
    lines, gain = register_answer(u, ok, 15 if verdict == "exact" else 10, "phrase")
    if verdict == "exact":
        msg = f"✅ <b>{mot.praise(True)}</b> +{gain} XP"
    elif verdict == "close":
        msg = f"✅ <b>Почти идеально!</b> Небольшая опечатка. Правильно: <b>{esc(p['en'][0])}</b> +{gain} XP"
    else:
        msg = f"❌ <b>{mot.praise(False)}</b> Правильно: <b>{esc(p['en'][0])}</b>"
    others = [v for v in p["en"][1:] if v != p["en"][0]]
    if others:
        msg += "\n\nЕщё варианты:\n" + "\n".join(f"• {esc(v)}" for v in others)
    if lines:
        msg += "\n\n" + "\n".join(lines)
    await m.answer(msg, reply_markup=kb([("▶️ Ещё фразу", "np"), ("🔊", "say")]))


TEXT_HANDLERS["phrase"] = check_phrase


@router.callback_query(F.data.startswith("pc:"))
async def on_phrase_cat(cb: CallbackQuery, bot: Bot):
    v = cb.data[3:]
    PH_CAT[cb.from_user.id] = None if v == "all" else int(v)
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await ask_phrase(bot, get_user(cb.from_user))


@router.callback_query(F.data == "pcats")
async def on_phrase_cats(cb: CallbackQuery):
    S.pop(cb.from_user.id, None)
    await cb.answer()
    await cb.message.answer("🗂 Выбери категорию фраз:", reply_markup=phrases_cat_kb())


@router.callback_query(F.data == "pk")
async def on_phrase_skip(cb: CallbackQuery):
    u = get_user(cb.from_user)
    s = get_session(u["uid"], {"phrase"})
    if not s:
        await cb.answer("Это упражнение уже неактуально")
        return
    S.pop(u["uid"], None)
    p = s["p"]
    COMBO[u["uid"]] = 0
    u["wrong"] += 1
    store.mark("users")
    SPEECH[u["uid"]] = p["en"][0]
    await safe_edit(cb, f"Ничего страшного! Правильно:\n\n<b>{esc(p['en'][0])}</b>",
                    kb([("▶️ Ещё фразу", "np"), ("🔊", "say")]))
    await cb.answer()


@router.callback_query(F.data == "exit")
async def on_exit(cb: CallbackQuery):
    S.pop(cb.from_user.id, None)
    await safe_edit(cb, "Хорошо, выходим из упражнения 👌 Теперь можешь писать мне слова для перевода.")
    await cb.answer()


# ============================ общие callback-кнопки ============================
@router.callback_query(F.data == "say")
async def on_say(cb: CallbackQuery, bot: Bot):
    text = SPEECH.get(cb.from_user.id)
    await cb.answer()
    if text:
        await speak(bot, cb.from_user.id, text)


@router.callback_query(F.data.in_({"next", "goquiz"}))
async def on_next_q(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await ask_question(bot, get_user(cb.from_user), None if cb.data == "next" else "")


@router.callback_query(F.data == "sl_quiz")
async def on_sl_quiz(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await ask_question(bot, get_user(cb.from_user), "slang")


@router.callback_query(F.data == "np")
async def on_next_phrase(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await ask_phrase(bot, get_user(cb.from_user))


@router.callback_query(F.data == "addp")
async def on_add_pending(cb: CallbackQuery):
    u = get_user(cb.from_user)
    w = PENDING.pop(u["uid"], None)
    if not w:
        await cb.answer("Не нашёл, что добавлять. Отправь слово ещё раз")
        return
    store.add_word(u["uid"], w["en"], w["ru"], w.get("ex", ""), "", "user", today_of(u), "slang" if w.get("note") else "")
    await cb.answer("Добавлено в словарь ✅")
    try:
        await cb.message.edit_reply_markup(reply_markup=kb([("🔊 Озвучить", "say")]))
    except TelegramBadRequest:
        pass


# ============================ команды ============================
@router.message(CommandStart())
async def cmd_start(m: Message):
    u = get_user(m.from_user)
    await m.answer(WELCOME, reply_markup=main_kb())
    await m.answer("🗂 Выбери темы слов (можно несколько) и нажми «Готово»:", reply_markup=topics_kb(u))


@router.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(WELCOME.replace("Начнём с выбора тем 👇", "Темы можно поменять в /settings."), reply_markup=main_kb())


@router.message(Command("new"))
@router.message(F.text == MENU["new"])
async def cmd_new(m: Message, bot: Bot):
    u = get_user(m.from_user)
    await deliver_words(bot, u, u["per_day"], "🆕 <b>Новые слова</b>")


@router.message(Command("quiz"))
@router.message(F.text == MENU["quiz"])
async def cmd_quiz(m: Message, bot: Bot):
    await ask_question(bot, get_user(m.from_user), "")


@router.message(Command("phrase"))
@router.message(F.text == MENU["phrase"])
async def cmd_phrase(m: Message):
    get_user(m.from_user)
    S.pop(m.from_user.id, None)
    await m.answer("✍️ <b>Фразы</b>: выбери категорию 👇", reply_markup=phrases_cat_kb())


@router.message(Command("words"))
@router.message(F.text == MENU["words"])
async def cmd_words(m: Message, bot: Bot):
    u = get_user(m.from_user)
    ws = sorted(store.words_of(u["uid"]), key=lambda w: (w["added"], w["en"]), reverse=True)
    if not ws:
        await m.answer("Словарь пока пуст. Жми «🆕 Новые слова» или пришли мне любое слово 🙂")
        return
    today = today_of(u).isoformat()
    learned = sum(w["box"] >= LEARNED_BOX for w in ws)
    due = sum(w["next_review"] <= today for w in ws)
    lines = [f"{'✅' if w['box'] >= LEARNED_BOX else '🔁'} {esc(w['en'])} — {esc(w['ru'])}" for w in ws[:60]]
    if len(ws) > 60:
        lines.append(f"… и ещё {len(ws) - 60}")
    lines.append("\n<i>Удалить слово: /del слово</i>")
    await send_chunks(bot, u["uid"], f"📚 <b>Мой словарь</b>: {len(ws)} (✅ выучено {learned}, 🔁 на повторе сегодня {due})\n", lines)


@router.message(Command("add"))
async def cmd_add(m: Message):
    u = get_user(m.from_user)
    arg = (m.text or "").partition(" ")[2].strip()
    if not arg:
        await m.answer("Напиши так:\n<code>/add apple</code>\nили со своим переводом:\n<code>/add apple - яблоко</code>")
        return
    parts = re.split(r"\s+[-—–=]\s+|\s*=\s*", arg, maxsplit=1)
    try:
        if len(parts) == 2:
            a, b = parts[0].strip(), parts[1].strip()
            en, ru = (b, a) if content.has_cyrillic(a) else (a, b)
            ex = content.BY_EN.get(en.lower(), {}).get("ex", "")
        else:
            r = await do_translate(arg)
            en, ru, ex = r["en"], r["ru"], r.get("ex", "")
    except Exception as e:
        log.exception("translate failed")
        await m.answer(f"Не получилось перевести 😕 ({type(e).__name__}). Укажи перевод сам: <code>/add apple - яблоко</code>")
        return
    kind = "slang" if content.BY_EN.get(en.lower(), {}).get("kind") == "slang" else ""
    if store.add_word(u["uid"], en, ru, ex, "", "user", today_of(u), kind):
        SPEECH[u["uid"]] = en
        await m.answer(f"➕ Добавлено: <b>{esc(en)}</b> — {esc(ru)}", reply_markup=kb([("🔊 Озвучить", "say")]))
    else:
        await m.answer("Это слово уже есть в словаре 👌")


@router.message(Command("del"))
async def cmd_del(m: Message):
    u = get_user(m.from_user)
    arg = (m.text or "").partition(" ")[2].strip()
    if not arg:
        await m.answer("Напиши так: <code>/del apple</code>")
        return
    await m.answer("🗑 Удалено." if store.del_word(u["uid"], arg) else "Не нашёл такого слова в словаре.")


@router.message(Command("tz"))
async def cmd_tz(m: Message):
    u = get_user(m.from_user)
    tz = (m.text or "").partition(" ")[2].strip()
    try:
        ZoneInfo(tz)
        if not tz:
            raise ValueError
    except Exception:
        await m.answer(f"Сейчас: <b>{esc(u['tz'])}</b>\nПример: <code>/tz Europe/Moscow</code>\n"
                       "Ещё: Europe/Berlin, Europe/Kyiv, Europe/Minsk, Asia/Almaty, Asia/Tbilisi, UTC")
        return
    u["tz"] = tz
    store.mark("users")
    await m.answer(f"🌍 Часовой пояс: <b>{esc(tz)}</b>. Сейчас у тебя {now_of(u):%H:%M}.")


# ============================ прогресс и значки ============================
@router.message(Command("stats"))
@router.message(F.text == MENU["stats"])
async def cmd_stats(m: Message):
    u = get_user(m.from_user)
    today = today_of(u)
    ws = store.words_of(u["uid"])
    ctx = build_ctx(u)
    due = sum(w["next_review"] <= today.isoformat() for w in ws)
    total = u["correct"] + u["wrong"]
    acc = f"{round(100 * u['correct'] / total)}% ({u['correct']} из {total})" if total else "пока нет ответов"
    goal = u["goal"] or 10
    n_today = u["today_n"] if u["today_date"] == today.isoformat() else 0
    goal_line = (f"🎯 Цель дня: {mot.bar(n_today, goal)} {min(n_today, goal)}/{goal}"
                 + (" ✅" if n_today >= goal else ""))
    await m.answer(
        f"📊 <b>Прогресс</b> · <i>{BRAND}</i>\n\n"
        f"🔥 Серия: <b>{live_streak(u, today)}</b> дн. (рекорд {u['best_streak']}) · 🧊 заморозок: {u['freezes']}\n"
        f"⭐ Уровень: <b>{mot.level_name(u['xp'])}</b> · {u['xp']} XP\n   {mot.level_progress(u['xp'])}\n"
        f"{goal_line}\n\n"
        f"📚 Слов в словаре: <b>{ctx['total']}</b> (выучено ✅ {ctx['words']}) · 😎 сленг выучен: {ctx['slang']}\n"
        f"📘 Неправильных глаголов выучено: <b>{ctx['verbs']}</b> из {len(content.VERBS)}\n"
        f"🔁 На повторе сегодня: <b>{due}</b>\n"
        f"🎯 Точность: {acc}\n"
        f"⚡ Лучший комбо: {u['best_combo']} подряд\n\n"
        f"📅 {mot.week_chart(u, today)}\n"
        f"🏅 Значков: {len(mot.owned(u))} из {len(mot.BADGES)} (/badges)")


@router.message(Command("badges"))
@router.message(F.text == MENU["badges"])
async def cmd_badges(m: Message, bot: Bot):
    u = get_user(m.from_user)
    have = mot.owned(u)
    got = [f"✅ {b[1]}" for b in mot.BADGES if b[0] in have]
    todo = [f"🔒 {b[1]} — <i>{b[2]}</i>" for b in mot.BADGES if b[0] not in have]
    lvl = "\n".join(f"{'➡️' if i == mot.level_index(u['xp']) else '▫️'} {n} — от {x} XP" for i, (x, n) in enumerate(mot.LEVELS))
    await send_chunks(bot, u["uid"], f"🏅 <b>Достижения</b> ({len(have)} из {len(mot.BADGES)})\n",
                      got + [""] + todo + ["", "<b>Уровни</b>", lvl,
                                           "\n🧊 <i>Заморозка серии: если пропустишь один день, серия не сгорит. "
                                           "Новая заморозка — за каждые 7 дней серии (максимум 2).</i>"])


# ============================ сленг ============================
def slang_menu_kb() -> InlineKeyboardMarkup:
    rows = [[(t, f"slt:{i}")] for i, t in enumerate(content.SLANG_TOPICS)]
    rows += [[("🆕 5 новых сленговых слов", "sl_new")], [("🧠 Сленг-квиз", "sl_quiz")]]
    return kb(*rows)


@router.message(Command("slang"))
@router.message(F.text == MENU["slang"])
async def cmd_slang(m: Message):
    get_user(m.from_user)
    await m.answer(f"😎 <b>Американский сленг</b>: {len(content.ALL_SLANG)} выражений.\n"
                   "Выбери тему, чтобы посмотреть все слова, или учи по 5 новых 👇\n\n"
                   "<i>Сленг — для неформального общения с друзьями. На собеседовании и в письмах лучше обычный язык.</i>",
                   reply_markup=slang_menu_kb())


@router.callback_query(F.data == "sl_menu")
async def on_sl_menu(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer("😎 <b>Американский сленг</b>: выбери тему 👇", reply_markup=slang_menu_kb())


@router.callback_query(F.data == "sl_new")
async def on_sl_new(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await deliver_words(bot, get_user(cb.from_user), 5, "😎 <b>Новый сленг</b>", kind="slang")


@router.callback_query(F.data.startswith("slt:"))
async def on_sl_topic(cb: CallbackQuery, bot: Bot):
    i = int(cb.data[4:])
    words = content.SLANG_BANK[content.SLANG_TOPICS[i]]
    await cb.answer()
    SPEECH[cb.from_user.id] = ". ".join(w["en"] for w in words[:12])
    await send_chunks(bot, cb.from_user.id, f"{esc(content.SLANG_TOPICS[i])} ({len(words)})\n",
                      ["• " + card_text(w) for w in words],
                      kb([("➕ Добавить все в квиз", f"slall:{i}")], [("🔊 Озвучить первые 12", "say")],
                         [("⬅️ К разделу сленга", "sl_menu")]))


@router.callback_query(F.data.startswith("slall:"))
async def on_sl_all(cb: CallbackQuery):
    u = get_user(cb.from_user)
    i = int(cb.data[6:])
    added = sum(store.add_word(u["uid"], w["en"], w["ru"], w["ex"], w["topic"], "slang", today_of(u), "slang")
                for w in content.SLANG_BANK[content.SLANG_TOPICS[i]])
    await cb.answer(f"Добавлено слов: {added}" if added else "Всё это уже в словаре 👌", show_alert=True)


# ============================ темы и настройки ============================
def topics_kb(u: dict) -> InlineKeyboardMarkup:
    chosen = set(topics_of(u))
    rows = [[(("✅ " if i in chosen else "⬜ ") + t, f"t:{i}")] for i, t in enumerate(content.TOPICS)]
    rows.append([("Готово 👍", "td")])
    return kb(*rows)


@router.callback_query(F.data.startswith("t:"))
async def on_topic(cb: CallbackQuery):
    u = get_user(cb.from_user)
    i = int(cb.data[2:])
    chosen = set(topics_of(u)) ^ {i}
    u["topics"] = ",".join(str(x) for x in sorted(chosen))
    store.mark("users")
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=topics_kb(u))
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "td")
async def on_topics_done(cb: CallbackQuery, bot: Bot):
    u = get_user(cb.from_user)
    names = ", ".join(content.TOPICS[i] for i in topics_of(u)) or "все темы"
    await safe_edit(cb, f"🗂 Темы сохранены: {esc(names)}")
    await cb.answer()
    if not store.words_of(u["uid"]):                      # самый первый урок
        await deliver_words(bot, u, u["per_day"], f"🎁 <b>Твой первый урок!</b> · <i>{BRAND}</i> 💖")


def settings_text(u: dict) -> str:
    names = ", ".join(content.TOPICS[i] for i in topics_of(u)) or "все"
    daily = f"вкл, с {u['hour']:02d}:00" if u["daily"] else "выкл"
    return ("⚙️ <b>Настройки</b>\n\n"
            f"📖 Слов за один раз (кнопка «Новые слова»): <b>{u['per_day']}</b>\n"
            f"☀️ «Слово дня» по утрам: <b>{daily}</b>\n"
            f"🎯 Цель дня: <b>{u['goal'] or 10}</b> ответов\n"
            f"🌍 Часовой пояс: <b>{esc(u['tz'])}</b> (сменить: <code>/tz Europe/Moscow</code>)\n"
            f"🗂 Темы: {esc(names)}\n\n"
            "<i>Бот сам ничего не присылает, пока ты не включишь «Слово дня».</i>")


def settings_kb(u: dict) -> InlineKeyboardMarkup:
    mark = lambda cur, v: ("• " if cur == v else "") + str(v)
    goal = u["goal"] or 10
    return kb([("Слов:", "noop")] + [(mark(u["per_day"], n), f"pd:{n}") for n in (3, 5, 8, 10)],
              [("Цель:", "noop")] + [(mark(goal, n), f"gl:{n}") for n in (5, 10, 20, 30)],
              [(f"☀️ Слово дня: {'вкл' if u['daily'] else 'выкл'}", "dl")],
              [("Время:", "noop")] + [(mark(u["hour"], h), f"hr:{h}") for h in (8, 9, 12, 18)],
              [("🗂 Изменить темы", "tp")])


@router.message(Command("settings"))
@router.message(F.text == MENU["settings"])
async def cmd_settings(m: Message):
    u = get_user(m.from_user)
    await m.answer(settings_text(u), reply_markup=settings_kb(u))


@router.callback_query(F.data == "noop")
async def on_noop(cb: CallbackQuery):
    await cb.answer()


@router.callback_query(F.data.regexp(r"^(pd|hr|gl):\d+$"))
async def on_setting(cb: CallbackQuery):
    u = get_user(cb.from_user)
    key, val = cb.data.split(":")
    u[{"pd": "per_day", "hr": "hour", "gl": "goal"}[key]] = int(val)
    store.mark("users")
    await safe_edit(cb, settings_text(u), settings_kb(u))
    await cb.answer("Сохранено ✅")


@router.callback_query(F.data == "dl")
async def on_daily_toggle(cb: CallbackQuery):
    u = get_user(cb.from_user)
    u["daily"] = 0 if u["daily"] else 1
    store.mark("users")
    await safe_edit(cb, settings_text(u), settings_kb(u))
    await cb.answer("Сохранено ✅")


@router.callback_query(F.data == "tp")
async def on_topics_open(cb: CallbackQuery):
    u = get_user(cb.from_user)
    await cb.message.answer("🗂 Выбери темы и нажми «Готово»:", reply_markup=topics_kb(u))
    await cb.answer()


# ============================ «записки» от мужа ============================
@router.message(Command("note"))
async def cmd_note(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        await m.answer("Напиши так: <code>/note Ты у меня самая умная ❤️</code>\n"
                       f"Записок в копилке: {len(store.notes)}. Бот иногда добавляет их к «слову дня».")
        return
    store.notes.append(text)
    store.mark("notes")
    await m.answer(f"💌 Записка добавлена (всего {len(store.notes)}).")


@router.message(Command("msg"))
async def cmd_msg(m: Message, bot: Bot):
    if m.from_user.id != ADMIN_ID:
        return
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        await m.answer("Напиши так: <code>/msg Привет! Горжусь тобой 🥰</code> — отправлю сразу всем, кроме тебя.")
        return
    sent = 0
    for uid in list(store.users):
        if uid == ADMIN_ID:
            continue
        try:
            await bot.send_message(uid, f"💌 {esc(text)}")
            sent += 1
        except Exception:
            log.warning("msg to %s failed", uid)
    await m.answer(f"Отправлено: {sent}")


# ============================ обычный текст: перевод / ответ на упражнение ============================
@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message, bot: Bot):
    u = get_user(m.from_user)
    uid = u["uid"]
    text = m.text.strip()
    s = get_session(uid)
    active = None
    if s and s["type"] in TEXT_HANDLERS:
        if not content.has_cyrillic(text):                # ответы на упражнения — только по-английски
            await TEXT_HANDLERS[s["type"]](m, u, s, text)
            return
        active = s
    if len(text) > 200:
        await m.answer("Слишком длинный текст 🙈 Пришли слово или короткую фразу.")
        return
    await bot.send_chat_action(uid, "typing")
    try:
        r = await do_translate(text)
    except Exception as e:
        log.exception("translate failed: %s", e)
        detail = f"\n<code>{esc(str(e)[:300])}</code>" if uid == ADMIN_ID else ""
        await m.answer(f"Не получилось перевести 😕 Онлайн-переводчики сейчас недоступны ({type(e).__name__}). "
                       f"Если слово есть в моих списках, я найду его и без интернета. Попробуй чуть позже.{detail}")
        return
    if "verb" in r:
        v = r["verb"]
        SPEECH[uid] = f"{v['v1']}, {v['v2']}, {v['v3']}. " + " ".join(v["en"])
        await m.answer(verb_card(v), reply_markup=kb([("🔊 Озвучить", "say"), ("📘 К группе", f"vg:{v['g']}")]))
        return
    SPEECH[uid] = r["en"]
    buttons = [("🔊 Озвучить", "say")]
    if len(r["en"].split()) <= 3:
        PENDING[uid] = r
        buttons.append(("➕ В словарь", "addp"))
    out = f"🇬🇧 <b>{esc(r['en'])}</b>"
    if r.get("tr"):
        out += f" <i>[{esc(r['tr'])}]</i>"
    out += f"\n🇷🇺 {esc(r['ru'])}"
    if r.get("ex"):
        out += f"\n<i>{esc(r['ex'])}</i>"
    if r.get("note"):
        out += f"\n💬 {esc(r['note'])}"
    if active:
        out += "\n\n<i>Упражнение ещё активно: ответь по-английски или нажми «🚪 Выйти».</i>"
    await m.answer(out, reply_markup=kb(buttons))


# ============================ рассылка «слова дня» по расписанию ============================
async def tick(bot: Bot) -> dict:
    """Вызывается внешним cron-ом (cron-job.org). Работает только для тех, кто включил «Слово дня»."""
    res = {"daily": 0, "errors": 0}
    for u in list(store.users.values()):
        try:
            if not u["daily"]:
                continue
            now = now_of(u)
            today = now.date().isoformat()
            if now.hour >= u["hour"] and u["last_daily"] != today:
                u["last_daily"] = today                      # ставим сразу — защита от двойных вызовов
                store.mark("users")
                note = random.choice(store.notes) if store.notes and random.random() < 0.5 else None
                await deliver_words(bot, u, u["per_day"], f"☀️ <b>Слова дня</b> · <i>{BRAND}</i> 💖", note)
                res["daily"] += 1
        except TelegramForbiddenError:
            log.info("user %s заблокировал бота", u["uid"])
        except Exception:
            log.exception("tick failed for %s", u["uid"])
            res["errors"] += 1
    await store.flush()
    return res
