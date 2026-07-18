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
            file={
                "content": b"audio",
                "file_name": "clip.webm",
                "content_type": "audio/webm",
            },
            language="en",
        )

    async def test_transcription_preserves_safari_mp4_content_type(self) -> None:
        complete = Mock(return_value=SimpleNamespace(text="Safari audio"))
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
            result = await stt.transcribe(
                b"audio", mime_type="audio/mp4;codecs=mp4a.40.2"
            )

        self.assertEqual(result, "Safari audio")
        complete.assert_called_once_with(
            model="voxtral-mini-latest",
            file={
                "content": b"audio",
                "file_name": "clip.mp4",
                "content_type": "audio/mp4;codecs=mp4a.40.2",
            },
            language="en",
        )

    def test_non_audio_content_type_falls_back_to_webm(self) -> None:
        self.assertEqual(
            stt._upload_metadata("text/plain"),
            ("audio/webm", "clip.webm"),
        )


if __name__ == "__main__":
    unittest.main()
