// Browser API/MediaRecorder doubles prove contracts, not actual Chrome/Edge capture.
"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const context = vm.createContext({setTimeout, clearTimeout, btoa, Uint8Array, performance});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../recording-core.js"), "utf8"), context);
const {NativeUploader, CaptureSession, CHUNK_BYTES, mimeChoices, captureOptions} = context.OpenDownloaderRecording;
const tick = () => new Promise(resolve => setImmediate(resolve));
class Event {
  constructor() {this.listeners = [];}
  addListener(listener) {this.listeners.push(listener);}
  fire(value) {for (const listener of this.listeners) listener(value);}
}
function host({manualChunks = false, badAck = false} = {}) {
  const id = "1".repeat(32), sent = [], waiting = [], port = {onMessage: new Event(), onDisconnect: new Event(), disconnect() {this.disconnected = true; this.onDisconnect.fire();}};
  let bytes = 0, connects = 0;
  function reply(body) {
    let result = {version: 1, ok: true, recording_id: id};
    if (body.command === "recording.start") result = {...result, max_chunk_bytes: CHUNK_BYTES, sequence: 0};
    if (body.command === "recording.chunk") {bytes += Buffer.from(body.data, "base64").length; result = {...result, sequence: body.sequence, bytes: bytes + (badAck ? 1 : 0)};}
    if (body.command === "recording.finish") result = {...result, status: "ready", bytes, sha256: "a".repeat(64)};
    if (body.command === "recording.abort") result = {...result, status: "interrupted", bytes};
    queueMicrotask(() => port.onMessage.fire(result));
  }
  port.postMessage = body => {sent.push(body); if (body.command === "recording.chunk" && manualChunks) waiting.push(body); else reply(body);};
  return {sent, waiting, port, reply: () => reply(waiting.shift()), runtime: {connectNative: name => {assert.equal(name, "app.lume.downloads"); connects++; return port;}}, get connects() {return connects;}};
}
function track(kind) {const value = {kind, stopped: 0, events: {}, addEventListener(name, listener) {this.events[name] = listener;}, stop() {this.stopped++;}}; return value;}
function stream(audio = true) {const tracks = [track("video"), ...(audio ? [track("audio")] : [])]; return {getTracks: () => tracks, getVideoTracks: () => tracks.filter(t => t.kind === "video"), getAudioTracks: () => tracks.filter(t => t.kind === "audio")};}
class Recorder {
  static isTypeSupported(mime) {return mime.includes("webm");}
  constructor(input, {mimeType}) {this.stream = input; this.mimeType = mimeType; this.state = "inactive"; this.events = {}; Recorder.last = this;}
  addEventListener(name, listener) {this.events[name] = listener;}
  start(timeslice) {assert.equal(timeslice, 1000); this.state = "recording";}
  pause() {this.state = "paused";}
  resume() {this.state = "recording";}
  stop() {this.state = "inactive"; queueMicrotask(() => {this.events.dataavailable?.({data: new Blob(["final-data"])}); this.events.stop?.();});}
}
function sessionHarness({audio = true, failCapture = false, manualChunks = false, uploaderOptions = {}, RecorderClass = Recorder, timing} = {}) {
  const native = host({manualChunks}), source = stream(audio), states = [], options = [], previews = [], diagnostics = [];
  const capture = new CaptureSession({Recorder: RecorderClass, runtime: native.runtime, mediaDevices: {getDisplayMedia: value => {options.push(value); return failCapture ? Promise.reject(new Error("picker cancelled")) : Promise.resolve(source);}}, onState: (state, detail) => states.push({state, detail}), onStream: value => previews.push(value), onDiagnostic: (code, message) => diagnostics.push({code, message}), uploaderOptions, ...(timing ? {timing} : {})});
  return {native, source, states, options, previews, diagnostics, capture};
}
test("capture options request display audio only; formats reflect runtime support", () => {
  assert.deepEqual(JSON.parse(JSON.stringify(captureOptions("720"))), {video: {frameRate: {ideal: 30, max: 60}, width: {max: 1280}, height: {max: 720}}, audio: true, selfBrowserSurface: "exclude", systemAudio: "include"});
  assert.equal(captureOptions().video.width, undefined);
  assert.equal(mimeChoices(Recorder).length, 3);
});
test("36 KiB chunks are ACK ordered and finish waits for every byte", async () => {
  const native = host({manualChunks: true}), uploader = new NativeUploader(native.runtime);
  await uploader.start("video/webm", "Test", true);
  uploader.enqueue(new Blob([Buffer.alloc(CHUNK_BYTES * 2 + 3, 7)]));
  const finished = uploader.finish(); await tick();
  assert.equal(native.waiting.length, 1); assert.equal(native.sent.some(s => s.command === "recording.finish"), false);
  for (let i = 0; i < 3; i++) {assert.equal(native.waiting.length, 1); native.reply(); await tick();}
  const result = await finished;
  const chunks = native.sent.filter(s => s.command === "recording.chunk");
  assert.deepEqual(chunks.map(s => Buffer.from(s.data, "base64").length), [CHUNK_BYTES, CHUNK_BYTES, 3]);
  assert.deepEqual(chunks.map(s => s.sequence), [0, 1, 2]);
  assert.equal(result.bytes, CHUNK_BYTES * 2 + 3); assert.equal(native.port.disconnected, true);
});
test("corrupt cumulative ACK fails closed", async () => {
  const native = host({badAck: true}), uploader = new NativeUploader(native.runtime);
  await uploader.start("video/mp4", "Test", true); uploader.enqueue(new Blob(["x"]));
  await assert.rejects(uploader.finish(), /confirmação dos dados/); uploader.abort();
  assert.equal(native.sent.some(s => s.command === "recording.finish"), false);
});
test("bounded pending queue interrupts instead of dropping data or claiming complete", async () => {
  const native = host({manualChunks: true}); let failure;
  const uploader = new NativeUploader(native.runtime, {maxPending: 4, onFailure: error => {failure = error;}});
  await uploader.start("video/webm", "Test", true); uploader.enqueue(new Blob(["1234"])); uploader.enqueue(new Blob(["5"]));
  assert.match(failure.message, /32 MiB/); await assert.rejects(uploader.finish(), /interrompida/); uploader.abort();
  assert.equal(native.sent.some(s => s.command === "recording.finish"), false);
});
test("picker cancellation never connects native host", async () => {
  const h = sessionHarness({failCapture: true}); await assert.rejects(h.capture.capture("source"), /picker cancelled/);
  assert.equal(h.native.connects, 0); assert.equal(h.capture.state, "interrupted");
});
test("no-audio selection waits for explicit continue or cancellation", async () => {
  const h = sessionHarness({audio: false}); assert.equal(await h.capture.capture("1080"), false);
  assert.equal(h.native.connects, 0); assert.equal(h.capture.state, "selected"); h.capture.interrupt();
  assert.equal(h.native.connects, 0); assert.equal(h.source.getTracks().every(t => t.stopped > 0), true);
});
test("normal stop includes late final dataavailable before ready receipt", async () => {
  const h = sessionHarness(); await h.capture.capture("source"); await h.capture.begin("video/webm", "Name");
  h.capture.pause(); assert.equal(h.capture.state, "paused"); h.capture.resume(); h.capture.stop();
  await tick(); await tick();
  assert.equal(h.capture.state, "ready"); assert.equal(h.states.at(-1).detail.bytes, 10);
  assert.equal(h.source.getTracks().every(t => t.stopped > 0), true);
  assert.equal(h.native.sent.filter(s => s.command === "recording.finish").length, 1);
  h.capture.finalize(); await tick(); assert.equal(h.native.sent.filter(s => s.command === "recording.finish").length, 1);
});
test("capture revocation stops recorder and all tracks, finalizes once", async () => {
  const h = sessionHarness(); await h.capture.capture("source"); await h.capture.begin("video/webm", "Name");
  h.source.getAudioTracks()[0].events.ended(); await tick(); await tick();
  assert.equal(h.capture.state, "ready"); assert.equal(h.source.getTracks().every(t => t.stopped > 0), true);
});
test("recording error and page close preserve partial without finish despite late recorder events", async () => {
  for (const kind of ["error", "close"]) {
    const h = sessionHarness(); await h.capture.capture("source"); await h.capture.begin("video/webm", "Name");
    if (kind === "error") Recorder.last.events.error({error: new Error("encoder failed")}); else h.capture.interrupt();
    await tick(); await tick(); assert.equal(h.capture.state, "interrupted");
    assert.equal(h.native.sent.some(s => s.command === "recording.finish"), false);
    assert.equal(h.source.getTracks().every(t => t.stopped > 0), true); assert.equal(h.native.port.disconnected, true);
  }
});
test("late picker resolution after closing releases capture without native state", async () => {
  const native = host(), source = stream(); let resolve;
  const capture = new CaptureSession({Recorder, runtime: native.runtime, mediaDevices: {getDisplayMedia: () => new Promise(done => {resolve = done;})}});
  const pending = capture.capture("source"); capture.interrupt(); resolve(source);
  await assert.rejects(pending); assert.equal(native.connects, 0); assert.equal(source.getTracks().every(t => t.stopped > 0), true);
});
test("disconnect while finalizing rejects wait and never becomes ready", async () => {
  const h = sessionHarness({manualChunks: true}); await h.capture.capture("source"); await h.capture.begin("video/webm", "Name");
  h.capture.stop(); await tick(); assert.equal(h.capture.state, "finalizing"); h.native.port.onDisconnect.fire(); await tick();
  assert.equal(h.capture.state, "interrupted"); assert.equal(h.states.some(s => s.state === "ready"), false);
});
test("explicit silent continue marks audio false and keeps supported MIME", async () => {
  const h = sessionHarness({audio: false}); await h.capture.capture("source"); await h.capture.begin("video/webm;codecs=vp8,opus", "🎬".repeat(300));
  const started = h.native.sent[0]; assert.equal(started.audio, false); assert.equal(started.mime, "video/webm;codecs=vp8,opus");
  assert.equal([...started.title].length, 256); h.capture.stop(); await tick(); await tick(); assert.equal(h.capture.state, "ready");
});
test("pending ACK timeout stops capture and retains partial status", async () => {
  const h = sessionHarness({manualChunks: true, uploaderOptions: {timeout: 10}}); await h.capture.capture("source"); await h.capture.begin("video/webm", "Test");
  Recorder.last.events.dataavailable({data: new Blob(["frame"])});
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(h.capture.state, "interrupted"); assert.match(h.states.at(-1).detail.message, /arquivo parcial/);
  assert.equal(h.source.getTracks().every(t => t.stopped > 0), true);
  // A late ACK after disconnect cannot resurrect or finalize this capture.
  h.native.reply(); await tick(); assert.equal(h.capture.state, "interrupted"); assert.equal(h.native.sent.some(s => s.command === "recording.finish"), false);
});
test("backpressure stops MediaRecorder rather than pausing and dropping intervals", async () => {
  const h = sessionHarness({manualChunks: true, uploaderOptions: {maxPending: 4}}); await h.capture.capture("source"); await h.capture.begin("video/webm", "Test");
  Recorder.last.events.dataavailable({data: new Blob(["12345"])}); await tick();
  assert.equal(h.capture.state, "interrupted"); assert.equal(Recorder.last.state, "inactive"); assert.equal(h.native.sent.some(s => s.command === "recording.finish"), false);
});

