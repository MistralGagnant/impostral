// Push-to-talk microphone capture and queued TTS playback.
// Exposed globally through window.ImpostralAudio.

(function () {
  let capture = null;
  let captureGeneration = 0;

  function releaseStream(stream) {
    if (!stream) return;
    for (const track of stream.getTracks()) {
      try { track.stop(); } catch { /* The track may already be stopped. */ }
    }
  }

  function toBase64(buffer) {
    let binary = "";
    const bytes = new Uint8Array(buffer);
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }

  // Invalidate pending permission requests and discard any active capture.
  function cancelRecording() {
    captureGeneration += 1;
    const current = capture;
    capture = null;
    if (!current) return;

    current.discarded = true;
    releaseStream(current.stream);
    if (current.recorder.state !== "inactive") {
      try { current.recorder.stop(); } catch { /* It may have stopped meanwhile. */ }
    }
    current.chunks.length = 0;
  }

  // Start recording. Returns true on success.
  async function startRecording() {
    cancelRecording();
    const generation = ++captureGeneration;
    let stream = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (generation !== captureGeneration) {
        releaseStream(stream);
        return false;
      }

      const recorder = new MediaRecorder(stream);
      const current = {
        stream,
        recorder,
        chunks: [],
        mimeType: recorder.mimeType || "",
        discarded: false,
        stopPromise: null,
      };
      recorder.ondataavailable = (event) => {
        if (!current.discarded && event.data && event.data.size > 0) {
          current.chunks.push(event.data);
        }
      };
      capture = current;
      recorder.start();
      return true;
    } catch (err) {
      releaseStream(stream);
      if (generation !== captureGeneration) return false;
      capture = null;
      console.warn("Microphone unavailable:", err);
      return false;
    }
  }

  // Stop, release the microphone, and preserve the browser's actual audio type.
  function stopRecording() {
    const current = capture;
    if (!current || current.recorder.state === "inactive") {
      if (current) {
        releaseStream(current.stream);
        capture = null;
      }
      return Promise.resolve(null);
    }
    if (current.stopPromise) return current.stopPromise;

    current.stopPromise = new Promise((resolve) => {
      let settled = false;
      const finish = async (failed = false) => {
        if (settled) return;
        settled = true;
        try {
          if (failed || current.discarded) {
            resolve(null);
            return;
          }
          const chunkType = current.chunks.find((chunk) => chunk.type)?.type || "";
          const audioMime = current.mimeType || chunkType;
          const blob = new Blob(current.chunks, audioMime ? { type: audioMime } : {});
          if (!blob.size) {
            resolve(null);
            return;
          }
          resolve({
            audio_b64: toBase64(await blob.arrayBuffer()),
            audio_mime: audioMime || null,
          });
        } catch (err) {
          console.warn("Could not finalize microphone recording:", err);
          resolve(null);
        } finally {
          current.discarded = true;
          releaseStream(current.stream);
          current.chunks.length = 0;
          if (capture === current) capture = null;
        }
      };

      current.recorder.addEventListener("stop", () => { void finish(); }, { once: true });
      current.recorder.addEventListener("error", () => { void finish(true); }, { once: true });
      try {
        current.recorder.stop();
      } catch {
        void finish(true);
      }
    });
    return current.stopPromise;
  }

  function isRecording() {
    return Boolean(capture && capture.recorder.state === "recording");
  }

  // --- Audio playback queue: TTS clips never overlap ---
  const queue = [];
  let playing = false;
  let playbackRate = 1.1;

  function setPlaybackRate(rate) {
    const parsed = Number(rate);
    if (Number.isFinite(parsed)) playbackRate = Math.min(2, Math.max(0.5, parsed));
  }

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
    audio.playbackRate = playbackRate;
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

  window.ImpostralAudio = {
    startRecording, stopRecording, cancelRecording, isRecording, enqueue, setPlaybackRate,
  };
})();
