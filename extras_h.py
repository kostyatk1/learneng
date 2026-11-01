"""Диалоги (сценарии), аудирование и мини-грамматика. Всё бесплатно и без нейросетей."""
import random
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import content
import handlers as h

router = Router()          # подключается в main.py раньше основного роутера
BASE = Path(__file__).parent


def _rows(name):
    for line in (BASE / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            yield line


def _load_dialogs():
    out, cur = [], None
    for line in _rows("dialogs.txt"):
        if line.startswith("#"):
            cur = {"title": line[1:].strip(), "steps": []}
            out.append(cur)
            continue
        tag, _, rest = line.partition("|")
        p = rest.split("|")
        if tag == "B":
            cur["steps"].append({"en": p[0], "ru": p[1], "you": None})
        elif tag == "Y":
            cur["steps"][-1]["you"] = {"ok": p[0], "ru": p[1], "bad": p[2:4]}
    return out


def _load_grammar():
    out, cur = [], None
    for line in _rows("grammar.txt"):
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


DIALOGS = _load_dialogs()
GRAMMAR = _load_grammar()
LMODE: dict[int, str] = {}


# ============================ 🎭 диалоги ============================
def dialogs_kb():
    return h.kb(*[[(d["title"], f"dg:{i}")] for i, d in enumerate(DIALOGS)])


@router.message(Command("dialogs"))
@router.message(F.text == h.MENU["dialogs"])
async def cmd_dialogs(m: Message):
    h.get_user(m.from_user)
    h.S.pop(m.from_user.id, None)
    await m.answer("🎭 <b>Диалоги</b>: ты играешь одну из сторон, а я — собеседника. "
                   "На каждую реплику выбери лучший ответ по-английски. "
                   "Русский перевод реплики спрятан: нажми на него, чтобы подсмотреть.\n\n"
                   "<i>Сценарии вымышленные, учебные.</i>", reply_markup=dialogs_kb())


@router.callback_query(F.data == "dm")
async def on_dialogs_menu(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer("🎭 Выбери диалог:", reply_markup=dialogs_kb())


async def send_step(bot: Bot, u: dict, s: dict):
    d = DIALOGS[s["d"]]
    uid = u["uid"]
    if s["s"] < len(d["steps"]):
        st = d["steps"][s["s"]]
        head = (f"🎭 <b>{h.esc(d['title'])}</b> · {s['s'] + 1}/{len(d['steps'])}\n\n💬 {h.esc(st['en'])}\n"
                f"<tg-spoiler>{h.esc(st['ru'])}</tg-spoiler>")
        h.SPEECH[uid] = st["en"]
    else:                                                   # диалог закончился репликой пользователя
        st = {"you": None}
        head = f"🎭 <b>{h.esc(d['title'])}</b>"
    if not st["you"]:                                       # финал диалога
        h.S.pop(uid, None)
        total = s["total"]
        bonus = ""
        if total:
            lines, gain = h.register_answer(u, s["score"] == total, 20, "phrase") if s["score"] == total else ([], 0)
            bonus = (f"\n\n🏆 Идеально! Бонус +{gain} XP" + ("\n" + "\n".join(lines) if lines else "")) if gain else ""
        await bot.send_message(uid, f"{head}\n\n🎉 <b>Диалог пройден!</b> Верных ответов: {s['score']} из {total}.{bonus}",
                               reply_markup=h.kb([("🔊", "say")], [("🎭 Другой диалог", "dm")]))
        return
    opts = [(st["you"]["ok"], True)] + [(b, False) for b in st["you"]["bad"]]
    random.shuffle(opts)
    s["opts"] = opts
    s["t"] = __import__("time").time()
    rows = [[(o[0], f"dc:{i}")] for i, o in enumerate(opts)] + [[("🔊", "say"), ("🚪 Выйти", "exit")]]
    await bot.send_message(uid, head + "\n\n<i>Как ответишь?</i>", reply_markup=h.kb(*rows))


@router.callback_query(F.data.startswith("dg:"))
async def on_dialog_start(cb: CallbackQuery, bot: Bot):
    u = h.get_user(cb.from_user)
    await cb.answer()
    h.set_session(u["uid"], type="dlg", d=int(cb.data[3:]), s=0, score=0, total=0)
    await send_step(bot, u, h.S[u["uid"]])


@router.callback_query(F.data.startswith("dc:"))
async def on_dialog_choice(cb: CallbackQuery, bot: Bot):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"dlg"})
    if not s or "opts" not in s:
        await cb.answer("Этот диалог уже неактуален — выбери его заново")
        return
    text, ok = s["opts"][int(cb.data[3:])]
    st = DIALOGS[s["d"]]["steps"][s["s"]]
    s["total"] += 1
    s["score"] += ok
    lines, gain = h.register_answer(u, ok, 8, "phrase")
    mark = f"✅ <b>{h.mot.praise(True)}</b> +{gain} XP" if ok else f"❌ Лучше так: <b>{h.esc(st['you']['ok'])}</b>"
    body = f"{mark}\n\n🗣 {h.esc(st['you']['ok'])}\n🇷🇺 <i>{h.esc(st['you']['ru'])}</i>"
    if lines:
        body += "\n\n" + "\n".join(lines)
    await h.safe_edit(cb, body, h.kb([("🔊", "say")], home=False))
    await cb.answer()
    h.SPEECH[u["uid"]] = st["you"]["ok"]
    s["s"] += 1
    s.pop("opts", None)
    await send_step(bot, u, s)