function clockHarness() {
  let now = 0, next = 0;
  const timers = new Map();
  return {now: () => now, setTimeout(callback, delay) {const id = ++next; timers.set(id, {callback, due: now + delay}); return id;}, clearTimeout(id) {timers.delete(id);}, advance(ms) {now += ms; for (const [id, timer] of [...timers]) if (timer.due <= now) {timers.delete(id); timer.callback();}}, get count() {return timers.size;}};
}

test("active recording limit excludes pause and waits for final chunk ACK", async () => {
  const timing = clockHarness(), h = sessionHarness({timing, manualChunks: true});
  await h.capture.capture("source"); await h.capture.begin("video/webm", "Limited", {maxDurationMs: 1000});
  timing.advance(400); h.capture.pause(); assert.equal(h.capture.elapsed(), 400); assert.equal(timing.count, 0);
  timing.advance(9000); assert.equal(h.capture.elapsed(), 400); assert.equal(h.capture.state, "paused");
  h.capture.resume(); timing.advance(599); assert.equal(h.capture.state, "recording");
  timing.advance(1); assert.equal(h.capture.state, "finalizing"); assert.equal(h.capture.elapsed(), 1000);
  assert.equal(h.diagnostics.at(-1).code, "duration-limit"); await tick();
  assert.equal(h.native.sent.some(value => value.command === "recording.finish"), false);
  assert.equal(h.native.waiting.length, 1); h.native.reply(); await tick(); await tick();
  assert.equal(h.capture.state, "ready"); assert.equal(h.states.at(-1).detail.bytes, 10); assert.equal(timing.count, 0);
});

