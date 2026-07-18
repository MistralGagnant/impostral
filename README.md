# Impostral

Jeu web de déduction sociale **humains vs LLM**, dans l'esprit d'une vidéo Jubilee.

Des humains et des agents LLM (Mistral) partagent une salle. À chaque manche, une **question** est
posée à tous, suivie d'une **délibération** où l'on s'interroge, puis d'un **vote** d'élimination.
Le but des IA : démasquer et éliminer les humains. Le but des humains : passer pour des IA.

**Mécanique centrale — anonymisation par la voix** : toute prise de parole (humaine comme LLM) est
transcrite puis resynthétisée en **voix de synthèse Voxtral fixée par siège**. Impossible de
distinguer un humain d'une IA à l'oreille. Le tell de timing est neutralisé (réponses révélées
groupées, en ordre aléatoire).

Pile : **Voxtral** (STT + TTS) + **chat Mistral** (raisonnement des agents), backend **FastAPI +
WebSockets**, front **JS vanilla**.

## Démarrage

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# (optionnel) clé API pour l'audio réel + vrais agents :
cp .env.example .env   # puis renseigner MISTRAL_API_KEY

./venv/bin/uvicorn app.main:app --reload
```

Ouvrir http://localhost:8000 dans un onglet par joueur humain (défaut : 2 humains + 3 IA). Cliquer
« prêt » dans chaque onglet ; la partie démarre quand tous les humains sont prêts.

**Sans clé API** : mode *mock* — agents scriptés, pas d'audio (texte seul), micro non requis. Idéal
pour tester la boucle de jeu.

Modèles utilisés : `mistral-large-latest` (agents), `voxtral-mini-latest` (STT),
`voxtral-mini-tts-latest` (TTS). Voir `AGENT.md` pour l'architecture, la configuration et les
particularités du SDK `mistralai` 2.x.
