"""Build the TTS voice pool from Voxtral preset voices.

Preset voices are fetched once and cached. The pool keeps one voice per speaker,
prefers neutral variants, and places the target language first. Mock mode and
network failures return `settings.voice_pool`, allowing graceful text-only play.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from ..config import get_settings
from ..mistral_client import get_client

log = logging.getLogger("impostral.voices")

_NEUTRAL_HINTS = ("neutral", "balanced", "neutre")


@lru_cache
def _preset_voice_ids() -> tuple[str, ...]:
    """Return one voice ID per speaker with the target language first."""
    client = get_client()
    if client is None:
        return ()
    settings = get_settings()
    prefix = settings.voice_lang_prefix

    try:
        items: list[dict] = []
        offset = 0
        while True:
            page = client.audio.voices.list(type_="preset", limit=50, offset=offset)
            d = page.model_dump()
            items.extend(d.get("items", []))
            total = d.get("total", len(items))
            offset += 50
            if offset >= total or not d.get("items"):
                break
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not list preset voices: %s", exc)
        return ()

    # Group variants by speaker name and prefer a neutral variant.
    by_speaker: dict[str, dict] = {}
    for it in items:
        speaker = str(it.get("name", "")).split(" - ")[0].strip() or it.get("id")
        cur = by_speaker.get(speaker)
        tags = " ".join(it.get("tags", [])).lower() + " " + str(it.get("name", "")).lower()
        is_neutral = any(h in tags for h in _NEUTRAL_HINTS)
        if cur is None or (is_neutral and not cur["_neutral"]):
            by_speaker[speaker] = {**it, "_neutral": is_neutral}

    def lang_ok(it: dict) -> bool:
        return any(str(l).startswith(prefix) for l in (it.get("languages") or []))

    # First choices: one distinct speaker each, target language first.
    heads = list(by_speaker.values())
    heads.sort(key=lambda it: (not lang_ok(it), str(it.get("name", ""))))
    head_ids = [it["id"] for it in heads if it.get("id")]

    # Reserve: remaining variants prevent immediate reuse when there are more
    # seats than distinct speakers.
    head_set = set(head_ids)
    rest = [it for it in items if it.get("id") and it["id"] not in head_set]
    rest.sort(key=lambda it: (not lang_ok(it), str(it.get("name", ""))))
    ids = head_ids + [it["id"] for it in rest]

    log.info("Preset voice pool: %d distinct speakers, %d voices total (%s first)",
             len(head_ids), len(ids), prefix)
    return tuple(ids)


def get_pool() -> list[str]:
    """Return real voice IDs outside mock mode, otherwise fallback labels."""
    settings = get_settings()
    if settings.mock_mode:
        return list(settings.voice_pool)
    ids = _preset_voice_ids()
    return list(ids) if ids else list(settings.voice_pool)
