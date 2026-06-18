"""Chat module: YouTube Data API v3 live-chat poller. Fetches raw messages only —
translation happens in the translate module.

Flow per §6 of the brief:
  videos.list(part=liveStreamingDetails) -> activeLiveChatId
  -> poll liveChatMessages.list, respecting pollingIntervalMillis.

Auth: a plain API key reads most public live chats; some streams 403. We surface
that as a ChatError rather than pretending the key always works.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger("bifrost.chat")


@dataclass
class ChatMessage:
    author: str
    text: str
    published_at: str
    is_moderator: bool
    is_owner: bool


class ChatError(Exception):
    """User-facing chat problem (no key, no live chat, auth required, quota)."""


def _classify_http_error(e: HttpError) -> ChatError:
    status = e.resp.status if e.resp else 0
    reason = ""
    try:
        reason = e.error_details[0].get("reason", "")  # type: ignore[index]
    except Exception:
        pass
    if status == 403 and reason in ("quotaExceeded", "rateLimitExceeded"):
        return ChatError("YouTube API quota exceeded — chat paused until quota resets.")
    if status in (401, 403):
        return ChatError(
            "YouTube refused chat access with this API key (this stream may require OAuth). "
            "Audio translation continues without chat."
        )
    if status == 404:
        return ChatError("Live chat not found — it may be disabled for this stream.")
    return ChatError(f"YouTube chat error ({status} {reason}).")


class ChatPoller:
    def __init__(self, api_key: str, video_id: str, poll_floor_seconds: int = 10):
        if not api_key:
            raise ChatError("No YouTube API key configured — chat panel disabled.")
        self._yt = build("youtube", "v3", developerKey=api_key, cache_discovery=False)
        self.video_id = video_id
        # Min ms between polls (quota knob, from config). YouTube's suggested
        # interval is honored when longer. Floor at 1s to avoid a busy loop.
        self._poll_floor_ms = max(int(poll_floor_seconds), 1) * 1000
        self._stopped = asyncio.Event()
        # Gates polling: each poll costs 1 quota unit regardless of message volume,
        # so we don't poll while the chat panel is closed or the tab is hidden.
        self._active = asyncio.Event()
        self._active.set()  # default on; the session applies the UI's state

    def set_active(self, active: bool) -> None:
        self._active.set() if active else self._active.clear()

    def _active_chat_id_sync(self) -> Optional[str]:
        resp = self._yt.videos().list(
            part="liveStreamingDetails", id=self.video_id
        ).execute()
        items = resp.get("items", [])
        if not items:
            raise ChatError("Video not found via the YouTube Data API.")
        details = items[0].get("liveStreamingDetails", {})
        return details.get("activeLiveChatId")

    def _poll_sync(self, chat_id: str, page_token: Optional[str]):
        return self._yt.liveChatMessages().list(
            liveChatId=chat_id,
            part="snippet,authorDetails",
            maxResults=200,
            pageToken=page_token,
        ).execute()

    async def messages(self) -> AsyncIterator[list[ChatMessage]]:
        """Yield batches of new chat messages until the stream/chat ends."""
        try:
            chat_id = await asyncio.to_thread(self._active_chat_id_sync)
        except HttpError as e:
            raise _classify_http_error(e) from e
        if not chat_id:
            raise ChatError("This video has no active live chat.")

        page_token: Optional[str] = None
        first_page = True
        while not self._stopped.is_set():
            if not self._active.is_set():
                # Panel closed / tab hidden — stop spending quota. Wait until it
                # reopens (stop() also sets _active so this releases on shutdown).
                await self._active.wait()
                if self._stopped.is_set():
                    return
                # Resume at "now": drop the page accumulated while paused so we
                # don't replay a backlog the user wasn't watching.
                page_token, first_page = None, True
                continue
            try:
                resp = await asyncio.to_thread(self._poll_sync, chat_id, page_token)
            except HttpError as e:
                raise _classify_http_error(e) from e

            if resp.get("offlineAt"):
                log.info("live chat went offline")
                return

            batch = []
            for item in resp.get("items", []):
                sn, au = item.get("snippet", {}), item.get("authorDetails", {})
                text = sn.get("displayMessage", "")
                if not text:
                    continue
                batch.append(ChatMessage(
                    author=au.get("displayName", "?"),
                    text=text,
                    published_at=sn.get("publishedAt", ""),
                    is_moderator=bool(au.get("isChatModerator")),
                    is_owner=bool(au.get("isChatOwner")),
                ))
            # The first page is backlog; skip it so we start at "now" like YouTube does.
            if batch and not first_page:
                yield batch
            first_page = False

            page_token = resp.get("nextPageToken")
            # Floor the poll interval at config's chat_poll_seconds (default 10s):
            # each poll is 1 quota unit, so 2s ≈ 1,800 u/h vs 10s ≈ 360 u/h.
            # Captions already lag ~chunk_seconds, so a slower chat poll is fine.
            floor = self._poll_floor_ms
            interval = max(resp.get("pollingIntervalMillis", floor), floor) / 1000
            try:
                await asyncio.wait_for(self._stopped.wait(), interval)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()
        self._active.set()  # release a paused poll loop so messages() can exit
