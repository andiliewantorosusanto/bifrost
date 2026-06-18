"""Translate module: chat text -> English. Never touches audio (Whisper already
translates captions). Two backends, picked by `chat_translate`:

  deepl  — DeepL API (best quality, but the free/dev allowance is finite).
  local  — a local LLM (llama.cpp `llama-server`, native Metal, like whisper-
           server) reached over HTTP. Offline, no quota.
  auto   — DeepL while it works; on quota/error fall back to local for good.

Quota/efficiency care: pure-ASCII messages are assumed already English and
skipped; each poll batch is one request.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional, Sequence

import deepl
import httpx

log = logging.getLogger("bifrost.translate")

_JA = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")  # kana + CJK ideographs + half-width kana


@dataclass
class Translated:
    text: str                 # English
    original: Optional[str]   # source text if a translation actually happened
    source_lang: str          # e.g. "JA", or "EN" when skipped


def _is_probably_english(text: str) -> bool:
    return all(ord(c) < 128 for c in text)


def _guess_lang(text: str) -> str:
    return "JA" if _JA.search(text) else "??"


class LocalLLMTranslator:
    """Translate via a local OpenAI-compatible LLM server (llama.cpp). One batch
    request returns a JSON array; falls back to per-message requests if the model
    doesn't return clean JSON. Raises httpx.HTTPError if the server is unreachable."""

    SYSTEM = (
        "You are a translation engine for YouTube live-chat messages. Translate "
        "each message into natural, casual English. Keep it short; keep names, "
        "emotes and @handles as-is; do not explain or add anything. Respond with "
        'ONLY a JSON object: {"out": ["<english>", ...]} with one entry per input '
        "message, in the same order."
    )

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0))

    async def healthy(self) -> bool:
        try:
            r = await self._http.get(self.base_url + "/health", timeout=3.0)
            return r.status_code < 500
        except httpx.HTTPError:
            return False

    async def _chat(self, system: str, user: str, max_tokens: int) -> str:
        r = await self._http.post(
            self.base_url + "/v1/chat/completions",
            json={
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "cache_prompt": True,  # llama.cpp reuses the system prefix across calls
            },
        )
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()

    async def translate(self, texts: list[str]) -> list[str]:
        """Return an English string for each input (same length/order)."""
        budget = sum(len(t) for t in texts) * 3 + 256  # generous token ceiling
        content = await self._chat(self.SYSTEM, json.dumps(texts, ensure_ascii=False),
                                   max_tokens=min(budget, 2048))
        out = self._parse_array(content)
        if out is not None and len(out) == len(texts):
            return out
        log.info("local LLM batch JSON mismatch (%s) — translating line by line",
                 "no array" if out is None else f"{len(out)}≠{len(texts)}")
        return [await self._translate_one(t) for t in texts]

    async def _translate_one(self, text: str) -> str:
        sys = ("Translate this YouTube chat message into short, natural English. "
               "Output ONLY the translation, nothing else.")
        try:
            res = (await self._chat(sys, text, max_tokens=min(len(text) * 3 + 64, 512)))
            return res or text
        except httpx.HTTPError:
            raise
        except Exception:
            return text

    @staticmethod
    def _parse_array(content: str) -> Optional[list[str]]:
        # Models sometimes wrap JSON in prose/code fences; pull the first {...} or [...].
        for pat in (r"\{.*\}", r"\[.*\]"):
            m = re.search(pat, content, re.DOTALL)
            if not m:
                continue
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
            arr = data.get("out") if isinstance(data, dict) else data
            if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
                return [x.strip() for x in arr]
        return None

    async def close(self) -> None:
        await self._http.aclose()


class Translator:
    """Orchestrates the chosen backend with DeepL→local auto-fallback."""

    def __init__(self, deepl_api_key: str, translate_url: str = "",
                 strategy: str = "auto"):
        self.strategy = strategy if strategy in ("auto", "deepl", "local") else "auto"
        self._deepl = deepl.Translator(deepl_api_key) if deepl_api_key else None
        self._llm = LocalLLMTranslator(translate_url) if translate_url else None
        self._deepl_down = False  # set once DeepL hits quota/error, to stop retrying
        # We can translate if some backend is reachable for the active strategy.
        self.enabled = bool(
            (self._deepl and self.strategy in ("auto", "deepl"))
            or (self._llm and self.strategy in ("auto", "local"))
        )

    # -- DeepL ----------------------------------------------------------------
    def _deepl_sync(self, texts: list[str]) -> list[deepl.TextResult]:
        assert self._deepl is not None
        res = self._deepl.translate_text(texts, target_lang="EN-US")
        return res if isinstance(res, list) else [res]

    # -- batch entry point ----------------------------------------------------
    async def translate_batch(self, texts: Sequence[str]) -> list[Translated]:
        out: list[Optional[Translated]] = [None] * len(texts)
        todo = []
        for i, t in enumerate(texts):
            if not self.enabled or _is_probably_english(t):
                out[i] = Translated(text=t, original=None, source_lang="EN")
            else:
                todo.append(i)
        if not todo:
            return [t for t in out if t is not None]

        src = [texts[i] for i in todo]
        translated = await self._run(src)  # list[str] aligned to src, or None on total fail
        for n, i in enumerate(todo):
            if translated is None:
                out[i] = Translated(text=texts[i], original=None, source_lang="??")
            else:
                en = translated[n]
                same = en.strip() == texts[i].strip()
                out[i] = Translated(text=en, original=None if same else texts[i],
                                    source_lang=_guess_lang(texts[i]))
        return [t for t in out if t is not None]

    async def _run(self, src: list[str]) -> Optional[list[str]]:
        """Apply the strategy. Returns translations, or None if all backends fail."""
        use_deepl = self._deepl and not self._deepl_down and self.strategy in ("auto", "deepl")
        if use_deepl:
            try:
                results = await asyncio.to_thread(self._deepl_sync, src)
                return [r.text for r in results]
            except deepl.DeepLException as e:
                # Quota/auth/etc. In auto, fall through to local and stop using
                # DeepL; in deepl-only, give up (passthrough).
                quota = "quota" in str(e).lower()
                if self.strategy == "auto" and self._llm:
                    self._deepl_down = self._deepl_down or quota
                    log.warning("DeepL failed (%s) — %s to local LLM", e,
                                "switching" if quota else "falling back")
                else:
                    log.warning("DeepL failed (%s); passing originals through", e)
                    return None
        if self._llm and self.strategy in ("auto", "local"):
            try:
                return await self._llm.translate(src)
            except httpx.HTTPError as e:
                log.warning("local LLM unreachable (%s); passing originals through. "
                            "Start it with ./llm/run-llm-server.sh (or `make translate`).", e)
                return None
        return None

    async def close(self) -> None:
        if self._llm:
            await self._llm.close()
