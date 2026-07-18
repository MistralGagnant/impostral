"""Magasin audio éphémère en mémoire, exposé via /audio/{id}.

Les clips TTS sont volatils : on garde un cache borné (FIFO) pour éviter de
grossir indéfiniment pendant une partie longue.
"""
from __future__ import annotations

import uuid
from collections import OrderedDict

_MAX_CLIPS = 512
_store: "OrderedDict[str, tuple[bytes, str]]" = OrderedDict()


def put(data: bytes, content_type: str = "audio/mpeg") -> str:
    """Enregistre un clip et renvoie son URL relative (/audio/{id})."""
    clip_id = uuid.uuid4().hex
    _store[clip_id] = (data, content_type)
    while len(_store) > _MAX_CLIPS:
        _store.popitem(last=False)
    return f"/audio/{clip_id}"


def get(clip_id: str) -> tuple[bytes, str] | None:
    return _store.get(clip_id)
