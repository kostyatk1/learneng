"""Вся логика бота: команды, квиз, фразы, настройки, ежедневная рассылка."""
import asyncio
import html
import io
import logging
import os
import random
import re
from collections import defaultdict, deque
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup)

import content
from storage import (DEFAULT_TZ, INTERVALS, LEARNED_BOX, Store, level_for, live_streak, mark_active, srs_review)

log = logging.getLogger("bot")
router = Router()

store: Store = None            # выставляется в main.py
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
REMIND_HOUR = int(os.getenv("REMIND_HOUR", "20"))

# временное состояние (живёт в памяти; после перезапуска сервера упражнение просто «устаревает»)
S: dict[int, dict] = {}                                   # текущее упражнение
RECENT: dict[int, deque] = defaultdict(lambda: deque(maxlen=4))
RECENT_PH: dict[int, deque] = defaultdict(lambda: deque(maxlen=6))
SPEECH: dict[int, str] = {}                               # что озвучивает кнопка 🔊
PENDING: dict[int, tuple[str, str]] = {}                  # перевод, который можно добавить в словарь

MENU = {"quiz": "🧠 Квиз", "new": "🆕 Новые слова", "phrase": "✍️ Фразы",
        "words": "📚 Мои слова", "stats": "📊 Прогресс", "settings": "⚙️ Настройки"}

BRAND = "Special FOR Ksen4ik"

WELCOME = (
    f"💖 <b>{BRAND}</b> 💖\n\n"
    "Привет! 👋 Я помогу учить английский (уровень A2) маленькими порциями.\n\n"
    "<b>Что я умею</b>\n"
    "🆕 /new — новые слова с примерами и озвучкой\n"
    "🧠 /quiz — повторение: умные интервалы, чтобы слова запоминались надолго\n"
    "✍️ /phrase — переведи фразу на английский\n"
    "📚 /words — твой словарь, /add — добавить своё слово\n"
    "💬 Просто напиши слово или фразу (по-русски или по-английски) — переведу и озвучу\n"
    "📊 /stats — серия дней и прогресс\n"
    "⚙️ /settings — темы, время «слова дня», напоминания\n\n"
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
    rows = [[MENU["quiz"], MENU["new"]], [MENU["phrase"], MENU["words"]], [MENU["stats"], MENU["settings"]]]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True)


async def safe_edit(cb: CallbackQuery, text: str, markup=None):
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            log.warning("edit failed: %s", e)


def _tts(text: str) -> bytes:
    from gtts import gTTS
    buf = io.BytesIO()
    gTTS(text=text, lang="en").write_to_fp(buf)
    return buf.getvalue()


async def speak(bot: Bot, uid: int, text: str):
    try:
        data = await asyncio.to_thread(_tts, text)
        await bot.send_voice(uid, BufferedInputFile(data, "voice.mp3"))
    except Exception:
        log.exception("TTS failed")
        await bot.send_message(uid, "Не получилось озвучить 😕 Попробуй ещё раз чуть позже.")


def _tr(text: str, src: str, tgt: str) -> str:
    from deep_translator import GoogleTranslator
    return GoogleTranslator(source=src, target=tgt).translate(text)


async def translate(text: str) -> tuple[str, str]:
    """Возвращает (english, русский) независимо от того, на каком языке ввели."""
    if content.has_cyrillic(text):
        en = (await asyncio.to_thread(_tr, text, "ru", "en")).strip()
        ru = text
    else:
        ru = (await asyncio.to_thread(_tr, text, "en", "ru")).strip()
        en = text
    if len(en.split()) == 1:
        en = en.lower()
    return en, ru


