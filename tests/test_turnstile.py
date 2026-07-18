"""Cloudflare Turnstile verification and WebSocket origin tests."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.config import Settings
from app.main import _same_origin_websocket, app
from app.rooms import Room, RoomManager, Seat
from app.turnstile import (
    GAME_ENTRY_ACTION,
    TurnstileVerification,
    verify_turnstile,
)


class TurnstileVerificationTest(unittest.IsolatedAsyncioTestCase):
    async def verify_response(
        self, body: dict, *, status_code: int = 200
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, json=body, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await verify_turnstile(
                "browser-token",
                secret_key="server-secret",
                expected_hostname="impostral.com",
                expected_action=GAME_ENTRY_ACTION,
                client=client,
            )

    async def test_accepts_matching_hostname_and_action(self) -> None:
        result = await self.verify_response({
            "success": True,
            "hostname": "impostral.com",
            "action": GAME_ENTRY_ACTION,
        })

        self.assertTrue(result.allowed)
        self.assertFalse(result.unavailable)

    async def test_rejects_failed_challenge(self) -> None:
        result = await self.verify_response({
            "success": False,
            "error-codes": ["invalid-input-response"],
        })

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "invalid-input-response")

    async def test_rejects_wrong_hostname_or_action(self) -> None:
        wrong_hostname = await self.verify_response({
            "success": True,
            "hostname": "example.com",
            "action": GAME_ENTRY_ACTION,
        })
        wrong_action = await self.verify_response({
            "success": True,
            "hostname": "impostral.com",
            "action": "different_action",
        })

        self.assertEqual(wrong_hostname.reason, "hostname_mismatch")
        self.assertEqual(wrong_action.reason, "action_mismatch")

    async def test_marks_siteverify_http_errors_as_unavailable(self) -> None:
        result = await self.verify_response({}, status_code=503)

        self.assertFalse(result.allowed)
        self.assertTrue(result.unavailable)

    async def test_rejects_missing_token_without_network_call(self) -> None:
        result = await verify_turnstile(
            "",
            secret_key="server-secret",
            expected_hostname="impostral.com",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "missing_token")


class TurnstileConfigurationTest(unittest.TestCase):
    def test_localhost_can_run_without_a_secret(self) -> None:
        settings = Settings(
            _env_file=None,
            MISTRAL_API_KEY="",
            TURNSTILE_SECRET_KEY="",
            K_SERVICE="",
        )

        self.assertFalse(settings.turnstile_required)

    def test_cloud_run_fails_closed_without_a_secret(self) -> None:
        settings = Settings(
            _env_file=None,
            MISTRAL_API_KEY="",
            TURNSTILE_SECRET_KEY="",
            K_SERVICE="impostral",
        )

        self.assertTrue(settings.turnstile_required)
        self.assertFalse(settings.turnstile_enabled)


class WebSocketOriginTest(unittest.TestCase):
    def test_accepts_same_origin(self) -> None:
        websocket = SimpleNamespace(headers={
            "origin": "https://impostral.com",
            "host": "impostral.com",
        })

        self.assertTrue(_same_origin_websocket(websocket))

    def test_rejects_cross_origin_or_missing_origin(self) -> None:
        cross_origin = SimpleNamespace(headers={
            "origin": "https://attacker.example",
            "host": "impostral.com",
        })
        missing_origin = SimpleNamespace(headers={"host": "impostral.com"})

        self.assertFalse(_same_origin_websocket(cross_origin))
        self.assertFalse(_same_origin_websocket(missing_origin))


class ProtectedEntryPointTest(unittest.IsolatedAsyncioTestCase):
    async def test_all_game_entry_routes_reject_failed_admission(self) -> None:
        settings = SimpleNamespace(
            turnstile_enabled=True,
            turnstile_required=True,
            turnstile_secret_key="server-secret",
            num_humans=2,
            num_llms=1,
            min_humans=1,
            max_humans=8,
        )
        rejected = TurnstileVerification(False, "invalid-input-response")
        transport = httpx.ASGITransport(app=app)
        requests = (
            ("/matchmaking", {
                "player_id": "player_0001",
                "session_id": "session_0001",
                "turnstile_token": "invalid-token",
            }),
            ("/lobby", {
                "name": "friends",
                "num_humans": 2,
                "player_id": "player_0001",
                "session_id": "session_0001",
                "turnstile_token": "invalid-token",
            }),
            ("/lobby/friends/join", {
                "player_id": "player_0001",
                "session_id": "session_0001",
                "turnstile_token": "invalid-token",
            }),
        )

        with (
            patch("app.main.get_settings", return_value=settings),
            patch(
                "app.main.verify_turnstile",
                new=AsyncMock(return_value=rejected),
            ) as verifier,
        ):
            async with httpx.AsyncClient(
                transport=transport, base_url="https://impostral.com"
            ) as client:
                for url, payload in requests:
                    with self.subTest(url=url):
                        response = await client.post(url, json=payload)
                        self.assertEqual(response.status_code, 403)
                        self.assertEqual(
                            response.json(), {"error": "security_check_failed"}
                        )

        self.assertEqual(verifier.await_count, len(requests))

    async def test_private_http_flow_returns_distinct_reservations_locally(self) -> None:
        settings = SimpleNamespace(
            turnstile_enabled=False,
            turnstile_required=False,
            turnstile_secret_key="",
            num_humans=2,
            num_llms=1,
            min_humans=1,
            max_humans=8,
            matchmaking_reservation_seconds=20,
            reconnect_grace_seconds=30,
            waiting_lobby_ttl_seconds=600,
            finished_lobby_ttl_seconds=300,
        )
        manager = RoomManager()

        def setup_seats(room: Room) -> None:
            for index in range(room.num_humans):
                seat_id = f"Player {chr(65 + index)}"
                room.seats[seat_id] = Seat(
                    id=seat_id, kind="human", voice="test"
                )
            for index in range(room.num_llms):
                seat_id = f"Player {chr(65 + room.num_humans + index)}"
                room.seats[seat_id] = Seat(
                    id=seat_id, kind="llm", voice="test"
                )

        transport = httpx.ASGITransport(app=app)
        with (
            patch("app.main.get_settings", return_value=settings),
            patch("app.main.rooms", manager),
            patch("app.rooms.get_settings", return_value=settings),
            patch.object(Room, "setup_seats", setup_seats),
        ):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                created = await client.post("/lobby", json={
                    "name": "friends",
                    "num_humans": 2,
                    "player_id": "player_0001",
                    "session_id": "session_0001",
                })
                joined = await client.post("/lobby/friends/join", json={
                    "player_id": "player_0002",
                    "session_id": "session_0002",
                })

        self.assertEqual(created.status_code, 200)
        self.assertEqual(joined.status_code, 200)
        self.assertNotEqual(
            created.json()["reservation_token"],
            joined.json()["reservation_token"],
        )


if __name__ == "__main__":
    unittest.main()
