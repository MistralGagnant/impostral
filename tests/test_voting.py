"""Voting and elimination rules without network calls."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.game.state_machine import GameEngine


class StubAgent:
    def __init__(self, votes: list[str] | None = None, *, error: bool = False) -> None:
        self.votes = list(votes or [])
        self.error = error
        self.eligible_history: list[list[str]] = []

    async def vote(self, transcript: str, eligible: list[str]) -> str:
        self.eligible_history.append(list(eligible))
        if self.error:
            raise RuntimeError("agent vote failed")
        return self.votes.pop(0)


class StubSeat:
    def __init__(
        self,
        seat_id: str,
        kind: str,
        agent: StubAgent | None = None,
        model: str | None = None,
    ) -> None:
        self.id = seat_id
        self.kind = kind
        self.agent = agent
        self.model = model
        self.alive = True
        self.connected = kind == "human"
        self.votes_total = 0
        self.votes_correct = 0
        self.eliminated_round = None

    def public(self) -> dict:
        return {"id": self.id, "alive": self.alive, "connected": self.connected}


class StubRoom:
    def __init__(self, seats: list[StubSeat]) -> None:
        self.seats = {seat.id: seat for seat in seats}
        self.round_no = 1
        self.phase = None
        self.messages: list[dict] = []

    def alive_seats(self) -> list[StubSeat]:
        return [seat for seat in self.seats.values() if seat.alive]

    def alive_ids(self, exclude: str | None = None) -> list[str]:
        return [seat.id for seat in self.alive_seats() if seat.id != exclude]

    def llms_alive(self) -> list[StubSeat]:
        return [seat for seat in self.alive_seats() if seat.kind == "llm"]

    def humans_alive(self) -> list[StubSeat]:
        return [seat for seat in self.alive_seats() if seat.kind == "human"]

    def render_transcript(self) -> str:
        return ""

    async def broadcast(self, message: dict) -> None:
        self.messages.append(message)


def make_engine(room: StubRoom) -> GameEngine:
    engine = GameEngine(room)
    engine.settings = SimpleNamespace(
        vote_seconds=1,
        reveal_role_on_elimination=True,
    )
    return engine


class VotingTest(unittest.IsolatedAsyncioTestCase):
    def test_game_ends_as_soon_as_only_ais_remain(self) -> None:
        human = StubSeat("Player A", "human")
        human.alive = False
        room = StubRoom([
            human,
            StubSeat("Player B", "llm", StubAgent(), "mistral-large-latest"),
            StubSeat("Player C", "llm", StubAgent(), "mistral-small-latest"),
        ])

        self.assertTrue(make_engine(room)._check_end())

    async def test_game_over_announces_ai_victory_when_no_humans_remain(self) -> None:
        human = StubSeat("Player A", "human")
        human.alive = False
        room = StubRoom([
            human,
            StubSeat("Player B", "llm", StubAgent(), "mistral-large-latest"),
            StubSeat("Player C", "llm", StubAgent(), "mistral-small-latest"),
        ])
        engine = make_engine(room)

        with patch("app.game.state_machine.stats.record_game"):
            await engine._game_over()

        game_over = next(msg for msg in room.messages if msg["type"] == "game_over")
        self.assertEqual(game_over["message"], "The AIs have won — no humans remain.")
        self.assertEqual(game_over["models"], {
            "Player B": "mistral-large-latest",
            "Player C": "mistral-small-latest",
        })

    async def test_one_human_and_one_ai_share_the_win(self) -> None:
        room = StubRoom([
            StubSeat("Player A", "human"),
            StubSeat("Player B", "llm", StubAgent(), "mistral-large-latest"),
        ])
        engine = make_engine(room)

        self.assertTrue(engine._check_end())
        with patch("app.game.state_machine.stats.record_game"):
            await engine._game_over()

        game_over = next(msg for msg in room.messages if msg["type"] == "game_over")
        self.assertEqual(game_over["winner"], "shared")
        self.assertEqual(game_over["winners"], ["Player A", "Player B"])
        self.assertIn("impossible to tell apart", game_over["message"])

    async def test_humans_win_when_every_ai_is_eliminated(self) -> None:
        ai = StubSeat("Player B", "llm", StubAgent(), "mistral-large-latest")
        ai.alive = False
        room = StubRoom([
            StubSeat("Player A", "human"),
            ai,
            StubSeat("Player C", "human"),
        ])
        engine = make_engine(room)
        engine.eliminated_llms = [ai.id]

        with patch("app.game.state_machine.stats.record_game"):
            await engine._game_over()

        game_over = next(msg for msg in room.messages if msg["type"] == "game_over")
        self.assertEqual(game_over["winner"], "humans")
        self.assertEqual(game_over["winners"], ["Player A", "Player C"])
        self.assertEqual(
            game_over["message"],
            "The humans have won — every AI was eliminated.",
        )

    async def test_every_seat_votes_and_a_tie_triggers_a_restricted_runoff(self) -> None:
        agent_b = StubAgent(["Player A", "Player A"])
        agent_d = StubAgent(["Player B", "Player B"])
        seats = [
            StubSeat("Player A", "human"),
            StubSeat("Player B", "llm", agent_b),
            StubSeat("Player C", "human"),
            StubSeat("Player D", "llm", agent_d),
        ]
        room = StubRoom(seats)
        engine = make_engine(room)
        human_votes = {
            "Player A": ["Player B", "Player B"],
            "Player C": ["Player A", "Player A"],
        }
        human_eligible_history = {"Player A": [], "Player C": []}

        async def request_human(seat, **kwargs):
            human_eligible_history[seat.id].append(kwargs["targets"])
            return {"target": human_votes[seat.id].pop(0)}

        engine._request_human = request_human
        with patch("app.game.state_machine.random.choice", side_effect=lambda items: items[0]):
            await engine._vote_phase()

        results = [msg for msg in room.messages if msg["type"] == "vote_result"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["tally"], {"Player B": 2, "Player A": 2})
        self.assertEqual(results[0]["runoff"], ["Player B", "Player A"])
        self.assertIsNone(results[0]["eliminated"])
        self.assertEqual(sum(results[1]["tally"].values()), len(seats))
        self.assertIn(results[1]["eliminated"], {"Player A", "Player B"})
        self.assertEqual(human_eligible_history["Player A"][1], ["Player B"])
        self.assertEqual(
            human_eligible_history["Player C"][1], ["Player B", "Player A"]
        )
        self.assertEqual(agent_b.eligible_history[1], ["Player A"])
        self.assertEqual(agent_d.eligible_history[1], ["Player B", "Player A"])
        self.assertEqual(seats[1].votes_total, 2)
        self.assertEqual(seats[3].votes_total, 2)
        self.assertEqual(seats[0].votes_total, 2)
        self.assertEqual(seats[2].votes_total, 2)

    async def test_missing_invalid_and_failed_votes_receive_a_fallback(self) -> None:
        seats = [
            StubSeat("Player A", "human"),
            StubSeat("Player B", "llm", StubAgent(error=True)),
            StubSeat("Player C", "llm", StubAgent(["not eligible"])),
        ]
        room = StubRoom(seats)
        engine = make_engine(room)
        engine._request_human = AsyncMock(return_value=None)

        with (
            patch("app.game.state_machine.random.choice", side_effect=lambda items: items[0]),
            patch("app.game.state_machine.log.exception"),
        ):
            tally = await engine._collect_ballot(seats, dur=1)

        self.assertEqual(sum(tally.values()), len(seats))
        self.assertEqual(seats[1].votes_total, 1)
        self.assertEqual(seats[2].votes_total, 1)

    async def test_a_selected_human_is_eliminated(self) -> None:
        human = StubSeat("Player A", "human")
        ai = StubSeat("Player B", "llm", StubAgent())
        room = StubRoom([human, ai])
        room.round_no = 3
        engine = make_engine(room)
        engine._pending_eliminated = human.id

        with patch("app.game.state_machine.asyncio.sleep", new=AsyncMock()):
            await engine._resolution_phase()

        self.assertFalse(human.alive)
        self.assertEqual(human.eliminated_round, 3)
        elimination = next(msg for msg in room.messages if msg["type"] == "elimination")
        self.assertEqual(elimination, {
            "type": "elimination",
            "seat": "Player A",
            "role": "human",
            "model": None,
        })


if __name__ == "__main__":
    unittest.main()
