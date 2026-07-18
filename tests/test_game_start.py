"""Lobby wait timeout and partial-start behavior."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.main import _maybe_start
from app.rooms import Room, Seat


class FakeEngine:
    def __init__(self, room: Room) -> None:
        self.room = room

    async def run(self) -> None:
        return


class GameStartTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_starts_with_only_connected_humans(self) -> None:
        room = Room(id="partial", num_humans=3, num_llms=1, visibility="public")
        room.seats = {
            "Player A": Seat(
                id="Player A", kind="human", voice="test",
                connected=True, claimed=True,
            ),
            "Player B": Seat(id="Player B", kind="human", voice="test"),
            "Player C": Seat(id="Player C", kind="human", voice="test"),
            "Player D": Seat(id="Player D", kind="llm", voice="test"),
        }
        room.ready_seats.add("Player A")

        with (
            patch("app.main.GameEngine", FakeEngine),
            patch(
                "app.main.get_settings",
                return_value=SimpleNamespace(human_wait_seconds=0),
            ),
        ):
            await _maybe_start(room)
            await room.start_wait_task
            await asyncio.sleep(0)

        self.assertTrue(room.started)
        self.assertEqual(room.status, "running")
        self.assertEqual(room.num_humans, 1)
        self.assertEqual(set(room.seats), {"Player A", "Player D"})


if __name__ == "__main__":
    unittest.main()