# ============================ выдача слов ============================
def pick_new(u: dict, n: int) -> list[dict]:
    have = {w["en"].lower() for w in store.words_of(u["uid"])}
    idxs = topics_of(u) or range(len(content.TOPICS))
    pool = [w for i in idxs for w in content.BANK[content.TOPICS[i]] if w["en"].lower() not in have]
    if not pool:                                           # выбранные темы закончились — берём из остальных
        pool = [w for w in content.ALL_WORDS if w["en"].lower() not in have]
    random.shuffle(pool)
    return pool[:n]


async def deliver_words(bot: Bot, u: dict, n: int, header: str, note: str | None = None):
    uid = u["uid"]
    words = pick_new(u, n)
    if not words:
        await bot.send_message(uid, "🎉 Все слова из моего банка уже в твоём словаре! "
                                    "Добавляй свои через /add или просто пиши мне любое слово.")
        return
    today = today_of(u)
    lines = []
    for w in words:
        store.add_word(uid, w["en"], w["ru"], w["ex"], w["topic"], "daily", today)
        lines.append(f"• <b>{esc(w['en'])}</b> — {esc(w['ru'])}\n   <i>{esc(w['ex'])}</i>")
    SPEECH[uid] = " ... ".join(f"{w['en']}. {w['ex']}" for w in words)
    text = f"{header}\n\n" + "\n".join(lines)
    if note:
        text += f"\n\n💌 {esc(note)}"
    await bot.send_message(uid, text, reply_markup=kb([("🔊 Послушать", "say")], [("🧠 Закрепить квизом", "goquiz")]))


# ============================ квиз ============================
async def ask_question(bot: Bot, u: dict):
    uid = u["uid"]
    today = today_of(u).isoformat()
    ws = store.words_of(uid)
    if not ws:
        await bot.send_message(uid, "Словарь пока пуст. Жми «🆕 Новые слова» 🙂")
        return
    due = [w for w in ws if w["next_review"] <= today]
    if not due:
        nxt = min(w["next_review"] for w in ws)
        await bot.send_message(uid, f"🎉 На сегодня всё повторено! Следующее повторение: <b>{nxt}</b>.\n"
                                    "Хочешь ещё? Жми «🆕 Новые слова».")
        return
    pool = [w for w in due if w["en"].lower() not in RECENT[uid]] or due
    low = min(w["box"] for w in pool)
    w = random.choice([x for x in pool if x["box"] <= low + 1])

    rev = random.random() < min(0.25 + 0.1 * w["box"], 0.8)          # чем лучше слово знаешь — тем чаще RU→EN
    correct = w["en"] if rev else w["ru"]
    prompt = w["ru"] if rev else w["en"]
    cands = {(x["en"] if rev else x["ru"]) for x in ws + content.ALL_WORDS} - {correct}
    opts = random.sample(sorted(cands), 3) + [correct]
    random.shuffle(opts)

    S[uid] = {"type": "quiz", "en": w["en"].lower(), "opts": opts, "ans": opts.index(correct)}
    RECENT[uid].append(w["en"].lower())
    SPEECH[uid] = w["en"]
    head = f"🇷🇺 <b>{esc(prompt)}</b>\nКак это по-английски? 👇" if rev else f"🇬🇧 <b>{esc(prompt)}</b>\nВыбери перевод 👇"
    rows = [[(o, f"a:{i}")] for i, o in enumerate(opts)] + [[("🔊", "say")]]
    await bot.send_message(uid, head, reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("a:"))
async def on_answer(cb: CallbackQuery):
    u = get_user(cb.from_user)
    uid = u["uid"]
    s = S.get(uid)
    if not s or s["type"] != "quiz":
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
    u["correct" if ok else "wrong"] += 1
    u["xp"] += 10 if ok else 2
    new_day = mark_active(u, today)
    store.mark("users")

    text = "✅ <b>Верно!</b>" if ok else f"❌ <b>Почти.</b> Правильный ответ: <b>{esc(s['opts'][s['ans']])}</b>"
    if w:
        text += f"\n\n<b>{esc(w['en'])}</b> — {esc(w['ru'])}"
        if w["ex"]:
            text += f"\n<i>{esc(w['ex'])}</i>"
    text += extra
    if new_day:
        text += f"\n\n🔥 Серия: <b>{u['streak']}</b> дн."
    await safe_edit(cb, text, kb([("▶️ Дальше", "next"), ("🔊", "say")]))
    await cb.answer()


