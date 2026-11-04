"""Админ-раздел (только для ADMIN_ID): статистика ученицы, управление обучением, напоминания, записки, дайджест."""
import logging
from datetime import date

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import content
import handlers as h
import motivation as mot
from storage import LEARNED_BOX, live_streak

log = logging.getLogger("admin")
router = Router()
SEL: dict[int, int] = {}                      # какой ученик выбран у админа

PRESETS = ["⏰ Время для английского! Пять минут — и цель дня будет ближе 💪",
           "🔥 Не дай серии прерваться — зайди на пару вопросов!",
           "Ты молодец! Давай повторим пару слов? 💖",
           "🐾 Тёма, Беляшка и Чернушка просят: позанимайся английским!"]


def is_admin(uid: int) -> bool:
    return bool(h.ADMIN_ID) and uid == h.ADMIN_ID


def students() -> list[dict]:
    us = [u for u in h.store.users.values() if u["uid"] != h.ADMIN_ID]
    return sorted(us, key=lambda u: u["last_active"], reverse=True)


def current(adm: int):
    st = students()
    if SEL.get(adm) in {u["uid"] for u in st}:
        return h.store.users[SEL[adm]]
    return st[0] if len(st) == 1 else None


def name(u: dict) -> str:
    return h.esc(u["name"] or u["uid"])


def ago(u: dict) -> str:
    if not u["last_active"]:
        return "ещё не занимался(ась)"
    try:
        d = (h.today_of(u) - date.fromisoformat(u["last_active"])).days
    except ValueError:
        return u["last_active"]
    return "сегодня" if d == 0 else ("вчера" if d == 1 else f"{d} дн. назад")


def acc(u: dict) -> str:
    t = u["correct"] + u["wrong"]
    return f"{round(100 * u['correct'] / t)}% ({u['correct']}/{t})" if t else "нет данных"


def brief(u: dict) -> str:
    c = h.build_ctx(u)
    today = h.today_of(u)
    n = u["today_n"] if u["today_date"] == today.isoformat() else 0
    return (f"👤 <b>{name(u)}</b>\n"
            f"   ⭐ {mot.level_name(u['xp'])} · {u['xp']} XP · 🔥 серия {live_streak(u, today)} (рекорд {u['best_streak']})\n"
            f"   🎯 сегодня {n}/{u['goal'] or 10} · точность {acc(u)}\n"
            f"   📚 выучено слов {c['words']} из {c['total']} · 📘 глаголов {c['verbs']}/{len(content.VERBS)} · 😎 сленг {c['slang']}\n"
            f"   🕒 последнее занятие: {ago(u)}")


def digest_text() -> str:
    st = students()
    if not st:
        return "📬 Итоги дня: пока нет учеников."
    parts = ["📬 <b>Итоги дня</b>\n"]
    for u in st:
        today = h.today_of(u)
        n = u["today_n"] if u["today_date"] == today.isoformat() else 0
        goal = u["goal"] or 10
        parts.append(f"👤 {name(u)}: ответов {n}/{goal} {'✅' if n >= goal else ('⏳' if n else '⚠️ не занималась')} · "
                     f"🔥 {live_streak(u, today)} дн. · точность {acc(u)} · {u['xp']} XP")
    return "\n".join(parts)


# ============================ экраны ============================
def panel():
    st = students()
    head = "🛠 <b>Админ-панель</b>\n\n"
    if not st:
        return head + "Пока нет учеников. Пусть жена напишет боту /start.", h.kb()
    sel = current(h.ADMIN_ID)
    who = f"Выбрана: <b>{name(sel)}</b>" if sel else "Ученик не выбран — открой «Карточку»."
    me = h.store.users.get(h.ADMIN_ID, {})
    dg = "вкл" if me.get("digest") else "выкл"
    return (head + f"Учеников: {len(st)}. {who}\n\nСтатистика, настройка обучения, напоминания и сообщения — всё здесь.",
            h.kb([("📊 Статистика всех", "ad:stats"), ("👤 Карточка ученика", "ad:card")],
                 [("🎯 Управление обучением", "ad:manage")],
                 [("⏰ Напоминания", "ad:rem"), ("📨 Сообщения", "ad:msg")],
                 [("💌 Записки", "ad:notes"), (f"📬 Дайджест: {dg}", "ad:digest")]))


