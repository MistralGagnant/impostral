"""Game flow engine: QUESTION -> DELIBERATION -> VOTE -> RESOLUTION.

Key properties:
- Every utterance passes through the seat's anonymized TTS voice.
- Answers are collected for the full window and revealed in random order at a
  fixed cadence, hiding response-time tells.
- Agents compete independently to pass as human.
- Humans and agents vote; selecting a human wastes the round without eliminating them.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
from typing import Optional

from ..audio import stt, tts
from ..config import get_settings
from . import events, questions
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
    # Phase DÉLIBÉRATION
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

        # Plafond d'échanges : borne la phase même quand les réponses arrivent
        # instantanément (agents rapides), pour ne pas noyer le transcript.
        max_exchanges = max(2, len(self.room.alive_seats()) * 2)
        done = 0

        # On enchaîne des échanges (question dirigée → réponse) tant qu'il reste
        # du temps, au moins deux sièges vivants, et sous le plafond.
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
        """Réalise un échange (question dirigée → réponse). Renvoie True si un
        échange a bien eu lieu (l'asker n'a pas passé)."""
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
                return False  # le joueur a passé
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
    # Phase VOTE
    # ------------------------------------------------------------------
    async def _vote_phase(self) -> None:
        self.room.phase = Phase.VOTE
        dur = self.settings.vote_seconds
        await self.room.broadcast(events.srv_phase_change(phase=Phase.VOTE.value, deadline=dur))
        await self._broadcast_state()

        voters = self.room.alive_seats()
        tasks = [asyncio.ensure_future(self._collect_vote(s, dur)) for s in voters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        tally: dict[str, int] = {}
        for res in results:
            if isinstance(res, tuple) and res[1]:
                tally[res[1]] = tally.get(res[1], 0) + 1

        eliminated = self._resolve_tally(tally)
        await self.room.broadcast(events.srv_vote_result(tally=tally, eliminated=eliminated))
        self._pending_eliminated = eliminated

    async def _collect_vote(self, seat, dur: int) -> tuple[str, Optional[str]]:
        others = self.room.alive_ids(exclude=seat.id)
        if seat.kind == "llm":
            target = await seat.agent.vote(self.room.render_transcript(), others)
            return seat.id, target if target in others else None
        payload = await self._request_human(seat, mode="vote", dur=dur, targets=others)
        target = payload.get("target") if payload else None
        return seat.id, target if target in others else None

    @staticmethod
    def _resolve_tally(tally: dict[str, int]) -> Optional[str]:
        if not tally:
            return None
        top = max(tally.values())
        leaders = [sid for sid, n in tally.items() if n == top]
        return random.choice(leaders)  # égalité tranchée au hasard

    # ------------------------------------------------------------------
    # Phase RÉSOLUTION
    # ------------------------------------------------------------------
    async def _resolution_phase(self) -> None:
        self.room.phase = Phase.RESOLUTION
        eliminated = getattr(self, "_pending_eliminated", None)
        if eliminated and eliminated in self.room.seats:
            seat = self.room.seats[eliminated]
            if seat.kind == "llm":
                seat.alive = False
                self.eliminated_llms.append(seat.id)
                role = seat.kind if self.settings.reveal_role_on_elimination else None
                await self.room.broadcast(events.srv_elimination(seat=eliminated, role=role))
                if role:
                    await self._system(f"{eliminated} is out… they were an AI.")
                else:
                    await self._system(f"{eliminated} is out.")
            else:
                await self._system(
                    f"The vote missed: {eliminated} is human and stays in the game."
                )
        else:
            await self._system("No one is eliminated this round.")
        await self._broadcast_state()
        await asyncio.sleep(1.5)

    # ------------------------------------------------------------------
    # Fin de partie
    # ------------------------------------------------------------------
    def _check_end(self) -> bool:
        return not self.room.llms_alive()

    async def _game_over(self) -> None:
        self.room.phase = Phase.GAME_OVER
        survivors = [s.id for s in self.room.llms_alive()]
        if survivors:
            winners = survivors
            result = (
                f"{', '.join(winners)} remained undetected and tie for the win."
                if len(winners) > 1
                else f"{winners[0]} remained undetected and wins the game."
            )
        else:
            winners = self.eliminated_llms[-1:]  # dernière IA éliminée
            result = (
                f"{winners[0]} was the last AI eliminated and wins the game."
                if winners
                else "No winning AI could be determined."
            )
        roles = {s.id: s.kind for s in self.room.seats.values()}
        await self.room.broadcast(
            events.srv_game_over(winner="agents", winners=winners, roles=roles)
        )
        await self._system("Game over. " + result)

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    async def _speak(self, seat, text: str, context: str = "") -> None:
        """Anonymise et diffuse une prise de parole (texte + audio voix du siège)."""
        self.room.add_utterance(seat.id, text, context)
        audio_url = await tts.synthesize(text, voice=seat.voice)
        await self.room.broadcast(
            events.srv_utterance(seat=seat.id, text=text, audio_url=audio_url, context=context)
        )
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
