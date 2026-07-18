"""FastAPI application: game WebSocket, audio endpoint, and web client."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
        "num_humans": s.num_humans,  # Default human count offered on creation.
        "num_llms": s.num_llms,
        "min_humans": s.min_humans,
        "max_humans": s.max_humans,
        "max_rounds": s.max_rounds,
        "mock_mode": s.mock_mode,
        "tts_playback_rate": s.tts_playback_rate,
        "human_wait_seconds": s.human_wait_seconds,
    }


class CreateLobbyRequest(BaseModel):
    name: str
    num_humans: Optional[int] = None


class MatchmakingRequest(BaseModel):
    player_id: str
    session_id: str
    name: str = ""


_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _valid_client_id(value: str) -> bool:
    return bool(_CLIENT_ID_RE.fullmatch(value))


@app.post("/lobby")
async def create_lobby(req: CreateLobbyRequest) -> JSONResponse:
    """Create a lobby with a chosen number of human seats.

    Others then join by typing the lobby name; joining never creates a room.
    """
    s = get_settings()
    name = req.name.strip()
    if not name:
        return JSONResponse({"error": "empty_name"}, status_code=400)

    num_humans = s.num_humans if req.num_humans is None else req.num_humans
    if not s.min_humans <= num_humans <= s.max_humans:
        return JSONResponse(
            {"error": "bad_humans", "min": s.min_humans, "max": s.max_humans},
            status_code=400,
        )

    room = await rooms.create_private(
        name, num_humans=num_humans, num_llms=s.num_llms
    )
    if room is None:
        return JSONResponse({"error": "exists", "name": name}, status_code=409)
    return JSONResponse(
        {"name": name, "num_humans": room.num_humans, "num_llms": room.num_llms}
    )


@app.post("/matchmaking")
async def matchmaking(req: MatchmakingRequest) -> JSONResponse:
    """Reserve a seat in the oldest public lobby, creating one if needed."""
    if not _valid_client_id(req.player_id) or not _valid_client_id(req.session_id):
        return JSONResponse({"error": "bad_identity"}, status_code=400)
    room, token, created = await rooms.matchmake(req.player_id, req.session_id)
    return JSONResponse({
        "room_id": room.id,
        "reservation_token": token,
        "created": created,
    })


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
    if t == "submit_vote":
        return {"target": msg.target}
    return {}


async def _launch_game(room, *, allow_partial: bool = False) -> None:
    if room.started:
        return
    if allow_partial:
        room.keep_connected_humans()
        if not room.connected_humans():
            return
    elif not room.all_humans_ready():
        return

    room.started = True
    room.status = "running"
    room.updated_at = time.time()
    wait_task = room.start_wait_task
    if wait_task and wait_task is not asyncio.current_task() and not wait_task.done():
        wait_task.cancel()
    await room.broadcast(events.srv_system(
        text=(
            f"Starting with {room.num_humans} human player"
            f"{'s' if room.num_humans != 1 else ''}."
        )
    ))
    await room.broadcast(events.srv_room_state(
        seats=[s.public() for s in room.seats.values()],
        phase=room.phase.value,
        round_no=room.round_no,
        you=None,
        auto_ready=room.visibility == "public",
        lobby_wait_remaining=0,
    ))
    engine = GameEngine(room)
    room.engine_task = asyncio.create_task(engine.run())
    room.engine_task.add_done_callback(
        lambda _task: asyncio.create_task(rooms.cleanup())
    )
    log.info("Game started in room %s", room.id)


async def _start_after_wait(room, delay: float) -> None:
    try:
        await asyncio.sleep(max(0, delay))
        await _launch_game(room, allow_partial=True)
    except asyncio.CancelledError:
        return


async def _maybe_start(room) -> None:
    if room.started:
        return
    if room.all_humans_ready():
        await _launch_game(room)
        return
    if not room.connected_humans():
        return

    now = time.time()
    if not room.start_deadline:
        wait_seconds = max(0, get_settings().human_wait_seconds)
        room.start_deadline = now + wait_seconds
        await room.broadcast(events.srv_system(
            text=(
                f"Waiting up to {wait_seconds} seconds for more human players. "
                "It should be quick."
            )
        ))
        room.start_wait_task = asyncio.create_task(
            _start_after_wait(room, wait_seconds)
        )
    elif room.start_deadline <= now:
        await _launch_game(room, allow_partial=True)


@app.websocket("/ws/{room_id}")
async def ws_endpoint(ws: WebSocket, room_id: str) -> None:
    await ws.accept()
    room = rooms.get(room_id)
    seat_id: str | None = None

    try:
        while True:
            raw = await ws.receive_json()
            msg = events.parse_client_message(raw)
            if msg is None:
                continue

            if msg.type == "join":
                if room is None:
                    # Joining never creates a lobby: the name must exist.
                    await ws.send_json(events.srv_system(
                        text=f"No lobby named “{room_id}”. Create it first.",
                        code="room_missing",
                    ))
                    break
                seat = await room.attach(
                    ws,
                    msg.name,
                    player_id=msg.player_id,
                    session_id=msg.session_id,
                    reservation_token=msg.reservation_token,
                )
                seat_id = seat.id if seat is not None else None
                if seat_id is None and room.visibility == "public":
                    await ws.send_json(events.srv_system(
                        text="Your matchmaking reservation expired. Click Play again.",
                        code="reservation_expired",
                    ))
                    break
                seats = [s.public() for s in room.seats.values()]
                await ws.send_json(
                    events.srv_room_state(
                        seats=seats, phase=room.phase.value,
                        round_no=room.round_no, you=seat_id,
                        auto_ready=room.visibility == "public",
                        lobby_wait_remaining=room.lobby_wait_remaining(),
                    )
                )
                if seat_id is None:
                    await ws.send_json(events.srv_system(text="Room full: you are spectating."))
                else:
                    if room.visibility == "public":
                        room.ready_seats.add(seat_id)
                    await room.broadcast(events.srv_system(
                        text=f"A player joined ({seat_id})."))
                    await room.resend_pending(seat_id)
                    await _maybe_start(room)
                    await room.broadcast(events.srv_room_state(
                        seats=[s.public() for s in room.seats.values()],
                        phase=room.phase.value,
                        round_no=room.round_no,
                        you=None,
                        auto_ready=room.visibility == "public",
                        lobby_wait_remaining=room.lobby_wait_remaining(),
                    ))
                continue

            if seat_id is None:
                continue  # Spectators cannot submit game actions.

            if msg.type == "ready":
                room.ready_seats.add(seat_id)
                await room.broadcast(events.srv_system(text=f"{seat_id} is ready."))
                await _maybe_start(room)
                continue

            if msg.type == "playback_complete":
                room.resolve_playback(seat_id, msg.playback_id)
                continue

            # audio_blob / submit_vote -> expected input
            room.resolve_input(seat_id, _normalize(msg))

    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("Error in WebSocket loop")
    finally:
        if room is not None:
            was_attached = room.seat_of(ws) is not None
            room.detach(ws)
            if was_attached:
                try:
                    await room.broadcast(events.srv_system(text="A player disconnected."))
                    await room.broadcast(events.srv_room_state(
                        seats=[s.public() for s in room.seats.values()],
                        phase=room.phase.value,
                        round_no=room.round_no,
                        you=None,
                        auto_ready=room.visibility == "public",
                        lobby_wait_remaining=room.lobby_wait_remaining(),
                    ))
                except Exception:  # noqa: BLE001
                    pass
            await rooms.cleanup()
            asyncio.create_task(_cleanup_after_reconnect_grace())


async def _cleanup_after_reconnect_grace() -> None:
    await asyncio.sleep(max(1, get_settings().reconnect_grace_seconds))
    await rooms.cleanup()
