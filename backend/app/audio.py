"""Audio module: yt-dlp pulls the stream, ffmpeg resamples to 16 kHz mono WAV
and segments it into fixed-length chunks on disk. Yields finished chunk paths
in order. Does NOT transcribe — that's the whisper module's job.

A chunk is considered finished when the next segment file appears (the segment
muxer opens N+1 only after closing N) or when ffmpeg has exited.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

log = logging.getLogger("bifrost.audio")

RECONNECT_DELAYS = [2, 5, 10, 20, 30]  # backoff for dropped live streams

# Optional Netscape cookie jar for members-only videos (exported on the host
# with `make cookies`; the container can't read browser stores — Chrome
# encrypts them via the macOS Keychain). Empty/missing file = disabled.
COOKIES_FILE = Path(os.environ.get(
    "BIFROST_COOKIES",
    Path(__file__).resolve().parent.parent.parent / "cookies.txt",
))


def cookie_args() -> list[str]:
    try:
        if COOKIES_FILE.stat().st_size > 0:
            return ["--cookies", str(COOKIES_FILE)]
    except OSError:
        pass
    return []


@dataclass
class SourceInfo:
    video_id: str
    title: str
    channel: str
    is_live: bool
    live_status: str  # is_live | was_live | not_live | post_live | is_upcoming
    duration: Optional[int]  # seconds; None for live
    info_json: Optional[Path] = None  # saved extraction, reused by the downloader


async def probe(url: str) -> SourceInfo:
    """yt-dlp -J metadata probe. Raises RuntimeError with yt-dlp's message on failure.

    The full info JSON (with signed media URLs) is saved so the first download
    can skip a second extraction — YouTube extraction can take a minute.
    """
    # skip=hls,dash: manifest playlists list every media segment, so parsing
    # them scales with video length (~84s for a 2h replay vs ~2s without).
    # Direct https audio formats remain, which is all the VOD downloader uses;
    # live streams DO need manifests, so their download re-extracts fresh.
    # --ignore-no-formats-error: an in-progress live has ONLY hls/dash formats,
    # so skipping them leaves zero formats and yt-dlp would abort with "No video
    # formats found". The flag lets -J still emit metadata (is_live etc.); the
    # live download path re-extracts without the skip, so it still gets formats.
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "-J", "--no-download", "--no-playlist", "--ignore-no-formats-error",
        "--extractor-args", "youtube:skip=hls,dash", *cookie_args(), url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        msg = (err.decode(errors="replace").strip().splitlines() or ["yt-dlp failed"])[-1]
        raise RuntimeError(f"Could not read that URL: {msg}")
    meta = json.loads(out)
    info_path = Path(tempfile.mkstemp(prefix="bifrost-info-", suffix=".json")[1])
    info_path.write_bytes(out)
    return SourceInfo(
        video_id=meta.get("id", ""),
        title=meta.get("title", "Untitled"),
        channel=meta.get("channel") or meta.get("uploader") or "",
        is_live=bool(meta.get("is_live")),
        live_status=meta.get("live_status") or ("is_live" if meta.get("is_live") else "not_live"),
        duration=meta.get("duration"),
        info_json=info_path,
    )


# Video format for the single-pull HLS path: H.264 (avc1) so ffmpeg can COPY it
# into the .ts segments (no software re-encode — the container has no Metal/VT,
# so re-encoding would cook the fanless Air). Capped at 720p to bound bandwidth.
HLS_VIDEO_FORMAT = "bv*[vcodec^=avc1][height<=720]+ba[ext=m4a]/b[ext=mp4]/b"


class ChunkStream:
    """Owns the yt-dlp|ffmpeg pipeline and a temp dir of WAV segments.

    In HLS mode (``hls_dir`` set) the SAME ffmpeg also fans the pulled stream
    out to a growing HLS playlist (``stream.m3u8`` + ``seg_*.ts``) the browser
    plays — one network pull serves both the player and the captions. The WAV
    segmentation (for Whisper) is unchanged; the .ts/playlist are written
    straight into the served library dir and the consumer never touches them."""

    def __init__(self, url: str, chunk_seconds: int, is_live: bool,
                 info_json: Optional[Path] = None, local_file: Optional[Path] = None,
                 start_offset: int = 0, expected_duration: Optional[int] = None,
                 hls_dir: Optional[Path] = None):
        self.url = url
        self.chunk_seconds = chunk_seconds
        self.is_live = is_live
        self.info_json = info_json
        self.local_file = local_file  # chunk a downloaded file instead of streaming
        self.start_offset = start_offset            # seconds into the media to begin
        self.expected_duration = expected_duration  # VOD length; enables stall resume
        self.hls_dir = hls_dir  # write the player's HLS stream here (single-pull mode)
        self.workdir = Path(tempfile.mkdtemp(prefix="bifrost-audio-"))
        # Seconds of audio captured so far (incl. the chunk being written) —
        # read by the session's pipeline monitor for the UI speed readout.
        self.captured_seconds: float = float(start_offset)
        self._stopped = asyncio.Event()
        self._ytdlp: Optional[asyncio.subprocess.Process] = None
        self._ffmpeg: Optional[asyncio.subprocess.Process] = None

    async def _spawn(self, attempt_dir: Path, fresh: bool, from_seconds: int = 0) -> None:
        attempt_dir.mkdir(parents=True, exist_ok=True)
        if self.local_file is not None:
            # Saved media on disk: ffmpeg reads it directly, no yt-dlp.
            seek = ["-ss", str(from_seconds)] if from_seconds else []
            self._ffmpeg = await asyncio.create_subprocess_exec(
                "ffmpeg", "-loglevel", "error", *seek, "-i", str(self.local_file),
                "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                "-f", "segment", "-segment_time", str(self.chunk_seconds),
                "-reset_timestamps", "1",
                str(attempt_dir / "chunk_%06d.wav"),
                stderr=asyncio.subprocess.DEVNULL,
            )
            return
        # First attempt reuses the probe's extraction; resumes/reconnects
        # re-extract because the signed media URLs in it eventually expire.
        if not fresh and self.info_json and self.info_json.exists():
            source_args = ["--load-info-json", str(self.info_json)]
        else:
            source_args = [self.url]
        if from_seconds and not self.is_live:
            # Start (or resume after a stall) mid-video.
            source_args = ["--download-sections", f"*{from_seconds}-", *source_args]
        if self.hls_dir is not None:
            # Single-pull HLS: pull H.264 video + audio (not bestaudio) so the
            # same stream can feed both the player and Whisper.
            fmt = HLS_VIDEO_FORMAT
        elif not self.is_live:
            # VODs download from direct https formats; skipping the segment
            # manifests makes fresh extractions fast (see probe()). Live needs
            # manifests, so this only applies to videos.
            fmt = "bestaudio/best"
            source_args = ["--extractor-args", "youtube:skip=hls,dash", *source_args]
        else:
            fmt = "bestaudio/best"
        # OS-level pipe between yt-dlp and ffmpeg (an asyncio StreamReader can't
        # be used as another process's stdin).
        r_fd, w_fd = os.pipe()
        err_file = open(attempt_dir / "ytdlp.err", "wb")
        try:
            self._ytdlp = await asyncio.create_subprocess_exec(
                "yt-dlp", "-q", "--no-playlist", "-f", fmt, "-o", "-",
                *cookie_args(), *source_args,
                stdout=w_fd, stderr=err_file,
            )
            self._ffmpeg = await asyncio.create_subprocess_exec(
                "ffmpeg", "-loglevel", "error", "-i", "pipe:0",
                *self._hls_output_args(),
                "-map", "0:a:0", "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                "-f", "segment", "-segment_time", str(self.chunk_seconds),
                "-reset_timestamps", "1",
                str(attempt_dir / "chunk_%06d.wav"),
                stdin=r_fd, stderr=asyncio.subprocess.DEVNULL,
            )
        finally:
            # Children hold their own copies; release the parent's.
            os.close(r_fd)
            os.close(w_fd)
            err_file.close()

    def _hls_output_args(self) -> list[str]:
        """The ffmpeg HLS output (player stream), or [] when not in HLS mode.

        Video is COPIED (avc1 source) so there's no software re-encode; audio is
        transcoded to AAC for the .ts. An EVENT playlist grows as segments land
        and gets #EXT-X-ENDLIST on clean exit (so hls.js flips live→VOD). Resumes
        after a stall continue the segment numbering and append to the playlist;
        a killed (stalled) ffmpeg never wrote ENDLIST, so appending stays valid."""
        if self.hls_dir is None:
            return []
        seg = len(list(self.hls_dir.glob("seg_*.ts")))
        return [
            "-map", "0:v:0", "-c:v", "copy", "-map", "0:a:0", "-c:a", "aac",
            "-f", "hls", "-hls_time", str(self.chunk_seconds),
            "-hls_playlist_type", "event",
            "-hls_flags", "independent_segments+append_list",
            "-start_number", str(seg),
            "-hls_segment_filename", str(self.hls_dir / "seg_%06d.ts"),
            str(self.hls_dir / "stream.m3u8"),
        ]

    async def _kill(self) -> None:
        for proc in (self._ytdlp, self._ffmpeg):
            if proc and proc.returncode is None:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except asyncio.TimeoutError:
                    pass
        self._ytdlp = self._ffmpeg = None

    def _full_chunk_bytes(self) -> int:
        # 16 kHz mono s16le WAV: 44-byte header + 32000 bytes per second.
        return 44 + 32000 * self.chunk_seconds

    async def chunks(self) -> AsyncIterator[tuple[Path, float]]:
        """Yield (chunk, completed_at) in order.

        Live streams reconnect on drops. VODs RESUME from the last completed
        chunk when the download stalls before the video's known duration —
        otherwise a mid-download failure silently truncates the transcript.

        completed_at is the wall time the chunk file finished — stamped here,
        not when the consumer gets around to it, so a Whisper backlog can't
        make captions look fresher than they are."""
        attempt = 0
        total_full = 0  # completed full-length chunks across all attempts
        produced_total = False
        while not self._stopped.is_set():
            attempt_dir = self.workdir / f"a{attempt:03d}"
            from_seconds = self.start_offset + total_full * self.chunk_seconds
            try:
                await self._spawn(attempt_dir, fresh=attempt > 0,
                                  from_seconds=from_seconds)
            except FileNotFoundError as e:
                raise RuntimeError(f"Missing binary: {e}") from e

            produced_any = False
            next_idx = 0
            while not self._stopped.is_set():
                ffmpeg_done = self._ffmpeg is not None and self._ffmpeg.returncode is not None
                current = attempt_dir / f"chunk_{next_idx:06d}.wav"
                nxt = attempt_dir / f"chunk_{next_idx + 1:06d}.wav"
                try:
                    partial = max(0, current.stat().st_size - 44) / 32000
                except OSError:
                    partial = 0.0
                self.captured_seconds = (self.start_offset
                                         + total_full * self.chunk_seconds + partial)
                if current.exists() and (nxt.exists() or ffmpeg_done):
                    is_partial = current.stat().st_size < self._full_chunk_bytes() * 0.95
                    covered = self.start_offset + (total_full + 1) * self.chunk_seconds
                    if (is_partial and ffmpeg_done and not nxt.exists()
                            and not self.is_live and self.expected_duration
                            and covered < self.expected_duration - 2 * self.chunk_seconds):
                        # Trailing fragment of a stalled download: drop it; the
                        # resume attempt re-fetches this region cleanly.
                        break
                    produced_any = True
                    if not is_partial:
                        total_full += 1
                    yield current, time.time()
                    next_idx += 1
                    continue
                if ffmpeg_done:
                    break
                await asyncio.sleep(0.5)

            await self._kill()
            if self._stopped.is_set():
                break
            if not self.is_live:
                if produced_any:
                    produced_total = True
                covered = self.start_offset + total_full * self.chunk_seconds
                done = (covered >= self.expected_duration - 2 * self.chunk_seconds
                        if self.expected_duration else produced_total)
                if done:
                    break  # VOD finished (full coverage, or best effort w/o duration)
                # Nothing yet, or stalled mid-video: retry/resume with backoff.
                attempt += 1
                if attempt >= 20:
                    raise RuntimeError(
                        f"Download kept failing after {attempt} attempts "
                        f"(got {covered - self.start_offset}s"
                        + (f" of {self.expected_duration}s" if self.expected_duration else "")
                        + "). Last error: " + self._last_error(attempt_dir)
                    )
                delay = RECONNECT_DELAYS[min(attempt - 1, len(RECONNECT_DELAYS) - 1)]
                log.warning("VOD download stalled at %ss; retrying in %ss (attempt %d)",
                            covered, delay, attempt)
                try:
                    await asyncio.wait_for(self._stopped.wait(), delay)
                except asyncio.TimeoutError:
                    pass
                continue
            # Live stream dropped: back off and reconnect.
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            if not produced_any:
                attempt += 1
                if attempt >= len(RECONNECT_DELAYS):
                    log.warning("giving up reconnecting after %d empty attempts", attempt)
                    break
            else:
                attempt += 1
            log.info("live stream dropped; reconnecting in %ss", delay)
            try:
                await asyncio.wait_for(self._stopped.wait(), delay)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _last_error(attempt_dir: Path) -> str:
        try:
            lines = (attempt_dir / "ytdlp.err").read_text(errors="replace").strip().splitlines()
            for line in reversed(lines):
                if line.startswith("ERROR"):
                    return line
            return lines[-1] if lines else "yt-dlp produced no output"
        except OSError:
            return "yt-dlp produced no output"

    async def stop(self) -> None:
        self._stopped.set()
        await self._kill()
        shutil.rmtree(self.workdir, ignore_errors=True)
        if self.info_json:
            self.info_json.unlink(missing_ok=True)
