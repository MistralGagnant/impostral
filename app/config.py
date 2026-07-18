"""Configuration centrale (variables d'env, préfixe IMPOSTRAL_).

Toutes les valeurs ont un défaut raisonnable pour permettre de lancer le jeu
immédiatement. Sans MISTRAL_API_KEY, le jeu bascule en mode « mock » : les agents
sont scriptés et il n'y a pas d'audio (texte seul).
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
    # La clé suit la convention standard MISTRAL_API_KEY (sans préfixe) :
    # l'alias explicite court-circuite env_prefix pour ce champ.
    mistral_api_key: str = Field("", alias="MISTRAL_API_KEY")
    chat_model: str = "mistral-large-latest"
    stt_model: str = "voxtral-mini-latest"
    tts_model: str = "voxtral-mini-tts-latest"

    # --- Composition d'une partie ---------------------------------------
    num_humans: int = 2
    num_llms: int = 3
    max_rounds: int = 5
    reveal_role_on_elimination: bool = True

    # --- Durées de phase (secondes) -------------------------------------
    question_seconds: int = 45
    deliberation_seconds: int = 90
    vote_seconds: int = 30
    # Cadence de révélation entre deux prises de parole (anti-tell de timing).
    reveal_gap_seconds: float = 1.2

    # --- Pool de voix TTS (repli mode mock uniquement) ------------------
    # Hors mock, le pool réel est construit dynamiquement depuis les voix preset
    # Voxtral (voir app/audio/voices.py). Ces étiquettes ne servent qu'en mock.
    voice_pool: list[str] = [
        "Aria", "Colette", "Emile", "Nadia", "Oskar", "Yara", "Timo", "Lise",
    ]
    # Langue préférée pour choisir les voix preset (préfixe de code langue).
    voice_lang_prefix: str = "fr"

    @property
    def mock_mode(self) -> bool:
        """Vrai si aucune clé API : agents scriptés, pas d'audio réel."""
        return not self.mistral_api_key.strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
