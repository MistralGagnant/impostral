// Impostral game client: WebSocket, state rendering, and contextual inputs.
(function () {
  const A = window.ImpostralAudio;

  let ws = null;
  let you = null;
  let seats = [];
  let currentQuestion = "";
  let currentRound = 0;
  let maxRounds = 5;
  let humanWaitSeconds = 15;
  let isLobbyHost = false;
  let gameFinished = false;
  let currentMatch = null;
  let reconnectAttempts = 0;
  let reconnectTimer = null;
  let connectionSerial = 0;
  let turnstileScriptPromise = null;
  let turnstileWidgetId = null;
  const latestUtterances = new Map();
  // Latest ballot: seat id -> votes received, shown as badges on the arena.
  let voteTally = {};
  let voteEliminated = null;

  function randomId() {
    return globalThis.crypto?.randomUUID?.() ||
      `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
  }

  function persistentId(storage, key) {
    try {
      let value = storage.getItem(key);
      if (!value) {
        value = randomId();
        storage.setItem(key, value);
      }
      return value;
    } catch {
      return randomId();
    }
  }

  // Anonymous technical identifiers only: no account or personal profile.
  const playerId = persistentId(localStorage, "impostral.playerId");
  const sessionId = persistentId(sessionStorage, "impostral.sessionId");

  // --- DOM elements ---
  const $ = (id) => document.getElementById(id);
  const joinScreen = $("join-screen");
  const gameScreen = $("game-screen");
  const seatsEl = $("seats");
  const transcriptEl = $("transcript");
  const phaseName = $("phase-name");
  const phaseTimer = $("phase-timer");
  const phasePrompt = $("phase-prompt");
  const inputPanel = $("input-panel");
  const inputControls = $("input-controls");
  const inputTimer = $("input-timer");
  const playBtn = $("play-btn");
  const joinBtn = $("join-btn");
  const joinHint = $("join-hint");
  const turnstileContainer = $("turnstile-container");
  const humansField = $("humans-field");
  const humansInput = $("humans-input");
  const modeCreate = $("mode-create");
  const modeJoin = $("mode-join");
  const votePanel = $("vote-panel");
  const voteOptions = $("vote-options");
  const submitVote = $("submit-vote");

  let phaseCountdown = null;
  let inputCountdown = null;
  let activeInputCleanup = null;

  // A full page reload always starts fresh on the home screen.
  try { sessionStorage.removeItem("impostral.activeMatch"); } catch {}

  function saveCurrentMatch(match) {
    currentMatch = match;
    try {
      if (match) sessionStorage.setItem("impostral.activeMatch", JSON.stringify(match));
      else sessionStorage.removeItem("impostral.activeMatch");
    } catch { /* Storage may be unavailable in private browsing modes. */ }
  }

  const configReady = fetch("/config")
    .then((response) => response.ok ? response.json() : null)
    .then((config) => {
      if (config?.max_rounds) maxRounds = config.max_rounds;
      if (config?.tts_playback_rate) A.setPlaybackRate(config.tts_playback_rate);
      if (typeof config?.human_wait_seconds === "number") {
        humanWaitSeconds = config.human_wait_seconds;
      }
      $("round-total").textContent = maxRounds;
      if (config) {
        humansInput.min = config.min_humans ?? 1;
        humansInput.max = config.max_humans ?? 8;
        humansInput.value = config.num_humans ?? 3;
      }
      return config;
    })
    .catch(() => null);

  function loadTurnstile() {
    if (window.turnstile) return Promise.resolve(window.turnstile);
    if (turnstileScriptPromise) return turnstileScriptPromise;

    turnstileScriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      const timeout = setTimeout(() => reject(new Error("security_check_unavailable")), 12000);
      script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      script.async = true;
      script.defer = true;
      script.onload = () => {
        clearTimeout(timeout);
        if (window.turnstile) resolve(window.turnstile);
        else reject(new Error("security_check_unavailable"));
      };
      script.onerror = () => {
        clearTimeout(timeout);
        reject(new Error("security_check_unavailable"));
      };
      document.head.appendChild(script);
    }).catch((error) => {
      turnstileScriptPromise = null;
      throw error;
    });
    return turnstileScriptPromise;
  }

  function removeTurnstileWidget() {
    if (turnstileWidgetId !== null && window.turnstile) {
      try { window.turnstile.remove(turnstileWidgetId); } catch { /* Already removed. */ }
    }
    turnstileWidgetId = null;
    turnstileContainer.replaceChildren();
  }

  async function requestTurnstileToken() {
    const config = await configReady;
    if (!config) throw new Error("security_check_unavailable");
    if (!config.turnstile_enabled) return "";
    if (!config.turnstile_site_key) throw new Error("security_check_unavailable");

    const turnstile = await loadTurnstile();
    removeTurnstileWidget();
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        callback(value);
        setTimeout(removeTurnstileWidget, 0);
      };
      const fail = () => finish(reject, new Error("security_check_failed"));

      turnstileWidgetId = turnstile.render(turnstileContainer, {
        sitekey: config.turnstile_site_key,
        action: "enter_game",
        appearance: "interaction-only",
        execution: "execute",
        theme: "auto",
        language: "auto",
        retry: "auto",
        "refresh-expired": "auto",
        callback: (token) => finish(resolve, token),
        "error-callback": () => { fail(); return true; },
        "expired-callback": fail,
        "timeout-callback": fail,
        "unsupported-callback": fail,
      });
      turnstile.execute(turnstileWidgetId);
    });
  }

  function entryErrorMessage(code, fallback) {
    if (code === "security_check_failed") {
      return "Security check failed. Disable any VPN or content blocker, then try again.";
    }
    if (code === "security_check_unavailable") {
      return "Security check unavailable. Please try again in a moment.";
    }
    if (code === "exists") return "This lobby already exists. Join it instead.";
    if (code === "missing") return "No lobby with that name exists yet.";
    if (code === "full") return "This lobby is full.";
    if (code === "started") return "This lobby has already started.";
    return fallback;
  }

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
  playBtn.addEventListener("click", play);
  joinBtn.addEventListener("click", enterRoom);
  $("name-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") play();
  });
  $("room-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") enterRoom();
  });

  function connectionActive() {
    return ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING);
  }

  async function play() {
    if (connectionActive()) return;
    gameFinished = false;
    playBtn.disabled = true;
    playBtn.querySelector("span").textContent = "Checking…";
    joinHint.textContent = "Running a quick security check…";
    try {
      const turnstileToken = await requestTurnstileToken();
      playBtn.querySelector("span").textContent = "Finding a game…";
      joinHint.textContent = "Looking for the first available game…";
      const response = await fetch("/matchmaking", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          player_id: playerId,
          session_id: sessionId,
          name: ($("name-input").value || "").trim(),
          turnstile_token: turnstileToken,
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || "matchmaking_failed");
      const match = {
        room: body.room_id,
        reservationToken: body.reservation_token,
        quick: true,
        name: ($("name-input").value || "").trim(),
      };
      saveCurrentMatch(match);
      connect(match);
    } catch (error) {
      playBtn.disabled = false;
      playBtn.querySelector("span").textContent = "Play";
      joinHint.textContent = entryErrorMessage(
        error.message,
        "Could not find a game. Try again.",
      );
    }
  }

  async function enterRoom() {
    if (connectionActive()) return;
    const room = ($("room-input").value || "lobby").trim();
    if (!room) { joinHint.textContent = "Enter a lobby name."; return; }

    joinBtn.disabled = true;
    joinBtn.querySelector("span").textContent = "Checking…";
    joinHint.textContent = "Running a quick security check…";
    try {
      const turnstileToken = await requestTurnstileToken();
      const creating = mode === "create";
      joinHint.textContent = creating
        ? `Creating lobby “${room}”…`
        : `Joining lobby “${room}”…`;
      const url = creating ? "/lobby" : `/lobby/${encodeURIComponent(room)}/join`;
      const payload = {
        player_id: playerId,
        session_id: sessionId,
        turnstile_token: turnstileToken,
      };
      if (creating) {
        payload.name = room;
        payload.num_humans = parseInt(humansInput.value, 10) || undefined;
      }
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (body.error === "bad_humans") {
          throw new Error(`bad_humans:${body.min}:${body.max}`);
        }
        throw new Error(body.error || "lobby_failed");
      }

      const match = {
        room: body.name || room,
        reservationToken: body.reservation_token,
        quick: false,
        name: ($("name-input").value || "").trim(),
      };
      saveCurrentMatch(match);
      connect(match);
    } catch (error) {
      joinBtn.disabled = false;
      joinBtn.querySelector("span").textContent = mode === "create"
        ? "Create & enter"
        : "Join lobby";
      if (error.message.startsWith("bad_humans:")) {
        const [, min, max] = error.message.split(":");
        joinHint.textContent = `Choose between ${min} and ${max} human players.`;
      } else if (error.message === "exists") {
        joinHint.textContent = `Lobby “${room}” already exists. Join it instead.`;
      } else {
        joinHint.textContent = entryErrorMessage(
          error.message,
          "Could not reach the lobby. Try again.",
        );
      }
    }
  }

  function connect(match, { reconnecting = false } = {}) {
    if (connectionActive()) return;
    const serial = ++connectionSerial;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    playBtn.disabled = true;
    joinBtn.disabled = true;
    joinBtn.querySelector("span").textContent = "Connecting…";
    playBtn.querySelector("span").textContent = reconnecting ? "Reconnecting…" : "Connecting…";
    joinHint.textContent = reconnecting
      ? "Reconnecting to your game…"
      : `Opening channel “${match.room}”…`;
    ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(match.room)}`);

    ws.onopen = () => {
      reconnectAttempts = 0;
      ws.send(JSON.stringify({
        type: "join",
        name: match.name || "",
        player_id: playerId,
        session_id: sessionId,
        reservation_token: match.reservationToken || "",
      }));
    };
    ws.onmessage = (event) => handle(JSON.parse(event.data));
    ws.onclose = () => {
      if (serial !== connectionSerial) return;
      hideInput();
      joinBtn.disabled = false;
      joinBtn.querySelector("span").textContent = mode === "create" ? "Create & enter" : "Join lobby";
      playBtn.disabled = false;
      playBtn.querySelector("span").textContent = "Play";
      if (!gameFinished && currentMatch && joinScreen.classList.contains("hidden")) {
        scheduleReconnect();
        return;
      }
      if (!joinScreen.classList.contains("hidden") && !joinHint.textContent) {
        joinHint.textContent = "Connection closed. Try again.";
      }
    };
    ws.onerror = () => {
      if (joinScreen.classList.contains("hidden")) addLog("Connection interrupted.");
      else joinHint.textContent = "The channel is not responding.";
    };
  }

  function scheduleReconnect() {
    if (reconnectTimer || !currentMatch || gameFinished) return;
    if (reconnectAttempts >= 8) {
      returnToJoin("The server restarted. Click Play to find a new game.");
      return;
    }
    const delay = Math.min(5000, 750 * (2 ** reconnectAttempts));
    reconnectAttempts += 1;
    addLog(`Connection lost. Reconnecting (${reconnectAttempts}/8)…`);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect(currentMatch, { reconnecting: true });
    }, delay);
  }

  function returnToJoin(message) {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = null;
    hideInput();
    hideVote();
    saveCurrentMatch(null);
    you = null;
    isLobbyHost = false;
    gameScreen.classList.add("hidden");
    joinScreen.classList.remove("hidden");
    document.body.dataset.screen = "join";
    document.body.dataset.phase = "lobby";
    playBtn.disabled = false;
    playBtn.querySelector("span").textContent = "Play";
    joinHint.textContent = message;
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
    if (msg.code === "room_missing" || msg.code === "reservation_expired") {
      returnToJoin(msg.text);
      return;
    }
    // While still on the join screen, surface errors (e.g. missing lobby) in
    // the hint line rather than the hidden in-game log.
    if (!joinScreen.classList.contains("hidden")) joinHint.textContent = msg.text;
    else addLog(msg.text);
  }

  function onRoomState(msg) {
    seats = msg.seats;
    if (msg.you) you = msg.you;
    if (typeof msg.is_host === "boolean") isLobbyHost = msg.is_host;
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
    }
    if (msg.phase === "lobby") phasePrompt.textContent = "Waiting for players…";
    renderMissionStatus();
    renderSeats();
    if (you && msg.phase === "lobby") {
      if (msg.started) {
        showGameStarting();
        return;
      }
      if (msg.visibility === "private") {
        showPrivateLobby(
          msg.connected_humans ?? 0,
          msg.expected_humans ?? 0,
        );
        return;
      }
      const remaining = typeof msg.lobby_wait_remaining === "number"
        ? msg.lobby_wait_remaining
        : humanWaitSeconds;
      showWaiting(remaining);
    }
  }

  function showWaiting(remaining = humanWaitSeconds) {
    inputPanel.classList.add("hidden");
    inputControls.innerHTML = "";
    startLobbyCountdown(remaining);
  }

  function showPrivateLobby(connectedHumans, expectedHumans) {
    if (inputCountdown) {
      clearInterval(inputCountdown);
      inputCountdown = null;
    }
    inputTimer.textContent = "";
    inputControls.innerHTML = "";

    const label = document.createElement("span");
    label.className = "lobby-wait-copy";
    label.textContent = isLobbyHost
      ? "Start whenever your group is ready"
      : "Waiting for the host to start…";
    const count = document.createElement("strong");
    count.className = "lobby-player-count";
    count.textContent = `${connectedHumans} / ${expectedHumans}`;
    const caption = document.createElement("small");
    caption.className = "lobby-player-caption";
    caption.textContent = "human players connected";
    phasePrompt.replaceChildren(label, count, caption);

    if (!isLobbyHost) {
      inputPanel.classList.add("hidden");
      return;
    }

    inputPanel.classList.remove("hidden");
    const playerLabel = connectedHumans === 1 ? "player" : "players";
    const btn = mkBtn(`Start game with ${connectedHumans} ${playerLabel}`, () => {
      btn.disabled = true;
      btn.textContent = "Starting…";
      ws.send(JSON.stringify({ type: "start_game" }));
    });
    btn.disabled = connectedHumans < 1;
    inputControls.appendChild(btn);
  }

  function showGameStarting() {
    if (inputCountdown) {
      clearInterval(inputCountdown);
      inputCountdown = null;
    }
    inputPanel.classList.add("hidden");
    inputControls.innerHTML = "";
    phasePrompt.textContent = "Starting game…";
  }

  function startLobbyCountdown(remaining) {
    if (inputCountdown) clearInterval(inputCountdown);
    inputTimer.textContent = "";
    let seconds = Math.round(remaining);
    const tick = () => {
      if (seconds <= 0) {
        phasePrompt.textContent = "Starting game…";
        clearInterval(inputCountdown);
        inputCountdown = null;
        return;
      }
      const label = document.createElement("span");
      label.className = "lobby-wait-copy";
      label.textContent = "Waiting for other players…";
      const countdown = document.createElement("strong");
      countdown.className = "lobby-countdown";
      countdown.textContent = `${seconds}s`;
      phasePrompt.replaceChildren(label, countdown);
      seconds -= 1;
    };
    inputCountdown = setInterval(tick, 1000);
    tick();
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
      div.style.setProperty("--seat-x", `${50 + Math.cos(angle) * 38}%`);
      div.style.setProperty("--seat-y", `${50 + Math.sin(angle) * 32}%`);

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
      role.className = "role" + (s.role ? (s.role === "human" ? " is-human" : " is-llm") : "");
      role.textContent = s.role
        ? (s.role === "human" ? "Human" : (prettyModel(s.model) || "AI"))
        : "identity masked";
      meta.append(name, role);
      const answer = document.createElement("span");
      answer.className = "seat-answer";
      answer.textContent = latestUtterances.get(s.id) || "";
      const votes = voteTally[s.id];
      if (votes) {
        const badge = document.createElement("span");
        badge.className = "vote-badge" + (s.id === voteEliminated ? " out" : "");
        badge.textContent = `${votes} vote${votes > 1 ? "s" : ""}`;
        avatarWrap.appendChild(badge);
      }
      div.append(avatarWrap, meta, answer);
      seatsEl.appendChild(div);
    }
    renderMissionStatus();
  }

  function markDead(seatId, role, model) {
    const s = seats.find((x) => x.id === seatId);
    if (s) { s.alive = false; if (role) s.role = role; if (model) s.model = model; }
    renderSeats();
  }

  // "mistral-large-latest" -> "mistral-large" for a cleaner reveal label.
  function prettyModel(model) {
    return model ? model.replace(/-latest$/, "") : "";
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
    if (msg.prompt) currentQuestion = msg.prompt;
    phasePrompt.textContent = currentQuestion || phaseFallback(msg.phase);
    if (msg.phase === "question") {
      latestUtterances.clear();
      // Keep the last ballot visible while the elimination reveal plays out.
      if (!elimActive) {
        voteTally = {};
        voteEliminated = null;
      }
    }
    hideInput();
    hideVote();
    renderSeats();
    if (phaseCountdown) clearInterval(phaseCountdown);
    startCountdown(phaseTimer, msg.deadline, (h) => (phaseCountdown = h), "", true);
  }

  function phaseFallback(phase) {
    const copy = {
      lobby: "Waiting for all players…",
      vote: "Who is the AI?",
      resolution: "Counting votes…",
      game_over: "The hunt is over.",
    };
    return copy[phase] || "Waiting…";
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
    if (msg.audio_url) {
      A.enqueue(msg.audio_url, () => {
        if (msg.playback_id && ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "playback_complete",
            playback_id: msg.playback_id,
          }));
        }
      });
    }
  }

  // ------------------------------------------------------------------
  // Input panels
  // ------------------------------------------------------------------
  function onRequestInput(msg) {
    hideInput();
    if (msg.mode === "vote") {
      buildVotePanel(msg.targets || []);
      return;
    }

    hideVote();
    inputPanel.classList.remove("hidden");
    inputControls.innerHTML = "";

    if (msg.mode === "answer") {
      const panel = buildSpeakPanel((payload) => {
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "audio_blob", ...payload }));
        }
        hideInput();
      });
      activeInputCleanup = panel.cleanup;
      startCountdown(
        inputTimer,
        msg.deadline,
        (handle) => (inputCountdown = handle),
        "Your turn: ",
        false,
        hideInput,
      );
    }
  }

  // Textarea plus a single action button: mic when empty, send when typing,
  // and "stop & send" while recording.
  function buildSpeakPanel(onSend) {
    const ta = document.createElement("textarea");
    ta.placeholder = "Type your answer… or use the mic";
    const btn = mkBtn("● Mic", null, "rec");
    let recording = false;
    let starting = false;
    let cancelled = false;
    let sent = false;

    const refresh = () => {
      btn.disabled = starting || sent;
      if (starting) {
        btn.textContent = "Opening mic…";
        btn.className = "rec";
      } else if (recording) {
        btn.textContent = "■ Stop & send";
        btn.className = "rec recording";
      } else if (ta.value.trim()) {
        btn.textContent = "Send";
        btn.className = "";
      } else {
        btn.textContent = "● Mic";
        btn.className = "rec";
      }
    };

    const send = (payload) => {
      if (sent || cancelled) return;
      sent = true;
      refresh();
      onSend(payload);
    };

    const cleanup = () => {
      cancelled = true;
      recording = false;
      starting = false;
    };

    ta.addEventListener("input", refresh);
    ta.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && ta.value.trim()) {
        event.preventDefault();
        send({ audio_b64: null, audio_mime: null, text: ta.value.trim() });
      }
    });
    btn.addEventListener("click", async () => {
      if (starting || sent || cancelled) return;
      if (recording) {
        recording = false;
        starting = true;
        refresh();
        const audio = await A.stopRecording();
        starting = false;
        if (cancelled || sent) return;
        send({
          audio_b64: audio?.audio_b64 || null,
          audio_mime: audio?.audio_mime || null,
          text: ta.value.trim(),
        });
        return;
      }
      if (ta.value.trim()) {
        send({ audio_b64: null, audio_mime: null, text: ta.value.trim() });
        return;
      }
      starting = true;
      refresh();
      const ok = await A.startRecording();
      starting = false;
      if (cancelled) {
        A.cancelRecording();
        return;
      }
      if (ok) recording = true;
      else ta.placeholder = "Mic unavailable — type your answer";
      refresh();
    });

    inputControls.append(ta, btn);
    return { textarea: ta, btn, cleanup };
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
    voteTally = msg.tally || {};
    voteEliminated = msg.eliminated || null;
    renderSeats();
    const parts = Object.entries(msg.tally).map(([k, v]) => `${k}: ${v}`);
    addLog("Votes — " + (parts.join(", ") || "none") +
      (msg.eliminated
        ? ` → ${msg.eliminated} eliminated.`
        : msg.runoff?.length
          ? ` → runoff between ${msg.runoff.join(", ")}.`
          : ""));
  }

  function onElimination(msg) {
    markDead(msg.seat, msg.role, msg.model);
    showElimination(msg.seat, msg.role, msg.model);
    if (msg.seat === you) {
      phasePrompt.textContent = "You have been eliminated.";
      hideInput();
      hideVote();
    }
  }

  let elimActive = false;

  // Full-arena overlay: eliminated avatar, red stamp, vote tally, role reveal.
  function showElimination(seatId, role, model) {
    const arena = document.querySelector(".arena-viz");
    if (!arena) return;
    arena.querySelector(".elim-overlay")?.remove();
    elimActive = true;

    const overlay = document.createElement("div");
    overlay.className = "elim-overlay";
    const card = document.createElement("div");
    card.className = "elim-card";

    const index = Math.max(0, seats.findIndex((seat) => seat.id === seatId));
    const img = document.createElement("img");
    img.src = `/assets/characters/character_${String((index % 10) + 1).padStart(2, "0")}.png`;
    img.alt = "";

    const name = document.createElement("span");
    name.className = "elim-name";
    name.textContent = seatId;

    const stamp = document.createElement("span");
    stamp.className = "elim-stamp";
    stamp.textContent = seatId === you ? "You are eliminated" : "Eliminated";

    card.append(img, name, stamp);
    const entries = Object.entries(voteTally).sort((a, b) => b[1] - a[1]);
    if (entries.length) {
      const tally = document.createElement("span");
      tally.className = "elim-tally";
      for (const [id, votes] of entries) {
        const item = document.createElement("span");
        item.className = "elim-tally-item" + (id === seatId ? " out" : "");
        item.textContent = `${id} ×${votes}`;
        tally.appendChild(item);
      }
      card.append(tally);
    }
    if (role) {
      const reveal = document.createElement("span");
      reveal.className = "elim-role " + (role === "human" ? "is-human" : "is-llm");
      reveal.append("They were ");
      const b = document.createElement("b");
      b.textContent = role === "human" ? "human" : (prettyModel(model) || "an AI");
      reveal.append(b);
      card.append(reveal);
    }
    overlay.appendChild(card);
    arena.appendChild(overlay);

    setTimeout(() => overlay.classList.add("leaving"), 3800);
    setTimeout(() => {
      overlay.remove();
      elimActive = false;
      voteTally = {};
      voteEliminated = null;
      renderSeats();
    }, 4300);
  }

  function onGameOver(msg) {
    gameFinished = true;
    saveCurrentMatch(null);
    hideInput();
    hideVote();
    document.body.dataset.phase = "game_over";
    phaseName.textContent = "Game over";
    phaseTimer.textContent = "";
    const models = msg.models || {};
    seats = seats.map((s) => ({ ...s, role: msg.roles[s.id], model: models[s.id] || s.model }));
    renderSeats();
    const banner = document.createElement("div");
    banner.className = "winner";
    const winners = msg.winners || [];
    const partner = winners.find((seatId) => seatId !== you);
    const sharedHumanAiWin = winners.includes(you) && partner
      && msg.roles?.[you] === "human" && msg.roles?.[partner] === "llm";
    const resultText = sharedHumanAiWin
      ? `You and ${partner} (AI) win together — impossible to tell you apart!`
      : msg.message || (winners.length === 1
      ? `${winners[0]} wins the game!`
      : winners.length > 1
        ? `${winners.join(", ")} survive and tie.`
        : "Game over.");
    const youWereEliminated = seats.some((seat) => seat.id === you && !seat.alive);
    banner.textContent = youWereEliminated
      ? `You were eliminated. ${resultText}`
      : resultText;
    phasePrompt.textContent = "The hunt is over.";
    const arena = document.querySelector(".arena-viz");
    arena.querySelector(".elim-overlay")?.remove();
    elimActive = false;
    document.querySelector(".winner")?.remove();
    arena.appendChild(banner);
  }

  // ------------------------------------------------------------------
  // Utilities
  // ------------------------------------------------------------------
  function hideInput() {
    if (activeInputCleanup) activeInputCleanup();
    activeInputCleanup = null;
    A.cancelRecording();
    inputPanel.classList.add("hidden");
    inputControls.innerHTML = "";
    if (inputCountdown) { clearInterval(inputCountdown); inputCountdown = null; }
    inputTimer.textContent = "";
  }

  function hideVote() {
    votePanel.classList.add("hidden");
    gameScreen.classList.remove("vote-open");
    voteOptions.innerHTML = "";
    submitVote.disabled = true;
    submitVote.onclick = null;
  }

  function startCountdown(
    el, seconds, store, prefix = "", compact = false, onExpire = null,
  ) {
    if (typeof seconds !== "number") { el.textContent = ""; return; }
    let remaining = Math.round(seconds);
    let expired = false;
    const tick = () => {
      el.textContent = compact
        ? String(Math.max(0, remaining))
        : prefix + (remaining > 0 ? `${remaining}s` : "…");
      if (remaining <= 0) {
        clearInterval(handle);
        if (!expired) {
          expired = true;
          if (onExpire) onExpire();
        }
        return;
      }
      remaining -= 1;
    };
    const handle = setInterval(tick, 1000);
    store(handle);
    tick();
  }

  window.addEventListener("pagehide", () => A.cancelRecording());

  function mkBtn(text, onClick, cls) {
    const b = document.createElement("button");
    b.textContent = text;
    if (cls) b.className = cls;
    if (onClick) b.addEventListener("click", onClick);
    return b;
  }

  // System messages share the live feed with player utterances.
  function addLog(text) {
    transcriptEl.querySelector(".transcript-empty")?.remove();
    const d = document.createElement("p");
    d.className = "utt sys";
    d.textContent = text;
    transcriptEl.appendChild(d);
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }

})();