def pick_screen(target: str):
    rows = [[(f"{u['name'] or u['uid']} · {mot.level_name(u['xp'])}", f"ad:u:{u['uid']}:{target}")] for u in students()]
    return "👥 Выбери ученика:", h.kb(*rows, [("⬅️ В админ-панель", "ad:home")])


def stats_screen():
    st = students()
    body = "\n\n".join(brief(u) for u in st) or "Пока нет учеников."
    return "📊 <b>Статистика</b>\n\n" + body, h.kb([("👤 Подробная карточка", "ad:card")], [("⬅️ В админ-панель", "ad:home")])


def card_screen(u: dict):
    today = h.today_of(u)
    hard = sorted([w for w in h.store.words_of(u["uid"]) if w["wrong"] > 0], key=lambda w: w["wrong"], reverse=True)[:6]
    hard_txt = "\n".join(f"   • {h.esc(w['en'])} — {h.esc(w['ru'])} (ошибок {w['wrong']})" for w in hard) or "   пока нет ошибок 🎉"
    topics = ", ".join(content.TOPICS[i] for i in h.topics_of(u)) or "все"
    due = sum(w["next_review"] <= today.isoformat() for w in h.store.words_of(u["uid"]))
    text = (f"👤 <b>Карточка: {name(u)}</b>\n\n{brief(u)}\n\n"
            f"🏅 Значков: {len(mot.owned(u))}/{len(mot.BADGES)} · 🧊 заморозок: {u['freezes']} · ⚡ рекорд комбо: {u['best_combo']}\n"
            f"🔁 На повторе сегодня: {due} · 🏁 Дней с выполненной целью: {u['goal_days']}\n"
            f"📅 {mot.week_chart(u, today)}\n\n"
            f"⚠️ <b>Сложные слова</b>\n{hard_txt}\n\n"
            f"⚙️ Настройки: слов за раз {u['per_day']}, цель {u['goal'] or 10}, "
            f"слово дня {'вкл с ' + str(u['hour']) + ':00' if u['daily'] else 'выкл'}, пояс {h.esc(u['tz'])}\n"
            f"🗂 Темы: {h.esc(topics)}\n"
            f"⏰ Напоминание: {('с ' + str(u['rem_hour']) + ':00') if u['rem_hour'] else 'выкл'}")
    return text, h.kb([("🎯 Управлять обучением", "ad:manage"), ("⏰ Напоминание", "ad:rem")],
                      [("👥 Другой ученик", "ad:pick:card")], [("⬅️ В админ-панель", "ad:home")])


def manage_screen(u: dict):
    mark = lambda cur, v: ("• " if cur == v else "") + str(v)
    goal = u["goal"] or 10
    text = (f"🎯 <b>Управление обучением: {name(u)}</b>\n\n"
            f"Цель дня: <b>{goal}</b> · слов за раз: <b>{u['per_day']}</b> · слово дня: "
            f"<b>{'вкл, с ' + str(u['hour']) + ':00' if u['daily'] else 'выкл'}</b>\n\n"
            "Можно также добавить слова командой <code>/assign слово - перевод</code> — они сразу появятся в её квизе, "
            "и бот сообщит ей об этом.")
    return text, h.kb([("Цель дня:", "noop")] + [(mark(goal, n), f"ad:g:{n}") for n in (5, 10, 20, 30)],
                      [("Слов за раз:", "noop")] + [(mark(u["per_day"], n), f"ad:p:{n}") for n in (3, 5, 8, 10)],
                      [(f"☀️ Слово дня: {'вкл' if u['daily'] else 'выкл'}", "ad:dl")],
                      [("Час слова дня:", "noop")] + [(mark(u["hour"], x), f"ad:h:{x}") for x in (8, 9, 12, 18)],
                      [("🗂 Темы слов", "ad:tp"), ("🔁 Всё на повтор сегодня", "ad:due")],
                      [("🎁 +50 XP", "ad:xp"), ("🧊 +1 заморозка", "ad:fz")],
                      [("👥 Другой ученик", "ad:pick:manage"), ("⬅️ В админ-панель", "ad:home")])


