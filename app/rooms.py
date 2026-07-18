"""Rooms, seats, and WebSocket connections.

A room contains human and LLM seats, the game transcript, and open connections.
The game flow lives in `game/state_machine.py`; this module owns shared state and
routes human input.
"""
from __future__ import annotations

import asyncio
import logging
import random
import string
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
    votes_correct: int = 0  # Votes targeting a competing AI.
    eliminated_round: Optional[int] = None

    def public(self, *, reveal_role: bool = False) -> dict:
        d = {"id": self.id, "alive": self.alive, "connected": self.connected}
        if reveal_role:
            d["role"] = self.kind
        return d


@dataclass
class Room:
    id: str
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
    ready_seats: set = field(default_factory=set)

    # Connections: WebSocket <-> seat
    _ws_all: set = field(default_factory=set)
    _seat_of_ws: dict = field(default_factory=dict)
    _ws_of_seat: dict = field(default_factory=dict)

    # Human inputs expected by the engine (seat_id -> Future)
    _pending: dict = field(default_factory=dict)

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

    def free_human_seat(self) -> Optional[Seat]:
        for seat in self.seats.values():
            if seat.kind == "human" and not seat.connected:
                return seat
        return None

    # ------------------------------------------------------------------
    # Connections
    # ------------------------------------------------------------------
    async def attach(self, ws, name: str) -> Optional[str]:
        """Attach a WebSocket to a free human seat, or return None for a spectator."""
        self._ws_all.add(ws)
        seat = self.free_human_seat()
        if seat is None:
            return None  # Spectator
        seat.connected = True
        seat.name = name
        self._seat_of_ws[ws] = seat.id
        self._ws_of_seat[seat.id] = ws
        return seat.id

    def detach(self, ws) -> None:
        self._ws_all.discard(ws)
        sid = self._seat_of_ws.pop(ws, None)
        if sid:
            self._ws_of_seat.pop(sid, None)
            if sid in self.seats:
                self.seats[sid].connected = False

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
        ws = self._ws_of_seat.get(seat_id)
        if ws is None:
            return False
        try:
            await ws.send_json(msg)
            return True
        except Exception:  # noqa: BLE001
            self.detach(ws)
            return False

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

    def all_humans_ready(self) -> bool:
        humans = [s for s in self.seats.values() if s.kind == "human"]
        return bool(humans) and all(
            s.connected and s.id in self.ready_seats for s in humans
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
        if fut is not None and not fut.done():
            fut.set_result(payload)

    def cancel_input(self, seat_id: str) -> None:
        self._pending.pop(seat_id, None)


class RoomManager:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def create(
        self,
        room_id: str,
        *,
        num_humans: Optional[int] = None,
        num_llms: Optional[int] = None,
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

    def get(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)


rooms = RoomManager()
