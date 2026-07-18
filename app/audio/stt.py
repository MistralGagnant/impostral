"""Voxtral speech-to-text transcription.

Reference: docs.mistral.ai/studio-api/audio/speech_to_text
Batch model: Voxtral Mini Transcribe V2.

Mock mode and failed calls return the client-provided fallback text, enabling
tests without a microphone or API key.
"""
from __future__ import annotations

import asyncio
import logging

from ..config import get_settings
from ..mistral_client import get_client

log = logging.getLogger("impostral.stt")


async def transcribe(audio_bytes: bytes | None, *, fallback_text: str = "") -> str:
    settings = get_settings()
    client = get_client()

    if client is None or not audio_bytes:
        return fallback_text.strip()

    def _call() -> str:
        # The SDK is synchronous, so run it in a worker thread.
        resp = client.audio.transcriptions.complete(
            model=settings.stt_model,
            file={"content": audio_bytes, "file_name": "clip.webm"},
            language=settings.stt_language,
        )
        return getattr(resp, "text", "") or ""

    try:
        text = await asyncio.to_thread(_call)
        return text.strip() or fallback_text.strip()
    except Exception as exc:  # noqa: BLE001 - gracefully fall back to text
        log.warning("Voxtral STT failed; using fallback text: %s", exc)
        return fallback_text.strip()
