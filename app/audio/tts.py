"""Voxtral Text-to-Speech (synthèse par voix de siège).

Réf. doc : docs.mistral.ai/studio-api/audio/text_to_speech
Services : Voices (profils) + Speech Generation (sortie pcm/mp3, streaming).

C'est ce wrapper qui réalise l'anonymisation : quel que soit l'émetteur (humain
ou LLM), la sortie est une voix de synthèse fixée par siège. En mode mock (ou si
l'appel échoue), renvoie None → le front n'affiche que le texte.
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
    """Synthétise `text` avec la voix `voice` ; renvoie une URL /audio/{id} ou None."""
    settings = get_settings()
    client = get_client()

    if client is None or not text.strip():
        return None

    def _call() -> bytes:
        # SDK mistralai 2.x : audio.speech.complete → SpeechResponse.audio_data
        # (chaîne base64). `voice` est ici un id de voix preset.
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
    except Exception as exc:  # noqa: BLE001 — dégradation propre en texte seul
        log.warning("TTS Voxtral a échoué, texte seul : %s", exc)
        return None
