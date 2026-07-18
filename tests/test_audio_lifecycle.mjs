import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const audioSource = await readFile(new URL("../web/audio.js", import.meta.url), "utf8");

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function makeStream() {
  const track = {
    stopCalls: 0,
    stop() { this.stopCalls += 1; },
  };
  return { stream: { getTracks: () => [track] }, track };
}

function loadAudio({ getUserMedia, mimeType = "audio/webm;codecs=opus" }) {
  class FakeMediaRecorder {
    constructor(stream) {
      this.stream = stream;
      this.mimeType = mimeType;
      this.state = "inactive";
      this.listeners = new Map();
    }

    addEventListener(type, callback) {
      const callbacks = this.listeners.get(type) || [];
      callbacks.push(callback);
      this.listeners.set(type, callbacks);
    }

    dispatch(type) {
      for (const callback of this.listeners.get(type) || []) callback({ type });
    }

    start() {
      this.state = "recording";
    }

    stop() {
      if (this.state === "inactive") throw new Error("already stopped");
      this.state = "inactive";
      queueMicrotask(() => {
        this.ondataavailable?.({
          data: new Blob(["voice"], { type: this.mimeType }),
        });
        this.dispatch("stop");
      });
    }
  }

  const context = {
    Audio: class {},
    Blob,
    MediaRecorder: FakeMediaRecorder,
    Uint8Array,
    btoa: (value) => Buffer.from(value, "binary").toString("base64"),
    console,
    navigator: { mediaDevices: { getUserMedia } },
    queueMicrotask,
    window: {},
  };
  vm.runInNewContext(audioSource, context, { filename: "web/audio.js" });
  return context.window.ImpostralAudio;
}

test("stopRecording returns the real MIME type and releases every track", async () => {
  const { stream, track } = makeStream();
  const audio = loadAudio({
    getUserMedia: async () => stream,
    mimeType: "audio/mp4;codecs=mp4a.40.2",
  });

  assert.equal(await audio.startRecording(), true);
  assert.equal(audio.isRecording(), true);
  const result = await audio.stopRecording();

  assert.equal(result.audio_mime, "audio/mp4;codecs=mp4a.40.2");
  assert.equal(result.audio_b64, Buffer.from("voice").toString("base64"));
  assert.equal(track.stopCalls, 1);
  assert.equal(audio.isRecording(), false);
});

test("cancelRecording discards an active capture and releases its track", async () => {
  const { stream, track } = makeStream();
  const audio = loadAudio({ getUserMedia: async () => stream });

  assert.equal(await audio.startRecording(), true);
  audio.cancelRecording();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(track.stopCalls, 1);
  assert.equal(audio.isRecording(), false);
  assert.equal(await audio.stopRecording(), null);
});

test("a permission request resolved after cancellation never starts recording", async () => {
  const permission = deferred();
  const { stream, track } = makeStream();
  const audio = loadAudio({ getUserMedia: () => permission.promise });

  const starting = audio.startRecording();
  audio.cancelRecording();
  permission.resolve(stream);

  assert.equal(await starting, false);
  assert.equal(track.stopCalls, 1);
  assert.equal(audio.isRecording(), false);
});