test("manual interruption clears limit and an automatic finalization cannot revive after disconnect", async () => {
  for (const manual of [true, false]) {
    const timing = clockHarness(), h = sessionHarness({timing, manualChunks: true});
    await h.capture.capture("source"); await h.capture.begin("video/webm", "Limit", {maxDurationMs: 1000});
    if (manual) h.capture.interrupt();
    else {timing.advance(1000); await tick(); h.native.port.onDisconnect.fire();}
    await tick(); timing.advance(10000); await tick();
    assert.equal(h.capture.state, "interrupted"); assert.equal(timing.count, 0);
    assert.equal(h.native.sent.some(value => value.command === "recording.finish"), false);
  }
});

test("selected preview is released exactly once on normal save, failure, or silent cancellation", async () => {
  for (const kind of ["save", "failure", "silent"]) {
    const h = sessionHarness({audio: kind !== "silent"}); await h.capture.capture("source");
    assert.equal(h.previews[0], h.source);
    if (kind === "silent") h.capture.interrupt();
    else {await h.capture.begin("video/webm", "Preview"); if (kind === "save") h.capture.stop(); else h.capture.interrupt();}
    await tick(); await tick();
    assert.equal(h.previews.length, 2); assert.equal(h.previews[1], null); assert.equal(h.capture.stream, null);
    assert.equal(h.source.getTracks().every(value => value.stopped > 0), true);
  }
});

