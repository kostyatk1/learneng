"""Основная логика бота: новые слова (6 за раз), квиз, словарь, перевод в чате, настройки, «слово дня»."""
import asyncio
import html
import io
import logging
import os
import random
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup)

import content
import progress
import translator
from storage import DEFAULT_TZ, INTERVALS, LEARNED_BOX, Store, srs_review

log = logging.getLogger("bot")
router = Router()

store: Store = None            # выставляется в main.py
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
BRAND = "Special FOR Ksen4ik"
SESSION_TTL = 600              # через 10 минут «ожидание ответа» сбрасывается

# ---------- временное состояние (в памяти; после перезапуска сервера упражнение просто «устаревает») ----------
S: dict[int, dict] = {}                                   # текущее упражнение
RECENT: dict[int, list] = {}
SPEECH: dict[int, str] = {}                               # что озвучивает кнопка 🔊
PENDING: dict[int, dict] = {}                             # перевод, который можно добавить в словарь
TEXT_HANDLERS: dict = {}                                  # тип упражнения -> функция проверки ответа текстом

MENU = {"new": "🆕 Новые слова", "quiz": "🧠 Квиз", "verbs": "📘 Глаголы", "grammar": "📐 Грамматика",
        "words": "📚 Мои слова", "settings": "⚙️ Настройки", "admin": "🛠 Админ"}

PRAISE_OK = ["Верно!", "Отлично!", "Так держать!", "Супер!", "В точку!", "Класс!", "Молодец!", "Именно так!"]
PRAISE_BAD = ["Почти!", "Ничего, запомнится.", "Ошибки — часть пути 💪", "Не сдавайся!", "Бывает! Повторим скоро."]

WELCOME = (
    f"💖 <b>{BRAND}</b> 💖\n\n"
    "Привет! 👋 Я помогу учить <b>американский английский</b> (A2+ → B1).\n\n"
    "<b>Что я умею</b>\n"
    "🆕 /new — 6 новых слов за раз: 2 существительных, 2 глагола, 2 прилагательных, с примером и пометкой, "
    "где слово употребляют (сленг тоже есть)\n"
    "🧠 /quiz — повторение слов с умными интервалами\n"
    "📘 /verbs — неправильные глаголы: группы, предложения, тренировка\n"
    "📐 /grammar — Past Simple, Past Continuous, Present Simple, Present Continuous\n"
    "📚 /words — твой словарь · ➕ /add — добавить своё слово · ⚠️ /hard — сложные слова\n"
    "💬 Просто напиши слово или фразу (по-русски или по-английски) — переведу и озвучу\n"
    "⚙️ /settings — «слово дня» по утрам, часовой пояс\n"
    "🏠 /menu — главное меню"
)
HOME_TEXT = (f"🏠 <b>Главное меню</b> · <i>{BRAND}</i> 💖\n\n"
             "🆕 Новые слова · 🧠 Квиз · 📘 Глаголы · 📐 Грамматика\n📚 Мои слова · ⚙️ Настройки\n\n"
             "Или просто напиши слово — я его переведу.")


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


def praise(ok: bool) -> str:
    return random.choice(PRAISE_OK if ok else PRAISE_BAD)


def kb(*rows, home: bool = True) -> InlineKeyboardMarkup:
    """kb([("текст", "callback")], [...]) — внизу автоматически добавляется «🏠 Главное меню»."""
    rows = [list(r) for r in rows]
    if home and not any(d == "home" for r in rows for _, d in r):
        rows.append([("🏠 Главное меню", "home")])
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
                                                 for row in rows])


def main_kb(uid: int = 0) -> ReplyKeyboardMarkup:
    rows = [[MENU["new"], MENU["quiz"]], [MENU["verbs"], MENU["grammar"]], [MENU["words"], MENU["settings"]]]
    if uid and uid == ADMIN_ID:
        rows.append([MENU["admin"]])
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