def topics_screen(u: dict):
    chosen = set(h.topics_of(u))
    rows = [[(("✅ " if i in chosen else "⬜ ") + t, f"ad:t:{i}")] for i, t in enumerate(content.TOPICS)]
    return f"🗂 <b>Темы слов для {name(u)}</b> (пусто = все темы):", h.kb(*rows, [("⬅️ Назад", "ad:manage")])


def rem_screen(u: dict):
    mark = lambda cur, v: ("• " if cur == v else "") + (f"{v}:00" if v else "выкл")
    txt = u["rem_text"] or "стандартный (с упоминанием серии)"
    text = (f"⏰ <b>Напоминания: {name(u)}</b>\n\n"
            f"<b>По расписанию:</b> {('каждый день с ' + str(u['rem_hour']) + ':00, если ещё не занималась') if u['rem_hour'] else 'выключено'}\n"
            f"<b>Текст:</b> {h.esc(txt)}\n\n"
            "• «Отправить сейчас» — мягкий пинок прямо сейчас.\n"
            "• Свой текст: <code>/remind текст</code> — отправить сразу; <code>/remtext текст</code> — задать текст для расписания "
            "(<code>/remtext</code> без текста вернёт стандартный).\n\n"
            "<i>Чтобы расписание срабатывало, на cron-job.org должна быть задача на /cron/tick около выбранного часа.</i>")
    return text, h.kb([("📤 Отправить сейчас", "ad:nudge")],
                      [(f"💬 {i + 1}", f"ad:np:{i}") for i in range(len(PRESETS))],
                      [("Час:", "noop")] + [(mark(u["rem_hour"], x), f"ad:rh:{x}") for x in (0, 12, 18, 20, 21)],
                      [("👥 Другой ученик", "ad:pick:rem"), ("⬅️ В админ-панель", "ad:home")])


def msg_screen():
    return ("📨 <b>Сообщения</b>\n\n<code>/msg текст</code> — сразу всем ученикам\n"
            "<code>/remind текст</code> — выбранному ученику, с кнопкой «Начать квиз»\n"
            "<code>/assign слово - перевод</code> — добавить слово в её квиз\n\nПримеры быстрых текстов — в разделе «Напоминания».",
            h.kb([("⏰ Напоминания", "ad:rem")], [("⬅️ В админ-панель", "ad:home")]))


def notes_screen():
    ns = h.store.notes
    lst = "\n".join(f"{i + 1}. {h.esc(n)}" for i, n in enumerate(ns)) or "Пока пусто."
    return (f"💌 <b>Записки</b> ({len(ns)})\nБот иногда добавляет их к «слову дня».\n\n{lst}\n\n"
            "<code>/note текст</code> — добавить · <code>/delnote 2</code> — удалить №2",
            h.kb([("⬅️ В админ-панель", "ad:home")]))


# ============================ вход ============================
@router.message(Command("admin"))
@router.message(F.text == h.MENU["admin"])
async def cmd_admin(m: Message):
    if not is_admin(m.from_user.id):
        return
    h.get_user(m.from_user)
    t, k = panel()
    await m.answer(t, reply_markup=k)