# ============================ 🎧 аудирование ============================
@router.message(Command("listen"))
@router.message(F.text == h.MENU["listen"])
async def cmd_listen(m: Message):
    h.get_user(m.from_user)
    await m.answer("🎧 <b>Аудирование</b>: я говорю по-американски, а ты выбираешь верный вариант.\n\n"
                   "🔤 <b>Слова</b> — что за слово прозвучало?\n💬 <b>Фразы</b> — что значит фраза?",
                   reply_markup=h.kb([("🔤 Слова", "lm:w"), ("💬 Фразы", "lm:p")]))


async def ask_listen(bot: Bot, u: dict):
    uid = u["uid"]
    mode = LMODE.get(uid, "w")
    if mode == "w":
        mine = [w for w in h.store.words_of(uid) if w["kind"] != "slang"]
        pool = mine if len(mine) >= 4 else content.ALL_WORDS
        w = random.choice(pool)
        wrong = random.sample([x["en"] for x in content.ALL_WORDS if x["en"] != w["en"]], 3)
        correct, spoken, opts_src = w["en"], w["en"], wrong + [w["en"]]
        info, q = f"<b>{h.esc(w['en'])}</b> — {h.esc(w['ru'])}", "🎧 Какое слово ты слышишь? 👇"
    else:
        p = random.choice(content.PHRASES)
        wrong = random.sample([x["ru"] for x in content.PHRASES if x["ru"] != p["ru"]], 3)
        correct, spoken, opts_src = p["ru"], p["en"][0], wrong + [p["ru"]]
        info, q = f"<b>{h.esc(p['en'][0])}</b>\n🇷🇺 {h.esc(p['ru'])}", "🎧 Что значит эта фраза? 👇"
    random.shuffle(opts_src)
    h.set_session(uid, type="lis", ans=opts_src.index(correct), opts=opts_src, info=info, mode=mode)
    h.SPEECH[uid] = spoken
    await h.speak(bot, uid, spoken)
    rows = [[(o, f"la:{i}")] for i, o in enumerate(opts_src)] + [[("🔁 Послушать ещё раз", "say")]]
    await bot.send_message(uid, q, reply_markup=h.kb(*rows))


@router.callback_query(F.data.startswith("lm:"))
async def on_listen_mode(cb: CallbackQuery, bot: Bot):
    LMODE[cb.from_user.id] = cb.data[3:]
    await cb.answer()
    await ask_listen(bot, h.get_user(cb.from_user))


@router.callback_query(F.data == "ln")
async def on_listen_next(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await ask_listen(bot, h.get_user(cb.from_user))


@router.callback_query(F.data.startswith("la:"))
async def on_listen_answer(cb: CallbackQuery):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"lis"})
    if not s:
        await cb.answer("Это задание уже неактуально — жми «Аудирование»")
        return
    h.S.pop(u["uid"], None)
    ok = int(cb.data[3:]) == s["ans"]
    lines, gain = h.register_answer(u, ok, 10, "word" if s["mode"] == "w" else "phrase")
    head = f"✅ <b>{h.mot.praise(True)}</b> +{gain} XP" if ok else f"❌ <b>{h.mot.praise(False)}</b> Правильно: <b>{h.esc(s['opts'][s['ans']])}</b>"
    text = f"{head}\n\n{s['info']}" + ("\n\n" + "\n".join(lines) if lines else "")
    await h.safe_edit(cb, text, h.kb([("🔊 Ещё раз", "say"), ("▶️ Дальше", "ln")]))
    await cb.answer()


