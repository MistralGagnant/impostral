"""Message schemas exchanged over the game WebSocket.

Outgoing server messages are dictionaries built by `srv_*` helpers. Incoming
client messages are validated by `parse_client_message`.

The role of an active seat is never disclosed. It is only revealed on
elimination when `reveal_role_on_elimination` is enabled.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ValidationError


class Phase(str, Enum):
    LOBBY = "lobby"
    QUESTION = "question"
    VOTE = "vote"
    RESOLUTION = "resolution"
    GAME_OVER = "game_over"


# --- Incoming messages: client -> server ---------------------------------


class JoinMsg(BaseModel):
    type: Literal["join"]
    name: str = ""
    player_id: str = ""
    session_id: str = ""
    reservation_token: str = ""


class AudioBlobMsg(BaseModel):
    type: Literal["audio_blob"]
    # Base64 WebM/Opus audio from MediaRecorder, or fallback text.
    audio_b64: Optional[str] = None
    text: Optional[str] = None


class SubmitVoteMsg(BaseModel):
    type: Literal["submit_vote"]
    target: str


class ReadyMsg(BaseModel):
    type: Literal["ready"]


class PlaybackCompleteMsg(BaseModel):
    type: Literal["playback_complete"]
    playback_id: str


ClientMessage = (
    JoinMsg | AudioBlobMsg | SubmitVoteMsg | ReadyMsg | PlaybackCompleteMsg
)

_PARSERS = {
    "join": JoinMsg,
    "audio_blob": AudioBlobMsg,
    "submit_vote": SubmitVoteMsg,
    "ready": ReadyMsg,
    "playback_complete": PlaybackCompleteMsg,
}


def parse_client_message(raw: dict[str, Any]) -> Optional[BaseModel]:
    """Validate an incoming message or return None when invalid or unknown."""
    parser = _PARSERS.get(raw.get("type"))
    if parser is None:
        return None
    try:
        return parser.model_validate(raw)
    except ValidationError:
        return None


# --- Outgoing messages: server -> client ---------------------------------


def srv_room_state(
    *, seats: list[dict], phase: str, round_no: int, you: Optional[str],
    auto_ready: bool = False,
) -> dict:
    return {
        "type": "room_state",
        "seats": seats,
        "phase": phase,
        "round": round_no,
        "you": you,
        "auto_ready": auto_ready,
    }


def srv_phase_change(*, phase: str, deadline: Optional[float], prompt: str = "") -> dict:
    return {"type": "phase_change", "phase": phase, "deadline": deadline, "prompt": prompt}


def srv_utterance(
    *, seat: str, text: str, audio_url: Optional[str], context: str = "",
    playback_id: str = "",
) -> dict:
    return {
        "type": "utterance",
        "seat": seat,
        "text": text,
        "audio_url": audio_url,
        "context": context,  # For example: "answer" or "to Player C".
        "playback_id": playback_id,
    }


def srv_request_input(*, mode: str, deadline: Optional[float], targets: Optional[list[str]] = None) -> dict:
    """Request an answer or vote from the relevant human client."""
    return {"type": "request_input", "mode": mode, "deadline": deadline, "targets": targets}


def srv_vote_result(
    *, tally: dict[str, int], eliminated: Optional[str],
    runoff: Optional[list[str]] = None,
) -> dict:
    return {
        "type": "vote_result",
        "tally": tally,
        "eliminated": eliminated,
        "runoff": runoff or [],
    }


def srv_elimination(*, seat: str, role: Optional[str], model: Optional[str] = None) -> dict:
    # `model` names the LLM behind an AI seat (e.g. "mistral-large-latest").
    return {"type": "elimination", "seat": seat, "role": role, "model": model}


def srv_game_over(
    *, winner: str, winners: list[str], roles: dict[str, str],
    models: dict[str, str], message: str = "",
) -> dict:
    return {
        "type": "game_over",
        "winner": winner,
        "winners": winners,
        "roles": roles,
        "models": models,  # seat id -> model name, for AI seats only.
        "message": message,
    }


def srv_system(*, text: str, code: str = "") -> dict:
    message = {"type": "system", "text": text}
    if code:
        message["code"] = code
    return message
