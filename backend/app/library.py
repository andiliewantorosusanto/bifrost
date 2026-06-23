"""Library: saved videos and cached transcripts on disk.

library/<video_id>/
  meta.json      — source info (title, channel, …) + save metadata
  captions.json  — the full caption stream (t0/t1/text/original), Whisper output
  media.mp4      — downloaded video (only after an explicit "Save offline")

Captions are cached automatically whenever a video finishes processing, so the
same video is never transcribed twice. Media is downloaded only on request.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

log = logging.getLogger("bifrost.library")

LIBRARY_DIR = Path(os.environ.get(
    "BIFROST_LIBRARY_DIR",
    Path(__file__).resolve().parent.parent.parent / "library",
))

_ID_RE = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")
_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")


def extract_video_id(url: str) -> Optional[str]:
    m = _ID_RE.search(url)
    return m.group(1) if m else None


def _dir(video_id: str) -> Path:
    return LIBRARY_DIR / video_id


def media_path(video_id: str) -> Path:
    return _dir(video_id) / "media.mp4"


# -- in-progress HLS (single-pull player stream) -----------------------------
# A video being watched is pulled once and fanned out to a growing HLS playlist
# (stream.m3u8 + seg_*.ts) the browser plays, gated to the transcribed second.
# These live in the served library dir; on completion they're concatenated into
# the permanent media.mp4 (no second download) and may be discarded.

HLS_PLAYLIST = "stream.m3u8"


def hls_url(video_id: str) -> str:
    return f"/library/{video_id}/{HLS_PLAYLIST}"


def ensure_hls_dir(video_id: str) -> Path:
    """Create the served dir and clear any stale HLS from a previous watch."""
    d = _dir(video_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / HLS_PLAYLIST).unlink(missing_ok=True)
    for ts in d.glob("seg_*.ts"):
        ts.unlink(missing_ok=True)
    return d


def clear_hls(video_id: str) -> None:
    ensure_hls_dir(video_id)


async def concat_hls_to_mp4(video_id: str) -> Optional[Path]:
    """Remux the captured .ts segments into media.mp4 (stream copy, no second
    pull, no re-encode). Returns the path, or None if there's nothing to build."""
    d = _dir(video_id)
    segs = sorted(d.glob("seg_*.ts"))
    if not segs:
        return None
    out = media_path(video_id)
    listing = "concat:" + "|".join(str(s) for s in segs)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", "-i", listing,
        "-c", "copy", "-bsf:a", "aac_adtstoasc", str(out),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not out.exists():
        log.warning("concat to mp4 failed for %s: %s", video_id,
                    err.decode(errors="replace").strip()[-200:])
        return None
    return out


@dataclass
class Entry:
    video_id: str
    meta: dict
    captions: Optional[list]   # None if not cached
    media: Optional[str]       # served URL path, e.g. /library/<id>/media.mp4
    covered_s: Optional[int] = None  # how far processing got (None = unknown/legacy)
    complete: bool = True            # False = partial transcript, resume on open


def lookup(video_id: str) -> Optional[Entry]:
    d = _dir(video_id)
    meta_file = d / "meta.json"
    if not meta_file.exists():
        return None
    try:
        meta = json.loads(meta_file.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    captions = None
    cap_file = d / "captions.json"
    if cap_file.exists():
        try:
            captions = json.loads(cap_file.read_text()).get("captions", [])
        except (OSError, json.JSONDecodeError):
            captions = None
    media = f"/library/{video_id}/media.mp4" if media_path(video_id).exists() else None
    return Entry(
        video_id=video_id, meta=meta, captions=captions, media=media,
        covered_s=meta.get("captions_covered_s"),
        complete=bool(meta.get("captions_complete", True)),  # legacy caches = complete
    )


def save_meta(video_id: str, meta: dict) -> None:
    d = _dir(video_id)
    d.mkdir(parents=True, exist_ok=True)
    meta = {**meta, "saved_at": int(time.time())}
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))


def save_captions(video_id: str, meta: dict, captions: list,
                  covered_s: Optional[int] = None, complete: bool = True) -> None:
    save_meta(video_id, {**meta, "captions_covered_s": covered_s,
                         "captions_complete": complete})
    (_dir(video_id) / "captions.json").write_text(
        json.dumps({"captions": captions}, ensure_ascii=False)
    )
    log.info("cached %d captions for %s (%s)", len(captions), video_id,
             "complete" if complete else f"partial to {covered_s}s")


