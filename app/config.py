"""Central configuration for IMPOSTRAL_-prefixed environment variables.

Every value has a practical default so the game can start immediately. Without
MISTRAL_API_KEY, the game uses scripted agents in text-only mock mode.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IMPOSTRAL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # --- Mistral ---------------------------------------------------------
    # The API key follows the standard unprefixed MISTRAL_API_KEY convention.
    mistral_api_key: str = Field("", alias="MISTRAL_API_KEY")
    chat_model: str = "mistral-large-latest"
    # Comma-separated pool of chat models. When set, each LLM seat is assigned a
    # model from this pool so several models compete in the same game. Kept as a
    # plain string to avoid pydantic-settings' JSON parsing of list env vars.
    model_pool: str = ""
    stt_model: str = "voxtral-mini-latest"
    tts_model: str = "voxtral-mini-tts-latest"

    # --- Model performance tracking -------------------------------------
    # JSONL file appended with one record per finished game.
    stats_path: str = "data/results.jsonl"

    # --- Game composition ------------------------------------------------
    num_humans: int = 2
    num_llms: int = 3
    max_rounds: int = 5
    reveal_role_on_elimination: bool = True

    # --- Phase durations in seconds -------------------------------------
    question_seconds: int = 45
    deliberation_seconds: int = 90
    vote_seconds: int = 30
    # Fixed reveal cadence used to hide response-time tells.
    reveal_gap_seconds: float = 1.2

    # --- TTS voice pool used only as a mock fallback ---------------------
    # Outside mock mode, preset Voxtral voices are loaded dynamically.
    voice_pool: list[str] = [
        "Aria", "Colette", "Emile", "Nadia", "Oskar", "Yara", "Timo", "Lise",
    ]
    # Preferred preset voice language code prefix.
    voice_lang_prefix: str = "en"

    @property
    def model_pool_list(self) -> list[str]:
        """Return the configured model pool, falling back to the single chat model."""
        pool = [m.strip() for m in self.model_pool.split(",") if m.strip()]
        return pool or [self.chat_model]

    @property
    def mock_mode(self) -> bool:
        """Return True when no API key is set and the game should use mock mode."""
        return not self.mistral_api_key.strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
