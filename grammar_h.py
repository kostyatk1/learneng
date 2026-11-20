"""Мини-грамматика: Present Simple, Present Continuous, Past Simple, Past Continuous (теория + упражнения)."""
import random
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import handlers as h

router = Router()          # подключается в main.py раньше основного роутера
BASE = Path(__file__).parent


def _load():
    out, cur = [], None
    for line in (BASE / "grammar.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("#"):
            cur = {"title": line[1:].strip(), "theory": [], "qs": []}
            out.append(cur)
            continue
        tag, _, rest = line.partition("|")
        if tag == "T":
            cur["theory"].append(rest)
        elif tag == "Q":
            p = rest.split("|")
            cur["qs"].append({"q": p[0], "ok": p[1], "bad": p[2:4], "why": p[4]})
    return out


GRAMMAR = _load()


def topics_kb():
    return h.kb(*[[(g["title"], f"gr:{i}")] for i, g in enumerate(GRAMMAR)])


@router.message(Command("grammar"))
@router.message(F.text == h.MENU["grammar"])
async def cmd_grammar(m: Message):
    h.get_user(m.from_user)
    h.S.pop(m.from_user.id, None)
    await m.answer("📐 <b>Грамматика</b>: четыре времени — короткая теория и 8 упражнений на каждое 👇", reply_markup=topics_kb())


@router.callback_query(F.data == "gm")
async def on_menu(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer("📐 Выбери время:", reply_markup=topics_kb())


@router.callback_query(F.data.startswith("gr:"))
async def on_topic(cb: CallbackQuery):
    i = int(cb.data[3:])
    g = GRAMMAR[i]
    await h.safe_edit(cb, f"<b>{h.esc(g['title'])}</b>\n\n" + "\n\n".join(g["theory"]),
                      h.kb([("✏️ Упражнения", f"gx:{i}")], [("⬅️ К временам", "gm")]))
    await cb.answer()


async def send_q(bot: Bot, u: dict, s: dict):
    g = GRAMMAR[s["g"]]
    q = g["qs"][s["order"][s["pos"]]]
    opts = [(q["ok"], True)] + [(b, False) for b in q["bad"]]
    random.shuffle(opts)
    s["opts"], s["t"] = opts, time.time()
    rows = [[(o[0], f"gq:{i}")] for i, o in enumerate(opts)]
    await bot.send_message(u["uid"], f"📐 <b>{h.esc(g['title'])}</b> · {s['pos'] + 1}/{len(s['order'])}\n\n<b>{h.esc(q['q'])}</b>",
                           reply_markup=h.kb(*rows, [("🚪 Выйти", "exit")]))


@router.callback_query(F.data.startswith("gx:"))
async def on_start(cb: CallbackQuery, bot: Bot):
    u = h.get_user(cb.from_user)
    i = int(cb.data[3:])
    order = list(range(len(GRAMMAR[i]["qs"])))
    random.shuffle(order)
    await cb.answer()
    h.set_session(u["uid"], type="gq", g=i, order=order, pos=0, score=0)
    await send_q(bot, u, h.S[u["uid"]])


@router.callback_query(F.data.startswith("gq:"))
async def on_answer(cb: CallbackQuery):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"gq"})
    if not s or "opts" not in s:
        await cb.answer("Это упражнение уже неактуально — выбери время заново")
        return
    q = GRAMMAR[s["g"]]["qs"][s["order"][s["pos"]]]
    text, ok = s["opts"][int(cb.data[3:])]
    s["score"] += ok
    s.pop("opts")
    h.register_answer(u, ok)
    done = q["q"].replace("___", f"<b>{h.esc(q['ok'])}</b>")
    head = f"✅ <b>{h.praise(True)}</b>" if ok else f"❌ Правильно: <b>{h.esc(q['ok'])}</b>"
    body = f"{head}\n\n{done}\n💡 <i>{h.esc(q['why'])}</i>"
    s["pos"] += 1
    if s["pos"] >= len(s["order"]):
        h.S.pop(u["uid"], None)
        body += f"\n\n🎉 <b>Тема пройдена!</b> Верно: {s['score']} из {len(s['order'])}."
        await h.safe_edit(cb, body, h.kb([("🔁 Ещё раз", f"gx:{s['g']}")], [("📐 К временам", "gm")]))
    else:
        await h.safe_edit(cb, body, h.kb([("▶️ Дальше", "gn")], home=False))
    await cb.answer()


@router.callback_query(F.data == "gn")
async def on_next(cb: CallbackQuery, bot: Bot):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"gq"})
    await cb.answer()
    if s:
        await send_q(bot, u, s)
