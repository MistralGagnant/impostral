# AGENT.md — Impostral

Social bluffing game where **humans** and **Mistral LLM agents** share a room.
Every AI competes independently to pass as human, while all active players vote
during elimination rounds. Humans win by eliminating every AI. Each round follows:
**question -> vote -> resolution**.

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

The default **Play** action uses anonymous quick matchmaking; the private
codename field may be left blank. `POST /matchmaking`
atomically reserves a human seat in the oldest waiting public lobby, or creates a
public lobby with the default composition. The browser opens the room WebSocket
with that short-lived reservation ticket; public players are automatically ready
and the game starts when all human seats are connected or after a configurable
15-second wait with the humans currently present. A stable anonymous
browser ID and a tab-specific session ID are stored locally for reconnection.
There is no sign-up or public user profile.

Every new game admission is protected by Cloudflare Turnstile when
`TURNSTILE_SECRET_KEY` is configured. The browser obtains a single-use token for
the `enter_game` action, the backend validates its result, hostname, and action,
then issues a short-lived seat reservation. The public site key is configured as
`turnstile_site_key` in `app/config.py`. Without the secret, enforcement is
disabled for local development, while Cloud Run fails closed. WebSockets
additionally require a same-origin handshake and a valid reservation ticket;
anonymous spectators are not accepted.

Named private lobbies remain available under **Private lobby options**. One
player creates a lobby and chooses its human count; other players join using the
same name. The lobby shows the number of connected humans live and only its
creator can start the game; private lobbies never use the public 15-second
timer. Joining never creates a private room, so a wrong name is rejected.
`IMPOSTRAL_NUM_HUMANS` is
the public/default human count (bounded by `IMPOSTRAL_MIN_HUMANS` and
`IMPOSTRAL_MAX_HUMANS`); the AI count comes from `IMPOSTRAL_NUM_LLMS`. Configure
timings through `IMPOSTRAL_`-prefixed variables such as `IMPOSTRAL_MAX_ROUNDS`
and `IMPOSTRAL_QUESTION_SECONDS`; TTS defaults to uniform 1.1x playback with a
0.15-second post-clip gap. See `app/config.py`. The first browser
interaction unlocks audio playback under autoplay policies.

## Default Mistral models (`app/config.py`)

| Role | Model | Environment override |
|------|-------|----------------------|
| Large agent | `mistral-large-latest` | `IMPOSTRAL_CHAT_MODEL_LARGE` |
| Medium agent | `mistral-medium-latest` | `IMPOSTRAL_CHAT_MODEL_MEDIUM` |
| Small agent | `mistral-small-latest` | `IMPOSTRAL_CHAT_MODEL_SMALL` |
| Ministral agent | `ministral-8b-latest` | `IMPOSTRAL_CHAT_MODEL_MINISTRAL` |
| STT | `voxtral-mini-latest` (English) | `IMPOSTRAL_STT_MODEL`, `IMPOSTRAL_STT_LANGUAGE` |
| TTS | `voxtral-mini-tts-latest` | `IMPOSTRAL_TTS_MODEL` |

The default room has three humans and three agents, using Large, Medium, and
Small respectively. Agents also use different personas, temperatures, and
persona-specific human few-shot examples from `PERSONAS` in
`app/agents/llm_agent.py`. Guided decoding enforces a strict JSON Schema with
private `thinking` and one public `output` utterance of at most 100 characters,
preferably 3 to 10 words. Outputs may be ultra-short, deflective, or strongly
accusatory. Only `output` enters the transcript.

## Model performance tracking

Each finished game appends a JSON record to `IMPOSTRAL_STATS_PATH` (default
`data/results.jsonl`). `app/game/stats.py` records each model's win, survival,
elimination round, and competitive vote accuracy. Humans are recorded too, but
grouped anonymously into a single `Humans` bucket (never per pseudonym), so the
dashboard compares humans against each AI model. `/stats` exposes aggregates and
`/stats.html` renders the player comparison dashboard. Records created before
human tracking remain readable and are reported as unavailable human history.

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
| `app/main.py` | FastAPI app, quick matchmaking, private lobby creation, WebSocket, audio endpoint, and static web client. |
| `app/config.py` | Models, timings, composition, and voice language settings. |
| `app/mistral_client.py` | Shared Mistral client with robust 1.x/2.x imports. |
| `app/turnstile.py` | Server-side Cloudflare Turnstile token verification. |
| `app/rooms.py` | Rooms with per-lobby composition, seats, connections, and human input routing. |
| `app/game/state_machine.py` | Phase engine, timing protection, and win conditions. |
| `app/game/events.py` | WebSocket message schemas; active roles are never exposed. |
| `app/game/questions.py` | Open-ended question bank. |
| `app/game/stats.py` | Per-game records and per-model performance aggregation. |
| `app/agents/llm_agent.py` | Structured LLM answers, votes, personas, few-shots, and mock fallback. |
| `app/audio/stt.py` / `tts.py` | Voxtral wrappers with graceful fallback. |
| `app/audio/voices.py` | Cached preset voice pool with distinct speakers. |
| `app/audio/store.py` | Ephemeral FIFO audio store served from `/audio/{id}`. |
| `web/` | Radial arena, model statistics dashboard, audio, and phase UI. |

## WebSocket protocol

Quick play calls
`POST /matchmaking {player_id, session_id, name, turnstile_token}`. It returns
`room_id` and `reservation_token`; concurrent calls are serialized so they cannot
claim the same seat. Reservations expire after 20 seconds by default. Private
lobby creation calls
`POST /lobby {name, num_humans, player_id, session_id, turnstile_token}` and
returns the creator's reservation. Joining calls
`POST /lobby/{room_id}/join {player_id, session_id, turnstile_token}` and returns
another reservation. Creation returns 409 if the name is taken and 400 if
`num_humans` is out of range. `GET /config` exposes composition bounds plus the
public Turnstile site key when enforcement is active.

- **Client -> server**: `join{name, player_id, session_id, reservation_token}`,
  `start_game` (private host only), `audio_blob{audio_b64,audio_mime,text}`,
  `submit_vote{target}`, and
  `playback_complete{playback_id}`.
- **Server -> client**: `room_state`, `phase_change{phase, deadline, prompt}`,
  `utterance{seat, text, audio_url, context, playback_id}`,
  `request_input{mode, deadline, targets}`, `vote_result{tally, eliminated, runoff}`,
  `elimination{seat, role}`,
  `game_over{winner, winners, roles}`, and `system`.

`deadline` is the number of remaining seconds; the client renders the countdown.

Rooms, reservations, audio clips, and open sockets are currently process-local.
Production deployment therefore requires one Uvicorn worker and one Cloud Run
instance (`max-instances=1`). A container restart intentionally ends active MVP
games; the client retries its WebSocket and returns to Play when the room is gone.

## Win conditions

- Every active human and AI casts a vote. Missing or invalid votes receive a fallback choice.
- A first-ballot tie triggers a second vote restricted to the tied seats; a persistent tie is then broken randomly.
- The selected seat is eliminated regardless of role, so every completed round eliminates one player.
- A final human and AI win together because neither can be distinguished from the other.
- Once every AI is eliminated, the surviving humans win.
- At `max_rounds`, all undetected AIs tie.

## Possible improvements

- Replace batch STT with `voxtral-mini-realtime-latest`.
- Add voice cloning through `ref_audio`, or more distinct speakers.
- Add player reconnection, multiple rooms, and a dedicated spectator screen.