test("throwing preview observer never owns start, complete save, or native abort", async () => {
  for (const finish of [true, false]) {
    const h = sessionHarness();
    h.capture.onStream = () => {throw new Error("preview DOM unavailable");};
    assert.equal(await h.capture.capture("source"), true);
    assert.equal(h.diagnostics[0].code, "preview-unavailable");
    await h.capture.begin("video/webm", "Preview observer");
    if (finish) h.capture.stop(); else h.capture.interrupt();
    await tick(); await tick();
    assert.equal(h.capture.state, finish ? "ready" : "interrupted");
    assert.equal(h.native.port.disconnected, true); assert.equal(h.capture.stream, null);
    assert.equal(h.source.getTracks().every(value => value.stopped > 0), true);
    assert.equal(h.native.sent.some(value => value.command === "recording.finish"), finish);
  }
});

test("audio diagnostics identify missing and ended tracks without asserting encrypted content", async () => {
  const silent = sessionHarness({audio: false}); await silent.capture.capture("source");
  assert.equal(silent.diagnostics[0].code, "audio-missing"); assert.equal(silent.native.connects, 0); silent.capture.interrupt();
  for (const paused of [true, false]) {
    const timing = clockHarness(), h = sessionHarness({timing}); await h.capture.capture("source"); await h.capture.begin("video/webm", "Audio", {maxDurationMs: 1000});
    timing.advance(100); if (paused) h.capture.pause();
    h.source.getAudioTracks()[0].events.ended(); await tick(); await tick();
    assert.equal(h.diagnostics[0].code, "audio-ended"); assert.equal(h.capture.state, "ready");
    assert.equal(h.capture.elapsed(), 100); assert.equal(timing.count, 0);
    assert.equal(/DRM|criptograf|protegido/i.test(h.diagnostics[0].message), false);
  }
});

test("bitrate presets are requested only with runtime support and no audio bitrate for silent capture", async () => {
  class RateRecorder extends Recorder {
    get videoBitsPerSecond() {return this.options.videoBitsPerSecond;}
    get audioBitsPerSecond() {return this.options.audioBitsPerSecond;}
    constructor(input, options) {super(input, options); this.options = options;}
  }
  for (const audio of [true, false]) {
    const h = sessionHarness({audio, RecorderClass: RateRecorder}); await h.capture.capture("source");
    await h.capture.begin("video/webm", "Quality", {bitrate: "high"});
    assert.equal(Recorder.last.options.videoBitsPerSecond, 12000000);
    assert.equal(Recorder.last.options.audioBitsPerSecond, audio ? 192000 : undefined); h.capture.interrupt();
  }
  const h = sessionHarness(); await h.capture.capture("source"); await h.capture.begin("video/webm", "Quality", {bitrate: "balanced"});
  assert.equal(h.diagnostics[0].code, "bitrate-unavailable"); h.capture.interrupt();
});

