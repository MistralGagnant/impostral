// Client de jeu Impostral : WebSocket, rendu d'état, panneaux de saisie.
(function () {
  const A = window.ImpostralAudio;

  let ws = null;
  let you = null;
  let seats = [];

  // --- Éléments DOM ---
  const $ = (id) => document.getElementById(id);
  const joinScreen = $("join-screen");
  const gameScreen = $("game-screen");
  const seatsEl = $("seats");
  const aliveCountEl = $("alive-count");
  const transcriptEl = $("transcript");
  const logEl = $("log");
  const phaseName = $("phase-name");
  const phaseTimer = $("phase-timer");
  const phasePrompt = $("phase-prompt");
  const inputPanel = $("input-panel");
  const inputControls = $("input-controls");
  const inputTimer = $("input-timer");

  let phaseCountdown = null;
  let inputCountdown = null;

  // ------------------------------------------------------------------
  // Connexion
  // ------------------------------------------------------------------
  $("join-btn").addEventListener("click", () => {
    const room = ($("room-input").value || "salon").trim();
    const name = ($("name-input").value || "").trim();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(room)}`);

    ws.onopen = () => ws.send(JSON.stringify({ type: "join", name }));
    ws.onmessage = (ev) => handle(JSON.parse(ev.data));
    ws.onclose = () => addLog("Connexion fermée.");
    ws.onerror = () => addLog("Erreur de connexion.");
  });

  // ------------------------------------------------------------------
  // Dispatch des messages serveur
  // ------------------------------------------------------------------
  function handle(msg) {
    switch (msg.type) {
      case "room_state": return onRoomState(msg);
      case "system": return addLog(msg.text);
      case "phase_change": return onPhaseChange(msg);
      case "utterance": return onUtterance(msg);
      case "request_input": return onRequestInput(msg);
      case "vote_result": return onVoteResult(msg);
      case "elimination": return onElimination(msg);
      case "game_over": return onGameOver(msg);
    }
  }

  function onRoomState(msg) {
    seats = msg.seats;
    if (msg.you) you = msg.you;
    joinScreen.classList.add("hidden");
    gameScreen.classList.remove("hidden");
    renderSeats();
    if (you && !readySent) showReady();
  }

  let readySent = false;
  function showReady() {
    inputPanel.classList.remove("hidden");
    inputTimer.textContent = "En attente des autres joueurs…";
    inputControls.innerHTML = "";
    const btn = mkBtn("Je suis prêt", () => {
      readySent = true;
      ws.send(JSON.stringify({ type: "ready" }));
      hideInput();
    });
    inputControls.appendChild(btn);
  }

  // ------------------------------------------------------------------
  // Rendu des sièges
  // ------------------------------------------------------------------
  function spriteForSeat(seatId) {
    const index = Math.max(0, seats.findIndex((s) => s.id === seatId));
    return `/assets/characters/character_${String((index % 10) + 1).padStart(2, "0")}.png`;
  }

  function renderSeats() {
    seatsEl.innerHTML = "";
    aliveCountEl.textContent = `${seats.filter((s) => s.alive).length}/${seats.length}`;
    for (const s of seats) {
      const div = document.createElement("div");
      div.className = "seat" + (s.id === you ? " you" : "") + (s.alive ? "" : " dead");
      div.dataset.seat = s.id;

      const avatarWrap = document.createElement("div");
      avatarWrap.className = "seat-avatar-wrap";
      const avatar = document.createElement("img");
      avatar.className = "seat-avatar";
      avatar.src = spriteForSeat(s.id);
      avatar.alt = "";
      avatarWrap.appendChild(avatar);

      const info = document.createElement("div");
      info.className = "seat-info";
      const name = document.createElement("span");
      name.className = "seat-name";
      name.textContent = s.id;
      if (s.role) {
        const role = document.createElement("span");
        role.className = "role";
        role.textContent = s.role === "human" ? "humain" : "IA";
        name.appendChild(role);
      }
      const status = document.createElement("span");
      status.className = "seat-status";
      status.title = s.alive ? "En jeu" : "Éliminé";
      info.append(name, status);
      div.append(avatarWrap, info);
      seatsEl.appendChild(div);
    }
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
  // Phases / transcript
  // ------------------------------------------------------------------
  const PHASE_LABEL = {
    lobby: "Salon", question: "Question", deliberation: "Délibération",
    vote: "Vote", resolution: "Résolution", game_over: "Fin",
  };

  function onPhaseChange(msg) {
    phaseName.textContent = PHASE_LABEL[msg.phase] || msg.phase;
    phasePrompt.textContent = msg.prompt || "";
    hideInput();
    startCountdown(phaseTimer, msg.deadline, (h) => (phaseCountdown = h));
  }

  function onUtterance(msg) {
    flashSpeaking(msg.seat);
    transcriptEl.querySelector(".transcript-empty")?.remove();
    const div = document.createElement("div");
    div.className = "utt";
    const avatar = document.createElement("img");
    avatar.className = "utt-avatar";
    avatar.src = spriteForSeat(msg.seat);
    avatar.alt = "";
    const body = document.createElement("div");
    body.className = "utt-body";
    const heading = document.createElement("div");
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = msg.seat;
    heading.appendChild(who);
    if (msg.context) {
      const ctx = document.createElement("span");
      ctx.className = "ctx";
      ctx.textContent = ` (${msg.context})`;
      heading.appendChild(ctx);
    }
    const text = document.createElement("div");
    text.className = "utt-text";
    text.textContent = msg.text || "";
    body.append(heading, text);
    div.append(avatar, body);
    transcriptEl.appendChild(div);
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
    if (msg.audio_url) A.enqueue(msg.audio_url);
  }

  // ------------------------------------------------------------------
  // Panneaux de saisie
  // ------------------------------------------------------------------
  function onRequestInput(msg) {
    inputPanel.classList.remove("hidden");
    inputControls.innerHTML = "";
    startCountdown(inputTimer, msg.deadline, (h) => (inputCountdown = h), "À vous : ");

    if (msg.mode === "answer" || msg.mode === "reply") {
      buildSpeakPanel((payload) => {
        ws.send(JSON.stringify({ type: "audio_blob", ...payload }));
        hideInput();
      });
    } else if (msg.mode === "deliberation") {
      buildDeliberationPanel(msg.targets || []);
    } else if (msg.mode === "vote") {
      buildVotePanel(msg.targets || []);
    }
  }

  // Textarea + bouton d'enregistrement micro + envoyer.
  function buildSpeakPanel(onSend, extraNode) {
    const ta = document.createElement("textarea");
    ta.placeholder = "Tapez… ou utilisez le micro";
    const recBtn = mkBtn("● Micro", null, "rec");
    let recording = false;
    recBtn.addEventListener("click", async () => {
      if (!recording) {
        const ok = await A.startRecording();
        if (ok) { recording = true; recBtn.textContent = "■ Stop"; }
      } else {
        recBtn.textContent = "● Micro";
        recording = false;
        const b64 = await A.stopRecording();
        recBtn.dataset.audio = b64 || "";
      }
    });
    const sendBtn = mkBtn("Envoyer", () =>
      onSend({ audio_b64: recBtn.dataset.audio || null, text: ta.value.trim() })
    );
    if (extraNode) inputControls.appendChild(extraNode);
    inputControls.append(ta, recBtn, sendBtn);
    return { textarea: ta, recBtn };
  }

  function buildDeliberationPanel(targets) {
    const select = document.createElement("select");
    for (const t of targets) {
      const opt = document.createElement("option");
      opt.value = t; opt.textContent = t;
      select.appendChild(opt);
    }
    const { } = buildSpeakPanel((payload) => {
      ws.send(JSON.stringify({
        type: "direct_question", target: select.value,
        audio_b64: payload.audio_b64, text: payload.text,
      }));
      hideInput();
    }, select);
    // Renomme "Envoyer" et ajoute "Passer".
    const btns = inputControls.querySelectorAll("button");
    btns[btns.length - 1].textContent = "Poser la question";
    const passBtn = mkBtn("Passer", () => {
      ws.send(JSON.stringify({ type: "direct_question", target: "", text: "" }));
      hideInput();
    }, "secondary");
    inputControls.appendChild(passBtn);
  }

  function buildVotePanel(targets) {
    const label = document.createElement("span");
    label.textContent = "Désignez le siège que vous pensez être une IA : ";
    inputControls.appendChild(label);
    const options = document.createElement("div");
    options.className = "vote-options";
    for (const t of targets) {
      const btn = mkBtn("", () => {
        ws.send(JSON.stringify({ type: "submit_vote", target: t }));
        hideInput();
      }, "vote-card");
      const avatar = document.createElement("img");
      avatar.src = spriteForSeat(t);
      avatar.alt = "";
      const name = document.createElement("span");
      name.textContent = t;
      btn.append(avatar, name);
      options.appendChild(btn);
    }
    inputControls.appendChild(options);
  }

  function onVoteResult(msg) {
    const parts = Object.entries(msg.tally).map(([k, v]) => `${k}: ${v}`);
    addLog("Votes — " + (parts.join(", ") || "aucun") +
      (msg.eliminated ? ` → ${msg.eliminated} désigné.` : ""));
  }

  function onElimination(msg) {
    markDead(msg.seat, msg.role);
  }

  function onGameOver(msg) {
    hideInput();
    phaseName.textContent = "Fin";
    phaseTimer.textContent = "";
    seats = seats.map((s) => ({ ...s, role: msg.roles[s.id] }));
    renderSeats();
    const banner = document.createElement("div");
    banner.className = "winner";
    const winners = msg.winners || [];
    banner.textContent = winners.length === 1
      ? `${winners[0]} remporte la partie !`
      : winners.length > 1
        ? `${winners.join(", ")} terminent ex æquo.`
        : "Partie terminée.";
    phasePrompt.textContent = "";
    transcriptEl.parentElement.insertBefore(banner, transcriptEl.parentElement.firstChild);
  }

  // ------------------------------------------------------------------
  // Utilitaires
  // ------------------------------------------------------------------
  function hideInput() {
    inputPanel.classList.add("hidden");
    inputControls.innerHTML = "";
    if (inputCountdown) { clearInterval(inputCountdown); inputCountdown = null; }
  }

  function startCountdown(el, seconds, store, prefix = "") {
    if (typeof seconds !== "number") { el.textContent = ""; return; }
    let remaining = Math.round(seconds);
    const tick = () => {
      el.textContent = prefix + (remaining > 0 ? `${remaining}s` : "…");
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

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
})();
