"""Voxtral Speech-to-Text (transcription).

Réf. doc : docs.mistral.ai/studio-api/audio/speech_to_text
Modèle batch : Voxtral Mini Transcribe V2.

En mode mock (ou si l'appel échoue), on renvoie le texte de repli fourni par le
client — utile pour tester sans micro ni clé API.
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
        # Le SDK est synchrone : on l'exécute dans un thread.
        resp = client.audio.transcriptions.complete(
            model=settings.stt_model,
            file={"content": audio_bytes, "file_name": "clip.webm"},
        )
        return getattr(resp, "text", "") or ""

    try:
        text = await asyncio.to_thread(_call)
        return text.strip() or fallback_text.strip()
    except Exception as exc:  # noqa: BLE001 — on veut dégrader proprement
        log.warning("STT Voxtral a échoué, repli sur le texte : %s", exc)
        return fallback_text.strip()
