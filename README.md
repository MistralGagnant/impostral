# Impostral

A web-based social deduction game **humans vs LLM**, inspired by a Jubilee video.

Humans and LLM agents (Mistral) share a room. In each round, a **question** is asked to everyone,
followed by a **deliberation** where players discuss, and then a **vote** to eliminate someone.
The goal of the AIs: uncover and eliminate humans. The goal of the humans: pass as AIs.

**Core mechanic — voice anonymization**: any speech (human or LLM) is transcribed and resynthesized
into **Voxtral synthetic voice fixed by seat**. It is impossible to distinguish a human from an AI by
ear. The timing tell is neutralized (responses revealed grouped, in random order).

Stack: **Voxtral** (STT + TTS) + **Mistral chat** (agent reasoning), backend **FastAPI +
WebSockets**, front **vanilla JS**.

## Getting Started

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# (optional) API key for real audio + real agents:
cp .env.example .env   # then fill in MISTRAL_API_KEY

./venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000 in one tab per human player (default: 2 humans + 3 AIs). Click
"ready" in each tab; the game starts when all humans are ready.

**Without API key**: *mock* mode — scripted agents, no audio (text only), no microphone required. Ideal
for testing the game loop.

Models used: `mistral-large-latest` (agents), `voxtral-mini-latest` (STT),
`voxtral-mini-tts-latest` (TTS). See `AGENT.md` for architecture, configuration, and
specifics of the `mistralai` 2.x SDK.

## Assets

The `assets` folder contains the game's graphical resources:
- **Characters**: Illustrations of the in-game characters.
  ![Characters](assets/characters.png)
- **Impostral Logo**: The main logo of the game.
  ![Impostral Logo](assets/impostral.png)
- **Game Icon**: The icon representing the game.
  ![Game Icon](assets/logo.png)
