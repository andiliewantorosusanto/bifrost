"""Orchestrator: wires audio -> whisper and chat -> translate, and broadcasts
JSON events to connected WebSocket clients. Holds no transcription or
translation logic itself.

Single active session (personal tool); starting a new URL stops the previous one.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from typing import Any, Optional

from . import audio, library
from .chat import ChatError, ChatPoller
from .config import Config
from .translate import Translator
from .whisper_client import WhisperClient

log = logging.getLogger("bifrost.session")

HISTORY = 300  # events replayed to a (re)connecting client
LIVE_CACHE_TTL = 12 * 3600  # restore a live feed only if last saved within 12h


def _fmt_media_time(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _parse_start_offset(url: str) -> int:
    """YouTube's t= parameter (e.g. ?t=269s or &t=4m29s) — start processing there."""
    m = re.search(r"[?&#]t=(?:(\d+)h)?(?:(\d+)m)?(\d+)s?(?:&|$)", url)
    if not m:
        return 0
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s


class Hub:
    """WebSocket fan-out + history replay."""

    def __init__(self) -> None:
        self.clients: set[Any] = set()
        self.transcript: deque[dict] = deque(maxlen=HISTORY)
        self.chat: deque[dict] = deque(maxlen=HISTORY)
        self.source: Optional[dict] = None
        self.status: dict = {"type": "status", "state": "idle"}
        self.chat_status: Optional[dict] = None

    async def add(self, ws: Any) -> None:
        self.clients.add(ws)
        await ws.send_json(self.status)
        if self.source:
            await ws.send_json(self.source)
        if self.chat_status:
            await ws.send_json(self.chat_status)
        for ev in list(self.transcript):
            await ws.send_json(ev)
        if self.chat:
            await ws.send_json({"type": "chat", "items": [e for e in self.chat]})

    def remove(self, ws: Any) -> None:
        self.clients.discard(ws)

    async def send(self, event: dict) -> None:
        if event["type"] == "caption":
            self.transcript.append(event)
        elif event["type"] == "chat":
            self.chat.extend(event["items"])
        elif event["type"] == "status":
            self.status = event
        elif event["type"] == "source":
            self.source = event
        elif event["type"] == "chat_status":
            self.chat_status = event
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def reset(self) -> None:
        self.transcript.clear()
        self.chat.clear()
        self.source = None
        self.chat_status = None


