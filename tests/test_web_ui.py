"""Static contracts for user-facing web behavior."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WebUiTest(unittest.TestCase):
    def test_codename_is_explicitly_optional(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("Codename <small>optional · private</small>", html)
        self.assertNotIn('id="name-input" required', html)

    def test_seat_answers_are_not_line_clamped(self) -> None:
        css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        answer_rule = css.split(".seat-answer {", 1)[1].split("}", 1)[0]

        self.assertIn("overflow: visible", answer_rule)
        self.assertIn("white-space: normal", answer_rule)
        self.assertNotIn("line-clamp", answer_rule)

    def test_tts_playback_is_accelerated(self) -> None:
        audio_js = (ROOT / "web" / "audio.js").read_text(encoding="utf-8")

        self.assertIn("let playbackRate = 1.1", audio_js)
        self.assertIn("audio.playbackRate = playbackRate", audio_js)

    def test_lobby_wait_is_explained_clearly(self) -> None:
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("let humanWaitSeconds = 15", app_js)
        self.assertIn('phasePrompt.textContent = "Waiting for players…"', app_js)
        self.assertIn('"Waiting for players · "', app_js)


if __name__ == "__main__":
    unittest.main()