# -- live session recovery ---------------------------------------------------
# Live captions/chat are NOT library entries: t0 is capture-relative (no media
# to scrub) and the stream is ongoing. They're persisted to a separate live.json
# purely so a refresh or backend restart can restore the in-progress feed. Kept
# out of meta.json so they never appear in the library list or the VOD path.

LIVE_FILE = "live.json"
LIVE_SAVE_CAP = 2000  # newest captions/chat kept on disk, to bound a 24/7 stream


def save_live_session(video_id: str, meta: dict, captions: list, chat: list) -> None:
    """Atomically snapshot an in-progress live session for refresh recovery."""
    d = _dir(video_id)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": meta,
        "updated_at": int(time.time()),
        "captions": captions[-LIVE_SAVE_CAP:],
        "chat": chat[-LIVE_SAVE_CAP:],
    }
    tmp = d / (LIVE_FILE + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    os.replace(tmp, d / LIVE_FILE)  # atomic: two writer tasks never tear the file


def load_live_session(video_id: str, max_age_s: int) -> Optional[dict]:
    """Return the saved live session if present and newer than max_age_s, else
    None (a stale snapshot from a much earlier broadcast is not restored)."""
    f = _dir(video_id) / LIVE_FILE
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if int(time.time()) - int(data.get("updated_at", 0)) > max_age_s:
        return None
    data.setdefault("captions", [])
    data.setdefault("chat", [])
    return data


def clear_live_session(video_id: str) -> None:
    (_dir(video_id) / LIVE_FILE).unlink(missing_ok=True)


def list_items() -> list[dict]:
    items = []
    if not LIBRARY_DIR.is_dir():
        return items
    for d in sorted(LIBRARY_DIR.iterdir()):
        meta_file = d / "meta.json"
        if not meta_file.is_file():
            continue
        try:
            meta = json.loads(meta_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        items.append({
            "video_id": d.name,
            "title": meta.get("title", d.name),
            "channel": meta.get("channel", ""),
            "duration": meta.get("duration"),
            "saved_at": meta.get("saved_at"),
            "has_media": (d / "media.mp4").exists(),
            "has_captions": (d / "captions.json").exists(),
        })
    items.sort(key=lambda i: i.get("saved_at") or 0, reverse=True)
    return items


def delete_captions(video_id: str) -> None:
    (_dir(video_id) / "captions.json").unlink(missing_ok=True)


def delete(video_id: str) -> None:
    import shutil
    shutil.rmtree(_dir(video_id), ignore_errors=True)


async def download_media(
    url: str, video_id: str,
    progress: Callable[[float], Awaitable[None]],
) -> Path:
    """Download the video as h264/aac mp4 (plays in <video>). Raises RuntimeError.

    With account cookies, YouTube's web video formats can 403 (PO-token
    enforcement for logged-in sessions). Public videos work fine anonymously,
    so a 403 with cookies gets one retry without them; members-only videos
    need the cookies and surface the error if YouTube still refuses."""
    from .audio import cookie_args
    try:
        return await _download_media_once(url, video_id, progress, cookie_args())
    except RuntimeError as e:
        if cookie_args() and "403" in str(e):
            log.warning("media download got 403 with cookies — retrying anonymously")
            return await _download_media_once(url, video_id, progress, [])
        raise


async def _download_media_once(
    url: str, video_id: str,
    progress: Callable[[float], Awaitable[None]],
    cookies: list[str],
) -> Path:
    d = _dir(video_id)
    d.mkdir(parents=True, exist_ok=True)
    out = d / "media.%(ext)s"
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "--newline", "--no-playlist",
        "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "--merge-output-format", "mp4",
        *cookies,
        "-o", str(out), url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    last_sent = 0.0
    last_line = ""
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode(errors="replace").strip()
        if text:
            last_line = text
        m = _PROGRESS_RE.search(text)
        if m:
            pct = float(m.group(1))
            if pct - last_sent >= 2 or pct >= 100:
                last_sent = pct
                await progress(pct)
    await proc.wait()
    if proc.returncode != 0 or not media_path(video_id).exists():
        raise RuntimeError(f"Download failed: {last_line or 'yt-dlp error'}")
    return media_path(video_id)
