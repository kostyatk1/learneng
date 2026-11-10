"""Раздел «🗣 Бытовые предложения»: короткие разговорные фразы по всем темам.
Под каждой: транскрипция русскими буквами, перевод под спойлером, озвучка по кнопке."""
import random

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import content
import handlers as h

router = Router()          # подключается в main.py раньше основного роутера
PER_PAGE = 6
NUMS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
RAND: dict[int, list] = {}


def items_of(cat: int) -> list[dict]:
    return [e for e in content.EVERYDAY if e["cat"] == cat]


def menu_kb():
    rows = [[(f"{t} · {len(items_of(i))}", f"dy:c:{i}:0")] for i, t in enumerate(content.EVERYDAY_CATS)]
    rows.append([("🎲 Случайные 6 фраз", "dy:r")])
    return h.kb(*rows)


MENU_TEXT = (f"🗣 <b>Бытовые предложения</b> · {len(content.EVERYDAY)} коротких фраз на живом американском английском "
             f"(с разговорными <i>gonna, wanna, gotta</i> и сленгом).\n\n"
             "Под каждой фразой: <i>[транскрипция русскими буквами]</i>, перевод под серым блоком (нажми, чтобы открыть) "
             "и кнопка 🔊 с озвучкой.\n\nВыбери тему 👇")


def render(items: list[dict], title: str) -> str:
    lines = [title, "<i>Нажми на серый блок, чтобы увидеть перевод</i>\n"]
    for k, e in enumerate(items):
        tr = f"\n   <i>[{h.esc(e['tr'])}]</i>" if e["tr"] else ""
        lines.append(f"{NUMS[k]} <b>{h.esc(e['en'])}</b>{tr}\n   🇷🇺 <tg-spoiler>{h.esc(e['ru'])}</tg-spoiler>\n")
    return "\n".join(lines)


def audio_row(n: int, cat: int, page: int):
    return [(f"🔊{k + 1}", f"dy:a:{cat}:{page}:{k}") for k in range(n)]


@router.message(Command("daily"))
@router.message(F.text == h.MENU["daily"])
async def cmd_daily(m: Message):
    h.get_user(m.from_user)
    h.S.pop(m.from_user.id, None)
    await m.answer(MENU_TEXT, reply_markup=menu_kb())


async def show_page(cb: CallbackQuery, cat: int, page: int):
    its = items_of(cat)
    pages = max(1, (len(its) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = its[page * PER_PAGE:(page + 1) * PER_PAGE]
    nav = []
    if page > 0:
        nav.append(("⬅️", f"dy:c:{cat}:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(("➡️", f"dy:c:{cat}:{page + 1}"))
    h.SPEECH[cb.from_user.id] = " ... ".join(e["en"] for e in chunk)
    text = render(chunk, f"🗣 <b>{h.esc(content.EVERYDAY_CATS[cat])}</b>")
    await h.safe_edit(cb, text, h.kb(audio_row(len(chunk), cat, page), nav,
                                     [("🔊 Всю страницу", f"dy:p:{cat}:{page}"), ("✍️ Потренировать", "dy:t")],
                                     [("⬅️ К темам", "dy:m")]))


@router.callback_query(F.data.startswith("dy:"))
async def on_daily(cb: CallbackQuery, bot: Bot):
    parts = cb.data.split(":")
    act, uid = parts[1], cb.from_user.id
    if act == "m":
        await h.safe_edit(cb, MENU_TEXT, menu_kb())
        await cb.answer()
    elif act == "c":
        await show_page(cb, int(parts[2]), int(parts[3]))
        await cb.answer()
    elif act == "r":
        pool = random.sample(content.EVERYDAY, PER_PAGE)
        RAND[uid] = pool
        h.SPEECH[uid] = " ... ".join(e["en"] for e in pool)
        await h.safe_edit(cb, render(pool, "🎲 <b>Случайные фразы</b>"),
                          h.kb(audio_row(len(pool), -1, 0), [("🔊 Все подряд", "dy:p:-1:0"), ("🎲 Ещё 6", "dy:r")],
                               [("✍️ Потренировать", "dy:t"), ("⬅️ К темам", "dy:m")]))
        await cb.answer()
    elif act == "a":
        cat, page, k = int(parts[2]), int(parts[3]), int(parts[4])
        if cat == -1:
            lst = RAND.get(uid, [])
            e = lst[k] if k < len(lst) else None
        else:
            its = items_of(cat)[page * PER_PAGE:(page + 1) * PER_PAGE]
            e = its[k] if k < len(its) else None
        await cb.answer()
        if e:
            h.SPEECH[uid] = e["en"]
            await h.speak(bot, uid, e["en"])
    elif act == "p":                                       # озвучить всю страницу / все случайные
        cat, page = int(parts[2]), int(parts[3])
        lst = RAND.get(uid, []) if cat == -1 else items_of(cat)[page * PER_PAGE:(page + 1) * PER_PAGE]
        await cb.answer()
        if lst:
            await h.speak(bot, uid, " ... ".join(e["en"] for e in lst))
    elif act == "t":                                       # тренировка: переведи по памяти (категория «Бытовые предложения»)
        await cb.answer()
        h.PH_CAT[uid] = len(content.PHRASE_CATS) - 1
        await h.ask_phrase(bot, h.get_user(cb.from_user))
