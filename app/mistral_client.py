"""Shared Mistral client for hosted chat, STT, and TTS APIs.

In mock mode without an API key, `get_client()` returns None and callers fall
back to scripted or text-only behavior.
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
    # Lazy import: mock mode does not require the SDK. The entry point changed
    # between SDK versions; 2.x also exposes the client under mistralai.client.
    try:
        from mistralai import Mistral  # SDK 1.x
    except ImportError:
        from mistralai.client import Mistral  # SDK 2.x

    return Mistral(api_key=settings.mistral_api_key)
