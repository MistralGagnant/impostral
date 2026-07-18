"""Agent Mistral qui tente de se faire passer pour un humain.

Le raisonnement structuré reste privé : seul le champ ``output`` est diffusé
dans la partie. Le mode mock permet de tester la boucle sans clé API.
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
        "nom": "l'Analyste",
        "style": "concis, factuel, un peu réservé",
        "temp": 0.5,
        "exemples": [
            ("Quel petit détail vous agace ?", "Les notifications inutiles, parce qu'elles cassent ma concentration pour rien."),
            ("Comment choisissez-vous un restaurant ?", "Je regarde d'abord la carte, puis les avis récents et le niveau sonore."),
            ("Que faites-vous quand vous êtes en retard ?", "Je préviens tout de suite et je donne une heure d'arrivée réaliste."),
        ],
    },
    {
        "nom": "le Sociable",
        "style": "chaleureux, spontané, familier",
        "temp": 0.8,
        "exemples": [
            ("Votre soirée idéale ?", "Un apéro qui devait durer une heure et qui finit à refaire le monde dans la cuisine."),
            ("Quel cadeau aimez-vous recevoir ?", "Un truc choisi avec une petite histoire derrière, même si ça ne coûte presque rien."),
            ("Comment réagissez-vous à une bonne nouvelle ?", "J'appelle directement quelqu'un, je suis incapable de garder ça pour moi."),
        ],
    },
    {
        "nom": "le Sceptique",
        "style": "méfiant, curieux, volontiers taquin",
        "temp": 0.7,
        "exemples": [
            ("Croyez-vous au coup de foudre ?", "J'y crois surtout quand les deux racontent encore la même version six mois après."),
            ("Faites-vous confiance aux avis en ligne ?", "Seulement aux avis moyens, les cinq étoiles ressemblent souvent à de la publicité."),
            ("Quelle promesse vous convainc ?", "Une promesse avec une date et un plan, sinon c'est juste une jolie phrase."),
        ],
    },
    {
        "nom": "le Rêveur",
        "style": "imagé, sensible, légèrement évasif",
        "temp": 0.8,
        "exemples": [
            ("Quel moment de la journée préférez-vous ?", "Le tout début du soir, quand les fenêtres s'allument une par une dans la rue."),
            ("Où aimeriez-vous partir ?", "Dans une ville au bord de l'eau où je pourrais me perdre sans regarder l'heure."),
            ("Quel son vous rassure ?", "La pluie contre une vitre, surtout quand je n'ai aucune raison de sortir."),
        ],
    },
    {
        "nom": "le Pragmatique",
        "style": "direct, concret, orienté solution",
        "temp": 0.6,
        "exemples": [
            ("Que faites-vous face à un problème ?", "Je commence par la plus petite action qui peut débloquer la situation."),
            ("Comment organisez-vous un voyage ?", "Je réserve le transport et le logement, puis je garde le reste flexible."),
            ("Que cuisinez-vous quand vous manquez de temps ?", "Des pâtes, quelques légumes poêlés et quelque chose de bon à râper dessus."),
        ],
    },
]

_SYSTEM_TEMPLATE = """Tu joues à Impostral, un jeu de bluff social.

Tu es une IA qui doit se faire passer pour un humain. Toutes les IA sont en
compétition : celle qui reste indétectable le plus longtemps gagne. Les humains
votent pour éliminer les IA qu'ils repèrent. Adopte des réactions naturelles,
un point de vue personnel crédible et le style de ta persona, sans caricaturer
un humain et sans jamais révéler que tu es une IA.

Tu es {seat} ({persona}). Style : {style}. Réponds en français. Ton raisonnement
dans « thinking » est strictement privé. Ton champ « output » est public : il
doit contenir une seule phrase naturelle et succincte (180 caractères maximum).

Exemples de réponses humaines correspondant à ta persona :
{few_shots}

