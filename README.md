# Impostral

A web-based social bluffing game **humans vs LLM**, inspired by a Jubilee video.

Humans and LLM agents (Mistral) share a room. In each round, everyone answers the same
**question**, then immediately takes part in a shared **vote** to identify an AI.
Each AI competes independently and tries to pass as human. Every active seat votes, and a tied
ballot triggers a runoff between the tied seats. One player is eliminated per round. Humans win
by eliminating every AI; surviving AIs tie if the round limit is reached.

**Core mechanic — voice anonymization**: any speech (human or LLM) is transcribed and resynthesized
into **Voxtral synthetic voice fixed by seat**. It is impossible to distinguish a human from an AI by
ear. The timing tell is neutralized (responses revealed grouped, in random order).

Stack: **Voxtral** (STT + TTS) + **Mistral chat** (agent reasoning), backend **FastAPI +
WebSockets**, front **vanilla JS**.

## How It Works

![Impostral architecture — humans vs Mistral agents](assets/diagram.png)

## Getting Started

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# (optional) API key for real audio + real agents:
cp .env.example .env   # then fill in MISTRAL_API_KEY

./venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000 and click **Play**. Quick play joins the oldest public lobby with
a free human seat, or creates one with the default composition (3 humans + 3 AIs). Public
games start automatically when all human seats are connected, or after 15 seconds with the
humans currently present. Named private lobbies remain available under **Private lobby
options**. They show the number of connected humans live and start only when their creator
uses the host-only **Start game** button; no automatic timer applies to private lobbies.

Quick play uses anonymous browser and tab identifiers stored locally. There is no sign-up,
account, email address, or public player profile. The current in-memory lobby manager is
intended for a single Cloud Run instance; configure `max-instances=1` until room state is
moved to shared infrastructure.

Production game admission is protected by Cloudflare Turnstile. Set the
unprefixed `TURNSTILE_SECRET_KEY` environment variable to enable enforcement;
the public site key is configured in `app/config.py`. Turnstile runs only when a
browser enters a game, and the backend exchanges a successful verification for
a short-lived room reservation ticket. Local development remains unchallenged
when the secret is absent; Cloud Run fails closed if the secret is missing.

**Without API key**: *mock* mode — scripted agents, no audio (text only), no microphone required. Ideal
for testing the game loop.

Agent models: `mistral-large-latest`, `mistral-medium-latest`,
`mistral-small-latest`, and `ministral-8b-latest`. Audio uses
`voxtral-mini-latest` (STT) and `voxtral-mini-tts-latest` (TTS). See `AGENT.md`
for architecture, configuration, and specifics of the `mistralai` 2.x SDK.

## Assets

The `assets` folder contains the game's graphical resources:
- **Characters**: Illustrations of the in-game characters.
  ![Characters](assets/characters.png)
- **Impostral Logo**: The main logo of the game.
  ![Impostral Logo](assets/impostral.png)
- **Game Icon**: The icon representing the game.
  ![Game Icon](assets/logo.png)
