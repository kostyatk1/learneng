"""Перевод через несколько бесплатных сервисов подряд: если один недоступен — пробуем следующий."""
import logging
from urllib.parse import quote

import aiohttp

log = logging.getLogger("translator")

GTX_URL = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_URL = "https://api.mymemory.translated.net/get"
LINGVA_URL = "https://lingva.ml/api/v1"
TIMEOUT = aiohttp.ClientTimeout(total=8)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EnglishBot/1.0)"}


class TranslateError(Exception):
    pass


async def _gtx(s, text, src, tgt):
    async with s.get(GTX_URL, params={"client": "gtx", "sl": src, "tl": tgt, "dt": "t", "q": text}) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


async def _mymemory(s, text, src, tgt):
    async with s.get(MYMEMORY_URL, params={"q": text, "langpair": f"{src}|{tgt}"}) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    if str(data.get("responseStatus")) != "200":
        raise TranslateError(f"mymemory status {data.get('responseStatus')}")
    return data["responseData"]["translatedText"]


async def _lingva(s, text, src, tgt):
    async with s.get(f"{LINGVA_URL}/{src}/{tgt}/{quote(text, safe='')}") as r:
        r.raise_for_status()
        data = await r.json(content_type=None)
    return data["translation"]


async def translate_text(text: str, src: str, tgt: str) -> str:
    errors = []
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=HEADERS) as s:
        for fn in (_gtx, _mymemory, _lingva):
            try:
                out = (await fn(s, text, src, tgt)).strip()
                if out:
                    return out
                errors.append(f"{fn.__name__}: empty")
            except Exception as e:                                  # сеть, таймаут, неверный ответ
                errors.append(f"{fn.__name__}: {type(e).__name__} {e}")
                log.warning("translator %s failed: %s", fn.__name__, e)
    raise TranslateError("; ".join(errors))
