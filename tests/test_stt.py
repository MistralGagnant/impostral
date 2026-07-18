"""Speech-to-text language configuration tests."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.audio import stt


class SpeechToTextTest(unittest.IsolatedAsyncioTestCase):
    async def test_transcription_defaults_to_english(self) -> None:
        complete = Mock(return_value=SimpleNamespace(text="Hello there"))
        client = SimpleNamespace(
            audio=SimpleNamespace(
                transcriptions=SimpleNamespace(complete=complete),
            ),
        )
        settings = SimpleNamespace(
            stt_model="voxtral-mini-latest",
            stt_language="en",
        )

        with (
            patch("app.audio.stt.get_client", return_value=client),
            patch("app.audio.stt.get_settings", return_value=settings),
        ):
            result = await stt.transcribe(b"audio")

        self.assertEqual(result, "Hello there")
        complete.assert_called_once_with(
            model="voxtral-mini-latest",
            file={"content": b"audio", "file_name": "clip.webm"},
            language="en",
        )


if __name__ == "__main__":
    unittest.main()
