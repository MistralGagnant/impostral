"""Banque de questions posées en phase QUESTION.

Questions ouvertes, sans « bonne réponse », qui poussent chacun à révéler un
style : c'est là que se jouent les tells (un humain se trahit par l'anecdote
trop vécue, un LLM par la prudence trop lisse).
"""
from __future__ import annotations

import random

QUESTIONS: list[str] = [
    "Quel est le dernier objet que vous avez tenu dans vos mains aujourd'hui ?",
    "Décrivez une odeur qui vous rappelle votre enfance.",
    "Si vous deviez mentir sur votre métier, lequel choisiriez-vous ?",
    "Quelle est la chose la plus inutile que vous connaissez par cœur ?",
    "Racontez un moment où vous avez eu tort mais refusé de l'admettre.",
    "Quel bruit vous agace au point de vouloir quitter une pièce ?",
    "Qu'avez-vous mangé à votre dernier repas, et l'avez-vous aimé ?",
    "Quelle habitude avez-vous que vous jugez un peu honteuse ?",
    "Décrivez la météo qu'il fait, là, autour de vous.",
    "Quel conseil donneriez-vous à quelqu'un qui a peur du noir ?",
    "Qu'est-ce qui vous a fait rire pour la dernière fois ?",
    "Si le silence avait une couleur, laquelle serait-ce pour vous ?",
]


def pick_question(exclude: set[str] | None = None) -> str:
    exclude = exclude or set()
    pool = [q for q in QUESTIONS if q not in exclude] or QUESTIONS
    return random.choice(pool)
