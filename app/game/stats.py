"""Per-game competitive AI result recording and per-model aggregation.

Each finished game appends one JSON line to `settings.stats_path`. Aggregation
groups those records by the model assigned to each LLM seat, so the `/stats` page
can compare model performance. Recording is best-effort: any failure is logged
and swallowed so it can never interrupt a game.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("impostral.stats")


def _path() -> Path:
    return Path(get_settings().stats_path)


def record_game(room, winners: list[str]) -> None:
    """Append one record with the winning AI seat or surviving tied seats."""
    try:
        rounds = room.round_no
        llms = [
            {
                "model": seat.model,
                "seat": seat.id,
                "won": seat.id in winners,
                "survived": seat.alive,
                "eliminated_round": seat.eliminated_round,
                "votes_total": seat.votes_total,
                "votes_correct": seat.votes_correct,
            }
            for seat in room.seats.values()
            if seat.kind == "llm"
        ]
        # Humans are recorded as one anonymous group; individual pseudonyms are
        # never stored, so aggregation compares "Humans" against each AI model.
        humans = [
            {
                "seat": seat.id,
                "won": seat.id in winners,
                "survived": seat.alive,
                "eliminated_round": seat.eliminated_round,
                "votes_total": seat.votes_total,
                "votes_correct": seat.votes_correct,
            }
            for seat in room.seats.values()
            if seat.kind == "human"
        ]
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "room": room.id,
            "winners": winners,
            "rounds": rounds,
            "llms": llms,
            "humans": humans,
        }
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not record game stats: %s", exc)


def _read_records() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    records = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # Skip corrupt lines.
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read game stats: %s", exc)
    return records


def aggregate() -> dict:
    """Return per-model aggregates plus the total number of recorded games."""
    records = _read_records()

    # Accumulators keyed by model name.
    acc: dict[str, dict] = {}

    def bucket(model: str) -> dict:
        return acc.setdefault(
            model,
            {
                "games": 0,
                "wins": 0,
                "survivals": 0,
                "votes_total": 0,
                "votes_correct": 0,
                "rounds_survived_sum": 0,
            },
        )

    def accumulate(seat: dict, model: str, rounds: int) -> None:
        b = bucket(model)
        b["games"] += 1
        if seat.get("won"):
            b["wins"] += 1
        if seat.get("survived"):
            b["survivals"] += 1
        b["votes_total"] += seat.get("votes_total", 0) or 0
        b["votes_correct"] += seat.get("votes_correct", 0) or 0
        elim = seat.get("eliminated_round")
        b["rounds_survived_sum"] += elim if elim is not None else rounds

    for rec in records:
        rounds = rec.get("rounds", 0) or 0
        for seat in rec.get("llms", []):
            accumulate(seat, seat.get("model") or "(unknown)", rounds)
        # All humans across every game collapse into one "Humans" bucket.
        for seat in rec.get("humans", []):
            accumulate(seat, "Humans", rounds)

    models = []
    for model, b in sorted(acc.items()):
        games = b["games"] or 1  # Guard against division by zero.
        votes = b["votes_total"] or 1
        models.append(
            {
                "model": model,
                "games": b["games"],
                "team_win_rate": b["wins"] / games,
                "survival_rate": b["survivals"] / games,
                "vote_accuracy": b["votes_correct"] / votes,
                "votes_total": b["votes_total"],
                "avg_rounds_survived": b["rounds_survived_sum"] / games,
            }
        )

    return {"total_games": len(records), "models": models}
