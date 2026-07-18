"""Client Mistral partagé (chat / STT / TTS via l'API hébergée).

En mode mock (pas de clé API), `get_client()` renvoie None ; les wrappers audio
et agents savent alors se rabattre sur un comportement scripté.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from .config import get_settings


@lru_cache
def get_client() -> Optional["object"]:
    settings = get_settings()
    if settings.mock_mode:
        return None
    # Import paresseux : le SDK n'est pas requis en mode mock. Le point d'entrée
    # a changé entre les versions (2.x range le client sous mistralai.client).
    try:
        from mistralai import Mistral  # SDK 1.x
    except ImportError:
        from mistralai.client import Mistral  # SDK 2.x

    return Mistral(api_key=settings.mistral_api_key)
