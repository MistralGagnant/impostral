"""Game flow engine: QUESTION -> VOTE -> RESOLUTION.

Key properties:
- Every utterance passes through the seat's anonymized TTS voice.
- Answers are collected for the full window and revealed in random order at a
  fixed cadence, hiding response-time tells.
- Agents compete independently to pass as human.
- Every active human and agent casts a vote.
- A tied first ballot triggers a runoff restricted to the tied candidates.
- Exactly one seat is eliminated after each vote phase.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import secrets
import time
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
        self.eliminated_llms: list[str] = []

    # ------------------------------------------------------------------
    # Boucle principale
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
            self.room.status = "finished"
            self.room.finished_at = time.time()
            self.room.updated_at = self.room.finished_at
            await self._system("An internal error interrupted the game.")

    # ------------------------------------------------------------------
    # Phase QUESTION
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
        # Collecte concurrente ; chaque tâche renvoie (seat_id, texte).
        tasks = [asyncio.ensure_future(self._collect_answer(s, question, dur)) for s in alive]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        answers: dict[str, str] = {}
        for res in results:
            if isinstance(res, tuple):
                sid, text = res
                answers[sid] = text or "(silence)"

        # Révélation groupée, ordre aléatoire, cadence fixe (anti-tell).
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
    # Phase VOTE
    # ------------------------------------------------------------------
    async def _vote_phase(self) -> None:
        self.room.phase = Phase.VOTE
        dur = self.settings.vote_seconds
        await self.room.broadcast(events.srv_phase_change(phase=Phase.VOTE.value, deadline=dur))
        await self._broadcast_state()

        voters = self.room.alive_seats()
        tally = await self._collect_ballot(voters, dur)
        leaders = self._leaders(tally)

        if len(leaders) > 1:
            await self.room.broadcast(
                events.srv_vote_result(tally=tally, eliminated=None, runoff=leaders)
            )
            await self._system(
                f"Tie between {', '.join(leaders)}. Runoff vote: tied seats only."
            )
            await self.room.broadcast(
                events.srv_phase_change(
                    phase=Phase.VOTE.value,
                    deadline=dur,
                    prompt=f"Runoff: vote between {', '.join(leaders)}.",
                )
            )
            tally = await self._collect_ballot(voters, dur, candidates=leaders)
            leaders = self._leaders(tally)

        # A persistent runoff tie is broken only after everyone has voted again.
        eliminated = (
            leaders[0]
            if len(leaders) == 1
            else random.choice(leaders) if leaders else None
        )
        await self.room.broadcast(events.srv_vote_result(tally=tally, eliminated=eliminated))
        self._pending_eliminated = eliminated

    async def _collect_ballot(
        self, voters: list, dur: int, candidates: Optional[list[str]] = None
    ) -> dict[str, int]:
        tasks = [
            asyncio.ensure_future(self._collect_vote(seat, dur, candidates=candidates))
            for seat in voters
        ]
        results = await asyncio.gather(*tasks)

        tally: dict[str, int] = {}
        for voter_id, target_id in results:
            if target_id is None:
                continue
            tally[target_id] = tally.get(target_id, 0) + 1
            voter = self.room.seats.get(voter_id)
            target = self.room.seats.get(target_id)
            if voter:
                voter.votes_total += 1
                if target and target.kind == "llm":
                    voter.votes_correct += 1
        return tally

    async def _collect_vote(
        self, seat, dur: int, candidates: Optional[list[str]] = None
    ) -> tuple[str, Optional[str]]:
        alive_others = self.room.alive_ids(exclude=seat.id)
        eligible = (
            [target for target in candidates if target in alive_others]
            if candidates is not None
            else alive_others
        )
        if not eligible:
            return seat.id, None

        target = None
        try:
            if seat.kind == "llm":
                target = await seat.agent.vote(self.room.render_transcript(), eligible)
            else:
                payload = await self._request_human(
                    seat, mode="vote", dur=dur, targets=eligible
                )
                target = payload.get("target") if payload else None
        except Exception:  # noqa: BLE001
            log.exception("Vote collection failed for %s", seat.id)

        if target not in eligible:
            target = random.choice(eligible)
            log.info("Assigned fallback vote for %s to %s", seat.id, target)
        return seat.id, target

    @staticmethod
    def _leaders(tally: dict[str, int]) -> list[str]:
        if not tally:
            return []
        top = max(tally.values())
        return [sid for sid, votes in tally.items() if votes == top]

    # ------------------------------------------------------------------
    # Phase RÉSOLUTION
    # ------------------------------------------------------------------
    async def _resolution_phase(self) -> None:
        self.room.phase = Phase.RESOLUTION
        eliminated = getattr(self, "_pending_eliminated", None)
        if eliminated and eliminated in self.room.seats:
            seat = self.room.seats[eliminated]
            seat.alive = False
            seat.eliminated_round = self.room.round_no
            if seat.kind == "llm":
                self.eliminated_llms.append(seat.id)
            role = seat.kind if self.settings.reveal_role_on_elimination else None
            model = seat.model if role == "llm" else None
            await self.room.broadcast(
                events.srv_elimination(seat=eliminated, role=role, model=model)
            )
            if role == "llm":
                await self._system(
                    f"{eliminated} is out… they were an AI ({model})."
                    if model else f"{eliminated} is out… they were an AI."
                )
            elif role == "human":
                await self._system(f"{eliminated} is out… they were human.")
            else:
                await self._system(f"{eliminated} is out.")
        else:
            await self._system("No one is eliminated this round.")
        await self._broadcast_state()
        await asyncio.sleep(1.5)

    # ------------------------------------------------------------------
    # Fin de partie
    # ------------------------------------------------------------------
    def _check_end(self) -> bool:
        return (
            not self.room.llms_alive()
            or not self.room.humans_alive()
            or len(self.room.alive_seats()) <= 1
            or (
                len(self.room.alive_seats()) == 2
                and len(self.room.humans_alive()) == 1
                and len(self.room.llms_alive()) == 1
            )
        )

    async def _game_over(self) -> None:
        self.room.phase = Phase.GAME_OVER
        self.room.status = "finished"
        self.room.finished_at = time.time()
        self.room.updated_at = self.room.finished_at
        surviving_humans = [s.id for s in self.room.humans_alive()]
        surviving_llms = [s.id for s in self.room.llms_alive()]
        if len(surviving_humans) == 1 and len(surviving_llms) == 1:
            winners = surviving_humans + surviving_llms
            winner_type = "shared"
            result = (
                f"{surviving_humans[0]} and {surviving_llms[0]} win together — "
                "one human and one AI are impossible to tell apart."
            )
        elif surviving_llms:
            winners = surviving_llms
            winner_type = "agents"
            if not self.room.humans_alive():
                result = "The AIs have won — no humans remain."
            else:
                result = (
                    f"{', '.join(winners)} remained undetected and tie for the win."
                    if len(winners) > 1
                    else f"{winners[0]} remained undetected and wins the game."
                )
        elif surviving_humans:
            winners = surviving_humans
            winner_type = "humans"
            result = "The humans have won — every AI was eliminated."
        else:
            winners = []
            winner_type = "none"
            result = "No winner could be determined."
        roles = {s.id: s.kind for s in self.room.seats.values()}
        models = {s.id: s.model for s in self.room.seats.values() if s.model}
        stats.record_game(self.room, winners)
        await self.room.broadcast(
            events.srv_game_over(
                winner=winner_type,
                winners=winners, roles=roles,
                models=models, message=result,
            )
        )
        await self._system("Game over. " + result)

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    async def _speak(self, seat, text: str, context: str = "") -> None:
        """Anonymise et diffuse une prise de parole (texte + audio voix du siège)."""
        self.room.add_utterance(seat.id, text, context)
        audio_url = await tts.synthesize(text, voice=seat.voice)
        playback_id = secrets.token_urlsafe(12) if audio_url else ""
        playback_done = self.room.expect_playback(playback_id) if playback_id else None
        await self.room.broadcast(
            events.srv_utterance(
                seat=seat.id, text=text, audio_url=audio_url, context=context,
                playback_id=playback_id,
            )
        )
        if playback_done is not None:
            try:
                await asyncio.wait_for(playback_done, timeout=60)
            except asyncio.TimeoutError:
                self.room.cancel_playback(playback_id)
            except asyncio.CancelledError:
                self.room.cancel_playback(playback_id)
                raise
        await asyncio.sleep(self.settings.reveal_gap_seconds)

    async def _request_human(self, seat, *, mode: str, dur: int,
                             targets: Optional[list[str]] = None) -> Optional[dict]:
        """Demande une saisie au siège humain et attend sa réponse (ou timeout)."""
        if not seat.connected:
            return None
        # Future créée AVANT l'envoi pour éviter de perdre une réponse très rapide.
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
        audio_mime = payload.get("audio_mime") or "audio/webm"
        fallback = (payload.get("text") or "").strip()
        audio_bytes = None
        if audio_b64:
            try:
                audio_bytes = base64.b64decode(audio_b64)
            except Exception:  # noqa: BLE001
                audio_bytes = None
        return await stt.transcribe(
            audio_bytes, mime_type=audio_mime, fallback_text=fallback
        )

    async def _broadcast_state(self) -> None:
        seats = [s.public() for s in self.room.seats.values()]
        await self.room.broadcast(
            events.srv_room_state(
                seats=seats, phase=self.room.phase.value, round_no=self.room.round_no,
                you=None,
                lobby_wait_remaining=(
                    0
                    if getattr(self.room, "started", False)
                    and self.room.phase == Phase.LOBBY
                    else None
                ),
            )
        )

    async def _system(self, text: str) -> None:
        await self.room.broadcast(events.srv_system(text=text))
