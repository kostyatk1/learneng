"""Запуск: веб-сервер для Telegram webhook (Render) или polling для локальных тестов (USE_POLLING=1)."""
import asyncio
import hashlib
import hmac
import logging
import os

from aiohttp import web
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

import handlers
import extras_h
import verbs_h
from storage import LocalBackend, SheetsBackend, Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

TOKEN = os.environ["BOT_TOKEN"]
SECRET = os.getenv("WEBHOOK_SECRET", "")
if SECRET:                                               # Telegram принимает только A-Z a-z 0-9 _ -, поэтому берём хэш
    SECRET = hashlib.sha256(SECRET.encode()).hexdigest()
CRON_KEY = os.getenv("CRON_KEY", "")
BASE_URL = (os.getenv("BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
ALLOWED = {int(x) for x in os.getenv("ALLOWED_IDS", "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}
PORT = int(os.getenv("PORT", "10000"))
_ADMIN = int(os.getenv("ADMIN_ID", "0") or 0)
if ALLOWED and _ADMIN:
    ALLOWED.add(_ADMIN)                                   # админ всегда допущен (иначе не сработают /diag, /note)


class Access(BaseMiddleware):
    """Если задан ALLOWED_IDS — бот отвечает только этим людям."""

    async def __call__(self, handler, event, data):
        if ALLOWED:
            user = data.get("event_from_user")
            if user is None or user.id not in ALLOWED:
                return None
        return await handler(event, data)


def make_store() -> Store:
    creds, sheet = os.getenv("GOOGLE_CREDS_JSON"), os.getenv("SHEET_ID")
    if creds and sheet:
        return Store(SheetsBackend(creds, sheet))
    log.warning("GOOGLE_CREDS_JSON / SHEET_ID не заданы — данные хранятся в data.json "
                "(на Render они пропадут при перезапуске!)")
    return Store(LocalBackend())


async def init_store():
    handlers.store = await asyncio.to_thread(make_store)
    await handlers.store.load()


def make_dp() -> Dispatcher:
    dp = Dispatcher()
    dp.update.outer_middleware(Access())
    dp.include_router(extras_h.router)
    dp.include_router(verbs_h.router)        # раньше основного: иначе кнопка «📘 Глаголы» уйдёт в переводчик
    dp.include_router(handlers.router)
    return dp


def build_app() -> web.Application:
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = make_dp()
    app = web.Application()
    SimpleRequestHandler(dp, bot, secret_token=SECRET or None).register(app, path="/webhook")

    async def health(_):
        return web.Response(text="ok")

    async def cron_tick(request: web.Request):
        key = request.query.get("key", "").replace(" ", "+")   # '+' в ссылке превращается в пробел
        if not CRON_KEY or not hmac.compare_digest(key.encode(), CRON_KEY.encode()):
            return web.Response(status=403, text="forbidden")
        return web.json_response(await handlers.tick(bot))

    async def on_startup(_):
        await init_store()
        if BASE_URL:
            await bot.set_webhook(f"{BASE_URL}/webhook", secret_token=SECRET or None,
                                  allowed_updates=dp.resolve_used_update_types())
            log.info("Webhook: %s/webhook", BASE_URL)
        else:
            log.warning("BASE_URL / RENDER_EXTERNAL_URL не задан — webhook не установлен")

    async def on_shutdown(_):
        if handlers.store:
            await handlers.store.flush()
        await bot.session.close()

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/cron/tick", cron_tick)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


async def run_polling():
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await init_store()
    await bot.delete_webhook()
    try:
        await make_dp().start_polling(bot)
    finally:
        await handlers.store.flush()


if __name__ == "__main__":
    if os.getenv("USE_POLLING"):
        asyncio.run(run_polling())
    else:
        web.run_app(build_app(), host="0.0.0.0", port=PORT, access_log=None)
