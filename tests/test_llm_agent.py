"""Tests sans appel réseau du contrat de sortie des agents."""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch


# Isole le module testé des dépendances de configuration non nécessaires ici.
config_stub = types.ModuleType("app.config")
config_stub.get_settings = lambda: None
client_stub = types.ModuleType("app.mistral_client")
client_stub.get_client = lambda: None
sys.modules.setdefault("app.config", config_stub)
sys.modules.setdefault("app.mistral_client", client_stub)

from app.agents.llm_agent import (  # noqa: E402
    _PUBLIC_RESPONSE_SCHEMA,
    LLMAgent,
    MAX_PUBLIC_CHARS,
    PERSONAS,
    _TACTICAL_FEW_SHOTS,
    _one_short_sentence,
    _vote_schema,
)
from app.game import stats  # noqa: E402


class SortieAgentTest(unittest.TestCase):
    def test_le_schema_separe_raisonnement_et_sortie(self) -> None:
        schema = _PUBLIC_RESPONSE_SCHEMA["json_schema"]["schema"]
        self.assertEqual(schema["required"], ["thinking", "output"])
        self.assertEqual(
            schema["properties"]["output"]["maxLength"], MAX_PUBLIC_CHARS
        )
        self.assertEqual(schema["properties"]["thinking"]["maxLength"], 800)
        self.assertFalse(schema["additionalProperties"])

    def test_deux_phrases_courtes_peuvent_etre_diffusees(self) -> None:
        self.assertEqual(
            _one_short_sentence("First sentence. Second sentence. Third sentence."),
            "First sentence. Second sentence.",
        )

    def test_une_reponse_de_quelques_mots_est_conservee(self) -> None:
        self.assertEqual(_one_short_sentence("Honestly, no idea."), "Honestly, no idea.")

    def test_une_relative_superflue_est_supprimee(self) -> None:
        self.assertEqual(
            _one_short_sentence(
                "A warm cup of coffee, that I was just holding in my hand."
            ),
            "A warm cup of coffee",
        )

    def test_les_tirets_sont_normalises(self) -> None:
        output = _one_short_sentence("I—think Player-C looks scripted - honestly.")
        self.assertNotRegex(output, r"[-‐‑‒–—―]")
        self.assertEqual(output, "I, think Player C looks scripted, honestly.")

    def test_la_sortie_est_bornee(self) -> None:
        output = _one_short_sentence("a" * 250)
        self.assertLessEqual(len(output), MAX_PUBLIC_CHARS)
        self.assertTrue(output.endswith("…"))

    def test_chaque_persona_possede_des_exemples_humains_courts(self) -> None:
        for persona in PERSONAS:
            with self.subTest(persona=persona["nom"]):
                self.assertGreaterEqual(len(persona["exemples"]), 3)
                for question, response in persona["exemples"]:
                    self.assertTrue(question)
                    self.assertTrue(response)
                    self.assertLessEqual(len(response), MAX_PUBLIC_CHARS)
                    self.assertEqual(_one_short_sentence(response), response)

    def test_le_prompt_contient_uniquement_les_exemples_de_la_persona(self) -> None:
        agent = LLMAgent("Player A", 0, model="mistral-large-latest")
        prompt = agent._system()
        self.assertEqual(agent.model, "mistral-large-latest")
        self.assertIn(PERSONAS[0]["exemples"][0][1], prompt)
        self.assertNotIn(PERSONAS[1]["exemples"][0][1], prompt)
        self.assertIn(_TACTICAL_FEW_SHOTS[1][1], prompt)
        self.assertIn("Never use hyphens", prompt)

    def test_le_vote_est_limite_aux_cibles_eligibles(self) -> None:
        targets = ["Player B", "Player C"]
        schema = _vote_schema(targets)["json_schema"]["schema"]
        self.assertEqual(schema["required"], ["thinking", "output"])
        self.assertEqual(schema["properties"]["output"]["enum"], targets)
        self.assertFalse(schema["additionalProperties"])

    def test_les_stats_mesurent_les_victoires_individuelles(self) -> None:
        records = [{
            "rounds": 3,
            "winners": ["Player A"],
            "llms": [
                {
                    "model": "mistral-large-latest",
                    "won": True,
                    "survived": False,
                    "eliminated_round": 3,
                    "votes_total": 2,
                    "votes_correct": 1,
                },
                {
                    "model": "mistral-small-latest",
                    "won": False,
                    "survived": False,
                    "eliminated_round": 1,
                    "votes_total": 2,
                    "votes_correct": 0,
                },
            ],
        }]
        with patch.object(stats, "_read_records", return_value=records):
            models = {row["model"]: row for row in stats.aggregate()["models"]}
        self.assertIn("Humans", models)
        self.assertFalse(models["Humans"]["data_available"])
        self.assertEqual(models["Humans"]["legacy_games_without_data"], 1)
        self.assertEqual(models["mistral-large-latest"]["team_win_rate"], 1.0)
        self.assertEqual(models["mistral-small-latest"]["team_win_rate"], 0.0)
        self.assertEqual(models["mistral-large-latest"]["vote_accuracy"], 0.5)

    def test_les_stats_humaines_sont_agregees_quand_disponibles(self) -> None:
        records = [{
            "rounds": 2,
            "winners": ["Player A", "Player B"],
            "llms": [],
            "humans": [{
                "won": True,
                "survived": True,
                "eliminated_round": None,
                "votes_total": 2,
                "votes_correct": 1,
            }],
        }]
        with patch.object(stats, "_read_records", return_value=records):
            result = stats.aggregate()
        humans = next(row for row in result["models"] if row["model"] == "Humans")
        self.assertTrue(humans["data_available"])
        self.assertEqual(humans["team_win_rate"], 1.0)
        self.assertEqual(humans["vote_accuracy"], 0.5)
        self.assertEqual(result["legacy_games_without_humans"], 0)


if __name__ == "__main__":
    unittest.main()
