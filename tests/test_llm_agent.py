"""Tests sans appel réseau du contrat de sortie des agents."""
from __future__ import annotations

import sys
import types
import unittest


# Isole le module testé des dépendances de configuration non nécessaires ici.
config_stub = types.ModuleType("app.config")
config_stub.get_settings = lambda: None
client_stub = types.ModuleType("app.mistral_client")
client_stub.get_client = lambda: None
sys.modules.setdefault("app.config", config_stub)
sys.modules.setdefault("app.mistral_client", client_stub)

from app.agents.llm_agent import (  # noqa: E402
    _DELIBERATION_SCHEMA,
    _PUBLIC_RESPONSE_SCHEMA,
    LLMAgent,
    PERSONAS,
    _one_short_sentence,
)


class SortieAgentTest(unittest.TestCase):
    def test_le_schema_separe_raisonnement_et_sortie(self) -> None:
        schema = _PUBLIC_RESPONSE_SCHEMA["json_schema"]["schema"]
        self.assertEqual(schema["required"], ["thinking", "output"])
        self.assertEqual(schema["properties"]["output"]["maxLength"], 180)
        self.assertEqual(schema["properties"]["thinking"]["maxLength"], 800)
        self.assertFalse(schema["additionalProperties"])

    def test_la_deliberation_garde_le_raisonnement_prive(self) -> None:
        schema = _DELIBERATION_SCHEMA["json_schema"]["schema"]
        self.assertIn("thinking", schema["required"])
        self.assertIn("output", schema["required"])
        self.assertNotIn("thinking", {"action", "target", "text"})

    def test_une_seule_phrase_est_diffusee(self) -> None:
        self.assertEqual(
            _one_short_sentence("Première phrase. Seconde phrase."),
            "Première phrase.",
        )

    def test_la_sortie_est_bornee(self) -> None:
        output = _one_short_sentence("a" * 250)
        self.assertLessEqual(len(output), 180)
        self.assertTrue(output.endswith("…"))

    def test_chaque_persona_possede_des_exemples_humains_courts(self) -> None:
        for persona in PERSONAS:
            with self.subTest(persona=persona["nom"]):
                self.assertGreaterEqual(len(persona["exemples"]), 3)
                for question, response in persona["exemples"]:
                    self.assertTrue(question)
                    self.assertTrue(response)
                    self.assertLessEqual(len(response), 180)
                    self.assertEqual(_one_short_sentence(response), response)

    def test_le_prompt_contient_uniquement_les_exemples_de_la_persona(self) -> None:
        agent = LLMAgent("Joueur A", 0)
        prompt = agent._system()
        self.assertIn(PERSONAS[0]["exemples"][0][1], prompt)
        self.assertNotIn(PERSONAS[1]["exemples"][0][1], prompt)


if __name__ == "__main__":
    unittest.main()
