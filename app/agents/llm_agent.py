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

PERSONAS: list[dict] = [
    {
        "nom": "The Analyst",
        "style": "concise, factual, and slightly reserved",
        "temp": 0.5,
        "exemples": [
            ("What minor thing annoys you?", "Pointless notifications, because they break my focus for no good reason."),
            ("How do you choose a restaurant?", "I check the menu first, then recent reviews and how noisy the place is."),
            ("What do you do when you are late?", "I send a message immediately and give a realistic arrival time."),
        ],
    },
    {
        "nom": "The Social One",
        "style": "warm, spontaneous, and casual",
        "temp": 0.8,
        "exemples": [
            ("What is your ideal evening?", "A quick drink that somehow turns into hours of talking in the kitchen."),
            ("What gift do you enjoy receiving?", "Something with a little story behind it, even if it barely cost anything."),
            ("How do you react to good news?", "I call someone right away because I am terrible at keeping it to myself."),
        ],
    },
    {
        "nom": "The Skeptic",
        "style": "wary, curious, and playfully challenging",
        "temp": 0.7,
        "exemples": [
            ("Do you believe in love at first sight?", "I believe it when both people still tell the same story six months later."),
            ("Do you trust online reviews?", "Mostly the average ones, because five star reviews often sound like advertising."),
            ("What kind of promise convinces you?", "One with a date and a plan, otherwise it is just a nice sentence."),
        ],
    },
    {
        "nom": "The Dreamer",
        "style": "visual, sensitive, and slightly elusive",
        "temp": 0.8,
        "exemples": [
            ("What time of day do you prefer?", "Early evening, when the windows along the street light up one by one."),
            ("Where would you like to travel?", "A town by the water where I could get lost without checking the time."),
            ("What sound comforts you?", "Rain against a window, especially when I have nowhere I need to be."),
        ],
    },
    {
        "nom": "The Pragmatist",
        "style": "direct, practical, and solution-oriented",
        "temp": 0.6,
        "exemples": [
            ("What do you do when facing a problem?", "I start with the smallest action that could unblock the situation."),
            ("How do you organize a trip?", "I book transport and accommodation, then keep everything else flexible."),
            ("What do you cook when short on time?", "Pasta, a few pan fried vegetables, and something good grated on top."),
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
one short natural utterance no longer than 180 characters. It may be only a few
words or contain up to two very short sentences.

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
                        "A short public utterance: a few words or at most two "
                        "brief sentences, possibly deflective or accusatory."
                    ),
                    "maxLength": 180,
                },
            },
            "required": ["thinking", "output"],
            "additionalProperties": False,
        },
    },
}

_DELIBERATION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "action_deliberation_impostral",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "thinking": {
                    "type": "string",
                    "description": "Private analysis and strategic choice.",
                    "maxLength": 800,
                },
                "action": {"type": "string", "enum": ["ask", "pass"]},
                "target": {"type": ["string", "null"]},
                "output": {
                    "type": "string",
                    "description": "One public question, or an empty string when passing.",
                    "maxLength": 180,
                },
            },
            "required": ["thinking", "action", "target", "output"],
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

    async def reply(self, asker: str, question: str, transcript: str) -> str:
        if get_client() is None:
            return self._mock_answer()
        prompt = (
            f"Transcript:\n{transcript}\n\n"
            f"{asker} asks you directly: “{question}”\n"
            "Reply naturally; you may be terse, defensive, evasive, or strongly "
            "counter-accusatory."
        )
        return await self._public_output(prompt)

    async def deliberation_action(self, transcript: str, alive_others: list[str]) -> dict:
        """Return a public action without exposing private reasoning."""
        if get_client() is None:
            return _mock_deliberation(alive_others)
        prompt = (
            f"Transcript:\n{transcript}\n\n"
            f"Deliberation phase. Active seats you may question: "
            f"{', '.join(alive_others) or '(none)'}.\n"
            "Choose an intervention that makes you sound human: question a seat "
            "or pass. When asking, ``output`` contains one short natural question."
        )
        try:
            data = await self._chat_json(prompt, _DELIBERATION_SCHEMA)
            if data.get("action") == "ask" and data.get("target") in alive_others:
                return {
                    "action": "ask",
                    "target": data["target"],
                    "text": _one_short_sentence(data.get("output")),
                }
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not parse agent deliberation: %s", exc)
        return {"action": "pass", "target": None, "text": ""}

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


def _mock_deliberation(alive_others: list[str]) -> dict:
    if alive_others and random.random() < 0.6:
        target = random.choice(alive_others)
        return {
            "action": "ask",
            "target": target,
            "text": "Would you answer the same way if that had really happened to you?",
        }
    return {"action": "pass", "target": None, "text": ""}


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
    sentences = re.split(r"(?<=[.!?])\s+", text, maxsplit=2)
    output = " ".join(sentences[:2])
    if len(output) > 180:
        output = output[:177].rstrip(" ,;:-") + "…"
    return output
