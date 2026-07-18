// Capture micro (push-to-talk) + lecture de la file audio TTS.
// Exposé globalement via window.ImpostralAudio.

(function () {
  let mediaRecorder = null;
  let chunks = [];
  let stream = null;

  async function ensureStream() {
    if (stream) return stream;
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return stream;
  }

  // Démarre l'enregistrement. Renvoie true si OK.
  async function startRecording() {
    try {
      const s = await ensureStream();
      chunks = [];
      mediaRecorder = new MediaRecorder(s);
      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunks.push(e.data);
      };
      mediaRecorder.start();
      return true;
    } catch (err) {
      console.warn("Micro indisponible :", err);
      return false;
    }
  }

  // Stoppe et renvoie l'audio encodé base64 (sans préfixe data:).
  function stopRecording() {
    return new Promise((resolve) => {
      if (!mediaRecorder || mediaRecorder.state === "inactive") {
        resolve(null);
        return;
      }
      mediaRecorder.onstop = async () => {
        const blob = new Blob(chunks, { type: "audio/webm" });
        const buf = await blob.arrayBuffer();
        let binary = "";
        const bytes = new Uint8Array(buf);
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        resolve(btoa(binary));
      };
      mediaRecorder.stop();
    });
  }

  function isRecording() {
    return mediaRecorder && mediaRecorder.state === "recording";
  }

  // --- File de lecture audio (les clips TTS ne se chevauchent pas) ---
  const queue = [];
  let playing = false;

  function enqueue(url) {
    if (!url) return;
    queue.push(url);
    pump();
  }

  function pump() {
    if (playing || queue.length === 0) return;
    playing = true;
    const url = queue.shift();
    const audio = new Audio(url);
    audio.onended = audio.onerror = () => {
      playing = false;
      pump();
    };
    audio.play().catch(() => {
      playing = false;
      pump();
    });
  }

  window.ImpostralAudio = { startRecording, stopRecording, isRecording, enqueue };
})();
