# AGENT.md — Impostral

Jeu de bluff social : des **humains** et des **agents LLM Mistral** cohabitent dans une salle.
Chaque IA cherche à passer pour un humain et joue pour elle-même ; les humains votent pour les
démasquer. La dernière IA éliminée gagne. Déroulé : **question → délibération → vote → résolution**.

État : **POC fonctionnel**, validé de bout en bout en mode réel (chat + STT + TTS Voxtral).

## Règle de langue

**Français partout** : code, commentaires, documentation, interface.

## Environnement

Venv dédié à la racine (`venv/`). Toujours l'invoquer par chemin explicite :
`./venv/bin/python`, `./venv/bin/pip`, `./venv/bin/uvicorn`.

La clé API va dans `.env` (gitignoré) : `MISTRAL_API_KEY=…`. Sans clé, le jeu tourne en **mode
mock** : agents scriptés, pas d'audio (texte seul), micro non requis — utile pour tester la boucle
de jeu sans clé. `mock_mode` est visible sur `GET /config`.

## Lancer

```bash
./venv/bin/uvicorn app.main:app --reload
# puis ouvrir http://localhost:8000 dans un onglet par joueur humain
```

Chaque onglet occupe un siège humain libre ; quand tous les humains ont cliqué « prêt », la partie
démarre. Composition et durées via l'env (préfixe `IMPOSTRAL_`) : `IMPOSTRAL_NUM_HUMANS`,
`IMPOSTRAL_NUM_LLMS`, `IMPOSTRAL_MAX_ROUNDS`, `IMPOSTRAL_QUESTION_SECONDS`, etc. (cf. `app/config.py`).
Le premier clic dans l'onglet débloque l'audio du navigateur (politique d'autoplay).

## Modèles Mistral utilisés (défauts `app/config.py`)

| Rôle | Modèle | Surcharge env |
|------|--------|---------------|
| Raisonnement des agents (chat) | `mistral-large-latest` | `IMPOSTRAL_CHAT_MODEL` |
| STT (transcription) | `voxtral-mini-latest` | `IMPOSTRAL_STT_MODEL` |
| TTS (synthèse voix) | `voxtral-mini-tts-latest` | `IMPOSTRAL_TTS_MODEL` |

Tous les agents partagent le même modèle chat ; ils se distinguent par leur **persona** et leur
**température** (`PERSONAS` dans `app/agents/llm_agent.py`). Le guided decoding impose un JSON
Schema avec un raisonnement privé `thinking` et une phrase publique `output` de 180 caractères
maximum. Seul `output` rejoint le transcript.

## SDK `mistralai` (piège de version)

Le projet cible **`mistralai` 2.x**, dont la structure diffère de la 1.x :

- Import du client : `from mistralai.client import Mistral` (la 1.x exposait `from mistralai import
  Mistral`). `app/mistral_client.py` tente les deux, 1.x puis 2.x.
- **TTS** : `client.audio.speech.complete(model=…, voice_id=…, input=…, response_format="mp3")`
  → `SpeechResponse.audio_data` (chaîne **base64** à décoder).
- **STT** : `client.audio.transcriptions.complete(model=…, file={"file_name","content","content_type"})`
  → `TranscriptionResponse.text`.
- **Voix** : `client.audio.voices.list(type_="preset")` renvoie des voix avec un `id` (UUID). Les
  voix françaises preset sont les variantes « Marie - … ». `app/audio/voices.py` construit le pool.

Les wrappers `stt.py`/`tts.py` **dégradent proprement** (texte seul / pas d'audio) si un appel
échoue, pour ne jamais bloquer une partie.

## Mécanique clé : anonymisation par la voix

Toute prise de parole — humaine ou LLM — sort en **voix de synthèse Voxtral fixée par siège**
(`_speak` → `audio/tts.py`). On ne distingue donc pas un humain d'un LLM à l'oreille. Le tell de
**timing** est neutralisé : en phase QUESTION les réponses sont collectées sur toute la fenêtre puis
révélées **groupées, dans un ordre aléatoire**, à cadence fixe (`reveal_gap_seconds`). Les agents
**ignorent qui est humain** : ils ne reçoivent que le transcript.

## Fichiers

| Fichier | Rôle |
|---------|------|
| `app/main.py` | App FastAPI : WebSocket `/ws/{room}`, endpoint `/audio/{id}`, service du front, démarrage auto. |
| `app/config.py` | Config (pydantic-settings), modèles, durées, composition, langue de voix. |
| `app/mistral_client.py` | Client Mistral partagé (import robuste 1.x/2.x). None en mode mock. |
| `app/rooms.py` | Salles, sièges, connexions, aiguillage des entrées humaines. |
| `app/game/state_machine.py` | Moteur des phases, timing anti-tell, plafond d'échanges, conditions de fin. |
| `app/game/events.py` | Schémas des messages WebSocket. Le rôle d'un siège vivant n'est jamais diffusé. |
| `app/game/questions.py` | Banque de questions. |
| `app/agents/llm_agent.py` | Joueur LLM : réponses / questions dirigées / votes (JSON). Personas + repli mock. |
| `app/audio/stt.py` / `tts.py` | Wrappers Voxtral (STT batch, TTS voix par siège), dégradation gracieuse. |
| `app/audio/voices.py` | Pool de voix preset (locuteurs distincts, langue cible en tête, cache). |
| `app/audio/store.py` | Magasin audio éphémère en mémoire (FIFO) servi via `/audio/{id}`. |
| `web/` | Front JS vanilla : WebSocket, micro push-to-talk, lecture audio, UI par phase. |

## Protocole WebSocket

- **client→serveur** : `join{name}`, `ready`, `audio_blob{audio_b64|text}`,
  `direct_question{target, audio_b64|text}` (target vide = passer), `submit_vote{target}`.
- **serveur→client** : `room_state`, `phase_change{phase, deadline, prompt}`,
  `utterance{seat, text, audio_url, context}`, `request_input{mode, deadline, targets}`,
  `vote_result{tally, eliminated}`, `elimination{seat, role}`,
  `game_over{winner, winners, roles}`, `system`.

`deadline` est un nombre de **secondes restantes** (compte à rebours côté client).

## Conditions de fin

- Seuls les humains votent. Une accusation contre un humain fait perdre la manche sans l'éliminer.
- Quand toutes les IA sont éliminées, la dernière IA sortie gagne.
- À `max_rounds`, les IA encore en jeu terminent ex æquo.

## Pistes d'évolution

- Faire varier les modèles selon les sièges (équilibrage).
- STT temps réel (`voxtral-mini-realtime-latest`) plutôt que batch.
- Voix : clonage via `ref_audio`, ou plus de locuteurs distincts.
- Reconnexion d'un joueur déconnecté, salles multiples, écran spectateur.
