"""Система мотивации: уровни, цель дня, серия, значки, комбо, история за неделю."""
import random
from datetime import date, timedelta

LEVELS = [(0, "🌱 Newbie"), (100, "🌿 Starter"), (250, "🌳 Explorer"), (500, "🚀 Traveler"), (900, "🎒 Speaker"),
          (1400, "⭐ Fluent-ish"), (2100, "🏅 Pro"), (3000, "💎 Expert"), (4500, "🏆 Master"), (7000, "👑 Legend")]

PRAISE_OK = ["Отлично!", "Верно!", "Так держать!", "Супер!", "Блестяще!", "В точку!", "Класс!", "Молодец!", "Именно так!",
             "Вот это да!", "Ты на волне!", "Идеально!"]
PRAISE_BAD = ["Почти!", "Ничего, в следующий раз получится.", "Ошибки — это часть пути 💪", "Не сдавайся!",
              "Запомнится лучше после ошибки.", "Бывает! Повторим скоро."]

# (id, название, подсказка, условие: (user, ctx) -> bool)
BADGES = [
    ("first", "🎬 Первый шаг", "Дай первый ответ", lambda u, c: u["correct"] + u["wrong"] >= 1),
    ("s3", "🔥 3 дня подряд", "Серия 3 дня", lambda u, c: u["best_streak"] >= 3),
    ("s7", "🔥 Неделя огня", "Серия 7 дней", lambda u, c: u["best_streak"] >= 7),
    ("s14", "🔥 Две недели", "Серия 14 дней", lambda u, c: u["best_streak"] >= 14),
    ("s30", "🔥 Месяц без пропусков", "Серия 30 дней", lambda u, c: u["best_streak"] >= 30),
    ("s100", "🔥 Сотка", "Серия 100 дней", lambda u, c: u["best_streak"] >= 100),
    ("c50", "🎯 50 верных ответов", "50 правильных ответов", lambda u, c: u["correct"] >= 50),
    ("c200", "🎯 200 верных ответов", "200 правильных ответов", lambda u, c: u["correct"] >= 200),
    ("c500", "🎯 500 верных ответов", "500 правильных ответов", lambda u, c: u["correct"] >= 500),
    ("c1000", "🎯 Тысяча!", "1000 правильных ответов", lambda u, c: u["correct"] >= 1000),
    ("w10", "📚 10 слов выучено", "Выучи 10 слов", lambda u, c: c["words"] >= 10),
    ("w50", "📚 50 слов выучено", "Выучи 50 слов", lambda u, c: c["words"] >= 50),
    ("w100", "📚 100 слов выучено", "Выучи 100 слов", lambda u, c: c["words"] >= 100),
    ("w250", "📚 250 слов выучено", "Выучи 250 слов", lambda u, c: c["words"] >= 250),
    ("w500", "📚 500 слов выучено", "Выучи 500 слов", lambda u, c: c["words"] >= 500),
    ("v10", "📘 10 глаголов", "Выучи 10 неправильных глаголов", lambda u, c: c["verbs"] >= 10),
    ("v50", "📘 50 глаголов", "Выучи 50 неправильных глаголов", lambda u, c: c["verbs"] >= 50),
    ("v100", "📘 Все 100 глаголов", "Выучи все неправильные глаголы", lambda u, c: c["verbs"] >= 100),
    ("sl10", "😎 Знаток сленга", "Выучи 10 сленговых слов", lambda u, c: c["slang"] >= 10),
    ("sl40", "😎 Свой в доску", "Выучи 40 сленговых слов", lambda u, c: c["slang"] >= 40),
    ("cb10", "⚡ 10 подряд", "10 верных ответов подряд", lambda u, c: u["best_combo"] >= 10),
    ("cb25", "⚡ 25 подряд", "25 верных ответов подряд", lambda u, c: u["best_combo"] >= 25),
    ("g1", "🏁 Цель дня", "Выполни цель дня", lambda u, c: u["goal_days"] >= 1),
    ("g7", "🏁 7 целей дня", "Выполни цель дня 7 раз", lambda u, c: u["goal_days"] >= 7),
    ("g30", "🏁 30 целей дня", "Выполни цель дня 30 раз", lambda u, c: u["goal_days"] >= 30),
    ("l5", "⭐ Уровень Speaker", "Достигни уровня Speaker", lambda u, c: level_index(u["xp"]) >= 4),
    ("l10", "👑 Легенда", "Достигни уровня Legend", lambda u, c: level_index(u["xp"]) >= 9),
]