class Session:
    def __init__(self, cfg: Config, hub: Hub):
        self.cfg = cfg
        self.hub = hub
        self.whisper = WhisperClient(cfg.whisper_url)
        self.translator = Translator(cfg.deepl_api_key, cfg.translate_url,
                                     cfg.chat_translate)
        self._tasks: list[asyncio.Task] = []
        self._stream: Optional[audio.ChunkStream] = None
        self._chat: Optional[ChatPoller] = None
        self._caption_seq = 0
        self._url: Optional[str] = None
        self._source_event: Optional[dict] = None
        self._session_captions: list[dict] = []
        self._session_chat: list[dict] = []  # accumulated for live-feed recovery
        self._chat_active = True  # UI gate: poll chat only when panel open + tab visible
        self._download_task: Optional[asyncio.Task] = None
        self._last_whisper_ms: Optional[int] = None
        # Partial transcripts may be saved/resumed only for VOD sessions that
        # cover the video from 0:00 (fresh or resumed-from-cache; not t= starts).
        self._save_allowed = False
        self._chunks_done = 0  # chunks processed by the current audio loop

    # -- lifecycle ---------------------------------------------------------

    def _meta_for_save(self) -> dict:
        src = self._source_event or {}
        return {k: src.get(k) for k in
                ("video_id", "title", "channel", "is_live", "live_status",
                 "duration", "chunk_seconds", "model")}

    async def _push_library(self) -> None:
        await self.hub.send({"type": "library", "items": library.list_items()})

    async def start(self, url: str) -> None:
        await self.stop()
        self.hub.reset()
        self._url = url
        self._session_captions = []
        self._session_chat = []
        await self.hub.send({"type": "status", "state": "probing"})

        # Library cache: replay saved captions (and play saved media) without
        # touching the network or Whisper.
        vid = library.extract_video_id(url)
        entry = library.lookup(vid) if vid else None

        if entry and entry.captions is not None:
            meta = entry.meta
            self._source_event = {
                "type": "source", "video_id": entry.video_id,
                "title": meta.get("title", entry.video_id),
                "channel": meta.get("channel", ""),
                "is_live": False, "live_status": meta.get("live_status", "not_live"),
                "duration": meta.get("duration"),
                "chunk_seconds": self.cfg.chunk_seconds,
                "model": meta.get("model", self.cfg.whisper_model),
                "media": entry.media, "cached": True,
            }
            await self.hub.send(self._source_event)
            await self.hub.send({"type": "status", "state": "running"})
            await self.hub.send({"type": "chat_status", "ok": False,
                                 "message": "Saved video — live chat isn't stored."})
            for cap in entry.captions:
                await self.hub.send(cap)
            if entry.complete:
                await self.hub.send({"type": "status", "state": "ended",
                                     "message": "Loaded from library — no reprocessing."})
                return
            # Partial transcript: replay what we have, then RESUME processing
            # where the previous session stopped.
            if not await self.whisper.healthy():
                await self.hub.send({
                    "type": "status", "state": "error",
                    "message": ("Cached part loaded, but the Whisper engine isn't "
                                "running — start it with ./run.sh to continue."),
                })
                return
            self._session_captions = list(entry.captions)
            self._caption_seq = max((c.get("id", 0) for c in entry.captions), default=0)
            covered = int(entry.covered_s or 0)
            log.info("resuming transcript for %s from %ss", entry.video_id, covered)
            self._stream = audio.ChunkStream(
                url, self.cfg.chunk_seconds, False,
                local_file=library.media_path(entry.video_id) if entry.media else None,
                start_offset=covered, expected_duration=meta.get("duration"),
            )
            self._save_allowed = True
            self._tasks = [
                asyncio.create_task(self._audio_loop(self._stream), name="audio"),
                asyncio.create_task(self._pipeline_monitor(), name="pipeline"),
            ]
            return

        if not await self.whisper.healthy():
            await self.hub.send({
                "type": "status", "state": "error",
                "message": ("Whisper engine is not running. Start it with "
                            "./run.sh or whisper/run-whisper-server.sh, then retry."),
            })
            return

        local_media = entry.media if entry else None  # media saved, captions not
        if local_media and entry:
            meta = entry.meta
            info = audio.SourceInfo(
                video_id=entry.video_id, title=meta.get("title", entry.video_id),
                channel=meta.get("channel", ""), is_live=False,
                live_status=meta.get("live_status", "not_live"),
                duration=meta.get("duration"),
            )
        else:
            try:
                info = await audio.probe(url)
            except RuntimeError as e:
                await self.hub.send({"type": "status", "state": "error", "message": str(e)})
                return
            if info.live_status == "is_upcoming":
                await self.hub.send({"type": "status", "state": "error",
                                     "message": "That stream hasn't started yet."})
                return

        # Single-pull HLS: a fresh VOD watch (no saved file, not live) is pulled
        # once and fanned out to a growing HLS stream the browser plays, gated to
        # the transcribed second — one connection feeds both player and captions.
        use_hls = not info.is_live and not local_media
        hls_dir = library.ensure_hls_dir(info.video_id) if use_hls else None

        self._source_event = {
            "type": "source", "video_id": info.video_id, "title": info.title,
            "channel": info.channel, "is_live": info.is_live,
            "live_status": info.live_status, "duration": info.duration,
            "chunk_seconds": self.cfg.chunk_seconds, "model": self.cfg.whisper_model,
            "media": local_media, "cached": False,
            "hls": library.hls_url(info.video_id) if use_hls else None,
        }
        await self.hub.send(self._source_event)
        await self.hub.send({"type": "status", "state": "running"})

        # Live recovery: if this same broadcast was captured recently (a refresh
        # or a backend/container restart), replay the persisted captions + chat
        # so the feed continues instead of starting blank. New captions keep
        # counting from the last id; live display orders by captured_at, so the
        # restored lines and fresh ones interleave correctly.
        if info.is_live and not local_media:
            cached = library.load_live_session(info.video_id, LIVE_CACHE_TTL)
            if cached:
                self._session_captions = list(cached["captions"])
                self._session_chat = list(cached["chat"])
                self._caption_seq = max(
                    (c.get("id", 0) for c in self._session_captions), default=0)
                log.info("restoring live session for %s: %d captions, %d chat",
                         info.video_id, len(self._session_captions), len(self._session_chat))
                for cap in self._session_captions[-HISTORY:]:
                    await self.hub.send(cap)
                if self._session_chat:
                    await self.hub.send({"type": "chat",
                                         "items": self._session_chat[-HISTORY:]})

        start_offset = 0 if info.is_live else _parse_start_offset(url)
        self._save_allowed = not info.is_live and start_offset == 0
        # The probe skips HLS/DASH manifests for speed; live capture needs
        # them, so live downloads re-extract instead of reusing the probe.
        if info.is_live and info.info_json:
            info.info_json.unlink(missing_ok=True)
            info.info_json = None
        self._stream = audio.ChunkStream(
            url, self.cfg.chunk_seconds, info.is_live, info_json=info.info_json,
            local_file=library.media_path(info.video_id) if local_media else None,
            start_offset=start_offset, expected_duration=info.duration,
            hls_dir=hls_dir,
        )
        self._tasks = [asyncio.create_task(self._audio_loop(self._stream), name="audio"),
                       asyncio.create_task(self._pipeline_monitor(), name="pipeline")]
        if info.is_live:
            self._tasks.append(asyncio.create_task(self._chat_loop(info.video_id), name="chat"))
        else:
            await self.hub.send({"type": "chat_status", "ok": False,
                                 "message": "Not a live stream — no live chat."})

    async def stop(self) -> None:
        if self._download_task:
            self._download_task.cancel()
            self._download_task = None
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        if self._chat:
            self._chat.stop()
            self._chat = None
        if self._stream:
            await self._stream.stop()
            self._stream = None

    async def _pipeline_monitor(self) -> None:
        """Broadcast capture speed every 2s so the UI can show throttling.

        speed = seconds of audio captured per second of wall time:
        ≥1× healthy; <1× on a video means YouTube is throttling the download;
        <1× on a live stream means capture is falling behind the broadcast."""
        loop = asyncio.get_event_loop()
        # Sliding ~14s window: chunk completions are discrete (one per whisper
        # cycle), so a short window quantizes to misleading 0× readings.
        window: deque[tuple[float, float]] = deque(maxlen=7)
        last_stream: Optional[audio.ChunkStream] = None
        while True:
            await asyncio.sleep(2)
            stream = self._stream
            if not stream:
                continue
            if stream is not last_stream:
                window.clear()
                last_stream = stream
            window.append((loop.time(), stream.captured_seconds))
            speed = None
            if len(window) >= 2:
                (t0, c0), (t1, c1) = window[0], window[-1]
                if t1 > t0:
                    speed = max(0.0, (c1 - c0) / (t1 - t0))
            await self.hub.send({
                "type": "pipeline",
                "captured_s": round(stream.captured_seconds, 1),
                "target_s": (self._source_event or {}).get("duration"),
                "speed": round(speed, 2) if speed is not None else None,
                "whisper_ms": self._last_whisper_ms,
                "local": stream.local_file is not None,
            })

    # -- audio track -------------------------------------------------------

    def _save_progress(self, stream: audio.ChunkStream, chunk_idx: int,
                       at_eof: bool = False) -> bool:
        """Persist the transcript so far; returns True if it covers the video."""
        if not (self._save_allowed and self._session_captions and self._source_event):
            return False
        duration = self._source_event.get("duration")
        covered = stream.start_offset + chunk_idx * self.cfg.chunk_seconds
        complete = (at_eof if duration is None
                    else covered >= duration - 2 * self.cfg.chunk_seconds)
        try:
            library.save_captions(self._source_event["video_id"], self._meta_for_save(),
                                  self._session_captions,
                                  covered_s=covered, complete=complete)
        except OSError as e:
            log.warning("could not cache captions: %s", e)
        return complete

    def _save_live_session(self) -> None:
        """Snapshot the live feed (captions + chat) so a refresh or backend
        restart can restore it. No-op for VODs (those use the library cache)."""
        src = self._source_event
        if not (src and src.get("is_live")):
            return
        try:
            library.save_live_session(src["video_id"], self._meta_for_save(),
                                      self._session_captions, self._session_chat)
        except OSError as e:
            log.warning("could not cache live session: %s", e)

    async def _audio_loop(self, stream: audio.ChunkStream) -> None:
        warned_non_english = False
        chunk_idx = 0
        self._chunks_done = 0
        try:
            async for wav, captured_at in stream.chunks():
                # Position of this chunk in the media (capture-relative for live).
                t0 = stream.start_offset + chunk_idx * self.cfg.chunk_seconds
                chunk_idx += 1
                original = None
                t_whisper = asyncio.get_event_loop().time()
                try:
                    res = await self.whisper.transcribe(wav, translate=True)
                    if res.text and self.cfg.dual_transcript:
                        # Second pass: same chunk, source language (e.g. Japanese).
                        try:
                            src = await self.whisper.transcribe(wav, translate=False)
                            if src.text and src.text.strip() != res.text.strip():
                                original = src.text
                        except Exception as e:
                            log.warning("source-language pass failed: %s", e)
                except Exception as e:
                    log.warning("whisper inference failed: %s", e)
                    await self.hub.send({"type": "status", "state": "error",
                                         "message": f"Whisper engine error: {e}"})
                    return
                finally:
                    wav.unlink(missing_ok=True)
                    self._last_whisper_ms = int(
                        (asyncio.get_event_loop().time() - t_whisper) * 1000)
                # Only now is this chunk's audio truly covered — counting it at
                # dequeue time made cancel-saves skip the in-flight chunk.
                self._chunks_done = chunk_idx
                if not res.text:
                    continue
                self._caption_seq += 1
                if res.non_english and not warned_non_english:
                    warned_non_english = True
                    await self.hub.send({
                        "type": "warning",
                        "message": ("Output doesn't look like English — the loaded model may "
                                    "not support the translate task (never use large-v3-turbo)."),
                    })
                event = {
                    "type": "caption", "id": self._caption_seq,
                    "time": _fmt_media_time(t0),
                    "t0": t0, "t1": t0 + self.cfg.chunk_seconds,
                    "captured_at": round(captured_at, 3),
                    "text": res.text, "original": original,
                    "non_english": res.non_english,
                }
                self._session_captions.append(event)
                await self.hub.send(event)
                if stream.is_live:
                    # Live: snapshot every chunk (small atomic write) so a refresh
                    # loses nothing; the library cache/_save_progress is VOD-only.
                    self._save_live_session()
                elif chunk_idx % 12 == 0:
                    # Crash/stop safety: persist progress every ~2 min of audio.
                    self._save_progress(stream, chunk_idx)
            # End of stream: persist whatever was covered. complete=False keeps
            # a stalled/partial transcript resumable instead of poisoning the
            # cache as if it were the whole video. Live is never cached
            # (capture-relative times wouldn't align with the replay).
            if self._save_allowed:
                complete = self._save_progress(stream, chunk_idx, at_eof=True)
                if not complete:
                    log.warning("transcript partial (to %ss) — saved as resumable",
                                stream.start_offset + chunk_idx * self.cfg.chunk_seconds)
                elif stream.hls_dir is not None and self._source_event:
                    # The .ts already on disk ARE the download — remux them into
                    # the permanent media.mp4 (stream copy) so reopening replays
                    # natively from the library. No second network pull.
                    if await library.concat_hls_to_mp4(self._source_event["video_id"]):
                        log.info("built media.mp4 from captured HLS segments")
                await self._push_library()
            self._save_live_session()  # no-op for VOD; finalizes a live snapshot
            await self.hub.send({"type": "status", "state": "ended",
                                 "message": "Stream ended."})
        except asyncio.CancelledError:
            # User stopped / switched videos / source swap: keep finished work.
            # _chunks_done (not chunk_idx) — the in-flight chunk isn't covered.
            self._save_progress(stream, self._chunks_done)
            self._save_live_session()
            raise
        except Exception as e:
            log.exception("audio loop crashed")
            await self.hub.send({"type": "status", "state": "error",
                                 "message": f"Audio pipeline error: {e}"})

    # -- library management --------------------------------------------------

    async def regenerate(self) -> None:
        """Drop the cached transcript for the current video and reprocess it.
        If the media is saved, processing reads the local file (no network)."""
        src = self._source_event
        if not src or not self._url:
            return
        library.delete_captions(src["video_id"])
        await self._push_library()
        await self.start(self._url)

    async def delete_item(self, video_id: str) -> None:
        library.delete(video_id)
        await self._push_library()

    # -- offline download --------------------------------------------------

    async def download_current(self) -> None:
        src = self._source_event
        if not src or not self._url:
            await self.hub.send({"type": "download_status", "state": "error",
                                 "message": "Nothing is playing."})
            return
        if src.get("is_live"):
            await self.hub.send({"type": "download_status", "state": "error",
                                 "message": "A live stream can be saved once it ends."})
            return
        if src.get("media"):
            await self.hub.send({"type": "download_status", "state": "done", "progress": 100})
            return
        if self._download_task and not self._download_task.done():
            return  # already downloading
        self._download_task = asyncio.create_task(
            self._download_loop(self._url, src["video_id"]), name="download")

    async def _download_loop(self, url: str, video_id: str) -> None:
        async def progress(pct: float) -> None:
            await self.hub.send({"type": "download_status", "state": "downloading",
                                 "progress": round(pct, 1)})
        await progress(0)
        try:
            library.save_meta(video_id, self._meta_for_save())
            await library.download_media(url, video_id, progress)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("download failed: %s", e)
            await self.hub.send({"type": "download_status", "state": "error",
                                 "message": str(e)})
            return
        # Hot-swap the player to the local file.
        if self._source_event and self._source_event.get("video_id") == video_id:
            self._source_event = {**self._source_event,
                                  "media": f"/library/{video_id}/media.mp4"}
            await self.hub.send(self._source_event)
        await self.hub.send({"type": "download_status", "state": "done", "progress": 100})
        await self._push_library()
        # The transcription stream no longer needs the network either.
        await self._switch_audio_to_local(video_id)

    async def _switch_audio_to_local(self, video_id: str) -> None:
        """Move an in-flight transcription from the network stream to the just-
        downloaded local file: processing then runs at GPU speed instead of
        being capped by YouTube's (often throttled) delivery."""
        stream, src = self._stream, self._source_event
        if (not stream or not src or src.get("is_live") or stream.local_file
                or src.get("video_id") != video_id or not self._url):
            return
        audio_task = next((t for t in self._tasks if t.get_name() == "audio"), None)
        if not audio_task or audio_task.done():
            return  # transcription already finished
        audio_task.cancel()  # its cancel handler persists progress
        try:
            await audio_task
        except (asyncio.CancelledError, Exception):
            pass
        await stream.stop()
        covered = stream.start_offset + self._chunks_done * self.cfg.chunk_seconds
        log.info("download finished — switching transcription to local media at %ss", covered)
        self._stream = audio.ChunkStream(
            self._url, self.cfg.chunk_seconds, False,
            local_file=library.media_path(video_id),
            start_offset=covered, expected_duration=src.get("duration"),
        )
        self._tasks = [t for t in self._tasks if t is not audio_task and not t.done()]
        self._tasks.append(asyncio.create_task(self._audio_loop(self._stream), name="audio"))

    # -- chat track --------------------------------------------------------

    def set_chat_active(self, active: bool) -> None:
        """UI signal: poll live chat only while its panel is open and the tab is
        visible. Persisted so a poller created later starts in the right state."""
        self._chat_active = active
        if self._chat:
            self._chat.set_active(active)

    async def _chat_loop(self, video_id: str) -> None:
        try:
            self._chat = ChatPoller(self.cfg.youtube_api_key, video_id,
                                    self.cfg.chat_poll_seconds)
            self._chat.set_active(self._chat_active)
        except ChatError as e:
            await self.hub.send({"type": "chat_status", "ok": False, "message": str(e)})
            return
        try:
            await self.hub.send({"type": "chat_status", "ok": True,
                                 "message": "Live chat connected."})
            async for batch in self._chat.messages():
                translated = await self.translator.translate_batch([m.text for m in batch])
                items = [{
                    "author": m.author, "mod": m.is_moderator or m.is_owner,
                    "text": tr.text, "original": tr.original, "src": tr.source_lang,
                    "published_at": m.published_at,  # ISO; client formats in local TZ
                } for m, tr in zip(batch, translated)]
                self._session_chat.extend(items)
                await self.hub.send({"type": "chat", "items": items})
                self._save_live_session()  # keep chat recoverable across a refresh
            await self.hub.send({"type": "chat_status", "ok": False,
                                 "message": "Live chat ended."})
        except ChatError as e:
            await self.hub.send({"type": "chat_status", "ok": False, "message": str(e)})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("chat loop crashed")
            await self.hub.send({"type": "chat_status", "ok": False,
                                 "message": f"Chat error: {e}"})
