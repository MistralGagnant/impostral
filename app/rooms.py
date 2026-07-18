"""Rooms, seats, and WebSocket connections.

A room contains human and LLM seats, the game transcript, and open connections.
The game flow lives in `game/state_machine.py`; this module owns shared state and
routes human input.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .agents.llm_agent import LLMAgent
from .config import get_settings
from .game.events import Phase

log = logging.getLogger("impostral.rooms")


@dataclass
class Seat:
    id: str  # "Player A", ...
    kind: str  # "human" | "llm"
    voice: str
    alive: bool = True
    name: str = ""  # Private name, never broadcast to other players.
    agent: Optional[LLMAgent] = None
    connected: bool = False  # Human-seat connection state.
    model: Optional[str] = None
    votes_total: int = 0
    votes_correct: int = 0  # Votes correctly targeting an AI.
    eliminated_round: Optional[int] = None
    # Anonymous browser identity. These values are server-private and never
    # included by `public`.
    player_id: str = ""
    session_id: str = ""
    reservation_token: str = ""
    reserved_until: float = 0.0
    claimed: bool = False
    disconnected_at: float = 0.0

    def public(self, *, reveal_role: bool = False) -> dict:
        d = {"id": self.id, "alive": self.alive, "connected": self.connected}
        if reveal_role:
            d["role"] = self.kind
        return d

    def reservation_active(self, now: Optional[float] = None) -> bool:
        checked_at = time.time() if now is None else now
        return bool(self.reservation_token) and self.reserved_until > checked_at

    def clear_occupant(self) -> None:
        self.connected = False
        self.name = ""
        self.player_id = ""
        self.session_id = ""
        self.reservation_token = ""
        self.reserved_until = 0.0
        self.claimed = False
        self.disconnected_at = 0.0


@dataclass
class Room:
    id: str
    visibility: str = "private"  # "public" matchmaking or "private" code.
    status: str = "waiting"  # "waiting" | "running" | "finished".
    # Composition chosen when the lobby is created. Defaults are filled from
    # settings by `RoomManager.create` so `setup_seats` never sees zero.
    num_humans: int = 0
    num_llms: int = 0
    seats: dict[str, Seat] = field(default_factory=dict)
    transcript: list[dict] = field(default_factory=list)
    phase: Phase = Phase.LOBBY
    round_no: int = 0
    started: bool = False
    engine_task: Optional[asyncio.Task] = None
    start_wait_task: Optional[asyncio.Task] = None
    start_deadline: float = 0.0
    # Private lobbies are controlled by the human seat reserved at creation.
    # This value is server-only and survives a normal WebSocket reconnect.
    host_seat_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    # Connections: WebSocket <-> seat
    _ws_all: set = field(default_factory=set)
    _seat_of_ws: dict = field(default_factory=dict)
    _ws_of_seat: dict = field(default_factory=dict)

    # Human inputs expected by the engine (seat_id -> Future)
    _pending: dict = field(default_factory=dict)
    _pending_messages: dict = field(default_factory=dict)
    _playback_waiters: dict[str, tuple[set[str], asyncio.Future]] = field(
        default_factory=dict
    )
    ever_occupied: bool = False

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------
    def setup_seats(self) -> None:
        """Create human and LLM seats, then assign voices and personas."""
        from .audio import voices as voices_mod

        settings = get_settings()
        letters = list(string.ascii_uppercase)
        voices = voices_mod.get_pool()
        random.shuffle(voices)

        total = self.num_humans + self.num_llms
        kinds = ["human"] * self.num_humans + ["llm"] * self.num_llms
        random.shuffle(kinds)  # Mix human and LLM seats.

        persona_idx = 0
        for i in range(total):
            sid = f"Player {letters[i]}"
            voice = voices[i % len(voices)]
            kind = kinds[i]
            seat = Seat(id=sid, kind=kind, voice=voice)
            if kind == "llm":
                model = settings.agent_models[persona_idx % len(settings.agent_models)]
                seat.model = model
                seat.agent = LLMAgent(sid, persona_idx, model=model)
                persona_idx += 1
            self.seats[sid] = seat

    def free_human_seat(self, now: Optional[float] = None) -> Optional[Seat]:
        now = now or time.time()
        for seat in self.seats.values():
            if (
                seat.kind == "human"
                and not seat.connected
                and not seat.claimed
                and not seat.reservation_active(now)
            ):
                return seat
        return None

    def reserve(self, seat: Seat, player_id: str, session_id: str, ttl: int) -> str:
        """Reserve an unclaimed human seat for a matchmaking WebSocket."""
        token = secrets.token_urlsafe(32)
        seat.player_id = player_id
        seat.session_id = session_id
        seat.reservation_token = token
        seat.reserved_until = time.time() + max(1, ttl)
        self.ever_occupied = True
        self.updated_at = time.time()
        return token

    # ------------------------------------------------------------------
    # Connections
    # ------------------------------------------------------------------
    async def attach(
        self,
        ws,
        name: str,
        *,
        player_id: str = "",
        session_id: str = "",
        reservation_token: str = "",
    ) -> Optional[Seat]:
        """Attach a reserved browser session or reconnect its claimed seat."""
        now = time.time()
        self.release_stale_waiting_seats(
            now, get_settings().reconnect_grace_seconds
        )

        # A disconnected session can reclaim its seat in either lobby mode.
        seat = next(
            (
                candidate
                for candidate in self.seats.values()
                if candidate.kind == "human"
                and candidate.claimed
                and candidate.player_id == player_id
                and candidate.session_id == session_id
                and player_id
                and session_id
            ),
            None,
        )

        if seat is None and reservation_token:
            seat = next(
                (
                    candidate
                    for candidate in self.seats.values()
                    if candidate.kind == "human"
                    and candidate.reservation_token == reservation_token
                    and candidate.player_id == player_id
                    and candidate.session_id == session_id
                    and candidate.reservation_active(now)
                ),
                None,
            )

        if seat is None:
            return None

        self._ws_all.add(ws)
        previous_ws = self._ws_of_seat.get(seat.id)
        if previous_ws is not None and previous_ws is not ws:
            self.detach(previous_ws)

        seat.connected = True
        seat.name = name[:80]
        seat.player_id = player_id
        seat.session_id = session_id
        seat.claimed = True
        self.ever_occupied = True
        seat.disconnected_at = 0.0
        self._seat_of_ws[ws] = seat.id
        self._ws_of_seat[seat.id] = ws
        self.updated_at = now
        return seat

    def detach(self, ws) -> None:
        self._ws_all.discard(ws)
        sid = self._seat_of_ws.pop(ws, None)
        if sid:
            self._ws_of_seat.pop(sid, None)
            if sid in self.seats:
                self.seats[sid].connected = False
                self.seats[sid].disconnected_at = time.time()
                self.updated_at = time.time()
            for playback_id in list(self._playback_waiters):
                self.resolve_playback(sid, playback_id)

    def seat_of(self, ws) -> Optional[str]:
        return self._seat_of_ws.get(ws)

    # ------------------------------------------------------------------
    # Message delivery
    # ------------------------------------------------------------------
    async def broadcast(self, msg: dict) -> None:
        dead = []
        for ws in list(self._ws_all):
            try:
                await ws.send_json(msg)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.detach(ws)

    async def send_seat(self, seat_id: str, msg: dict) -> bool:
        if msg.get("type") == "request_input":
            self._pending_messages[seat_id] = msg
        ws = self._ws_of_seat.get(seat_id)
        if ws is None:
            return False
        try:
            await ws.send_json(msg)
            return True
        except Exception:  # noqa: BLE001
            self.detach(ws)
            return False

    async def resend_pending(self, seat_id: str) -> bool:
        msg = self._pending_messages.get(seat_id)
        return await self.send_seat(seat_id, msg) if msg else False

    # ------------------------------------------------------------------
    # Transcript
    # ------------------------------------------------------------------
    def add_utterance(self, seat_id: str, text: str, context: str = "") -> None:
        self.transcript.append({"seat": seat_id, "text": text, "context": context})

    def render_transcript(self) -> str:
        lines = []
        for u in self.transcript:
            ctx = f" ({u['context']})" if u.get("context") else ""
            lines.append(f"{u['seat']}{ctx} : {u['text']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Active seats
    # ------------------------------------------------------------------
    def alive_seats(self) -> list[Seat]:
        return [s for s in self.seats.values() if s.alive]

    def alive_ids(self, exclude: Optional[str] = None) -> list[str]:
        return [s.id for s in self.alive_seats() if s.id != exclude]

    def humans_alive(self) -> list[Seat]:
        return [s for s in self.alive_seats() if s.kind == "human"]

    def connected_humans(self) -> list[Seat]:
        return [
            seat for seat in self.seats.values()
            if seat.kind == "human" and seat.connected
        ]

    def is_host(self, seat_id: str) -> bool:
        """Return whether a connected seat may start this private lobby."""
        seat = self.seats.get(seat_id)
        return bool(
            self.visibility == "private"
            and seat_id == self.host_seat_id
            and seat is not None
            and seat.kind == "human"
            and seat.connected
        )

    def keep_connected_humans(self) -> None:
        """Drop unfilled human seats immediately before a partial start."""
        absent_ids = {
            seat.id for seat in self.seats.values()
            if seat.kind == "human" and not seat.connected
        }
        for seat_id in absent_ids:
            self.seats.pop(seat_id, None)
        self.num_humans = len(self.connected_humans())

    def lobby_wait_remaining(self) -> Optional[int]:
        if not self.start_deadline or self.started:
            return None
        return max(0, math.ceil(self.start_deadline - time.time()))

    def release_stale_waiting_seats(self, now: float, reconnect_grace: int) -> None:
        """Release expired reservations and disconnected pre-game claims."""
        if self.status != "waiting":
            return
        for seat in self.seats.values():
            if seat.kind != "human" or seat.connected:
                continue
            expired_reservation = not seat.claimed and (
                not seat.reservation_token or seat.reserved_until <= now
            )
            expired_claim = seat.claimed and seat.disconnected_at and (
                seat.disconnected_at + reconnect_grace <= now
            )
            if expired_reservation or expired_claim:
                seat.clear_occupant()

    def has_waiting_occupants(self, now: float) -> bool:
        return any(
            seat.kind == "human"
            and (seat.connected or seat.claimed or seat.reservation_active(now))
            for seat in self.seats.values()
        )

    def llms_alive(self) -> list[Seat]:
        return [s for s in self.alive_seats() if s.kind == "llm"]

    # ------------------------------------------------------------------
    # Human inputs resolved by the WebSocket handler
    # ------------------------------------------------------------------
    def expect_input(self, seat_id: str) -> asyncio.Future:
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[seat_id] = fut
        return fut

    def resolve_input(self, seat_id: str, payload: Any) -> None:
        fut = self._pending.pop(seat_id, None)
        self._pending_messages.pop(seat_id, None)
        if fut is not None and not fut.done():
            fut.set_result(payload)

    def cancel_input(self, seat_id: str) -> None:
        self._pending.pop(seat_id, None)
        self._pending_messages.pop(seat_id, None)

    def expect_playback(self, playback_id: str) -> Optional[asyncio.Future]:
        """Wait until every currently connected player finishes one TTS clip."""
        listeners = {
            seat.id
            for seat in self.seats.values()
            if seat.kind == "human" and seat.connected
        }
        if not listeners:
            return None
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._playback_waiters[playback_id] = (listeners, future)
        return future

    def resolve_playback(self, seat_id: str, playback_id: str) -> None:
        waiter = self._playback_waiters.get(playback_id)
        if waiter is None:
            return
        listeners, future = waiter
        listeners.discard(seat_id)
        if not listeners:
            self._playback_waiters.pop(playback_id, None)
            if not future.done():
                future.set_result(None)

    def cancel_playback(self, playback_id: str) -> None:
        waiter = self._playback_waiters.pop(playback_id, None)
        if waiter is not None and not waiter[1].done():
            waiter[1].cancel()


class RoomManager:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._lock = asyncio.Lock()

    def create(
        self,
        room_id: str,
        *,
        num_humans: Optional[int] = None,
        num_llms: Optional[int] = None,
        visibility: str = "private",
    ) -> Optional[Room]:
        """Create a lobby with the chosen composition.

        Return None when a lobby with this id already exists so callers can
        report the collision instead of silently reusing another game.
        """
        if room_id in self._rooms:
            return None
        settings = get_settings()
        room = Room(
            id=room_id,
            visibility=visibility,
            num_humans=settings.num_humans if num_humans is None else num_humans,
            num_llms=settings.num_llms if num_llms is None else num_llms,
        )
        room.setup_seats()
        self._rooms[room_id] = room
        log.info(
            "Lobby created: %s (%d humans, %d AIs)",
            room_id, room.num_humans, room.num_llms,
        )
        return room

    async def create_private(
        self, room_id: str, *, num_humans: int, num_llms: int
    ) -> Optional[Room]:
        async with self._lock:
            self._cleanup_locked()
            return self.create(
                room_id,
                num_humans=num_humans,
                num_llms=num_llms,
                visibility="private",
            )

    async def create_private_and_reserve(
        self,
        room_id: str,
        *,
        num_humans: int,
        num_llms: int,
        player_id: str,
        session_id: str,
    ) -> tuple[Optional[Room], str, bool]:
        """Atomically create a private room and reserve its creator's seat."""
        settings = get_settings()
        async with self._lock:
            self._cleanup_locked()
            existing_room = self._rooms.get(room_id)
            if existing_room is not None:
                if (
                    existing_room.visibility != "private"
                    or existing_room.status != "waiting"
                    or existing_room.started
                ):
                    return None, "", False
                existing_seat = next(
                    (
                        seat
                        for seat in existing_room.seats.values()
                        if seat.kind == "human"
                        and seat.player_id == player_id
                        and seat.session_id == session_id
                        and (seat.claimed or seat.reservation_active())
                    ),
                    None,
                )
                if existing_seat is not None:
                    return existing_room, existing_seat.reservation_token, False
                return None, "", False

            room = self.create(
                room_id,
                num_humans=num_humans,
                num_llms=num_llms,
                visibility="private",
            )
            if room is None:  # Defensive: the manager lock prevents a race.
                return None, "", False
            seat = room.free_human_seat()
            if seat is None:
                raise RuntimeError("Private lobby has no human seat")
            token = room.reserve(
                seat,
                player_id,
                session_id,
                settings.matchmaking_reservation_seconds,
            )
            room.host_seat_id = seat.id
            return room, token, True

    async def reserve_private(
        self, room_id: str, player_id: str, session_id: str
    ) -> tuple[Optional[Room], str, str]:
        """Reserve a private human seat after HTTP admission checks."""
        settings = get_settings()
        async with self._lock:
            self._cleanup_locked()
            room = self._rooms.get(room_id)
            if room is None or room.visibility != "private":
                return None, "", "missing"
            if room.status != "waiting" or room.started:
                return room, "", "started"

            now = time.time()
            existing = next(
                (
                    seat
                    for seat in room.seats.values()
                    if seat.kind == "human"
                    and seat.player_id == player_id
                    and seat.session_id == session_id
                    and (seat.claimed or seat.reservation_active(now))
                ),
                None,
            )
            if existing is not None:
                return room, existing.reservation_token, ""

            seat = room.free_human_seat(now)
            if seat is None:
                return room, "", "full"
            token = room.reserve(
                seat,
                player_id,
                session_id,
                settings.matchmaking_reservation_seconds,
            )
            return room, token, ""

    async def matchmake(
        self, player_id: str, session_id: str
    ) -> tuple[Room, str, bool]:
        """Atomically reserve the first seat in the oldest public lobby."""
        settings = get_settings()
        async with self._lock:
            self._cleanup_locked()
            now = time.time()
            candidates = sorted(
                (
                    room
                    for room in self._rooms.values()
                    if room.visibility == "public"
                    and room.status == "waiting"
                    and not room.started
                ),
                key=lambda room: room.created_at,
            )

            # Make retries idempotent while a reservation or claim is alive.
            for candidate in candidates:
                existing = next(
                    (
                        seat
                        for seat in candidate.seats.values()
                        if seat.kind == "human"
                        and seat.player_id == player_id
                        and seat.session_id == session_id
                        and (seat.claimed or seat.reservation_active(now))
                    ),
                    None,
                )
                if existing is not None:
                    return candidate, existing.reservation_token, False

            room = None
            seat = None
            created = False
            for candidate in candidates:
                available = candidate.free_human_seat(now)
                if available is not None:
                    room, seat = candidate, available
                    break

            if room is None or seat is None:
                while True:
                    room_id = f"quick-{secrets.token_hex(6)}"
                    room = self.create(
                        room_id,
                        num_humans=settings.num_humans,
                        num_llms=settings.num_llms,
                        visibility="public",
                    )
                    if room is not None:
                        created = True
                        break
                seat = room.free_human_seat(now)
                if seat is None:  # Defensive: configuration guarantees humans.
                    raise RuntimeError("Matchmaking room has no human seat")

            token = room.reserve(
                seat,
                player_id,
                session_id,
                settings.matchmaking_reservation_seconds,
            )
            return room, token, created

    async def cleanup(self) -> None:
        async with self._lock:
            self._cleanup_locked()

    def _cleanup_locked(self) -> None:
        settings = get_settings()
        now = time.time()
        stale_ids: list[str] = []
        for room_id, room in self._rooms.items():
            room.release_stale_waiting_seats(now, settings.reconnect_grace_seconds)
            if room.status == "finished":
                stale_ids.append(room_id)
            elif room.status == "running" and not any(
                seat.connected for seat in room.seats.values() if seat.kind == "human"
            ) and all(
                not seat.claimed
                or (
                    seat.disconnected_at
                    and seat.disconnected_at + settings.reconnect_grace_seconds <= now
                )
                for seat in room.seats.values()
                if seat.kind == "human"
            ):
                if room.engine_task and not room.engine_task.done():
                    room.engine_task.cancel()
                stale_ids.append(room_id)
            elif room.status == "waiting" and room.ever_occupied and not room.has_waiting_occupants(now):
                stale_ids.append(room_id)
            elif room.status == "waiting" and not room.has_waiting_occupants(now) and (
                room.created_at + settings.waiting_lobby_ttl_seconds <= now
            ):
                stale_ids.append(room_id)
        for room_id in stale_ids:
            room = self._rooms.pop(room_id, None)
            if room and room.start_wait_task and not room.start_wait_task.done():
                room.start_wait_task.cancel()
            log.info("Lobby removed: %s", room_id)

    def get(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)


rooms = RoomManager()
