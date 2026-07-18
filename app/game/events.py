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
    DELIBERATION = "deliberation"
    VOTE = "vote"
    RESOLUTION = "resolution"
    GAME_OVER = "game_over"


# --- Incoming messages: client -> server ---------------------------------


class JoinMsg(BaseModel):
    type: Literal["join"]
    name: str = ""


class AudioBlobMsg(BaseModel):
    type: Literal["audio_blob"]
    # Base64 WebM/Opus audio from MediaRecorder, or fallback text.
    audio_b64: Optional[str] = None
    text: Optional[str] = None


class DirectQuestionMsg(BaseModel):
    type: Literal["direct_question"]
    target: str  # Target seat ID.
    audio_b64: Optional[str] = None
    text: Optional[str] = None


class SubmitVoteMsg(BaseModel):
    type: Literal["submit_vote"]
    target: str


class ReadyMsg(BaseModel):
    type: Literal["ready"]


ClientMessage = (
    JoinMsg | AudioBlobMsg | DirectQuestionMsg | SubmitVoteMsg | ReadyMsg
)

_PARSERS = {
    "join": JoinMsg,
    "audio_blob": AudioBlobMsg,
    "direct_question": DirectQuestionMsg,
    "submit_vote": SubmitVoteMsg,
    "ready": ReadyMsg,
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
    *, seats: list[dict], phase: str, round_no: int, you: Optional[str]
) -> dict:
    return {
        "type": "room_state",
        "seats": seats,
        "phase": phase,
        "round": round_no,
        "you": you,
    }


def srv_phase_change(*, phase: str, deadline: Optional[float], prompt: str = "") -> dict:
    return {"type": "phase_change", "phase": phase, "deadline": deadline, "prompt": prompt}


def srv_utterance(*, seat: str, text: str, audio_url: Optional[str], context: str = "") -> dict:
    return {
        "type": "utterance",
        "seat": seat,
        "text": text,
        "audio_url": audio_url,
        "context": context,  # For example: "answer" or "to Player C".
    }


def srv_request_input(*, mode: str, deadline: Optional[float], targets: Optional[list[str]] = None) -> dict:
    """Request an answer, vote, or question from the relevant human client."""
    return {"type": "request_input", "mode": mode, "deadline": deadline, "targets": targets}


def srv_vote_result(*, tally: dict[str, int], eliminated: Optional[str]) -> dict:
    return {"type": "vote_result", "tally": tally, "eliminated": eliminated}


def srv_elimination(*, seat: str, role: Optional[str]) -> dict:
    return {"type": "elimination", "seat": seat, "role": role}


def srv_game_over(*, winner: str, winners: list[str], roles: dict[str, str]) -> dict:
    return {
        "type": "game_over",
        "winner": winner,
        "winners": winners,
        "roles": roles,
    }


def srv_system(*, text: str) -> dict:
    return {"type": "system", "text": text}
