"""Open-ended prompts used during the QUESTION phase.

There is no correct answer. Each prompt encourages players to reveal a personal
style, creating tells: a human may sound overly lived-in, while an LLM may sound
too cautious or polished.
"""
from __future__ import annotations

import random

QUESTIONS: list[str] = [
    "What is the last object you held in your hands today?",
    "Describe a smell that reminds you of childhood.",
    "If you had to lie about your job, which one would you choose?",
    "What is the most useless thing you know by heart?",
    "Tell us about a time you were wrong but refused to admit it.",
    "What sound annoys you enough to make you leave a room?",
    "What did you eat for your last meal, and did you enjoy it?",
    "What habit of yours do you find slightly embarrassing?",
    "Describe the weather around you right now.",
    "What advice would you give someone who is afraid of the dark?",
    "What made you laugh most recently?",
    "If silence had a color, what color would it be for you?",
]


def pick_question(exclude: set[str] | None = None) -> str:
    exclude = exclude or set()
    pool = [q for q in QUESTIONS if q not in exclude] or QUESTIONS
    return random.choice(pool)