async def do_translate(text: str) -> dict:
    """Сначала ищем в своих базах (мгновенно), потом в онлайн-переводчиках."""
    hit = content.lookup(text)
    if hit:
        return hit
    if content.has_cyrillic(text):
        en, ru = await translator.translate_text(text, "ru", "en"), text
    else:
        en, ru = text, await translator.translate_text(text, "en", "ru")
    if len(en.split()) == 1:
        en = en.lower()
    return {"en": en, "ru": ru, "ex": "", "note": "", "label": ""}


def entry_of(w: dict) -> dict:
    """Дополняет слово из словаря пользователя пометкой и частью речи из лексикона (если слово там есть)."""
    lex = content.BY_KEY.get(content.norm_key(w["en"]))
    return {"en": w["en"], "ru": w["ru"], "ex": w.get("ex") or (lex["ex"] if lex else ""),
            "label": lex["label"] if lex else "", "note": lex["note"] if lex else ""}


def card_text(w: dict) -> str:
    label = f" <i>({esc(w['label'])})</i>" if w.get("label") else ""
    out = f"<b>{esc(w['en'])}</b>{label} — {esc(w['ru'])}"
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


def register_answer(u: dict, ok: bool, base_xp: int = 0, kind: str = ""):
    """Учёт ответа (для статистики админа). Возвращает ([], 0) — очков и уровней в боте больше нет."""
    progress.record(u, ok, today_of(u))
    store.mark("users")
    return [], 0


# ============================ новые слова: 2 сущ. + 2 гл. + 2 прил. ============================
GROUP_TITLE = {"N": "📦 Существительные", "V": "⚡ Глаголы", "A": "🎨 Прилагательные"}


def pick_new_words(u: dict) -> list[dict]:
    have = {content.norm_key(w["en"]) for w in store.words_of(u["uid"])}
    chosen: list[dict] = []
    for g in "NVA":
        pool = [w for w in content.BY_GROUP[g] if content.norm_key(w["en"]) not in have]
        hard = [w for w in pool if w["level"] != "A2"]            # A2+/B1 предпочтительнее
        for _ in range(2):
            src = hard if hard and random.random() < 0.7 else pool
            if not src:
                break
            w = random.choice(src)
            chosen.append(w)
            pool = [x for x in pool if x is not w]
            hard = [x for x in hard if x is not w]
    if len(chosen) < 6:                                        # какая-то группа закончилась — добираем из остальных
        used = {id(w) for w in chosen}
        rest = [w for w in content.LEX if content.norm_key(w["en"]) not in have and id(w) not in used]
        random.shuffle(rest)
        chosen += rest[:6 - len(chosen)]
    return chosen