test("unsupported bitrate falls back once; resource errors remain explicit failures", async () => {
  for (const name of ["NotSupportedError", "TypeError", "UnknownError"]) {
    let calls = 0;
    class RejectRate extends Recorder {
      get videoBitsPerSecond() {return 0;}
      constructor(input, options) {calls++; if (options.videoBitsPerSecond) {const error = new Error("recorder refused"); error.name = name; throw error;} super(input, options);}
    }
    const h = sessionHarness({RecorderClass: RejectRate}); await h.capture.capture("source");
    if (name === "UnknownError") {await assert.rejects(h.capture.begin("video/webm", "Quality", {bitrate: "high"}), /refused/); assert.equal(calls, 1); assert.equal(h.native.connects, 0);}
    else {await h.capture.begin("video/webm", "Quality", {bitrate: "high"}); assert.equal(calls, 2); assert.equal(h.diagnostics[0].code, "bitrate-fallback"); h.capture.interrupt();}
  }
});

test("unsupported MIME and invalid duration never create native recording", async () => {
  for (const options of [{maxDurationMs: -1}, {maxDurationMs: Infinity}, {maxDurationMs: 86400001}, {maxDurationMs: 0.5}, {bitrate: "inherited"}]) {
    const h = sessionHarness(); await h.capture.capture("source");
    await assert.rejects(h.capture.begin("video/webm", "Invalid", options), /inválida/); assert.equal(h.native.connects, 0); h.capture.interrupt();
  }
  const h = sessionHarness(); await h.capture.capture("source"); await assert.rejects(h.capture.begin("video/mp4", "Unsupported"), /não é suportado/);
  assert.equal(h.native.connects, 0); assert.equal(h.capture.state, "interrupted"); assert.equal(h.previews.at(-1), null);
});

test("recorder page preview survives synchronous DOM errors and ignores late playback rejection", async () => {
  for (const mode of ["async-play", "sync-play", "pause-error"]) {
  let configuration, rejectPlay;
  const elements = new Map(), selected = {};
  function element() {return {value: "", hidden: true, events: {}, classList: {toggle() {}}, addEventListener(name, listener) {this.events[name] = listener;}, append() {}, pause() {if (mode === "pause-error") throw new Error("detached");}, play() {if (mode === "sync-play") throw new Error("playback unavailable"); return new Promise((_, reject) => {rejectPlay = reject;});}};}
  const ui = vm.createContext({OpenDownloaderRecording: {mimeChoices: () => ["video/webm"], CaptureSession: class {
    constructor(options) {configuration = options;}
    async capture() {configuration.onStream(selected); return false;}
    elapsed() {return 0;}
  }}, MediaRecorder: Recorder, navigator: {mediaDevices: {getDisplayMedia() {}}}, chrome: {runtime: {}}, document: {getElementById(id) {if (!elements.has(id)) elements.set(id, element()); return elements.get(id);}, createElement: element}, window: {addEventListener() {}}, setInterval() {}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../recorder.js"), "utf8"), ui);
  await elements.get("start").events.click();
  assert.equal(elements.get("preview").srcObject, selected); assert.equal(elements.get("preview").muted, true); assert.equal(elements.get("preview-panel").hidden, false);
  configuration.onStream(null); rejectPlay?.(new Error("preview closed")); await tick();
  assert.equal(elements.get("preview").srcObject, null); assert.equal(elements.get("preview-panel").hidden, true); assert.equal(elements.get("diagnostic").hidden, mode !== "sync-play");
  }
});

test("selected crop reaches MediaRecorder and is released with the capture", async () => {
  const h = sessionHarness(); await h.capture.capture("source");
  let releases = 0; const croppedVideo = track("video");
  const output = {stream: {getVideoTracks: () => [croppedVideo], getAudioTracks: () => h.source.getAudioTracks()}, release() {releases++; croppedVideo.stop();}};
  h.capture.setVideoOutput(output); await h.capture.begin("video/webm", "Cropped");
  assert.equal(Recorder.last.stream, output.stream); h.capture.interrupt(); h.capture.release();
  assert.equal(releases, 1); assert.equal(croppedVideo.stopped, 1); assert.equal(h.source.getAudioTracks()[0].stopped, 1);
});
