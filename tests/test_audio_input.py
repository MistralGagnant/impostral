"""Audio input WebSocket contract and game-engine propagation tests."""
from __future__ import annotations

import base64
import unittest
from unittest.mock import AsyncMock, patch

from app.game import events
from app.game.state_machine import GameEngine
from app.main import _normalize


class AudioInputTest(unittest.IsolatedAsyncioTestCase):
    def test_websocket_message_keeps_media_recorder_mime_type(self) -> None:
        message = events.parse_client_message({
            "type": "audio_blob",
            "audio_b64": "dm9pY2U=",
            "audio_mime": "audio/mp4;codecs=mp4a.40.2",
            "text": "fallback",
        })

        self.assertIsNotNone(message)
        self.assertEqual(_normalize(message), {
            "audio_b64": "dm9pY2U=",
            "audio_mime": "audio/mp4;codecs=mp4a.40.2",
            "text": "fallback",
        })

    def test_websocket_message_rejects_an_unbounded_mime_type(self) -> None:
        self.assertIsNone(events.parse_client_message({
            "type": "audio_blob",
            "audio_mime": "audio/" + ("x" * 100),
        }))

    async def test_engine_passes_audio_bytes_and_mime_type_to_stt(self) -> None:
        engine = GameEngine.__new__(GameEngine)
        payload = {
            "audio_b64": base64.b64encode(b"voice").decode("ascii"),
            "audio_mime": "audio/mp4",
            "text": "fallback",
        }

        with patch(
            "app.game.state_machine.stt.transcribe",
            new=AsyncMock(return_value="transcript"),
        ) as transcribe:
            result = await engine._payload_to_text(payload)

        self.assertEqual(result, "transcript")
        transcribe.assert_awaited_once_with(
            b"voice", mime_type="audio/mp4", fallback_text="fallback"
        )


if __name__ == "__main__":
    unittest.main()
