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

_MIME_EXTENSIONS = {
    "audio/webm": ".webm",
    "audio/mp4": ".mp4",
    "audio/x-m4a": ".m4a",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def _upload_metadata(mime_type: str) -> tuple[str, str]:
    cleaned = (mime_type or "").strip()
    base_type = cleaned.partition(";")[0].strip().lower()
    if not base_type.startswith("audio/"):
        cleaned = "audio/webm"
        base_type = cleaned
    extension = _MIME_EXTENSIONS.get(base_type, ".audio")
    return cleaned, f"clip{extension}"


async def transcribe(
    audio_bytes: bytes | None,
    *,
    mime_type: str = "audio/webm",
    fallback_text: str = "",
) -> str:
    settings = get_settings()
    client = get_client()

    if client is None or not audio_bytes:
        return fallback_text.strip()

    content_type, file_name = _upload_metadata(mime_type)

    def _call() -> str:
        # The SDK is synchronous, so run it in a worker thread.
        resp = client.audio.transcriptions.complete(
            model=settings.stt_model,
            file={
                "content": audio_bytes,
                "file_name": file_name,
                "content_type": content_type,
            },
            language=settings.stt_language,
        )
        return getattr(resp, "text", "") or ""

    try:
        text = await asyncio.to_thread(_call)
        return text.strip() or fallback_text.strip()
    except Exception as exc:  # noqa: BLE001 - gracefully fall back to text
        log.warning("Voxtral STT failed; using fallback text: %s", exc)
        return fallback_text.strip()
