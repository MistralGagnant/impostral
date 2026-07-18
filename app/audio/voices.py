"""Construction du pool de voix TTS à partir des voix preset Voxtral.

On récupère les voix preset une fois (cache), on garde un locuteur distinct par
voix (variante « neutre » de préférence) et on place les voix de la langue cible
en tête. En mode mock (ou en cas d'échec réseau), on renvoie les étiquettes de
`settings.voice_pool` — le TTS échouera alors proprement en texte seul.
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
    """Un id de voix par locuteur distinct, langue cible en tête. Vide si mock."""
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
        log.warning("Impossible de lister les voix preset : %s", exc)
        return ()

    # Regroupe par locuteur (nom avant « - »), choisit une variante neutre.
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

    # Têtes : un locuteur distinct chacun (langue cible d'abord, ordre stable).
    heads = list(by_speaker.values())
    heads.sort(key=lambda it: (not lang_ok(it), str(it.get("name", ""))))
    head_ids = [it["id"] for it in heads if it.get("id")]

    # Réserve : toutes les autres variantes, pour éviter de réutiliser une voix
    # quand il y a plus de sièges que de locuteurs distincts.
    head_set = set(head_ids)
    rest = [it for it in items if it.get("id") and it["id"] not in head_set]
    rest.sort(key=lambda it: (not lang_ok(it), str(it.get("name", ""))))
    ids = head_ids + [it["id"] for it in rest]

    log.info("Pool de voix preset : %d locuteurs distincts, %d voix au total (%s en tête)",
             len(head_ids), len(ids), prefix)
    return tuple(ids)


def get_pool() -> list[str]:
    """Renvoie la liste des voix (ids réels hors mock, étiquettes sinon)."""
    settings = get_settings()
    if settings.mock_mode:
        return list(settings.voice_pool)
    ids = _preset_voice_ids()
    return list(ids) if ids else list(settings.voice_pool)
