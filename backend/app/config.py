"""Configuration: config.toml on disk, overridable per-key by BIFROST_* env vars.

Env vars win so the Docker container can be configured without touching the file.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("BIFROST_CONFIG", "config.toml"))


@dataclass(frozen=True)
class Config:
    # Keys
    youtube_api_key: str = ""
    deepl_api_key: str = ""
    # Whisper
    whisper_url: str = "http://127.0.0.1:8178"  # whisper-server (native, Metal)
    whisper_model: str = "large-v3"             # informational; the server loads the model
    # Chat translation backend: "auto" (DeepL until quota/error, then local LLM),
    # "deepl" (DeepL only), or "local" (local LLM only). The local LLM is a native
    # llama-server reached at translate_url (overridden to host.docker.internal in
    # Docker). Empty translate_url disables the local backend.
    chat_translate: str = "auto"
    translate_url: str = "http://127.0.0.1:8180"
    # Audio
    chunk_seconds: int = 15
    # Also transcribe each chunk in the source language (second Whisper pass per
    # chunk) so the UI can show original + translation. ~2× Whisper load.
    dual_transcript: bool = True
    # Chat
    # Minimum seconds between live-chat polls. Each poll costs 1 YouTube API quota
    # unit regardless of message volume, so this is the quota knob: 10s ≈ 360
    # units/h, 5s ≈ 720, 30s ≈ 120. YouTube's own suggested interval is honored
    # when longer. Clamped to ≥1s.
    chat_poll_seconds: int = 10
    # Server
    host: str = "0.0.0.0"
    port: int = 7842


def load() -> Config:
    raw: dict = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            raw = tomllib.load(f)

    def get(key: str, default):
        env = os.environ.get(f"BIFROST_{key.upper()}")
        if env is not None:
            if isinstance(default, bool):
                return env.strip().lower() in ("1", "true", "yes", "on")
            return type(default)(env) if not isinstance(default, str) else env
        return raw.get(key, default)

    d = Config()
    return Config(
        youtube_api_key=get("youtube_api_key", d.youtube_api_key),
        deepl_api_key=get("deepl_api_key", d.deepl_api_key),
        whisper_url=get("whisper_url", d.whisper_url),
        whisper_model=get("whisper_model", d.whisper_model),
        chat_translate=get("chat_translate", d.chat_translate),
        translate_url=get("translate_url", d.translate_url),
        chunk_seconds=get("chunk_seconds", d.chunk_seconds),
        dual_transcript=get("dual_transcript", d.dual_transcript),
        chat_poll_seconds=get("chat_poll_seconds", d.chat_poll_seconds),
        host=get("host", d.host),
        port=get("port", d.port),
    )