async def student_or_pick(cb: CallbackQuery, target: str):
    u = current(cb.from_user.id)
    if u is None:
        t, k = pick_screen(target)
        await h.safe_edit(cb, t, k)
    return u


@router.callback_query(F.data.startswith("ad:"))
async def on_admin(cb: CallbackQuery, bot: Bot):
    adm = cb.from_user.id
    if not is_admin(adm):
        await cb.answer("Нет доступа")
        return
    parts = cb.data.split(":")
    act = parts[1]
    screen = None

    if act == "home":
        screen = panel()
    elif act == "stats":
        screen = stats_screen()
    elif act == "pick":
        screen = pick_screen(parts[2])
    elif act == "u":                                        # выбор ученика: ad:u:<uid>:<куда>
        SEL[adm] = int(parts[2])
        u = h.store.users.get(SEL[adm])
        target = parts[3] if len(parts) > 3 else "card"
        screen = {"card": card_screen, "manage": manage_screen, "rem": rem_screen}[target](u)
    elif act == "msg":
        screen = msg_screen()
    elif act == "notes":
        screen = notes_screen()
    elif act == "digest":
        me = h.store.ensure_user(adm)
        me["digest"] = 0 if me["digest"] else 1
        h.store.mark("users")
        screen = panel()
        await cb.answer("Дайджест включён: вечером придёт сводка" if me["digest"] else "Дайджест выключен")
    else:
        u = await student_or_pick(cb, {"card": "card", "manage": "manage", "rem": "rem"}.get(act, "manage"))
        if u is None:
            await cb.answer()
            return
        h.store.mark("users")
        toast = None
        if act == "card":
            screen = card_screen(u)
        elif act == "manage":
            screen = manage_screen(u)
        elif act == "rem":
            screen = rem_screen(u)
        elif act == "g":
            u["goal"] = int(parts[2]); screen, toast = manage_screen(u), "Цель дня изменена"
        elif act == "p":
            u["per_day"] = int(parts[2]); screen, toast = manage_screen(u), "Готово"
        elif act == "h":
            u["hour"] = int(parts[2]); screen, toast = manage_screen(u), "Готово"
        elif act == "dl":
            u["daily"] = 0 if u["daily"] else 1; screen, toast = manage_screen(u), "Слово дня переключено"
        elif act == "tp":
            screen = topics_screen(u)
        elif act == "t":
            chosen = set(h.topics_of(u)) ^ {int(parts[2])}
            u["topics"] = ",".join(str(x) for x in sorted(chosen)); screen = topics_screen(u)
        elif act == "due":
            today = h.today_of(u).isoformat()
            for w in h.store.words_of(u["uid"]):
                w["next_review"] = today
            h.store.mark("words"); screen, toast = manage_screen(u), "Все её слова ждут повторения сегодня"
        elif act == "xp":
            u["xp"] += 50; screen, toast = manage_screen(u), "+50 XP отправлено"
            await _safe_send(bot, u["uid"], "🎁 Тебе подарок: <b>+50 XP</b>! Так держать 💖")
        elif act == "fz":
            u["freezes"] = min(u["freezes"] + 1, 3); screen, toast = manage_screen(u), "+1 заморозка"
            await _safe_send(bot, u["uid"], "🎁 Тебе подарок: <b>🧊 +1 заморозка серии</b>!")
        elif act == "rh":
            u["rem_hour"] = int(parts[2]); screen, toast = rem_screen(u), "Расписание обновлено"
        elif act == "nudge":
            ok = await send_nudge(bot, u, None); screen, toast = rem_screen(u), ("Отправлено ✅" if ok else "Не удалось отправить")
        elif act == "np":
            ok = await send_nudge(bot, u, PRESETS[int(parts[2])]); screen, toast = rem_screen(u), ("Отправлено ✅" if ok else "Не удалось отправить")
        if toast:
            await cb.answer(toast)
    if screen:
        await h.safe_edit(cb, *screen)
    try:
        await cb.answer()
    except Exception:
        pass


