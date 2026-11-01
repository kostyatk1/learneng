"""Онлайн-перевод. Все сервисы опрашиваются ОДНОВРЕМЕННО, берём первый успешный ответ.

Почему так: на бесплатном Render много ботов делят одни и те же IP-адреса, и Google / MyMemory часто отвечают
429 «Too Many Requests». Несколько независимых сервисов + кэш делают перевод надёжным.
"""
import asyncio
import logging
import os
import time
from functools import partial
from urllib.parse import quote

import aiohttp

log = logging.getLogger("translator")

GTX_URL = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_URL = "https://api.mymemory.translated.net/get"
# Публичные зеркала Lingva (прокси к Google с другими IP). Список можно заменить переменной LINGVA_URLS (через запятую).
LINGVA_URLS = [u.strip().rstrip("/") for u in os.getenv(
    "LINGVA_URLS", "https://lingva.ml/api/v1,https://lingva.garudalinux.org/api/v1,"
                   "https://translate.plausibility.cloud/api/v1,https://lingva.lunar.icu/api/v1").split(",") if u.strip()]
# Любой настоящий e-mail: MyMemory тогда считает лимит на тебя, а не на общий IP Render (50 000 символов в день бесплатно)
MYMEMORY_EMAIL = os.getenv("MYMEMORY_EMAIL", "")

# Свой «прокси-переводчик» на Google Apps Script (бесплатно, не блокируется как общий IP Render) — см. google_translate_proxy.gs
GAS_URL = os.getenv("GAS_URL", "").strip()
GAS_KEY = os.getenv("GAS_KEY", "")

TIMEOUT = aiohttp.ClientTimeout(total=10)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EnglishBot/1.0)"}
CACHE: dict[tuple, str] = {}


class TranslateError(Exception):
    pass


async def _gtx(s, text, src, tgt):
    async with s.get(GTX_URL, params={"client": "gtx", "sl": src, "tl": tgt, "dt": "t", "q": text}) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


async def _mymemory(s, text, src, tgt):
    params = {"q": text, "langpair": f"{src}|{tgt}"}
    if MYMEMORY_EMAIL:
        params["de"] = MYMEMORY_EMAIL
    async with s.get(MYMEMORY_URL, params=params) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    out = (data.get("responseData") or {}).get("translatedText", "")
    if str(data.get("responseStatus")) != "200" or out.upper().startswith(("MYMEMORY WARNING", "PLEASE SELECT")):
        raise TranslateError(f"MyMemory: {data.get('responseStatus')} {out[:60]}")
    return out


async def _lingva(base, s, text, src, tgt):
    async with s.get(f"{base}/{src}/{tgt}/{quote(text, safe='')}") as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    return data["translation"]


async def _gas(s, text, src, tgt):
    async with s.get(GAS_URL, params={"q": text, "s": src, "t": tgt, "key": GAS_KEY}, allow_redirects=True) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    if "t" not in data:
        raise TranslateError(f"AppsScript: {data.get('error', 'bad response')}")
    return data["t"]


def _providers():
    ps = [("google", _gtx), ("mymemory", _mymemory)]
    if GAS_URL:
        ps.insert(0, ("apps-script", _gas))
    ps += [(f"lingva[{u.split('//')[1].split('/')[0]}]", partial(_lingva, u)) for u in LINGVA_URLS]
    return ps


async def translate_text(text: str, src: str, tgt: str) -> str:
    key = (text.strip().lower(), src, tgt)
    if key in CACHE:
        return CACHE[key]
    errors = []
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=HEADERS) as s:
        names = {}
        tasks = []
        for name, fn in _providers():
            t = asyncio.create_task(fn(s, text, src, tgt))
            names[t] = name
            tasks.append(t)
        try:
            for fut in asyncio.as_completed(tasks):
                try:
                    out = (await fut).strip()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    errors.append(f"{type(e).__name__}: {str(e)[:70]}")
                    continue
                if out:
                    if len(CACHE) > 2000:
                        CACHE.clear()
                    CACHE[key] = out
                    return out
        finally:
            for t in tasks:
                t.cancel()
    log.warning("all translators failed for %r: %s", text[:40], " | ".join(errors))
    raise TranslateError(" | ".join(errors))


async def diagnose(text: str = "hello", src: str = "en", tgt: str = "ru") -> list[str]:
    """Проверяет каждый сервис отдельно (для команды /diag): кто работает с этого сервера."""
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=HEADERS) as s:
        async def one(name, fn):
            t0 = time.time()
            try:
                out = (await fn(s, text, src, tgt)).strip()
                return f"✅ {name}: {out} ({time.time() - t0:.1f} с)"
            except Exception as e:
                return f"❌ {name}: {type(e).__name__} {str(e)[:90]}"
        return list(await asyncio.gather(*(one(n, f) for n, f in _providers())))