# ============================ фразы ============================
async def ask_phrase(bot: Bot, u: dict):
    uid = u["uid"]
    pool = [p for p in content.PHRASES if p["ru"] not in RECENT_PH[uid]] or content.PHRASES
    p = random.choice(pool)
    RECENT_PH[uid].append(p["ru"])
    S[uid] = {"type": "phrase", "p": p}
    await bot.send_message(uid, f"✍️ Переведи на английский:\n\n<b>{esc(p['ru'])}</b>\n\n<i>Напиши ответ сообщением.</i>",
                           reply_markup=kb([("🤷 Не знаю", "pk")]))


async def check_phrase(m: Message, u: dict, s: dict, text: str):
    uid, p = u["uid"], s["p"]
    S.pop(uid, None)
    verdict = content.check_answer(text, p["en"])
    ok = verdict != "wrong"
    today = today_of(u)
    u["correct" if ok else "wrong"] += 1
    u["xp"] += 15 if verdict == "exact" else (10 if ok else 2)
    new_day = mark_active(u, today)
    store.mark("users")
    SPEECH[uid] = p["en"][0]
    if verdict == "exact":
        msg = "✅ <b>Отлично!</b>"
    elif verdict == "close":
        msg = f"✅ <b>Почти идеально!</b> Небольшая опечатка. Правильно: <b>{esc(p['en'][0])}</b>"
    else:
        msg = f"❌ <b>Не совсем.</b> Правильно: <b>{esc(p['en'][0])}</b>"
    if len(p["en"]) > 1:
        msg += "\n\nЕщё варианты:\n" + "\n".join(f"• {esc(v)}" for v in p["en"][1:] if v != p["en"][0])
    if new_day:
        msg += f"\n\n🔥 Серия: <b>{u['streak']}</b> дн."
    await m.answer(msg, reply_markup=kb([("▶️ Ещё фразу", "np"), ("🔊", "say")]))


@router.callback_query(F.data == "pk")
async def on_phrase_skip(cb: CallbackQuery):
    u = get_user(cb.from_user)
    s = S.pop(u["uid"], None)
    if not s or s["type"] != "phrase":
        await cb.answer("Это упражнение уже неактуально")
        return
    p = s["p"]
    u["wrong"] += 1
    store.mark("users")
    SPEECH[u["uid"]] = p["en"][0]
    await safe_edit(cb, f"Ничего страшного! Правильно:\n\n<b>{esc(p['en'][0])}</b>",
                    kb([("▶️ Ещё фразу", "np"), ("🔊", "say")]))
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
    await ask_question(bot, get_user(cb.from_user))


