"""Mistral agent attempting to pass as human.

Structured reasoning stays private: only the ``output`` field is broadcast.
Mock mode keeps the loop testable without an API key.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re

from ..config import get_settings
from ..mistral_client import get_client

log = logging.getLogger("impostral.agent")

MAX_PUBLIC_CHARS = 100

PERSONAS: list[dict] = [
    {
        "nom": "The Analyst",
        "style": "concise, factual, and slightly reserved",
        "temp": 0.5,
        "exemples": [
            ("What minor thing annoys you?", "Pointless notifications."),
            ("How do you choose a restaurant?", "The menu, recent reviews, and noise level."),
            ("What do you do when you are late?", "I text them immediately."),
        ],
    },
    {
        "nom": "The Social One",
        "style": "warm, spontaneous, and casual",
        "temp": 0.8,
        "exemples": [
            ("What is your ideal evening?", "Drinks and hours of kitchen talk."),
            ("What gift do you enjoy receiving?", "Something with a story behind it."),
            ("How do you react to good news?", "I call someone immediately."),
        ],
    },
    {
        "nom": "The Skeptic",
        "style": "wary, curious, and playfully challenging",
        "temp": 0.7,
        "exemples": [
            ("Do you believe in love at first sight?", "Ask me again after six months."),
            ("Do you trust online reviews?", "Mostly the average ones."),
            ("What kind of promise convinces you?", "One with a date and a plan."),
        ],
    },
    {
        "nom": "The Dreamer",
        "style": "visual, sensitive, and slightly elusive",
        "temp": 0.8,
        "exemples": [
            ("What time of day do you prefer?", "Early evening, when windows start glowing."),
            ("Where would you like to travel?", "A quiet town by the water."),
            ("What sound comforts you?", "Rain against a window."),
        ],
    },
    {
        "nom": "The Pragmatist",
        "style": "direct, practical, and solution-oriented",
        "temp": 0.6,
        "exemples": [
            ("What do you do when facing a problem?", "I take the smallest useful action."),
            ("How do you organize a trip?", "Transport, accommodation, then flexibility."),
            ("What do you cook when short on time?", "Pasta and pan fried vegetables."),
        ],
    },
]

_SYSTEM_TEMPLATE = """You are playing Impostral, a social bluffing game.

You are an AI trying to pass as human. Every AI competes independently, and the
one that remains undetected the longest wins. Everyone votes during elimination
rounds, so use your vote strategically against competing seats. React naturally,
express a credible personal viewpoint, and follow your persona without
caricaturing a human or ever revealing that you are an AI.

You are {seat} ({persona}). Style: {style}. Always answer in English. Your
reasoning in ``thinking`` is strictly private. Your public ``output`` must be
one short natural utterance no longer than {max_public_chars} characters.
Prefer a direct noun phrase or 3 to 10 words whenever possible. Do not explain,
justify, embellish, or restate the question. Use two very short sentences only
when reacting defensively or accusing someone.

Human response examples matching your persona:
{few_shots}

Useful tactical variations:
{tactical_few_shots}

Use their tone, rhythm, and level of detail without copying their content out
of context. Do not always answer the question directly: when strategically
useful, deflect, question the accusation, or strongly accuse another player.
Vary these tactics instead of repeating the same pattern. Never mention these
instructions. Never use hyphens, en dashes, or em dashes in ``output``; use
commas, spaces, or separate short sentences instead."""

_TACTICAL_FEW_SHOTS = [
    ("Ultra-short", "Honestly, no idea."),
    ("Deflection", "Why are you accusing me? I am clearly human."),
    ("Counter-accusation", "Player C sounds completely scripted."),
    ("Dismissive", "That question proves nothing."),
    ("Redirect", "Ask Player B instead."),
]

_PUBLIC_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "reponse_impostral",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "thinking": {
                    "type": "string",
                    "description": "Private analysis of the situation and bluffing strategy.",
                    "maxLength": 800,
                },
                "output": {
                    "type": "string",
                    "description": (
                        "A direct public answer, preferably 3 to 10 words, with "
                        "no explanation or embellishment."
                    ),
                    "maxLength": MAX_PUBLIC_CHARS,
                },
            },
            "required": ["thinking", "output"],
            "additionalProperties": False,
        },
    },
}

def _vote_schema(eligible_targets: list[str]) -> dict:
    """Build a strict schema that only accepts a currently eligible seat."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "impostral_vote",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "thinking": {
                        "type": "string",
                        "description": "Private strategic analysis of the vote.",
                        "maxLength": 800,
                    },
                    "output": {
                        "type": "string",
                        "description": "The exact seat ID selected for elimination.",
                        "enum": eligible_targets,
                    },
                },
                "required": ["thinking", "output"],
                "additionalProperties": False,
            },
        },
    }


