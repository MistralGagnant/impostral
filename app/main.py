"""FastAPI application: game WebSocket, audio endpoint, and web client."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .audio import store
from .config import get_settings
from .game import events, stats
from .game.state_machine import GameEngine
from .rooms import rooms

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("impostral")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

app = FastAPI(title="Impostral")
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/config")
async def public_config() -> dict:
    s = get_settings()
    return {
        "num_humans": s.num_humans,
        "num_llms": s.num_llms,
        "max_rounds": s.max_rounds,
        "mock_mode": s.mock_mode,
    }


@app.get("/stats")
async def game_stats() -> dict:
    """Return per-model performance aggregated over all recorded games."""
    return stats.aggregate()


@app.get("/stats.html")
async def stats_page() -> FileResponse:
    return FileResponse(str(WEB_DIR / "stats.html"))


@app.get("/audio/{clip_id}")
async def audio(clip_id: str) -> Response:
    item = store.get(clip_id)
    if item is None:
        return Response(status_code=404)
    data, content_type = item
    return Response(content=data, media_type=content_type)


def _normalize(msg) -> dict:
    """Convert a validated client message into a game-engine payload."""
    t = msg.type
    if t == "audio_blob":
        return {"audio_b64": msg.audio_b64, "text": msg.text}
    if t == "direct_question":
        return {"target": msg.target, "audio_b64": msg.audio_b64, "text": msg.text}
    if t == "submit_vote":
        return {"target": msg.target}
    return {}


async def _maybe_start(room) -> None:
    if room.started or not room.all_humans_ready():
        return
    room.started = True
    engine = GameEngine(room)
    room.engine_task = asyncio.create_task(engine.run())
    log.info("Game started in room %s", room.id)


@app.websocket("/ws/{room_id}")
async def ws_endpoint(ws: WebSocket, room_id: str) -> None:
    await ws.accept()
    room = rooms.get_or_create(room_id)
    seat_id: str | None = None

    try:
        while True:
            raw = await ws.receive_json()
            msg = events.parse_client_message(raw)
            if msg is None:
                continue

            if msg.type == "join":
                seat_id = await room.attach(ws, msg.name)
                seats = [s.public() for s in room.seats.values()]
                await ws.send_json(
                    events.srv_room_state(
                        seats=seats, phase=room.phase.value,
                        round_no=room.round_no, you=seat_id,
                    )
                )
                if seat_id is None:
                    await ws.send_json(events.srv_system(text="Room full: you are spectating."))
                else:
                    await room.broadcast(events.srv_system(
                        text=f"A player joined ({seat_id})."))
                continue

            if seat_id is None:
                continue  # Spectators cannot submit game actions.

            if msg.type == "ready":
                room.ready_seats.add(seat_id)
                await room.broadcast(events.srv_system(text=f"{seat_id} is ready."))
                await _maybe_start(room)
                continue

            # audio_blob / direct_question / submit_vote -> expected input
            room.resolve_input(seat_id, _normalize(msg))

    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("Error in WebSocket loop")
    finally:
        if seat_id:
            room.cancel_input(seat_id)
        room.detach(ws)
        try:
            await room.broadcast(events.srv_system(text="A player disconnected."))
        except Exception:  # noqa: BLE001
            pass
