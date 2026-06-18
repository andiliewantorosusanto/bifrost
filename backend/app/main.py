"""FastAPI entry point: serves the Bifröst frontend and the /ws WebSocket.

Client -> server: {"action": "start", "url": "..."} | {"action": "stop"}
Server -> client: status / source / caption / chat / chat_status / warning events.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from . import config, library
from .session import Hub, Session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("bifrost")

FRONTEND = Path(os.environ.get(
    "BIFROST_FRONTEND",
    Path(__file__).resolve().parent.parent.parent / "frontend" / "dist",
))

cfg = config.load()
app = FastAPI(title="Bifröst")
hub = Hub()
session = Session(cfg, hub)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await hub.add(ws)
    await ws.send_json({"type": "library", "items": library.list_items()})
    try:
        while True:
            msg = await ws.receive_json()
            action = msg.get("action")
            if action == "start" and msg.get("url"):
                await session.start(msg["url"].strip())
            elif action == "download":
                await session.download_current()
            elif action == "regenerate":
                await session.regenerate()
            elif action == "delete_item" and msg.get("video_id"):
                await session.delete_item(msg["video_id"])
            elif action == "stop":
                await session.stop()
                hub.reset()
                await hub.send({"type": "status", "state": "idle"})
            elif action == "chat_active":
                session.set_chat_active(bool(msg.get("active", True)))
    except (WebSocketDisconnect, RuntimeError):
        # RuntimeError: starlette raises it when receiving after a disconnect
        # that arrived while we were busy handling an action.
        pass
    finally:
        hub.remove(ws)


@app.on_event("shutdown")
async def shutdown() -> None:
    await session.stop()
    await session.whisper.close()
    await session.translator.close()


class SPAStatic(StaticFiles):
    """Cache-Control for a hashed-asset SPA: bundles under /assets/ are immutable
    (their hash changes on every content change), so cache them hard; index.html
    must always revalidate or a rebuild's new asset hashes go unseen — the cause
    of a stale entry point pointing at a deleted bundle (blank page)."""

    async def get_response(self, path: str, scope: Scope):
        resp = await super().get_response(path, scope)
        if path.startswith("assets/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"  # revalidate (304 if unchanged)
        return resp


# Sub-mounts must come before / (the root mount catches everything).
library.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/library", StaticFiles(directory=str(library.LIBRARY_DIR)), name="library")
app.mount("/", SPAStatic(directory=str(FRONTEND), html=True), name="frontend")


def run() -> None:
    import uvicorn
    uvicorn.run(app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    run()
