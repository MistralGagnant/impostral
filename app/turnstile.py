"""Server-side Cloudflare Turnstile verification."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
GAME_ENTRY_ACTION = "enter_game"


@dataclass(frozen=True)
class TurnstileVerification:
    """Normalized verification outcome used by the HTTP entry points."""

    allowed: bool
    reason: str
    unavailable: bool = False


async def verify_turnstile(
    token: str,
    *,
    secret_key: str,
    expected_hostname: str,
    expected_action: str = GAME_ENTRY_ACTION,
    client: httpx.AsyncClient | None = None,
) -> TurnstileVerification:
    """Validate one short-lived, single-use Turnstile token.

    The caller owns an injected client. Otherwise a short-lived client is
    created because this endpoint is called only once per game admission.
    """
    if not token:
        return TurnstileVerification(False, "missing_token")
    if not secret_key:
        return TurnstileVerification(False, "missing_secret", unavailable=True)
    if not expected_hostname:
        return TurnstileVerification(False, "missing_hostname", unavailable=True)

    payload = {"secret": secret_key, "response": token}

    async def submit(http_client: httpx.AsyncClient) -> TurnstileVerification:
        try:
            response = await http_client.post(SITEVERIFY_URL, data=payload)
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return TurnstileVerification(False, "siteverify_unavailable", unavailable=True)

        if not body.get("success"):
            error_codes = body.get("error-codes")
            reason = (
                str(error_codes[0])
                if isinstance(error_codes, list) and error_codes
                else "challenge_rejected"
            )
            return TurnstileVerification(False, reason)
        if body.get("hostname") != expected_hostname:
            return TurnstileVerification(False, "hostname_mismatch")
        if body.get("action") != expected_action:
            return TurnstileVerification(False, "action_mismatch")
        return TurnstileVerification(True, "accepted")

    if client is not None:
        return await submit(client)
    async with httpx.AsyncClient(timeout=5.0) as http_client:
        return await submit(http_client)
