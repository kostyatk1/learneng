"""Хранилище: данные живут в памяти и сохраняются в Google Sheets (или в локальный JSON для тестов)."""
import asyncio
import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger("storage")

DEFAULT_TZ = os.getenv("DEFAULT_TZ", "Europe/Berlin")

USER_COLS = ["uid", "name", "topics", "per_day", "hour", "tz", "remind", "streak", "best_streak",
             "last_active", "xp", "correct", "wrong", "last_daily", "last_remind", "joined"]
WORD_COLS = ["uid", "en", "ru", "ex", "topic", "source", "box", "next_review", "added", "correct", "wrong"]
NOTE_COLS = ["text"]
USER_INT = {"uid", "per_day", "hour", "remind", "streak", "best_streak", "xp", "correct", "wrong"}
WORD_INT = {"uid", "box", "correct", "wrong"}

INTERVALS = [0, 1, 2, 4, 8, 16, 32]          # дни до повтора для коробок 0..6 (метод Лейтнера)
LEARNED_BOX = 4                              # с этой коробки слово считается выученным
LEVELS = [(0, "🌱 Starter"), (100, "🌿 Explorer"), (300, "🌳 Speaker"), (700, "⭐ Pro"), (1500, "🏆 Master")]


def _conv(row: dict, cols: list[str], ints: set[str]) -> dict:
    out = {}
    for c in cols:
        v = row.get(c, "")
        if c in ints:
            try:
                v = int(float(v)) if str(v).strip() != "" else 0
            except (ValueError, TypeError):
                v = 0
        else:
            v = "" if v is None else str(v)
        out[c] = v
    return out


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class LocalBackend:
    """Для локальных тестов. На Render файлы пропадают при перезапуске — там нужен Sheets."""

    def __init__(self, path="data.json"):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}

    def load(self, tab, cols):
        return list(self.data.get(tab, []))

    def save(self, tab, cols, rows):
        self.data[tab] = rows
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), "utf-8")


class SheetsBackend:
    def __init__(self, creds_json: str, sheet_id: str):
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            json.loads(creds_json), scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.gspread = gspread
        self.sh = gspread.authorize(creds).open_by_key(sheet_id)
        self._sizes: dict[str, int] = {}

    def _ws(self, tab, cols):
        try:
            return self.sh.worksheet(tab)
        except self.gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(tab, rows=200, cols=max(len(cols), 4))
            ws.update(range_name="A1", values=[cols])
            return ws

    def load(self, tab, cols):
        ws = self._ws(tab, cols)
        rows = ws.get_all_records(numericise_ignore=["all"])
        self._sizes[tab] = len(rows) + 1
        return rows

    def save(self, tab, cols, rows):
        ws = self._ws(tab, cols)
        values = [cols] + [[r.get(c, "") for c in cols] for r in rows]
        if ws.row_count < len(values):
            ws.add_rows(len(values) - ws.row_count + 100)
        ws.update(range_name="A1", values=values)
        prev = self._sizes.get(tab, 0)
        if prev > len(values):                                    # если строк стало меньше — чистим хвост
            ws.batch_clear([f"A{len(values) + 1}:{col_letter(len(cols))}{prev}"])
        self._sizes[tab] = len(values)


class Store:
    def __init__(self, backend):
        self.b = backend
        self.users: dict[int, dict] = {}
        self.words: dict[tuple[int, str], dict] = {}
        self.notes: list[str] = []
        self._dirty: set[str] = set()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ---------- загрузка / сохранение ----------
    async def load(self):
        for r in await asyncio.to_thread(self.b.load, "users", USER_COLS):
            u = _conv(r, USER_COLS, USER_INT)
            if u["uid"]:
                self.users[u["uid"]] = u
        for r in await asyncio.to_thread(self.b.load, "words", WORD_COLS):
            w = _conv(r, WORD_COLS, WORD_INT)
            if w["uid"] and w["en"]:
                self.words[(w["uid"], w["en"].lower())] = w
        self.notes = [t for t in (str(r.get("text", "")).strip()
                                  for r in await asyncio.to_thread(self.b.load, "notes", NOTE_COLS)) if t]
        log.info("Загружено: %d польз., %d слов, %d записок", len(self.users), len(self.words), len(self.notes))

    def _snapshot(self, tab):
        if tab == "users":
            return USER_COLS, [dict(u) for u in self.users.values()]
        if tab == "words":
            return WORD_COLS, [dict(w) for w in self.words.values()]
        return NOTE_COLS, [{"text": t} for t in self.notes]

    def mark(self, tab: str):
        self._dirty.add(tab)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._flush_later())

    async def _flush_later(self, delay: float = 3):
        await asyncio.sleep(delay)
        for _ in range(5):
            await self.flush()
            if not self._dirty:
                return
            await asyncio.sleep(30)

    async def flush(self):
        async with self._lock:
            tabs = list(self._dirty)
            self._dirty.clear()
            for tab in tabs:
                try:
                    cols, rows = self._snapshot(tab)
                    await asyncio.to_thread(self.b.save, tab, cols, rows)
                except Exception:
                    log.exception("Не удалось сохранить вкладку %s — повторю позже", tab)
                    self._dirty.add(tab)

    # ---------- пользователи ----------
    def ensure_user(self, uid: int, name: str = "") -> dict:
        u = self.users.get(uid)
        if u is None:
            u = {"uid": uid, "name": name, "topics": "", "per_day": 5, "hour": 9, "tz": DEFAULT_TZ,
                 "remind": 1, "streak": 0, "best_streak": 0, "last_active": "", "xp": 0, "correct": 0,
                 "wrong": 0, "last_daily": "", "last_remind": "", "joined": date.today().isoformat()}
            self.users[uid] = u
            self.mark("users")
        return u

    # ---------- слова ----------
    def words_of(self, uid: int) -> list[dict]:
        return [w for (u, _), w in self.words.items() if u == uid]

    def add_word(self, uid, en, ru, ex="", topic="", source="daily", today: date | None = None) -> bool:
        key = (uid, en.lower())
        if key in self.words:
            return False
        d = (today or date.today()).isoformat()
        self.words[key] = {"uid": uid, "en": en, "ru": ru, "ex": ex, "topic": topic, "source": source,
                           "box": 0, "next_review": d, "added": d, "correct": 0, "wrong": 0}
        self.mark("words")
        return True

    def del_word(self, uid, en) -> bool:
        if self.words.pop((uid, en.lower()), None) is None:
            return False
        self.mark("words")
        return True


# ---------- логика прогресса ----------
def srs_review(w: dict, ok: bool, today: date):
    if ok:
        w["box"] = min(w["box"] + 1, len(INTERVALS) - 1)
        w["correct"] += 1
    else:
        w["box"] = 0
        w["wrong"] += 1
    w["next_review"] = (today + timedelta(days=INTERVALS[w["box"]])).isoformat()


def mark_active(u: dict, today: date) -> bool:
    """Отмечает занятие. True — если это первое занятие за сегодня (серия обновилась)."""
    t = today.isoformat()
    if u["last_active"] == t:
        return False
    u["streak"] = u["streak"] + 1 if u["last_active"] == (today - timedelta(days=1)).isoformat() else 1
    u["best_streak"] = max(u["best_streak"], u["streak"])
    u["last_active"] = t
    return True


def live_streak(u: dict, today: date) -> int:
    ok = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
    return u["streak"] if u["last_active"] in ok else 0


def level_for(xp: int) -> str:
    name = LEVELS[0][1]
    for need, n in LEVELS:
        if xp >= need:
            name = n
    return name
