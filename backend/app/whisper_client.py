"""Whisper module: HTTP client for whisper-server (whisper.cpp, Metal, model
loaded once at server start). Audio chunk in → English text out. Never touches
chat, never does text-to-text translation.

The server must be launched with `--translate -l auto` (see whisper/run-whisper-server.sh);
every inference then performs Whisper's translate task (output is always English).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("bifrost.whisper")

# Hiragana, katakana, CJK ideographs, full-width forms — if a large share of the
# output is in these ranges the model returned source-language text, which is the
# turbo-model trap §4/§9 of the brief tells us to guard against.
_JA_CHARS = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")


@dataclass
class Transcription:
    text: str
    non_english: bool  # turbo-trap guard tripped


def _is_non_speech(text: str) -> bool:
    """True for whisper.cpp's silence/music markers and note-only output."""
    if not text:
        return True
    # Strip music notes, brackets and punctuation; if nothing remains it was
    # a non-speech chunk (e.g. "♪", "[Music]", "(拍手)").
    stripped = re.sub(r"[♪♫♬♩\s\[\]()（）.,。、!！?？~〜・·\-—]", "", text)
    if not stripped:
        return True
    return stripped.lower() in ("blankaudio", "music", "applause", "laughter", "音楽", "拍手")


def looks_non_english(text: str) -> bool:
    stripped = re.sub(r"\s", "", text)
    if len(stripped) < 8:
        return False
    ja = len(_JA_CHARS.findall(stripped))
    return ja / len(stripped) > 0.3


class WhisperClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        # A 15 s chunk on large-v3/M4 Metal should take a few seconds; allow slack.
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))

    async def healthy(self) -> bool:
        try:
            r = await self._http.get(self.base_url + "/", timeout=5.0)
            return r.status_code < 500
        except httpx.HTTPError:
            return False

    async def transcribe(self, wav: Path, translate: bool = True) -> Transcription:
        """translate=True -> Whisper translate task (English out).
        translate=False -> plain transcription in the source language."""
        with open(wav, "rb") as f:
            r = await self._http.post(
                self.base_url + "/inference",
                files={"file": (wav.name, f, "audio/wav")},
                data={"response_format": "json", "temperature": "0.0",
                      "temperature_inc": "0.2",
                      "translate": "true" if translate else "false"},
            )
        r.raise_for_status()
        text = (r.json().get("text") or "").strip()
        if _is_non_speech(text):
            text = ""
        return Transcription(text=text, non_english=looks_non_english(text))

    async def close(self) -> None:
        await self._http.aclose()
