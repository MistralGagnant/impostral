"""Game flow engine: QUESTION -> DELIBERATION -> VOTE -> RESOLUTION.

Key properties:
- Anonymization: every utterance passes through `_speak` and the seat's TTS voice.
- Timing protection: QUESTION answers are collected for the full window, then
  revealed as a shuffled group at a fixed cadence (`reveal_gap_seconds`). An LLM
  never appears to answer faster than a human.
- Agents do not know who is human; they only receive the transcript.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
from typing import Optional

from ..audio import stt, tts
from ..config import get_settings
from . import events, questions, stats
from .events import Phase

log = logging.getLogger("impostral.engine")


class GameEngine:
    def __init__(self, room) -> None:
        self.room = room
        self.settings = get_settings()
        self.used_questions: set[str] = set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def run(self) -> None:
        try:
            await self._broadcast_state()
            await asyncio.sleep(1.0)
            while True:
                self.room.round_no += 1
                await self._system(f"— Round {self.room.round_no} —")

                await self._question_phase()
                if self._check_end():
                    break
                await self._deliberation_phase()
                await self._vote_phase()
                await self._resolution_phase()

                if self._check_end():
                    break
                if self.room.round_no >= self.settings.max_rounds:
                    break

            await self._game_over()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Game engine crashed")
            await self._system("An internal error interrupted the game.")

    # ------------------------------------------------------------------
    # QUESTION phase
    # ------------------------------------------------------------------
    async def _question_phase(self) -> None:
        self.room.phase = Phase.QUESTION
        question = questions.pick_question(self.used_questions)
        self.used_questions.add(question)
        dur = self.settings.question_seconds

        await self.room.broadcast(
            events.srv_phase_change(phase=Phase.QUESTION.value, deadline=dur, prompt=question)
        )
        await self._broadcast_state()

        alive = self.room.alive_seats()
        # Collect concurrently; each task returns (seat_id, text).
        tasks = [asyncio.ensure_future(self._collect_answer(s, question, dur)) for s in alive]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        answers: dict[str, str] = {}
        for res in results:
            if isinstance(res, tuple):
                sid, text = res
                answers[sid] = text or "(silence)"

        # Reveal as a shuffled group at a fixed cadence to hide response timing.
        order = list(answers.keys())
        random.shuffle(order)
        for sid in order:
            await self._speak(self.room.seats[sid], answers[sid], context="answer")

    async def _collect_answer(self, seat, question: str, dur: int) -> tuple[str, str]:
        if seat.kind == "llm":
            text = await seat.agent.answer(question, self.room.render_transcript())
            return seat.id, text
        payload = await self._request_human(seat, mode="answer", dur=dur)
        return seat.id, await self._payload_to_text(payload)

    # ------------------------------------------------------------------
    # DELIBERATION phase
    # ------------------------------------------------------------------
    async def _deliberation_phase(self) -> None:
        self.room.phase = Phase.DELIBERATION
        dur = self.settings.deliberation_seconds
        await self.room.broadcast(
            events.srv_phase_change(phase=Phase.DELIBERATION.value, deadline=dur)
        )
        await self._broadcast_state()

        loop = asyncio.get_event_loop()
        end_at = loop.time() + dur
        askers = self.room.alive_seats()
        random.shuffle(askers)
        idx = 0

        # Cap exchanges even when fast agents answer immediately, so the
        # transcript remains readable.
        max_exchanges = max(2, len(self.room.alive_seats()) * 2)
        done = 0

        # Continue targeted question-and-answer exchanges while time remains,
        # at least two seats are active, and the cap has not been reached.
        while (loop.time() < end_at and done < max_exchanges
               and len(self.room.alive_seats()) >= 2):
            asker = askers[idx % len(askers)]
            idx += 1
            if not asker.alive:
                continue
            remaining = int(end_at - loop.time())
            spoke = await self._one_exchange(asker, remaining)
            if spoke:
                done += 1

    async def _one_exchange(self, asker, remaining: int) -> bool:
        """Run one targeted exchange and return True when the asker participates."""
        others = self.room.alive_ids(exclude=asker.id)
        if not others:
            return False

        if asker.kind == "llm":
            action = await asker.agent.deliberation_action(self.room.render_transcript(), others)
            if action["action"] != "ask":
                return False
            target_id, q_text = action["target"], action["text"] or "Could you elaborate?"
        else:
            payload = await self._request_human(
                asker, mode="deliberation", dur=min(remaining, 25), targets=others
            )
            if not payload or not payload.get("target"):
                return False  # The player skipped.
            target_id = payload["target"]
            q_text = await self._payload_to_text(payload)
            if target_id not in others:
                return False

        await self._speak(asker, q_text or "Could you elaborate?", context=f"to {target_id}")

        target = self.room.seats.get(target_id)
        if target is None or not target.alive:
            return True
        if target.kind == "llm":
            reply = await target.agent.reply(asker.id, q_text, self.room.render_transcript())
        else:
            payload = await self._request_human(target, mode="reply", dur=min(remaining, 25))
            reply = await self._payload_to_text(payload)
        await self._speak(target, reply or "(silence)", context=f"reply to {asker.id}")
        return True

    # ------------------------------------------------------------------
    # VOTE phase
    # ------------------------------------------------------------------
    async def _vote_phase(self) -> None:
        self.room.phase = Phase.VOTE
        dur = self.settings.vote_seconds
        await self.room.broadcast(events.srv_phase_change(phase=Phase.VOTE.value, deadline=dur))
        await self._broadcast_state()

        alive = self.room.alive_seats()
        tasks = [asyncio.ensure_future(self._collect_vote(s, dur)) for s in alive]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        tally: dict[str, int] = {}
        for res in results:
            if isinstance(res, tuple) and res[1]:
                voter_id, target_id = res
                tally[target_id] = tally.get(target_id, 0) + 1
                # Track LLM detection accuracy: a vote names the seat believed to
                # be human, so it is "correct" when the target is actually human.
                voter = self.room.seats.get(voter_id)
                target = self.room.seats.get(target_id)
                if voter and voter.kind == "llm":
                    voter.votes_total += 1
                    if target and target.kind == "human":
                        voter.votes_correct += 1

        eliminated = self._resolve_tally(tally)
        await self.room.broadcast(events.srv_vote_result(tally=tally, eliminated=eliminated))
        self._pending_eliminated = eliminated

    async def _collect_vote(self, seat, dur: int) -> tuple[str, Optional[str]]:
        others = self.room.alive_ids(exclude=seat.id)
        if seat.kind == "llm":
            return seat.id, await seat.agent.vote(self.room.render_transcript(), others)
        payload = await self._request_human(seat, mode="vote", dur=dur, targets=others)
        target = payload.get("target") if payload else None
        return seat.id, target if target in others else None

    @staticmethod
    def _resolve_tally(tally: dict[str, int]) -> Optional[str]:
        if not tally:
            return None
        top = max(tally.values())
        leaders = [sid for sid, n in tally.items() if n == top]
        return random.choice(leaders)  # Break ties randomly.

    # ------------------------------------------------------------------
    # RESOLUTION phase
    # ------------------------------------------------------------------
    async def _resolution_phase(self) -> None:
        self.room.phase = Phase.RESOLUTION
        eliminated = getattr(self, "_pending_eliminated", None)
        if eliminated and eliminated in self.room.seats:
            seat = self.room.seats[eliminated]
            seat.alive = False
            seat.eliminated_round = self.room.round_no
            role = seat.kind if self.settings.reveal_role_on_elimination else None
            await self.room.broadcast(events.srv_elimination(seat=eliminated, role=role))
            if role:
                label = "a HUMAN" if role == "human" else "an AI"
                await self._system(f"{eliminated} is out… they were {label}.")
            else:
                await self._system(f"{eliminated} is out.")
        else:
            await self._system("No one is eliminated this round.")
        await self._broadcast_state()
        await asyncio.sleep(1.5)

    # ------------------------------------------------------------------
    # Game over
    # ------------------------------------------------------------------
    def _check_end(self) -> bool:
        return not self.room.humans_alive() or not self.room.llms_alive()

    async def _game_over(self) -> None:
        self.room.phase = Phase.GAME_OVER
        if not self.room.humans_alive():
            winner = "llms"
        else:
            winner = "humans"  # Humans survived, or all LLMs were eliminated.
        roles = {s.id: s.kind for s in self.room.seats.values()}
        stats.record_game(self.room, winner)
        await self.room.broadcast(events.srv_game_over(winner=winner, roles=roles))
        msg = ("AI eliminated every human." if winner == "llms"
               else "Humans survived — they win!")
        await self._system("Game over. " + msg)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    async def _speak(self, seat, text: str, context: str = "") -> None:
        """Anonymize and broadcast an utterance with the seat's synthetic voice."""
        self.room.add_utterance(seat.id, text, context)
        audio_url = await tts.synthesize(text, voice=seat.voice)
        await self.room.broadcast(
            events.srv_utterance(seat=seat.id, text=text, audio_url=audio_url, context=context)
        )
        await asyncio.sleep(self.settings.reveal_gap_seconds)

    async def _request_human(self, seat, *, mode: str, dur: int,
                             targets: Optional[list[str]] = None) -> Optional[dict]:
        """Request human input and wait for a response or timeout."""
        if not seat.connected:
            return None
        # Create the Future before sending, so an immediate response cannot be lost.
        fut = self.room.expect_input(seat.id)
        await self.room.send_seat(
            seat.id, events.srv_request_input(mode=mode, deadline=dur, targets=targets)
        )
        try:
            return await asyncio.wait_for(fut, timeout=max(1, dur))
        except asyncio.TimeoutError:
            self.room.cancel_input(seat.id)
            return None

    async def _payload_to_text(self, payload: Optional[dict]) -> str:
        if not payload:
            return ""
        audio_b64 = payload.get("audio_b64")
        fallback = (payload.get("text") or "").strip()
        audio_bytes = None
        if audio_b64:
            try:
                audio_bytes = base64.b64decode(audio_b64)
            except Exception:  # noqa: BLE001
                audio_bytes = None
        return await stt.transcribe(audio_bytes, fallback_text=fallback)

    async def _broadcast_state(self) -> None:
        seats = [s.public() for s in self.room.seats.values()]
        await self.room.broadcast(
            events.srv_room_state(
                seats=seats, phase=self.room.phase.value, round_no=self.room.round_no, you=None
            )
        )

    async def _system(self, text: str) -> None:
        await self.room.broadcast(events.srv_system(text=text))