@router.callback_query(F.data == "np")
async def on_next_phrase(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await ask_phrase(bot, get_user(cb.from_user))


@router.callback_query(F.data == "addp")
async def on_add_pending(cb: CallbackQuery):
    u = get_user(cb.from_user)
    pair = PENDING.pop(u["uid"], None)
    if not pair:
        await cb.answer("Не нашёл, что добавлять. Отправь слово ещё раз")
        return
    en, ru = pair
    ex = content.BY_EN.get(en.lower(), {}).get("ex", "")
    store.add_word(u["uid"], en, ru, ex, "", "user", today_of(u))
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
    await m.answer("🗂 Выбери темы (можно несколько) и нажми «Готово»:", reply_markup=topics_kb(u))


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
    await ask_question(bot, get_user(m.from_user))


@router.message(Command("phrase"))
@router.message(F.text == MENU["phrase"])
async def cmd_phrase(m: Message, bot: Bot):
    await ask_phrase(bot, get_user(m.from_user))


@router.message(Command("words"))
@router.message(F.text == MENU["words"])
async def cmd_words(m: Message):
    u = get_user(m.from_user)
    ws = sorted(store.words_of(u["uid"]), key=lambda w: (w["added"], w["en"]), reverse=True)
    if not ws:
        await m.answer("Словарь пока пуст. Жми «🆕 Новые слова» или пришли мне любое слово 🙂")
        return
    today = today_of(u).isoformat()
    learned = sum(w["box"] >= LEARNED_BOX for w in ws)
    due = sum(w["next_review"] <= today for w in ws)
    lines = [f"{'✅' if w['box'] >= LEARNED_BOX else '🔁'} {esc(w['en'])} — {esc(w['ru'])}" for w in ws[:40]]
    more = f"\n… и ещё {len(ws) - 40}" if len(ws) > 40 else ""
    await m.answer(f"📚 <b>Мой словарь</b>: {len(ws)} (✅ выучено {learned}, 🔁 на повторе сегодня {due})\n\n"
                   + "\n".join(lines) + more + "\n\n<i>Удалить слово: /del слово</i>")


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
        else:
            en, ru = await translate(arg)
    except Exception:
        log.exception("translate failed")
        await m.answer("Не получилось перевести 😕 Попробуй так: <code>/add apple - яблоко</code>")
        return
    ex = content.BY_EN.get(en.lower(), {}).get("ex", "")
    if store.add_word(u["uid"], en, ru, ex, "", "user", today_of(u)):
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


@router.message(Command("stats"))
@router.message(F.text == MENU["stats"])
async def cmd_stats(m: Message):
    u = get_user(m.from_user)
    today = today_of(u)
    ws = store.words_of(u["uid"])
    learned = sum(w["box"] >= LEARNED_BOX for w in ws)
    due = sum(w["next_review"] <= today.isoformat() for w in ws)
    total = u["correct"] + u["wrong"]
    acc = f"{round(100 * u['correct'] / total)}% ({u['correct']} из {total})" if total else "пока нет ответов"
    practiced = "✅ сегодня уже занимались" if u["last_active"] == today.isoformat() else "⏳ сегодня ещё не занимались"
    await m.answer(
        "📊 <b>Прогресс</b>\n\n"
        f"🔥 Серия: <b>{live_streak(u, today)}</b> дн. (рекорд {u['best_streak']})\n"
        f"⭐ Уровень: <b>{level_for(u['xp'])}</b> · {u['xp']} XP\n"
        f"📚 В словаре: <b>{len(ws)}</b> (выучено ✅ {learned} · в процессе {len(ws) - learned})\n"
        f"🔁 На повторе сегодня: <b>{due}</b>\n"
        f"🎯 Точность: {acc}\n\n{practiced}\n\n💖 <i>{BRAND}</i>")


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
    return ("⚙️ <b>Настройки</b>\n\n"
            f"📖 Слов в день: <b>{u['per_day']}</b>\n"
            f"⏰ «Слово дня» приходит с: <b>{u['hour']:02d}:00</b>\n"
            f"🔔 Вечернее напоминание ({REMIND_HOUR}:00): <b>{'вкл' if u['remind'] else 'выкл'}</b>\n"
            f"🌍 Часовой пояс: <b>{esc(u['tz'])}</b> (сменить: <code>/tz Europe/Moscow</code>)\n"
            f"🗂 Темы: {esc(names)}")


def settings_kb(u: dict) -> InlineKeyboardMarkup:
    mark = lambda cur, v: ("• " if cur == v else "") + str(v)
    return kb([("Слов в день:", "noop")] + [(mark(u["per_day"], n), f"pd:{n}") for n in (3, 5, 8, 10)],
              [("Время:", "noop")] + [(mark(u["hour"], h), f"hr:{h}") for h in (8, 9, 12, 18)],
              [(f"🔔 Напоминания: {'вкл' if u['remind'] else 'выкл'}", "rm")],
              [("🗂 Изменить темы", "tp")])


@router.message(Command("settings"))
@router.message(F.text == MENU["settings"])
async def cmd_settings(m: Message):
    u = get_user(m.from_user)
    await m.answer(settings_text(u), reply_markup=settings_kb(u))


@router.callback_query(F.data == "noop")
async def on_noop(cb: CallbackQuery):
    await cb.answer()


@router.callback_query(F.data.regexp(r"^(pd|hr):\d+$"))
async def on_setting(cb: CallbackQuery):
    u = get_user(cb.from_user)
    key, val = cb.data.split(":")
    u["per_day" if key == "pd" else "hour"] = int(val)
    store.mark("users")
    await safe_edit(cb, settings_text(u), settings_kb(u))
    await cb.answer("Сохранено ✅")


@router.callback_query(F.data == "rm")
async def on_remind(cb: CallbackQuery):
    u = get_user(cb.from_user)
    u["remind"] = 0 if u["remind"] else 1
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
                       f"Записок в копилке: {len(store.notes)}. Бот будет иногда добавлять их к «слову дня».")
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


