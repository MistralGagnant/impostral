"""Static contracts for user-facing web behavior."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WebUiTest(unittest.TestCase):
    def test_home_page_has_search_and_social_metadata(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn('<link rel="canonical" href="https://impostral.com/"', html)
        self.assertIn('property="og:image" content="https://impostral.com/assets/logo.png"', html)
        self.assertIn('name="twitter:card" content="summary_large_image"', html)
        self.assertIn('type="application/ld+json"', html)

    def test_crawler_files_use_canonical_urls(self) -> None:
        robots = (ROOT / "web" / "robots.txt").read_text(encoding="utf-8")
        sitemap = (ROOT / "web" / "sitemap.xml").read_text(encoding="utf-8")

        self.assertIn("Sitemap: https://impostral.com/sitemap.xml", robots)
        self.assertIn("<loc>https://impostral.com/</loc>", sitemap)
        self.assertIn("<loc>https://impostral.com/stats.html</loc>", sitemap)

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
        css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")

        self.assertIn("let humanWaitSeconds = 15", app_js)
        self.assertIn('phasePrompt.textContent = "Waiting for players…"', app_js)
        self.assertIn('label.textContent = "Waiting for other players…"', app_js)
        self.assertIn("phasePrompt.replaceChildren(label, countdown)", app_js)
        self.assertIn(".lobby-countdown {", css)
        self.assertIn("font-size: clamp(2.8rem, 8vh, 5.4rem)", css)


if __name__ == "__main__":
    unittest.main()
