// Push-to-talk microphone capture and queued TTS playback.
// Exposed globally through window.ImpostralAudio.

(function () {
  let mediaRecorder = null;
  let chunks = [];
  let stream = null;

  async function ensureStream() {
    if (stream) return stream;
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return stream;
  }

  // Start recording. Returns true on success.
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
      console.warn("Microphone unavailable:", err);
      return false;
    }
  }

  // Stop and return base64-encoded audio without a data URL prefix.
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

  // --- Audio playback queue: TTS clips never overlap ---
  const queue = [];
  let playing = false;

  function enqueue(url, onComplete) {
    if (!url) {
      if (onComplete) onComplete();
      return;
    }
    queue.push({ url, onComplete });
    pump();
  }

  function pump() {
    if (playing || queue.length === 0) return;
    playing = true;
    const { url, onComplete } = queue.shift();
    const audio = new Audio(url);
    let completed = false;
    const finish = () => {
      if (completed) return;
      completed = true;
      playing = false;
      if (onComplete) onComplete();
      pump();
    };
    audio.onended = audio.onerror = finish;
    audio.play().catch(finish);
  }

  window.ImpostralAudio = { startRecording, stopRecording, isRecording, enqueue };
})();
