"""FastAPI application: game WebSocket, audio endpoint, and web client."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audio import store
from .config import get_settings
from .game import events, stats
from .game.state_machine import GameEngine
from .rooms import rooms
from .turnstile import GAME_ENTRY_ACTION, verify_turnstile

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


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(str(ASSETS_DIR / "favicon.ico"), media_type="image/x-icon")


@app.get("/robots.txt", include_in_schema=False)
async def robots() -> FileResponse:
    return FileResponse(str(WEB_DIR / "robots.txt"), media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap() -> FileResponse:
    return FileResponse(str(WEB_DIR / "sitemap.xml"), media_type="application/xml")


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
        "turnstile_enabled": s.turnstile_required,
        "turnstile_site_key": s.turnstile_site_key if s.turnstile_required else "",
    }


class CreateLobbyRequest(BaseModel):
    name: str
    num_humans: Optional[int] = None
    player_id: str = ""
    session_id: str = ""
    turnstile_token: str = Field("", max_length=2048)


class JoinLobbyRequest(BaseModel):
    player_id: str
    session_id: str
    turnstile_token: str = Field("", max_length=2048)


class MatchmakingRequest(BaseModel):
    player_id: str
    session_id: str
    name: str = ""
    turnstile_token: str = Field("", max_length=2048)


_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _valid_client_id(value: str) -> bool:
    return bool(_CLIENT_ID_RE.fullmatch(value))


async def _validate_game_entry(
    request: Request, turnstile_token: str
) -> Optional[JSONResponse]:
    """Return an error response when browser admission cannot be verified."""
    settings = get_settings()
    if not settings.turnstile_required:
        return None

    verification = await verify_turnstile(
        turnstile_token,
        secret_key=settings.turnstile_secret_key,
        expected_hostname=request.url.hostname or "",
        expected_action=GAME_ENTRY_ACTION,
    )
    if verification.allowed:
        log.info(
            "Turnstile validation accepted: action=%s hostname=%s",
            GAME_ENTRY_ACTION,
            request.url.hostname,
        )
        return None

    log.warning(
        "Turnstile validation failed: action=%s hostname=%s reason=%s unavailable=%s",
        GAME_ENTRY_ACTION,
        request.url.hostname,
        verification.reason,
        verification.unavailable,
    )
    if verification.unavailable:
        return JSONResponse(
            {"error": "security_check_unavailable"}, status_code=503
        )
    return JSONResponse({"error": "security_check_failed"}, status_code=403)


@app.post("/lobby")
async def create_lobby(req: CreateLobbyRequest, request: Request) -> JSONResponse:
    """Create a lobby with a chosen number of human seats.

    Others then join by typing the lobby name; joining never creates a room.
    """
    s = get_settings()
    name = req.name.strip()
    if not name:
        return JSONResponse({"error": "empty_name"}, status_code=400)
    if not _valid_client_id(req.player_id) or not _valid_client_id(req.session_id):
        return JSONResponse({"error": "bad_identity"}, status_code=400)

    num_humans = s.num_humans if req.num_humans is None else req.num_humans
    if not s.min_humans <= num_humans <= s.max_humans:
        return JSONResponse(
            {"error": "bad_humans", "min": s.min_humans, "max": s.max_humans},
            status_code=400,
        )

    admission_error = await _validate_game_entry(request, req.turnstile_token)
    if admission_error is not None:
        return admission_error

    room, token, _created = await rooms.create_private_and_reserve(
        name,
        num_humans=num_humans,
        num_llms=s.num_llms,
        player_id=req.player_id,
        session_id=req.session_id,
    )
    if room is None:
        return JSONResponse({"error": "exists", "name": name}, status_code=409)
    return JSONResponse(
        {
            "name": name,
            "num_humans": room.num_humans,
            "num_llms": room.num_llms,
            "reservation_token": token,
        }
    )


@app.post("/lobby/{room_id}/join")
async def join_lobby(
    room_id: str, req: JoinLobbyRequest, request: Request
) -> JSONResponse:
    """Reserve a human seat in an existing private lobby."""
    name = room_id.strip()
    if not name:
        return JSONResponse({"error": "empty_name"}, status_code=400)
    if not _valid_client_id(req.player_id) or not _valid_client_id(req.session_id):
        return JSONResponse({"error": "bad_identity"}, status_code=400)

    admission_error = await _validate_game_entry(request, req.turnstile_token)
    if admission_error is not None:
        return admission_error

    room, token, error = await rooms.reserve_private(
        name, req.player_id, req.session_id
    )
    if error == "missing":
        return JSONResponse({"error": "missing", "name": name}, status_code=404)
    if error == "started":
        return JSONResponse({"error": "started", "name": name}, status_code=409)
    if error == "full":
        return JSONResponse({"error": "full", "name": name}, status_code=409)
    return JSONResponse({"name": room.id, "reservation_token": token})


@app.post("/matchmaking")
async def matchmaking(req: MatchmakingRequest, request: Request) -> JSONResponse:
    """Reserve a seat in the oldest public lobby, creating one if needed."""
    if not _valid_client_id(req.player_id) or not _valid_client_id(req.session_id):
        return JSONResponse({"error": "bad_identity"}, status_code=400)
    admission_error = await _validate_game_entry(request, req.turnstile_token)
    if admission_error is not None:
        return admission_error
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


def _room_state(room, *, you: Optional[str] = None) -> dict:
    """Build a lobby state without exposing which anonymous seats are human."""
    return events.srv_room_state(
        seats=[seat.public() for seat in room.seats.values()],
        phase=room.phase.value,
        round_no=room.round_no,
        you=you,
        auto_ready=room.visibility == "public",
        lobby_wait_remaining=room.lobby_wait_remaining(),
        visibility=room.visibility,
        connected_humans=len(room.connected_humans()),
        expected_humans=room.num_humans,
        is_host=room.is_host(you) if you else None,
        started=room.started,
    )


async def _broadcast_room_state(room) -> None:
    """Send personalized host permissions to every connected human."""
    for seat in list(room.connected_humans()):
        await room.send_seat(seat.id, _room_state(room, you=seat.id))


async def _launch_game(room, *, allow_partial: bool = False) -> None:
    if room.started or room.status != "waiting":
        return
    connected_humans = room.connected_humans()
    if not connected_humans:
        return
    if not allow_partial and len(connected_humans) < room.num_humans:
        return
    room.keep_connected_humans()

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
    await _broadcast_room_state(room)
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
    if room.started or room.visibility != "public":
        return
    if len(room.connected_humans()) >= room.num_humans:
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


async def _start_private_game(room, seat_id: str) -> bool:
    """Start a private lobby only when its connected creator requests it."""
    if room.started or not room.is_host(seat_id):
        return False
    await _launch_game(room, allow_partial=True)
    return room.started


def _same_origin_websocket(ws: WebSocket) -> bool:
    """Accept browser sockets only when their Origin matches the request host."""
    origin = ws.headers.get("origin", "")
    host = ws.headers.get("host", "")
    parsed = urlparse(origin)
    return (
        parsed.scheme in {"http", "https"}
        and bool(host)
        and parsed.netloc.lower() == host.lower()
    )


@app.websocket("/ws/{room_id}")
async def ws_endpoint(ws: WebSocket, room_id: str) -> None:
    if not _same_origin_websocket(ws):
        log.warning("Rejected WebSocket with an invalid Origin header")
        await ws.close(code=1008)
        return
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
                if seat_id is None:
                    await ws.send_json(events.srv_system(
                        text="Your seat reservation expired. Click Play or join again.",
                        code="reservation_expired",
                    ))
                    break
                await room.broadcast(events.srv_system(
                    text=f"A player joined ({seat_id})."))
                await room.resend_pending(seat_id)
                await _maybe_start(room)
                await _broadcast_room_state(room)
                continue

            if seat_id is None:
                continue  # Spectators cannot submit game actions.

            if msg.type == "start_game":
                if not await _start_private_game(room, seat_id):
                    await ws.send_json(events.srv_system(
                        text="Only the private lobby host can start the game.",
                        code="host_only",
                    ))
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
                    await _broadcast_room_state(room)
                except Exception:  # noqa: BLE001
                    pass
            await rooms.cleanup()
            asyncio.create_task(_cleanup_after_reconnect_grace())


async def _cleanup_after_reconnect_grace() -> None:
    await asyncio.sleep(max(1, get_settings().reconnect_grace_seconds))
    await rooms.cleanup()