def level_index(xp: int) -> int:
    idx = 0
    for i, (need, _) in enumerate(LEVELS):
        if xp >= need:
            idx = i
    return idx


def level_name(xp: int) -> str:
    return LEVELS[level_index(xp)][1]


def bar(cur: int, total: int, n: int = 10) -> str:
    total = max(total, 1)
    filled = min(n, int(n * min(cur, total) / total))
    return "▰" * filled + "▱" * (n - filled)


def level_progress(xp: int) -> str:
    i = level_index(xp)
    if i == len(LEVELS) - 1:
        return "максимальный уровень 👑"
    lo, hi = LEVELS[i][0], LEVELS[i + 1][0]
    return f"{bar(xp - lo, hi - lo)} до «{LEVELS[i + 1][1]}» ещё {hi - xp} XP"


def owned(u: dict) -> set:
    return {b for b in u["badges"].split(",") if b}


def check_badges(u: dict, ctx: dict) -> list[str]:
    have = owned(u)
    new = [b for b in BADGES if b[0] not in have and b[3](u, ctx)]
    if new:
        u["badges"] = ",".join(sorted(have | {b[0] for b in new}))
    return [b[1] for b in new]


# ---------- история занятий за 2 недели ----------
def _hist(u: dict) -> dict:
    out = {}
    for part in u["hist"].split(","):
        if ":" in part:
            d, n = part.split(":", 1)
            if n.isdigit():
                out[d] = int(n)
    return out


def add_hist(u: dict, today: date):
    h = _hist(u)
    h[today.isoformat()] = h.get(today.isoformat(), 0) + 1
    keep = sorted(h)[-14:]
    u["hist"] = ",".join(f"{d}:{h[d]}" for d in keep)


def week_chart(u: dict, today: date) -> str:
    h = _hist(u)
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    vals = [h.get(d.isoformat(), 0) for d in days]
    top = max(vals) or 1
    blocks = "▁▂▃▄▅▆▇█"
    chart = "".join(blocks[min(7, int(7 * v / top))] if v else "·" for v in vals)
    return f"{chart}  (за 7 дней: {sum(vals)} ответов)"


# ---------- главное: учёт одного ответа ----------
def record(u: dict, ctx: dict, ok: bool, base_xp: int, today: date, combo: int, kind: str = "word"):
    """Обновляет статистику и возвращает (строки для сообщения, полученный XP). ctx — уже после обновления слова."""
    from storage import mark_active                                  # избегаем циклического импорта
    lines = []
    old_level = level_index(u["xp"])
    gain = base_xp if ok else max(1, base_xp // 5)
    u["correct" if ok else "wrong"] += 1
    if ok and kind == "verb":
        u["v_ok"] += 1
    if ok and kind == "slang":
        u["s_ok"] += 1
    u["best_combo"] = max(u["best_combo"], combo)
    if ok and combo >= 5 and combo % 5 == 0:
        bonus = min(5 * (combo // 5), 25)
        gain += bonus
        lines.append(f"⚡ {combo} подряд! Бонус +{bonus} XP")

    if u["today_date"] != today.isoformat():
        u["today_date"], u["today_n"] = today.isoformat(), 0
    u["today_n"] += 1
    add_hist(u, today)
    goal = u["goal"] or 10
    if u["today_n"] == goal:
        gain += 20
        u["goal_days"] += 1
        lines.append("🎯 <b>Цель дня выполнена!</b> Бонус +20 XP")

    new_day, note = mark_active(u, today)
    if new_day:
        lines.append(f"🔥 Серия: <b>{u['streak']}</b> дн.")
    if note:
        lines.append(note)

    u["xp"] += gain
    if level_index(u["xp"]) > old_level:
        lines.append(f"🎉 <b>Новый уровень: {level_name(u['xp'])}!</b>")
    for name in check_badges(u, ctx):
        lines.append(f"🏅 Новый значок: <b>{name}</b>")
    if u["today_n"] < goal:
        lines.append(f"🎯 Цель дня: {bar(u['today_n'], goal)} {u['today_n']}/{goal}")
    return lines, gain


def praise(ok: bool) -> str:
    return random.choice(PRAISE_OK if ok else PRAISE_BAD)