# ============================ обычный текст: перевод / ответ на фразу ============================
@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message, bot: Bot):
    u = get_user(m.from_user)
    uid = u["uid"]
    text = m.text.strip()
    s = S.get(uid)
    if s and s["type"] == "phrase":
        await check_phrase(m, u, s, text)
        return
    if len(text) > 200:
        await m.answer("Слишком длинный текст 🙈 Пришли слово или короткую фразу.")
        return
    await bot.send_chat_action(uid, "typing")
    try:
        en, ru = await translate(text)
    except Exception:
        log.exception("translate failed")
        await m.answer("Не получилось перевести 😕 Попробуй ещё раз чуть позже.")
        return
    SPEECH[uid] = en
    buttons = [("🔊 Озвучить", "say")]
    if len(en.split()) <= 3:
        PENDING[uid] = (en, ru)
        buttons.append(("➕ В словарь", "addp"))
    await m.answer(f"🇬🇧 <b>{esc(en)}</b>\n🇷🇺 {esc(ru)}", reply_markup=kb(buttons))


# ============================ рассылка по расписанию ============================
async def tick(bot: Bot) -> dict:
    """Вызывается внешним cron-ом (cron-job.org). Идемпотентна: повторный вызов ничего не продублирует."""
    res = {"daily": 0, "remind": 0, "errors": 0}
    for u in list(store.users.values()):
        try:
            now = now_of(u)
            today = now.date().isoformat()
            if now.hour >= u["hour"] and u["last_daily"] != today:
                u["last_daily"] = today                      # ставим сразу — защита от двойных вызовов
                store.mark("users")
                note = random.choice(store.notes) if store.notes and random.random() < 0.5 else None
                await deliver_words(bot, u, u["per_day"], f"☀️ <b>Слова дня</b> · <i>{BRAND}</i> 💖", note)
                res["daily"] += 1
            elif (u["remind"] and REMIND_HOUR <= now.hour < 23
                  and u["last_remind"] != today and u["last_active"] != today):
                u["last_remind"] = today
                store.mark("users")
                streak = live_streak(u, now.date())
                text = (f"⏰ Сегодня ещё не было практики. 5 минут — и серия 🔥 <b>{streak}</b> дн. продолжится!"
                        if streak else "⏰ Пара минут английского на сегодня? Квиз ждёт 👇")
                await bot.send_message(u["uid"], text, reply_markup=kb([("🧠 Начать квиз", "goquiz")]))
                res["remind"] += 1
        except TelegramForbiddenError:
            log.info("user %s заблокировал бота", u["uid"])
        except Exception:
            log.exception("tick failed for %s", u["uid"])
            res["errors"] += 1
    await store.flush()
    return res
