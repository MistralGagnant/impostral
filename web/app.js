// Impostral game client: WebSocket, state rendering, and contextual inputs.
(function () {
  const A = window.ImpostralAudio;

  let ws = null;
  let you = null;
  let seats = [];
  let currentQuestion = "";
  let currentRound = 0;
  let maxRounds = 5;
  const latestUtterances = new Map();

  // --- DOM elements ---
  const $ = (id) => document.getElementById(id);
  const joinScreen = $("join-screen");
  const gameScreen = $("game-screen");
  const seatsEl = $("seats");
  const transcriptEl = $("transcript");
  const logEl = $("log");
  const phaseName = $("phase-name");
  const phaseTimer = $("phase-timer");
  const phasePrompt = $("phase-prompt");
  const inputPanel = $("input-panel");
  const inputControls = $("input-controls");
  const inputTimer = $("input-timer");
  const joinBtn = $("join-btn");
  const joinHint = $("join-hint");
  const humansField = $("humans-field");
  const humansInput = $("humans-input");
  const modeCreate = $("mode-create");
  const modeJoin = $("mode-join");
  const votePanel = $("vote-panel");
  const voteOptions = $("vote-options");
  const submitVote = $("submit-vote");
  const questionStatus = $("question-status");

  let phaseCountdown = null;
  let inputCountdown = null;

  fetch("/config")
    .then((response) => response.ok ? response.json() : null)
    .then((config) => {
      if (config?.max_rounds) maxRounds = config.max_rounds;
      $("round-total").textContent = maxRounds;
      if (config) {
        humansInput.min = config.min_humans ?? 1;
        humansInput.max = config.max_humans ?? 8;
        humansInput.value = config.num_humans ?? 2;
      }
    })
    .catch(() => {});

  // ------------------------------------------------------------------
  // Lobby mode: create a new lobby or join an existing one by name.
  // ------------------------------------------------------------------
  let mode = "create";
  function setMode(next) {
    mode = next;
    const creating = mode === "create";
    modeCreate.setAttribute("aria-selected", String(creating));
    modeJoin.setAttribute("aria-selected", String(!creating));
    humansField.classList.toggle("hidden", !creating);
    joinBtn.querySelector("span").textContent = creating ? "Create & enter" : "Join lobby";
    joinHint.textContent = "";
  }
  modeCreate.addEventListener("click", () => setMode("create"));
  modeJoin.addEventListener("click", () => setMode("join"));

  // ------------------------------------------------------------------
  // Connection
  // ------------------------------------------------------------------
  joinBtn.addEventListener("click", enterRoom);
  $("name-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") enterRoom();
  });
  $("room-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") enterRoom();
  });

  async function enterRoom() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    const room = ($("room-input").value || "lobby").trim();
    if (!room) { joinHint.textContent = "Enter a lobby name."; return; }

    if (mode === "create") {
      joinBtn.disabled = true;
      joinHint.textContent = `Creating lobby “${room}”…`;
      const numHumans = parseInt(humansInput.value, 10) || undefined;
      try {
        const res = await fetch("/lobby", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: room, num_humans: numHumans }),
        });
        if (!res.ok) {
          joinBtn.disabled = false;
          const body = await res.json().catch(() => ({}));
          joinHint.textContent = body.error === "exists"
            ? `Lobby “${room}” already exists. Join it instead.`
            : body.error === "bad_humans"
              ? `Choose between ${body.min} and ${body.max} human players.`
              : "Could not create the lobby.";
          return;
        }
      } catch {
        joinBtn.disabled = false;
        joinHint.textContent = "Could not reach the server.";
        return;
      }
    }
    connect(room);
  }

  function connect(room) {
    const name = ($("name-input").value || "").trim();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    joinBtn.disabled = true;
    joinBtn.querySelector("span").textContent = "Connecting…";
    joinHint.textContent = `Opening channel “${room}”…`;
    ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(room)}`);

    ws.onopen = () => ws.send(JSON.stringify({ type: "join", name }));
    ws.onmessage = (ev) => handle(JSON.parse(ev.data));
    ws.onclose = () => {
      joinBtn.disabled = false;
      joinBtn.querySelector("span").textContent = mode === "create" ? "Create & enter" : "Join lobby";
      if (!joinScreen.classList.contains("hidden") && !joinHint.textContent) {
        joinHint.textContent = "Connection closed. Try again.";
      }
      addLog("Connection closed.");
    };
    ws.onerror = () => {
      joinHint.textContent = "The channel is not responding.";
      addLog("Connection error.");
    };
  }

  // ------------------------------------------------------------------
  // Server message dispatch
  // ------------------------------------------------------------------
  function handle(msg) {
    switch (msg.type) {
      case "room_state": return onRoomState(msg);
      case "system": return onSystem(msg);
      case "phase_change": return onPhaseChange(msg);
      case "utterance": return onUtterance(msg);
      case "request_input": return onRequestInput(msg);
      case "vote_result": return onVoteResult(msg);
      case "elimination": return onElimination(msg);
      case "game_over": return onGameOver(msg);
    }
  }

  function onSystem(msg) {
    // While still on the join screen, surface errors (e.g. missing lobby) in
    // the hint line rather than the hidden in-game log.
    if (!joinScreen.classList.contains("hidden")) joinHint.textContent = msg.text;
    else addLog(msg.text);
  }

  function onRoomState(msg) {
    seats = msg.seats;
    if (msg.you) you = msg.you;
    if (typeof msg.round === "number" && msg.round !== currentRound) {
      currentRound = msg.round;
      latestUtterances.clear();
    }
    joinScreen.classList.add("hidden");
    gameScreen.classList.remove("hidden");
    document.body.dataset.screen = "game";
    document.body.dataset.phase = msg.phase || "lobby";
    if (msg.phase) {
      phaseName.textContent = PHASE_LABEL[msg.phase] || msg.phase;
      questionStatus.textContent = PHASE_LABEL[msg.phase] || msg.phase;
    }
    renderMissionStatus();
    renderSeats();
    if (you && !readySent) showReady();
  }

  let readySent = false;
  function showReady() {
    inputPanel.classList.remove("hidden");
    inputTimer.textContent = "Waiting for other players…";
    inputControls.innerHTML = "";
    const btn = mkBtn("I'm ready", () => {
      readySent = true;
      ws.send(JSON.stringify({ type: "ready" }));
      hideInput();
    });
    inputControls.appendChild(btn);
  }

  // ------------------------------------------------------------------
  // Seat rendering
  // ------------------------------------------------------------------
  function renderMissionStatus() {
    $("round-current").textContent = currentRound;
    $("round-total").textContent = maxRounds;
    $("players-alive").textContent = seats.filter((seat) => seat.alive).length;
    $("players-total").textContent = seats.length;
  }

  function renderSeats() {
    seatsEl.innerHTML = "";
    for (const [index, s] of seats.entries()) {
      const div = document.createElement("div");
      div.className = "seat" + (s.id === you ? " you" : "") + (s.alive ? "" : " dead");
      div.dataset.seat = s.id;
      const angle = (-90 + (360 / Math.max(seats.length, 1)) * index) * (Math.PI / 180);
      div.style.setProperty("--seat-x", `${50 + Math.cos(angle) * 41}%`);
      div.style.setProperty("--seat-y", `${50 + Math.sin(angle) * 39}%`);

      const avatarWrap = document.createElement("span");
      avatarWrap.className = "seat-avatar-wrap";
      const avatar = document.createElement("span");
      avatar.className = "seat-avatar";
      const avatarNumber = String((index % 10) + 1).padStart(2, "0");
      avatar.style.backgroundImage =
        `url("/assets/characters/character_${avatarNumber}.png")`;
      avatar.setAttribute("aria-hidden", "true");
      const seatIndex = document.createElement("span");
      seatIndex.className = "seat-index";
      seatIndex.textContent = String(index + 1);
      avatarWrap.append(avatar, seatIndex);

      const meta = document.createElement("span");
      meta.className = "seat-meta";
      const name = document.createElement("span");
      name.className = "seat-name";
      name.textContent = s.id;
      const role = document.createElement("span");
      role.className = "role";
      role.textContent = s.role ? (s.role === "human" ? "human detected" : "AI detected") : "identity masked";
      meta.append(name, role);
      const answer = document.createElement("span");
      answer.className = "seat-answer";
      answer.textContent = latestUtterances.get(s.id) || "";
      div.append(avatarWrap, meta, answer);
      seatsEl.appendChild(div);
    }
    renderMissionStatus();
  }

  function markDead(seatId, role) {
    const s = seats.find((x) => x.id === seatId);
    if (s) { s.alive = false; if (role) s.role = role; }
    renderSeats();
  }

  function flashSpeaking(seatId) {
    document.querySelectorAll(".seat").forEach((el) =>
      el.classList.toggle("speaking", el.dataset.seat === seatId)
    );
    setTimeout(() => {
      const el = document.querySelector(`.seat[data-seat="${CSS.escape(seatId)}"]`);
      if (el) el.classList.remove("speaking");
    }, 2500);
  }

  // ------------------------------------------------------------------
  // Phases and transcript
  // ------------------------------------------------------------------
  const PHASE_LABEL = {
    lobby: "Lobby", question: "Question",
    vote: "Vote", resolution: "Resolution", game_over: "Game over",
  };

  function onPhaseChange(msg) {
    document.body.dataset.phase = msg.phase;
    phaseName.textContent = PHASE_LABEL[msg.phase] || msg.phase;
    questionStatus.textContent = PHASE_LABEL[msg.phase] || msg.phase;
    if (msg.prompt) currentQuestion = msg.prompt;
    phasePrompt.textContent = currentQuestion || phaseFallback(msg.phase);
    if (msg.phase === "question") latestUtterances.clear();
    hideInput();
    hideVote();
    renderSeats();
    if (phaseCountdown) clearInterval(phaseCountdown);
    startCountdown(phaseTimer, msg.deadline, (h) => (phaseCountdown = h), "", true);
  }

  function phaseFallback(phase) {
    const copy = {
      lobby: "Waiting for all players to connect.",
      vote: "Choose carefully. The wrong signal can expose you.",
      resolution: "Analyzing the vote…",
      game_over: "The protocol is complete.",
    };
    return copy[phase] || "Waiting for protocol…";
  }

  function onUtterance(msg) {
    flashSpeaking(msg.seat);
    latestUtterances.set(msg.seat, msg.text || "(silence)");
    const seatAnswer = document.querySelector(`.seat[data-seat="${CSS.escape(msg.seat)}"] .seat-answer`);
    if (seatAnswer) seatAnswer.textContent = msg.text || "(silence)";
    transcriptEl.querySelector(".transcript-empty")?.remove();
    const div = document.createElement("div");
    div.className = "utt";
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = msg.seat;
    div.appendChild(who);
    if (msg.context) {
      const context = document.createElement("span");
      context.className = "ctx";
      context.textContent = ` // ${msg.context}`;
      div.appendChild(context);
    }
    const text = document.createElement("span");
    text.className = "utterance-text";
    text.textContent = msg.text || "";
    div.appendChild(text);
    transcriptEl.appendChild(div);
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
    if (msg.audio_url) A.enqueue(msg.audio_url);
  }

  // ------------------------------------------------------------------
  // Input panels
  // ------------------------------------------------------------------
  function onRequestInput(msg) {
    if (msg.mode === "vote") {
      hideInput();
      buildVotePanel(msg.targets || []);
      return;
    }

    hideVote();
    inputPanel.classList.remove("hidden");
    inputControls.innerHTML = "";
    startCountdown(inputTimer, msg.deadline, (h) => (inputCountdown = h), "Your turn: ");

    if (msg.mode === "answer") {
      buildSpeakPanel((payload) => {
        ws.send(JSON.stringify({ type: "audio_blob", ...payload }));
        hideInput();
      });
    }
  }

  // Textarea, microphone recording button, and submit action.
  function buildSpeakPanel(onSend) {
    const ta = document.createElement("textarea");
    ta.placeholder = "Type… or use the microphone";
    const recBtn = mkBtn("● Mic", null, "rec");
    let recording = false;
    recBtn.addEventListener("click", async () => {
      if (!recording) {
        const ok = await A.startRecording();
        if (ok) { recording = true; recBtn.textContent = "■ Stop"; }
      } else {
        recBtn.textContent = "● Mic";
        recording = false;
        const b64 = await A.stopRecording();
        recBtn.dataset.audio = b64 || "";
      }
    });
    const sendBtn = mkBtn("Send", () =>
      onSend({ audio_b64: recBtn.dataset.audio || null, text: ta.value.trim() })
    );
    inputControls.append(ta, recBtn, sendBtn);
    return { textarea: ta, recBtn };
  }

  function buildVotePanel(targets) {
    let selectedTarget = "";
    voteOptions.innerHTML = "";
    submitVote.disabled = true;
    for (const t of targets) {
      const seatIndex = Math.max(0, seats.findIndex((seat) => seat.id === t));
      const option = document.createElement("button");
      option.className = "vote-option";
      option.type = "button";
      option.setAttribute("role", "radio");
      option.setAttribute("aria-checked", "false");
      const img = document.createElement("img");
      img.src = `/assets/characters/character_${String((seatIndex % 10) + 1).padStart(2, "0")}.png`;
      img.alt = "";
      const label = document.createElement("span");
      label.textContent = t;
      option.append(img, label);
      option.addEventListener("click", () => {
        selectedTarget = t;
        submitVote.disabled = false;
        voteOptions.querySelectorAll(".vote-option").forEach((node) =>
          node.setAttribute("aria-checked", String(node === option))
        );
      });
      voteOptions.appendChild(option);
    }
    submitVote.onclick = () => {
      if (!selectedTarget) return;
      ws.send(JSON.stringify({ type: "submit_vote", target: selectedTarget }));
      hideVote();
    };
    votePanel.classList.remove("hidden");
    gameScreen.classList.add("vote-open");
  }

  function onVoteResult(msg) {
    hideVote();
    const parts = Object.entries(msg.tally).map(([k, v]) => `${k}: ${v}`);
    addLog("Votes — " + (parts.join(", ") || "none") +
      (msg.eliminated
        ? ` → ${msg.eliminated} eliminated.`
        : msg.runoff?.length
          ? ` → runoff between ${msg.runoff.join(", ")}.`
          : ""));
  }

  function onElimination(msg) {
    markDead(msg.seat, msg.role);
  }

  function onGameOver(msg) {
    hideInput();
    hideVote();
    document.body.dataset.phase = "game_over";
    phaseName.textContent = "Game over";
    phaseTimer.textContent = "";
    seats = seats.map((s) => ({ ...s, role: msg.roles[s.id] }));
    renderSeats();
    const banner = document.createElement("div");
    banner.className = "winner";
    const winners = msg.winners || [];
    banner.textContent = winners.length === 1
      ? `${winners[0]} wins the game!`
      : winners.length > 1
        ? `${winners.join(", ")} survive and tie.`
        : "Game over.";
    phasePrompt.textContent = "";
    document.querySelector(".winner")?.remove();
    document.querySelector(".arena-shell").appendChild(banner);
  }

  // ------------------------------------------------------------------
  // Utilities
  // ------------------------------------------------------------------
  function hideInput() {
    inputPanel.classList.add("hidden");
    inputControls.innerHTML = "";
    if (inputCountdown) { clearInterval(inputCountdown); inputCountdown = null; }
  }

  function hideVote() {
    votePanel.classList.add("hidden");
    gameScreen.classList.remove("vote-open");
    voteOptions.innerHTML = "";
    submitVote.disabled = true;
    submitVote.onclick = null;
  }

  function startCountdown(el, seconds, store, prefix = "", compact = false) {
    if (typeof seconds !== "number") { el.textContent = ""; return; }
    let remaining = Math.round(seconds);
    const tick = () => {
      el.textContent = compact
        ? String(Math.max(0, remaining))
        : prefix + (remaining > 0 ? `${remaining}s` : "…");
      if (remaining <= 0) clearInterval(handle);
      remaining -= 1;
    };
    const handle = setInterval(tick, 1000);
    tick();
    store(handle);
  }

  function mkBtn(text, onClick, cls) {
    const b = document.createElement("button");
    b.textContent = text;
    if (cls) b.className = cls;
    if (onClick) b.addEventListener("click", onClick);
    return b;
  }

  function addLog(text) {
    const d = document.createElement("div");
    d.textContent = text;
    logEl.appendChild(d);
    logEl.scrollTop = logEl.scrollHeight;
  }

})();