# ============================ 📐 грамматика ============================
def grammar_kb():
    return h.kb(*[[(g["title"], f"gr:{i}")] for i, g in enumerate(GRAMMAR)])


@router.message(Command("grammar"))
@router.message(F.text == h.MENU["grammar"])
async def cmd_grammar(m: Message):
    h.get_user(m.from_user)
    h.S.pop(m.from_user.id, None)
    await m.answer("📐 <b>Мини-грамматика</b>: короткая теория и 5 упражнений на каждую тему 👇", reply_markup=grammar_kb())


@router.callback_query(F.data == "gm")
async def on_grammar_menu(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer("📐 Выбери тему:", reply_markup=grammar_kb())


@router.callback_query(F.data.startswith("gr:"))
async def on_grammar_topic(cb: CallbackQuery):
    i = int(cb.data[3:])
    g = GRAMMAR[i]
    await h.safe_edit(cb, f"<b>{h.esc(g['title'])}</b>\n\n" + "\n\n".join(g["theory"]),
                      h.kb([("✏️ Упражнения", f"gx:{i}")], [("⬅️ К темам", "gm")]))
    await cb.answer()


async def send_gq(bot: Bot, u: dict, s: dict):
    g = GRAMMAR[s["g"]]
    q = g["qs"][s["order"][s["pos"]]]
    opts = [(q["ok"], True)] + [(b, False) for b in q["bad"]]
    random.shuffle(opts)
    s["opts"], s["t"] = opts, __import__("time").time()
    rows = [[(o[0], f"gq:{i}")] for i, o in enumerate(opts)]
    await bot.send_message(u["uid"], f"📐 <b>{h.esc(g['title'])}</b> · {s['pos'] + 1}/{len(s['order'])}\n\n<b>{h.esc(q['q'])}</b>",
                           reply_markup=h.kb(*rows, [("🚪 Выйти", "exit")]))


@router.callback_query(F.data.startswith("gx:"))
async def on_grammar_start(cb: CallbackQuery, bot: Bot):
    u = h.get_user(cb.from_user)
    i = int(cb.data[3:])
    order = list(range(len(GRAMMAR[i]["qs"])))
    random.shuffle(order)
    await cb.answer()
    h.set_session(u["uid"], type="gq", g=i, order=order, pos=0, score=0)
    await send_gq(bot, u, h.S[u["uid"]])


@router.callback_query(F.data.startswith("gq:"))
async def on_grammar_answer(cb: CallbackQuery):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"gq"})
    if not s or "opts" not in s:
        await cb.answer("Это упражнение уже неактуально — выбери тему заново")
        return
    q = GRAMMAR[s["g"]]["qs"][s["order"][s["pos"]]]
    text, ok = s["opts"][int(cb.data[3:])]
    s["score"] += ok
    s.pop("opts")
    lines, gain = h.register_answer(u, ok, 8, "phrase")
    done = q["q"].replace("___", f"<b>{h.esc(q['ok'])}</b>") if "___" in q["q"] else h.esc(q["q"])
    head = f"✅ <b>{h.mot.praise(True)}</b> +{gain} XP" if ok else f"❌ Правильно: <b>{h.esc(q['ok'])}</b>"
    body = f"{head}\n\n{done}\n💡 <i>{h.esc(q['why'])}</i>" + ("\n\n" + "\n".join(lines) if lines else "")
    s["pos"] += 1
    if s["pos"] >= len(s["order"]):
        h.S.pop(u["uid"], None)
        body += f"\n\n🎉 <b>Тема пройдена!</b> Верно: {s['score']} из {len(s['order'])}."
        await h.safe_edit(cb, body, h.kb([("🔁 Ещё раз", f"gx:{s['g']}")], [("📐 К темам", "gm")]))
    else:
        await h.safe_edit(cb, body, h.kb([("▶️ Дальше", "gn")], home=False))
    await cb.answer()


@router.callback_query(F.data == "gn")
async def on_grammar_next(cb: CallbackQuery, bot: Bot):
    u = h.get_user(cb.from_user)
    s = h.get_session(u["uid"], {"gq"})
    await cb.answer()
    if s:
        await send_gq(bot, u, s)
