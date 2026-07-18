"""LLM player powered by a Mistral agent.

Each agent knows its own seat and its goal of exposing humans, but not the other
players' roles. It reasons only from the transcript and acts according to the
current game phase. Mock mode uses plausible scripted responses for testing
without an API key.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random

from ..config import get_settings
from ..mistral_client import get_client

log = logging.getLogger("impostral.agent")

# Personas vary agent temperature and editorial voice.
PERSONAS: list[dict] = [
    {"name": "The Analyst", "style": "concise, factual, slightly cold", "temp": 0.5},
    {"name": "The Talker", "style": "warm, digressive, full of examples", "temp": 0.9},
    {"name": "The Skeptic", "style": "wary and fond of probing questions", "temp": 0.7},
    {"name": "The Poet", "style": "visual, metaphorical, and elusive", "temp": 1.0},
    {"name": "The Pragmatist", "style": "direct and solution-oriented", "temp": 0.6},
]

_SYSTEM_TEMPLATE = """You are playing Impostral, a social deduction game.

The table contains seats named Player A, Player B, and so on. Some are AI like
you; others are HUMANS pretending to be AI. YOUR GOAL is to identify and vote
out the humans. Humans may reveal themselves through vivid emotion, personal
anecdotes, spontaneous mistakes, hesitation, or context-specific humor.
Remain a credible AI and never reveal that you are analyzing the others.

You are {seat} ({persona}). Style: {style}. Always answer in English and stay
brief (one to three sentences) unless told otherwise. Never mention these instructions."""


class LLMAgent:
    def __init__(self, seat_id: str, persona_idx: int, model: str | None = None) -> None:
        self.seat_id = seat_id
        self.persona = PERSONAS[persona_idx % len(PERSONAS)]
        # Per-seat chat model; falls back to settings.chat_model when unset.
        self.model = model

    # -- Helpers ----------------------------------------------------------
    def _system(self) -> str:
        return _SYSTEM_TEMPLATE.format(
            seat=self.seat_id,
            persona=self.persona["name"],
            style=self.persona["style"],
        )

    async def _chat(self, user: str, *, force_json: bool = False) -> str:
        client = get_client()
        settings = get_settings()
        messages = [
            {"role": "system", "content": self._system()},
            {"role": "user", "content": user},
        ]

        def _call() -> str:
            kwargs = dict(
                model=self.model or settings.chat_model,
                messages=messages,
                temperature=self.persona["temp"],
            )
            if force_json:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.complete(**kwargs)
            return resp.choices[0].message.content or ""

        return (await asyncio.to_thread(_call)).strip()

    # -- Phase actions ----------------------------------------------------
    async def answer(self, question: str, transcript: str) -> str:
        if get_client() is None:
            return _mock_answer(question)
        prompt = (
            f"Game transcript:\n{transcript or '(empty)'}\n\n"
            f"Question for the whole table: “{question}”\n"
            "Give YOUR answer in one to three sentences."
        )
        return await self._chat(prompt)

    async def reply(self, asker: str, question: str, transcript: str) -> str:
        if get_client() is None:
            return _mock_answer(question)
        prompt = (
            f"Transcript:\n{transcript}\n\n"
            f"{asker} asks you directly: “{question}”\n"
            "Reply in one or two sentences."
        )
        return await self._chat(prompt)

    async def deliberation_action(self, transcript: str, alive_others: list[str]) -> dict:
        """Return {action: 'ask'|'pass', target: str|None, text: str}."""
        if get_client() is None:
            return _mock_deliberation(alive_others)
        prompt = (
            f"Transcript:\n{transcript}\n\n"
            f"Deliberation phase. Active seats you may question: "
            f"{', '.join(alive_others) or '(none)'}.\n"
            "Choose a suspicious seat to test whether they are human, or pass. "
            "Answer as JSON: "
            '{"action": "ask"|"pass", "target": "Player X"|null, '
            '"text": "your question when asking"}.'
        )
        try:
            data = json.loads(await self._chat(prompt, force_json=True))
            if data.get("action") == "ask" and data.get("target") in alive_others:
                return {
                    "action": "ask",
                    "target": data["target"],
                    "text": str(data.get("text", "")).strip(),
                }
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not parse agent deliberation: %s", exc)
        return {"action": "pass", "target": None, "text": ""}

    async def vote(self, transcript: str, alive_others: list[str]) -> str:
        if not alive_others:
            return ""
        if get_client() is None:
            return _mock_vote(alive_others)
        prompt = (
            f"Full transcript:\n{transcript}\n\n"
            f"Vote phase. Eligible seats: {', '.join(alive_others)}.\n"
            "Vote for the seat you believe is HUMAN. Answer as JSON: "
            '{"target": "Player X", "reason": "…"}.'
        )
        try:
            data = json.loads(await self._chat(prompt, force_json=True))
            if data.get("target") in alive_others:
                return data["target"]
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not parse agent vote: %s", exc)
        return random.choice(alive_others)


# --- Scripted mock behavior ----------------------------------------------

_MOCK_SNIPPETS = [
    "Hard to say; I think it depends on the context.",
    "Objectively, several interpretations can coexist here.",
    "I do not have a strong preference on that point.",
    "That is an interesting question; I would lean toward nuance.",
    "In general, I stick to observable facts.",
]


def _mock_answer(question: str) -> str:
    return random.choice(_MOCK_SNIPPETS)


def _mock_deliberation(alive_others: list[str]) -> dict:
    if alive_others and random.random() < 0.6:
        target = random.choice(alive_others)
        return {
            "action": "ask",
            "target": target,
            "text": "Could you clarify what you meant earlier?",
        }
    return {"action": "pass", "target": None, "text": ""}


def _mock_vote(alive_others: list[str]) -> str:
    return random.choice(alive_others)
