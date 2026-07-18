# AGENT.md — Impostral

Social deduction game where **humans** and **Mistral LLM agents** share a room.
LLMs try to identify and eliminate humans; humans try to pass as AI, inspired by
a Jubilee video. Each round follows: **question -> deliberation -> vote -> resolution**.

Status: **functional POC**, validated end to end with chat, Voxtral STT, and TTS.

## Language rule

**English everywhere**: code, comments, documentation, prompts, and interface.

## Environment

The dedicated virtual environment lives at the repository root in `venv/`.
Always invoke it through explicit paths: `./venv/bin/python`, `./venv/bin/pip`,
and `./venv/bin/uvicorn`.

The API key belongs in the gitignored `.env` file as `MISTRAL_API_KEY=...`.
Without a key, the game uses scripted agents in text-only **mock mode**. This is
useful for testing the game loop without audio or a microphone. `GET /config`
exposes `mock_mode`.

## Run

```bash
./venv/bin/uvicorn app.main:app --reload
# Open http://localhost:8000 in one tab per human player.
```

Each tab occupies a free human seat. The game starts when every human clicks
“I'm ready”. Configure composition and timings through `IMPOSTRAL_`-prefixed
environment variables such as `IMPOSTRAL_NUM_HUMANS`, `IMPOSTRAL_NUM_LLMS`,
`IMPOSTRAL_MAX_ROUNDS`, and `IMPOSTRAL_QUESTION_SECONDS`; see `app/config.py`.
The first browser interaction unlocks audio playback under autoplay policies.

## Default Mistral models (`app/config.py`)

| Role | Model | Environment override |
|------|-------|----------------------|
| Agent reasoning | `mistral-large-latest` (default) | `IMPOSTRAL_CHAT_MODEL` |
| Agent model pool | *(empty → single chat model)* | `IMPOSTRAL_MODEL_POOL` |
| STT | `voxtral-mini-latest` | `IMPOSTRAL_STT_MODEL` |
| TTS | `voxtral-mini-tts-latest` | `IMPOSTRAL_TTS_MODEL` |

Agents differ by persona and temperature (`PERSONAS` in
`app/agents/llm_agent.py`). When `IMPOSTRAL_MODEL_POOL` is set (comma-separated),
each LLM seat is assigned a **different model** from the pool, shuffled each game,
so several models compete in the same game. Left empty, all seats use
`IMPOSTRAL_CHAT_MODEL`.

## Model performance tracking

Each finished game appends one JSON record to `IMPOSTRAL_STATS_PATH`
(default `data/results.jsonl`, gitignored) via `app/game/stats.py`, capturing per
LLM seat: model, survival, elimination round, and vote/detection accuracy. The
`GET /stats` endpoint returns per-model aggregates and `/stats.html` renders a
comparison table + bar chart (win rate, survival rate, vote accuracy). Recording
is best-effort and never interrupts a game.

## `mistralai` SDK version caveat

The project targets **`mistralai` 2.x**, whose structure differs from 1.x:

- Client import: `from mistralai.client import Mistral`; `app/mistral_client.py`
  supports both the 1.x and 2.x entry points.
- TTS: `client.audio.speech.complete(model=..., voice_id=..., input=...,
  response_format="mp3")` returns base64 in `SpeechResponse.audio_data`.
- STT: `client.audio.transcriptions.complete(model=...,
  file={"file_name","content","content_type"})` returns
  `TranscriptionResponse.text`.
- Voices: `client.audio.voices.list(type_="preset")` returns voices with UUID
  identifiers. `app/audio/voices.py` builds an English-first pool.

The STT and TTS wrappers degrade gracefully to text-only play when calls fail.

## Core mechanic: voice anonymization

Every human and LLM utterance uses the synthetic Voxtral voice assigned to that
seat through `_speak` and `audio/tts.py`. Listeners cannot identify a human by
voice. Response-time tells are also hidden: QUESTION responses are collected for
the full window and revealed together in random order at a fixed cadence. Agents
never receive role information; they only see the transcript.

## Files

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app, WebSocket, audio endpoint, and static web client. |
| `app/config.py` | Models, timings, composition, and voice language settings. |
| `app/mistral_client.py` | Shared Mistral client with robust 1.x/2.x imports. |
| `app/rooms.py` | Rooms, seats, connections, and human input routing. |
| `app/game/state_machine.py` | Phase engine, timing protection, exchange cap, and win conditions. |
| `app/game/events.py` | WebSocket message schemas; active roles are never exposed. |
| `app/game/questions.py` | Open-ended question bank. |
| `app/game/stats.py` | Per-game result recording and per-model aggregation. |
| `app/agents/llm_agent.py` | LLM player answers, questions, votes, personas, and mock fallback. |
| `app/audio/stt.py` / `tts.py` | Voxtral wrappers with graceful fallback. |
| `app/audio/voices.py` | Cached preset voice pool with distinct speakers. |
| `app/audio/store.py` | Ephemeral FIFO audio store served from `/audio/{id}`. |
| `web/` | Vanilla JS client, push-to-talk input, audio playback, and phase UI. |

## WebSocket protocol

- **Client -> server**: `join{name}`, `ready`, `audio_blob{audio_b64|text}`,
  `direct_question{target, audio_b64|text}` (empty target means skip), and
  `submit_vote{target}`.
- **Server -> client**: `room_state`, `phase_change{phase, deadline, prompt}`,
  `utterance{seat, text, audio_url, context}`, `request_input{mode, deadline,
  targets}`, `vote_result{tally, eliminated}`, `elimination{seat, role}`,
  `game_over{winner, roles}`, and `system`.

`deadline` is the number of remaining seconds; the client renders the countdown.

## Win conditions

- LLMs win when every human is eliminated.
- Humans win by surviving `max_rounds`, or when every LLM is eliminated.

## Possible improvements

- ~~Use different models per seat for balance testing.~~ Done via
  `IMPOSTRAL_MODEL_POOL` + `/stats.html`.
- Replace batch STT with `voxtral-mini-realtime-latest`.
- Add voice cloning through `ref_audio`, or more distinct speakers.
- Add player reconnection, multiple rooms, and a dedicated spectator screen.
