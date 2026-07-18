"""Voxtral text-to-speech using one synthetic voice per seat.

Reference: docs.mistral.ai/studio-api/audio/text_to_speech
Services: Voices profiles and speech generation.

This wrapper provides anonymization: human and LLM speech both use the fixed
synthetic voice assigned to their seat. Mock mode and failed calls return None,
leaving the web client in text-only mode.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..config import get_settings
from ..mistral_client import get_client
from . import store

log = logging.getLogger("impostral.tts")


async def synthesize(text: str, *, voice: str) -> Optional[str]:
    """Synthesize text with a voice and return an /audio/{id} URL or None."""
    settings = get_settings()
    client = get_client()

    if client is None or not text.strip():
        return None

    def _call() -> bytes:
        # mistralai 2.x returns base64 in SpeechResponse.audio_data.
        resp = client.audio.speech.complete(
            model=settings.tts_model,
            voice_id=voice,
            input=text,
            response_format="mp3",
        )
        if isinstance(resp, (bytes, bytearray)):
            return bytes(resp)
        audio_data = getattr(resp, "audio_data", None)
        if isinstance(audio_data, str):
            import base64
            return base64.b64decode(audio_data)
        if isinstance(audio_data, (bytes, bytearray)):
            return bytes(audio_data)
        return bytes(getattr(resp, "audio", b"") or getattr(resp, "content", b""))

    try:
        audio = await asyncio.to_thread(_call)
        if not audio:
            return None
        return store.put(audio, "audio/mpeg")
    except Exception as exc:  # noqa: BLE001 - gracefully fall back to text only
        log.warning("Voxtral TTS failed; continuing with text only: %s", exc)
        return None