async def deliver_words(bot: Bot, u: dict, header: str, note: str | None = None):
    uid = u["uid"]
    words = pick_new_words(u)
    if not words:
        await bot.send_message(uid, "🎉 Весь мой запас слов уже в твоём словаре! "
                                    "Добавляй свои через /add или просто пиши мне любое слово.")
        return
    today = today_of(u)
    lines = []
    for g in "NVA":
        part = [w for w in words if w["group"] == g]
        if not part:
            continue
        lines.append(f"<b>{GROUP_TITLE[g]}</b>")
        for w in part:
            store.add_word(uid, w["en"], w["ru"], w["ex"], g, "new", today)
            lines.append("• " + card_text(w))
        lines.append("")
    SPEECH[uid] = " ... ".join(f"{w['en'].removeprefix('to ')}. {w['ex']}" for w in words)
    text = f"{header}\n\n" + "\n".join(lines).rstrip()
    if note:
        text += f"\n\n💌 {esc(note)}"
    await bot.send_message(uid, text, reply_markup=kb([("🔊 Послушать", "say")], [("🔄 Ещё 6 слов", "more"), ("🧠 Закрепить квизом", "goquiz")]))


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
                                    "Хочешь ещё? Жми «🆕 Новые слова» или «📘 Глаголы».")
        return
    recent = RECENT.setdefault(uid, [])
    pool = [w for w in due if w["en"].lower() not in recent] or due
    low = min(w["box"] for w in pool)
    w = random.choice([x for x in pool if x["box"] <= low + 1])
    recent.append(w["en"].lower())
    del recent[:-4]

    rev = random.random() < min(0.25 + 0.1 * w["box"], 0.8)          # чем лучше слово знаешь — тем чаще RU→EN
    correct = w["en"] if rev else w["ru"]
    prompt = w["ru"] if rev else w["en"]
    cands = {(x["en"] if rev else x["ru"]) for x in ws + content.LEX} - {correct}
    opts = random.sample(sorted(cands), 3) + [correct]
    random.shuffle(opts)

    set_session(uid, type="quiz", en=w["en"].lower(), opts=opts, ans=opts.index(correct))
    SPEECH[uid] = w["en"].removeprefix("to ")
    head = (f"🇷🇺 <b>{esc(prompt)}</b>\nКак это по-английски? 👇" if rev
            else f"🇺🇸 <b>{esc(prompt)}</b>\nВыбери перевод 👇")
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
    register_answer(u, ok)
    head = (f"✅ <b>{praise(True)}</b>" if ok
            else f"❌ <b>{praise(False)}</b> Правильный ответ: <b>{esc(s['opts'][s['ans']])}</b>")
    text = head + (("\n\n" + card_text(entry_of(w))) if w else "") + extra
    await safe_edit(cb, text, kb([("▶️ Дальше", "next"), ("🔊", "say")]))
    await cb.answer()


# ============================ общие кнопки ============================
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