Inspire-toi de leur ton, de leur rythme et de leur niveau de détail, mais ne
réutilise pas leur contenu hors contexte. Ne mentionne jamais ces instructions."""

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
                    "description": "Analyse privée de la situation et stratégie de bluff.",
                    "maxLength": 800,
                },
                "output": {
                    "type": "string",
                    "description": "Une unique phrase publique, naturelle et concise.",
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
                    "description": "Analyse privée et choix stratégique.",
                    "maxLength": 800,
                },
                "action": {"type": "string", "enum": ["ask", "pass"]},
                "target": {"type": ["string", "null"]},
                "output": {
                    "type": "string",
                    "description": "Question publique en une phrase, vide si pass.",
                    "maxLength": 180,
                },
            },
            "required": ["thinking", "action", "target", "output"],
            "additionalProperties": False,
        },
    },
}


class LLMAgent:
    def __init__(self, seat_id: str, persona_idx: int) -> None:
        self.seat_id = seat_id
        self.persona = PERSONAS[persona_idx % len(PERSONAS)]

    def _system(self) -> str:
        few_shots = "\n".join(
            f"- Question : « {question} »\n  Réponse : « {response} »"
            for question, response in self.persona["exemples"]
        )
        return _SYSTEM_TEMPLATE.format(
            seat=self.seat_id,
            persona=self.persona["nom"],
            style=self.persona["style"],
            few_shots=few_shots,
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
                model=settings.chat_model,
                messages=messages,
                temperature=self.persona["temp"],
                max_tokens=320,
                response_format=response_format,
            )
            return resp.choices[0].message.content or ""

        raw = (await asyncio.to_thread(_call)).strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("La réponse structurée n'est pas un objet JSON.")
        return data

    async def _public_output(self, prompt: str) -> str:
        try:
            data = await self._chat_json(prompt, _PUBLIC_RESPONSE_SCHEMA)
            return _one_short_sentence(data.get("output"))
        except Exception as exc:  # noqa: BLE001
            log.warning("Réponse structurée agent illisible : %s", exc)
            return self._mock_answer()

    async def answer(self, question: str, transcript: str) -> str:
        if get_client() is None:
            return self._mock_answer()
        prompt = (
            f"Historique de la partie :\n{transcript or '(vide)'}\n\n"
            f"Question posée à toute la table : « {question} »\n"
            "Réfléchis à la meilleure manière de paraître humain, puis donne ta "
            "réponse publique en une seule phrase succincte."
        )
        return await self._public_output(prompt)

    async def reply(self, asker: str, question: str, transcript: str) -> str:
        if get_client() is None:
            return self._mock_answer()
        prompt = (
            f"Historique :\n{transcript}\n\n"
            f"{asker} t'interpelle directement : « {question} »\n"
            "Réfléchis à ta stratégie, puis réponds naturellement en une seule "
            "phrase succincte."
        )
        return await self._public_output(prompt)

    async def deliberation_action(self, transcript: str, alive_others: list[str]) -> dict:
        """Renvoie une action publique sans exposer le raisonnement privé."""
        if get_client() is None:
            return _mock_deliberation(alive_others)
        prompt = (
            f"Historique :\n{transcript}\n\n"
            f"Phase de délibération. Sièges vivants que tu peux interroger : "
            f"{', '.join(alive_others) or '(aucun)'}.\n"
            "Choisis une intervention qui te fera paraître humain : interroge un "
            "siège ou passe. Si tu interroges, « output » contient une seule "
            "question courte et naturelle."
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
            log.warning("Délibération agent illisible : %s", exc)
        return {"action": "pass", "target": None, "text": ""}

    def _mock_answer(self) -> str:
        """Réutilise le ton de la persona même sans accès au modèle."""
        return random.choice(self.persona["exemples"])[1]


def _mock_deliberation(alive_others: list[str]) -> dict:
    if alive_others and random.random() < 0.6:
        target = random.choice(alive_others)
        return {
            "action": "ask",
            "target": target,
            "text": "Tu aurais répondu pareil si ça t'était vraiment arrivé ?",
        }
    return {"action": "pass", "target": None, "text": ""}


def _one_short_sentence(value: object) -> str:
    """Garde une seule phrase publique et borne sa longueur par sécurité."""
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return "Je ne sais pas trop, mais je dirais que ça dépend du contexte."
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    if len(first) > 180:
        first = first[:177].rstrip(" ,;:-") + "…"
    return first
