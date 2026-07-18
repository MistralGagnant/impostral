"""Anonymous matchmaking and lobby lifecycle tests without network calls."""
from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.rooms import Room, RoomManager, Seat


def _setup_test_seats(room: Room) -> None:
    for index in range(room.num_humans):
        seat = Seat(id=f"Player {chr(65 + index)}", kind="human", voice="test")
        room.seats[seat.id] = seat
    for index in range(room.num_llms):
        seat = Seat(
            id=f"Player {chr(65 + room.num_humans + index)}",
            kind="llm",
            voice="test",
        )
        room.seats[seat.id] = seat


class MatchmakingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.settings = SimpleNamespace(
            num_humans=2,
            num_llms=1,
            matchmaking_reservation_seconds=20,
            reconnect_grace_seconds=30,
            waiting_lobby_ttl_seconds=600,
            finished_lobby_ttl_seconds=300,
        )
        self.settings_patch = patch("app.rooms.get_settings", return_value=self.settings)
        self.seats_patch = patch.object(Room, "setup_seats", _setup_test_seats)
        self.settings_patch.start()
        self.seats_patch.start()
        self.manager = RoomManager()

    async def asyncTearDown(self) -> None:
        self.seats_patch.stop()
        self.settings_patch.stop()

    async def test_players_fill_the_oldest_public_lobby_before_creating_another(self) -> None:
        first_room, first_token, first_created = await self.manager.matchmake(
            "player_0001", "session_0001"
        )
        second_room, second_token, second_created = await self.manager.matchmake(
            "player_0002", "session_0002"
        )
        third_room, _, third_created = await self.manager.matchmake(
            "player_0003", "session_0003"
        )

        self.assertEqual(first_room.id, second_room.id)
        self.assertNotEqual(first_room.id, third_room.id)
        self.assertNotEqual(first_token, second_token)
        self.assertEqual(first_room.visibility, "public")
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertTrue(third_created)

    async def test_concurrent_matchmaking_reserves_distinct_seats(self) -> None:
        results = await asyncio.gather(
            self.manager.matchmake("player_0001", "session_0001"),
            self.manager.matchmake("player_0002", "session_0002"),
        )

        rooms = [result[0] for result in results]
        self.assertEqual(rooms[0].id, rooms[1].id)
        reservations = [
            seat.reservation_token
            for seat in rooms[0].seats.values()
            if seat.kind == "human"
        ]
        self.assertEqual(len(set(reservations)), 2)

    async def test_retry_returns_the_same_reservation(self) -> None:
        first_room, first_token, _ = await self.manager.matchmake(
            "player_0001", "session_0001"
        )
        retry_room, retry_token, retry_created = await self.manager.matchmake(
            "player_0001", "session_0001"
        )

        self.assertEqual(retry_room.id, first_room.id)
        self.assertEqual(retry_token, first_token)
        self.assertFalse(retry_created)
        occupied = [
            seat
            for seat in first_room.seats.values()
            if seat.kind == "human" and seat.reservation_token
        ]
        self.assertEqual(len(occupied), 1)

    async def test_public_seat_requires_a_ticket_and_can_be_reconnected(self) -> None:
        room, token, _ = await self.manager.matchmake("player_0001", "session_0001")

        rejected = await room.attach(
            object(),
            "Intruder",
            player_id="player_9999",
            session_id="session_9999",
            reservation_token="wrong-ticket",
        )
        self.assertIsNone(rejected)

        first_socket = object()
        seat = await room.attach(
            first_socket,
            "Anonymous",
            player_id="player_0001",
            session_id="session_0001",
            reservation_token=token,
        )
        self.assertIsNotNone(seat)
        room.detach(first_socket)

        reconnected = await room.attach(
            object(),
            "Anonymous",
            player_id="player_0001",
            session_id="session_0001",
        )
        self.assertEqual(reconnected.id, seat.id)
        self.assertTrue(reconnected.connected)

    async def test_private_lobbies_are_never_used_by_quick_play(self) -> None:
        private = await self.manager.create_private(
            "friends", num_humans=2, num_llms=1
        )
        public, _, _ = await self.manager.matchmake("player_0001", "session_0001")

        self.assertIsNotNone(private)
        self.assertEqual(private.visibility, "private")
        self.assertNotEqual(private.id, public.id)

    async def test_private_seat_requires_its_http_reservation_ticket(self) -> None:
        room, token, created = await self.manager.create_private_and_reserve(
            "friends",
            num_humans=2,
            num_llms=1,
            player_id="player_0001",
            session_id="session_0001",
        )

        rejected_socket = object()
        rejected = await room.attach(
            rejected_socket,
            "Intruder",
            player_id="player_9999",
            session_id="session_9999",
        )
        creator_socket = object()
        accepted = await room.attach(
            creator_socket,
            "Creator",
            player_id="player_0001",
            session_id="session_0001",
            reservation_token=token,
        )

        self.assertTrue(created)
        self.assertIsNone(rejected)
        self.assertNotIn(rejected_socket, room._ws_all)
        self.assertIsNotNone(accepted)
        self.assertEqual(room.host_seat_id, accepted.id)
        self.assertTrue(room.is_host(accepted.id))

        room.detach(creator_socket)
        reconnected = await room.attach(
            object(),
            "Creator",
            player_id="player_0001",
            session_id="session_0001",
        )
        self.assertEqual(reconnected.id, accepted.id)
        self.assertTrue(room.is_host(reconnected.id))

    async def test_private_join_reserves_a_distinct_seat(self) -> None:
        room, creator_token, _ = await self.manager.create_private_and_reserve(
            "friends",
            num_humans=2,
            num_llms=1,
            player_id="player_0001",
            session_id="session_0001",
        )
        joined_room, joiner_token, error = await self.manager.reserve_private(
            "friends", "player_0002", "session_0002"
        )

        self.assertIs(joined_room, room)
        self.assertEqual(error, "")
        self.assertNotEqual(joiner_token, creator_token)

    async def test_disconnected_waiting_seat_is_released_after_the_grace_period(self) -> None:
        room, token, _ = await self.manager.matchmake("player_0001", "session_0001")
        socket = object()
        seat = await room.attach(
            socket,
            "Anonymous",
            player_id="player_0001",
            session_id="session_0001",
            reservation_token=token,
        )
        room.detach(socket)
        seat.disconnected_at = time.time() - self.settings.reconnect_grace_seconds - 1

        await self.manager.cleanup()

        self.assertFalse(seat.claimed)
        self.assertEqual(seat.player_id, "")
        self.assertIsNone(self.manager.get(room.id))

    async def test_finished_lobby_is_removed_immediately(self) -> None:
        room, _, _ = await self.manager.matchmake("player_0001", "session_0001")
        room.status = "finished"
        room.finished_at = time.time()

        await self.manager.cleanup()

        self.assertIsNone(self.manager.get(room.id))

    async def test_playback_waits_for_every_connected_human(self) -> None:
        room = Room(id="audio", num_humans=2, num_llms=0)
        _setup_test_seats(room)
        humans = list(room.seats.values())
        for seat in humans:
            seat.connected = True

        done = room.expect_playback("clip-1")
        room.resolve_playback(humans[0].id, "clip-1")
        self.assertFalse(done.done())
        room.resolve_playback(humans[1].id, "clip-1")
        self.assertTrue(done.done())

    async def test_abandoned_running_lobby_is_removed_after_reconnect_grace(self) -> None:
        room, token, _ = await self.manager.matchmake("player_0001", "session_0001")
        socket = object()
        seat = await room.attach(
            socket,
            "Anonymous",
            player_id="player_0001",
            session_id="session_0001",
            reservation_token=token,
        )
        room.status = "running"
        room.detach(socket)
        seat.disconnected_at = time.time() - self.settings.reconnect_grace_seconds - 1

        await self.manager.cleanup()

        self.assertIsNone(self.manager.get(room.id))


if __name__ == "__main__":
    unittest.main()