async def _safe_send(bot: Bot, uid: int, text: str, markup=None) -> bool:
    try:
        await bot.send_message(uid, text, reply_markup=markup)
        return True
    except Exception as e:
        log.warning("send to %s failed: %s", uid, e)
        return False


async def send_nudge(bot: Bot, u: dict, text: str | None) -> bool:
    streak = live_streak(u, h.today_of(u))
    body = text if text else h.DEFAULT_REMINDER.format(streak=f", и серия 🔥 {streak} дн. продолжится" if streak else "")
    return await _safe_send(bot, u["uid"], h.esc(body) if text else body, h.kb([("🧠 Начать квиз", "goquiz")]))


# ============================ команды ============================
def _arg(m: Message) -> str:
    return (m.text or "").partition(" ")[2].strip()


@router.message(Command("remind"))
async def cmd_remind(m: Message, bot: Bot):
    if not is_admin(m.from_user.id):
        return
    u = current(m.from_user.id)
    if u is None:
        await m.answer("Сначала выбери ученика: /admin → «Карточка ученика».")
        return
    text = _arg(m)
    if not text:
        await m.answer("Напиши так: <code>/remind Пора заниматься, зайка 💖</code>")
        return
    ok = await send_nudge(bot, u, text)
    await m.answer("Отправлено ✅" if ok else "Не удалось отправить (возможно, она заблокировала бота).")


@router.message(Command("remtext"))
async def cmd_remtext(m: Message):
    if not is_admin(m.from_user.id):
        return
    u = current(m.from_user.id)
    if u is None:
        await m.answer("Сначала выбери ученика: /admin → «Карточка ученика».")
        return
    u["rem_text"] = _arg(m)
    h.store.mark("users")
    await m.answer("Текст напоминания по расписанию обновлён ✅" if u["rem_text"] else "Вернул стандартный текст напоминания ✅")


@router.message(Command("assign"))
async def cmd_assign(m: Message, bot: Bot):
    if not is_admin(m.from_user.id):
        return
    u = current(m.from_user.id)
    if u is None:
        await m.answer("Сначала выбери ученика: /admin → «Карточка ученика».")
        return
    arg = _arg(m)
    import re
    parts = re.split(r"\s+[-—–=]\s+|\s*=\s*", arg, maxsplit=1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        await m.answer("Напиши так: <code>/assign apple - яблоко</code>")
        return
    a, b = parts[0].strip(), parts[1].strip()
    en, ru = (b, a) if content.has_cyrillic(a) else (a, b)
    if h.store.add_word(u["uid"], en, ru, content.BY_EN.get(en.lower(), {}).get("ex", ""), "", "admin", h.today_of(u)):
        await _safe_send(bot, u["uid"], f"🎁 Тебе добавили новое слово: <b>{h.esc(en)}</b> — {h.esc(ru)}\nОно уже в твоём квизе!",
                         h.kb([("🧠 Повторить", "goquiz")]))
        await m.answer(f"Добавлено ученику {name(u)}: {h.esc(en)} — {h.esc(ru)} ✅")
    else:
        await m.answer("Такое слово у неё уже есть.")


@router.message(Command("notes"))
async def cmd_notes(m: Message):
    if not is_admin(m.from_user.id):
        return
    t, k = notes_screen()
    await m.answer(t, reply_markup=k)


@router.message(Command("delnote"))
async def cmd_delnote(m: Message):
    if not is_admin(m.from_user.id):
        return
    arg = _arg(m)
    if not arg.isdigit() or not (1 <= int(arg) <= len(h.store.notes)):
        await m.answer("Напиши номер записки: <code>/delnote 2</code> (список — /notes)")
        return
    gone = h.store.notes.pop(int(arg) - 1)
    h.store.mark("notes")
    await m.answer(f"Удалено: {h.esc(gone)}")