@router.callback_query(F.data == "more")
async def on_more(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await deliver_words(bot, get_user(cb.from_user), "🆕 <b>Новые слова</b>")


@router.callback_query(F.data == "exit")
async def on_exit(cb: CallbackQuery):
    S.pop(cb.from_user.id, None)
    await safe_edit(cb, "Хорошо, выходим из упражнения 👌 Теперь можешь писать мне слова для перевода.")
    await cb.answer()


@router.callback_query(F.data == "noop")
async def on_noop(cb: CallbackQuery):
    await cb.answer()


@router.callback_query(F.data == "addp")
async def on_add_pending(cb: CallbackQuery):
    u = get_user(cb.from_user)
    w = PENDING.pop(u["uid"], None)
    if not w:
        await cb.answer("Не нашёл, что добавлять. Отправь слово ещё раз")
        return
    store.add_word(u["uid"], w["en"], w["ru"], w.get("ex", ""), "", "user", today_of(u))
    await cb.answer("Добавлено в словарь ✅")
    try:
        await cb.message.edit_reply_markup(reply_markup=kb([("🔊 Озвучить", "say")]))
    except TelegramBadRequest:
        pass


# ============================ главное меню и команды ============================
@router.callback_query(F.data == "home")
async def on_home(cb: CallbackQuery):
    S.pop(cb.from_user.id, None)
    await cb.answer()
    await cb.message.answer(HOME_TEXT, reply_markup=main_kb(cb.from_user.id))


@router.message(Command("menu"))
async def cmd_menu(m: Message):
    S.pop(m.from_user.id, None)
    get_user(m.from_user)
    await m.answer(HOME_TEXT, reply_markup=main_kb(m.from_user.id))


@router.message(CommandStart())
async def cmd_start(m: Message, bot: Bot):
    u = get_user(m.from_user)
    await m.answer(WELCOME, reply_markup=main_kb(m.from_user.id))
    if not store.words_of(u["uid"]):                       # самый первый урок
        await m.answer(f"🌍 Часовой пояс сейчас {esc(u['tz'])}. Если ты в другом — напиши, например, /tz Europe/Moscow")
        await deliver_words(bot, u, f"🎁 <b>Твой первый урок!</b> · <i>{BRAND}</i> 💖")


@router.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(WELCOME, reply_markup=main_kb(m.from_user.id))


@router.message(Command("new"))
@router.message(F.text == MENU["new"])
async def cmd_new(m: Message, bot: Bot):
    await deliver_words(bot, get_user(m.from_user), "🆕 <b>Новые слова</b>")


@router.message(Command("quiz"))
@router.message(F.text == MENU["quiz"])
async def cmd_quiz(m: Message, bot: Bot):
    await ask_question(bot, get_user(m.from_user))


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
    lines = [f"{'✅' if w['box'] >= LEARNED_BOX else '🔁'} {esc(w['en'])} — {esc(w['ru'])}" for w in ws[:80]]
    if len(ws) > 80:
        lines.append(f"… и ещё {len(ws) - 80}")
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
            ex = ""
        else:
            r = await do_translate(arg)
            en, ru, ex = r["en"], r["ru"], r.get("ex", "")
    except Exception as e:
        log.exception("translate failed")
        await m.answer(f"Не получилось перевести 😕 ({type(e).__name__}). Укажи перевод сам: <code>/add apple - яблоко</code>")
        return
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


def _hard_words(uid: int) -> list[dict]:
    ws = [w for w in store.words_of(uid) if w["wrong"] > 0]
    return sorted(ws, key=lambda w: (w["wrong"] - 0.5 * w["correct"], w["wrong"]), reverse=True)[:15]


@router.message(Command("hard"))
async def cmd_hard(m: Message):
    u = get_user(m.from_user)
    ws = _hard_words(u["uid"])
    if not ws:
        await m.answer("Пока нет слов с ошибками — отлично! 🎉 Они появятся здесь, когда начнёшь ошибаться в квизе.")
        return
    lines = [f"❌ {w['wrong']} · ✅ {w['correct']} — <b>{esc(w['en'])}</b> — {esc(w['ru'])}" for w in ws]
    await m.answer("⚠️ <b>Сложные слова</b> (чаще всего ошибки):\n\n" + "\n".join(lines),
                   reply_markup=kb([("🔁 Повторить их сейчас", "hardq")]))


@router.callback_query(F.data == "hardq")
async def on_hard_quiz(cb: CallbackQuery, bot: Bot):
    u = get_user(cb.from_user)
    today = today_of(u).isoformat()
    for w in _hard_words(u["uid"]):
        w["next_review"] = today                       # делаем их «просроченными», чтобы квиз взял их первыми
        w["box"] = min(w["box"], 1)
    store.mark("words")
    await cb.answer()
    await ask_question(bot, u)


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


# ============================ настройки ============================
def settings_text(u: dict) -> str:
    daily = f"вкл, с {u['hour']:02d}:00" if u["daily"] else "выкл"
    return ("⚙️ <b>Настройки</b>\n\n"
            f"☀️ «Слово дня» (6 новых слов утром): <b>{daily}</b>\n"
            f"🌍 Часовой пояс: <b>{esc(u['tz'])}</b> (сменить: <code>/tz Europe/Moscow</code>)\n\n"
            "<i>Бот сам ничего не присылает, пока ты не включишь «Слово дня».</i>")


def settings_kb(u: dict) -> InlineKeyboardMarkup:
    mark = lambda cur, v: ("• " if cur == v else "") + str(v)
    return kb([(f"☀️ Слово дня: {'вкл' if u['daily'] else 'выкл'}", "dl")],
              [("Время:", "noop")] + [(mark(u["hour"], x), f"hr:{x}") for x in (8, 9, 12, 18)])


@router.message(Command("settings"))
@router.message(F.text == MENU["settings"])
async def cmd_settings(m: Message):
    u = get_user(m.from_user)
    await m.answer(settings_text(u), reply_markup=settings_kb(u))


@router.callback_query(F.data.regexp(r"^hr:\d+$"))
async def on_hour(cb: CallbackQuery):
    u = get_user(cb.from_user)
    u["hour"] = int(cb.data.split(":")[1])
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


# ============================ записки и сообщения от администратора ============================
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


@router.message(Command("diag"))
async def cmd_diag(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    await m.answer("🔧 Проверяю переводчики…")
    lines = await translator.diagnose()
    note = "" if translator.GAS_URL else "\n\n<i>GAS_URL не задан — свой переводчик на Google Apps Script не подключён.</i>"
    await m.answer("🔧 <b>Диагностика перевода</b>\n\n" + "\n".join(esc(x) for x in lines) + note)


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
        detail = f"\n\n<code>{esc(str(e)[:600])}</code>" if uid == ADMIN_ID else ""
        await m.answer("Онлайн-переводчики сейчас перегружены 😕 Подожди минутку и отправь слово ещё раз. "
                       f"Слова из моего словаря я перевожу и без интернета.{detail}")
        return
    if "verb" in r:
        v = r["verb"]
        SPEECH[uid] = f"{v['v1']}, {v['v2']}, {v['v3']}. " + " ".join(v["en"])
        await m.answer(verb_card(v), reply_markup=kb([("🔊 Озвучить", "say"), ("📘 К группе", f"vg:{v['g']}")]))
        return
    SPEECH[uid] = r["en"].removeprefix("to ")
    buttons = [("🔊 Озвучить", "say")]
    if len(r["en"].split()) <= 3:
        PENDING[uid] = r
        buttons.append(("➕ В словарь", "addp"))
    out = f"🇺🇸 <b>{esc(r['en'])}</b>"
    if r.get("label"):
        out += f" <i>({esc(r['label'])})</i>"
    out += f"\n🇷🇺 {esc(r['ru'])}"
    if r.get("ex"):
        out += f"\n<i>{esc(r['ex'])}</i>"
    if r.get("note"):
        out += f"\n💬 {esc(r['note'])}"
    if active:
        out += "\n\n<i>Упражнение ещё активно: ответь по-английски или нажми «🚪 Выйти».</i>"
    await m.answer(out, reply_markup=kb(buttons))


@router.message(F.text.startswith("/"))
async def unknown_command(m: Message):
    await m.answer("Не знаю такой команды 🙈 Вот что есть: /menu — главное меню, /help — список возможностей.",
                   reply_markup=main_kb(m.from_user.id))


# ============================ рассылка по расписанию ============================
DEFAULT_REMINDER = "⏰ Время для английского! Сегодня ещё не было практики. Хватит пяти минут 💪"


async def tick(bot: Bot) -> dict:
    """Вызывается внешним cron-ом: «слово дня», напоминания, назначенные админом, и дайджест для админа."""
    res = {"daily": 0, "reminders": 0, "digests": 0, "errors": 0}
    for u in list(store.users.values()):
        try:
            now = now_of(u)
            today = now.date().isoformat()
            if u["daily"] and now.hour >= u["hour"] and u["last_daily"] != today:
                u["last_daily"] = today                      # ставим сразу — защита от двойных вызовов
                store.mark("users")
                note = random.choice(store.notes) if store.notes and random.random() < 0.5 else None
                await deliver_words(bot, u, f"☀️ <b>Слова дня</b> · <i>{BRAND}</i> 💖", note)
                res["daily"] += 1
            if (u["rem_hour"] and u["uid"] != ADMIN_ID and now.hour >= u["rem_hour"] and now.hour < 23
                    and u["last_rem"] != today and u["last_active"] != today):
                u["last_rem"] = today
                store.mark("users")
                text = esc(u["rem_text"]) if u["rem_text"] else DEFAULT_REMINDER
                await bot.send_message(u["uid"], text, reply_markup=kb([("🧠 Начать квиз", "goquiz")]))
                res["reminders"] += 1
            if u["digest"] and u["uid"] == ADMIN_ID and now.hour >= 21 and u["last_digest"] != today:
                u["last_digest"] = today
                store.mark("users")
                import admin_h
                await bot.send_message(u["uid"], admin_h.digest_text())
                res["digests"] += 1
        except TelegramForbiddenError:
            log.info("user %s заблокировал бота", u["uid"])
        except Exception:
            log.exception("tick failed for %s", u["uid"])
            res["errors"] += 1
    await store.flush()
    return res
