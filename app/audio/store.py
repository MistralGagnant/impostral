"""Ephemeral in-memory audio store exposed through /audio/{id}.

TTS clips are volatile and kept in a bounded FIFO cache to avoid unbounded
growth during long games.
"""
from __future__ import annotations

import uuid
from collections import OrderedDict

_MAX_CLIPS = 512
_store: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()


def put(data: bytes, content_type: str = "audio/mpeg") -> str:
    """Store a clip and return its relative /audio/{id} URL."""
    clip_id = uuid.uuid4().hex
    _store[clip_id] = (data, content_type)
    while len(_store) > _MAX_CLIPS:
        _store.popitem(last=False)
    return f"/audio/{clip_id}"


def get(clip_id: str) -> tuple[bytes, str] | None:
    return _store.get(clip_id)