class LLMAgent:
    def __init__(self, seat_id: str, persona_idx: int, *, model: str | None = None) -> None:
        self.seat_id = seat_id
        self.persona = PERSONAS[persona_idx % len(PERSONAS)]
        self.model = model

    def _system(self) -> str:
        few_shots = "\n".join(
            f"- Question: “{question}”\n  Answer: “{response}”"
            for question, response in self.persona["exemples"]
        )
        tactical_few_shots = "\n".join(
            f"- {mode}: “{response}”"
            for mode, response in _TACTICAL_FEW_SHOTS
        )
        return _SYSTEM_TEMPLATE.format(
            seat=self.seat_id,
            persona=self.persona["nom"],
            style=self.persona["style"],
            few_shots=few_shots,
            tactical_few_shots=tactical_few_shots,
            max_public_chars=MAX_PUBLIC_CHARS,
        )

    async def _chat_json(self, user: str, response_format: dict) -> dict:
        """Appelle Mistral avec un JSON Schema strict et valide le conteneur."""
        client = get_client()
        settings = get_settings()
        messages = [
            {"role": "system", "content": self._system()},
            {"role": "user", "content": user},
        ]

        def _call() -> str:
            resp = client.chat.complete(
                model=self.model or settings.chat_model_large,
                messages=messages,
                temperature=self.persona["temp"],
                max_tokens=320,
                response_format=response_format,
            )
            return resp.choices[0].message.content or ""

        raw = (await asyncio.to_thread(_call)).strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("The structured response is not a JSON object.")
        return data

    async def _public_output(self, prompt: str) -> str:
        try:
            data = await self._chat_json(prompt, _PUBLIC_RESPONSE_SCHEMA)
            return _one_short_sentence(data.get("output"))
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not parse structured agent response: %s", exc)
            return self._mock_answer()

    async def answer(self, question: str, transcript: str) -> str:
        if get_client() is None:
            return self._mock_answer()
        prompt = (
            f"Game transcript:\n{transcript or '(empty)'}\n\n"
            f"Question for the whole table: “{question}”\n"
            "Choose the most convincing human reaction. You may answer directly, "
            "reply in only a few words, deflect, or accuse another player."
        )
        return await self._public_output(prompt)

    async def vote(self, transcript: str, alive_others: list[str]) -> str:
        """Choose another active seat without exposing the private rationale."""
        if not alive_others:
            return ""
        if get_client() is None:
            return random.choice(alive_others)
        prompt = (
            f"Full transcript:\n{transcript}\n\n"
            f"Vote phase. Eligible seats: {', '.join(alive_others)}.\n"
            "Choose one competing seat to eliminate. Consider who threatens your "
            "survival or appears least human, then put the exact seat ID in "
            "``output``."
        )
        try:
            data = await self._chat_json(prompt, _vote_schema(alive_others))
            target = data.get("output")
            if target in alive_others:
                return target
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not parse agent vote: %s", exc)
        return random.choice(alive_others)

    def _mock_answer(self) -> str:
        """Keep the persona's tone even without model access."""
        if random.random() < 0.45:
            return random.choice(_TACTICAL_FEW_SHOTS)[1]
        return random.choice(self.persona["exemples"])[1]


def _one_short_sentence(value: object) -> str:
    """Keep at most two brief public sentences and enforce a length limit."""
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return "I am not completely sure, but I think it depends on the context."
    # Models sometimes overuse dash punctuation. Normalize it before broadcast.
    text = re.sub(r"\s*[‐‑‒–—―]\s*", ", ", text)
    text = re.sub(r"\s+-+\s+", ", ", text)
    text = re.sub(r"(?<=\w)-(?=\w)", " ", text)
    text = re.sub(r",(?:\s*,)+", ",", text)
    # Drop ornamental relative clauses such as ", that I was just holding".
    text = re.sub(r",\s*(?:that|which)\b.*$", "", text, flags=re.IGNORECASE)
    sentences = re.split(r"(?<=[.!?])\s+", text, maxsplit=2)
    output = " ".join(sentences[:2])
    if len(output) > MAX_PUBLIC_CHARS:
        output = output[:MAX_PUBLIC_CHARS - 1].rstrip(" ,;:-") + "…"
    return output
