"""Простой учёт занятий (без очков, уровней и серий): ответы, точность, активность по дням."""
from datetime import date, timedelta


def bar(cur: int, total: int, n: int = 10) -> str:
    total = max(total, 1)
    filled = min(n, int(n * min(cur, total) / total))
    return "▰" * filled + "▱" * (n - filled)


def _hist(u: dict) -> dict:
    out = {}
    for part in u["hist"].split(","):
        if ":" in part:
            d, n = part.split(":", 1)
            if n.isdigit():
                out[d] = int(n)
    return out


def record(u: dict, ok: bool, today: date):
    u["correct" if ok else "wrong"] += 1
    t = today.isoformat()
    if u["today_date"] != t:
        u["today_date"], u["today_n"] = t, 0
    u["today_n"] += 1
    u["last_active"] = t
    h = _hist(u)
    h[t] = h.get(t, 0) + 1
    u["hist"] = ",".join(f"{d}:{h[d]}" for d in sorted(h)[-14:])


def week_chart(u: dict, today: date) -> str:
    h = _hist(u)
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    vals = [h.get(d.isoformat(), 0) for d in days]
    top = max(vals) or 1
    blocks = "▁▂▃▄▅▆▇█"
    chart = "".join(blocks[min(7, int(7 * v / top))] if v else "·" for v in vals)
    return f"{chart}  (за 7 дней: {sum(vals)} ответов)"
