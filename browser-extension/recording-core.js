"use strict";
(function (root) {
  const CHUNK_BYTES = 36864, MAX_PENDING_BYTES = 32 * 1024 * 1024;
  const MIME_TYPES = ["video/mp4;codecs=avc1.42E01E,mp4a.40.2", "video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm", "video/mp4"];
  const BITRATES = Object.freeze({auto: null, economy: {video: 2500000, audio: 96000}, balanced: {video: 6000000, audio: 128000}, high: {video: 12000000, audio: 192000}});
  function mimeChoices(Recorder) {return MIME_TYPES.filter(mime => Recorder.isTypeSupported(mime));}
  function captureOptions(quality = "source") {
    const video = {frameRate: {ideal: 30, max: 60}};
    if (quality === "720") {video.width = {max: 1280}; video.height = {max: 720};}
    if (quality === "1080") {video.width = {max: 1920}; video.height = {max: 1080};}
    return {video, audio: true, selfBrowserSurface: "exclude", systemAudio: "include"};
  }
  function base64(bytes) {
    let value = "";
    for (let i = 0; i < bytes.length; i++) value += String.fromCharCode(bytes[i]);
    return btoa(value);
  }
  class NativeUploader {
    constructor(runtime, {timeout = 30000, maxPending = MAX_PENDING_BYTES, onProgress = () => {}, onFailure = () => {}} = {}) {
      this.runtime = runtime; this.timeout = timeout; this.maxPending = maxPending;
      this.onProgress = onProgress; this.onFailure = onFailure; this.pendingBytes = 0;
      this.bytes = 0; this.sequence = 0; this.tail = Promise.resolve(); this.failed = null; this.closed = false;
    }
    async start(mime, title, audio) {
      this.port = this.runtime.connectNative("app.lume.downloads");
      this.port.onMessage.addListener(value => {
        if (this.closed) return;
        const waiter = this.waiter;
        if (!waiter) {this.fail(new Error("A ponte enviou uma resposta inesperada.")); return;}
        this.waiter = null; clearTimeout(waiter.timer);
        if (!value || value.ok !== true || value.version !== 1) {
          const reason = typeof value?.error === "string" ? value.error : value?.error?.message;
          waiter.reject(new Error(reason || "A ponte recusou a gravação.")); return;
        }
        waiter.resolve(value);
      });
      this.port.onDisconnect.addListener(() => {
        const detail = this.runtime.lastError?.message;
        if (!this.closed) this.fail(new Error(`A conexão com o aplicativo foi encerrada${detail ? `: ${detail}` : "."}`));
      });
      try {
        const cleanTitle = [...String(title)].filter(char => {const code = char.codePointAt(0); return code < 0xD800 || code > 0xDFFF;}).slice(0, 256).join("");
        const result = await this.request({version: 1, command: "recording.start", mime, title: cleanTitle, audio: Boolean(audio)});
        if (!/^[a-f0-9]{32}$/.test(result.recording_id) || result.max_chunk_bytes !== CHUNK_BYTES || result.sequence !== 0) throw new Error("Resposta de início inválida.");
        this.id = result.recording_id; return result;
      } catch (error) {this.fail(error); this.disconnect(); throw error;}
    }
    request(body) {
      if (this.failed) return Promise.reject(this.failed);
      if (this.closed || this.waiter) return Promise.reject(new Error("A conexão de gravação não está disponível."));
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => this.fail(new Error("A ponte demorou para confirmar a gravação; o arquivo parcial foi preservado.")), this.timeout);
        this.waiter = {resolve, reject, timer};
        try {this.port.postMessage(body);} catch (error) {this.fail(error);}
      });
    }
    fail(error) {
      if (this.failed) return;
      this.failed = error;
      if (this.waiter) {clearTimeout(this.waiter.timer); this.waiter.reject(error); this.waiter = null;}
      this.onFailure(error);
    }
    enqueue(blob) {
      if (!blob.size || this.failed || this.closed) return;
      if (this.pendingBytes + blob.size > this.maxPending) {
        this.fail(new Error("O aplicativo não acompanhou a gravação (limite de 32 MiB pendentes). A captura foi interrompida; o arquivo parcial foi preservado."));
        return;
      }
      this.pendingBytes += blob.size;
      this.tail = this.tail.then(async () => {
        if (this.failed) throw this.failed;
        const buffer = new Uint8Array(await blob.arrayBuffer());
        for (let start = 0; start < buffer.length; start += CHUNK_BYTES) {
          const part = buffer.subarray(start, start + CHUNK_BYTES), sequence = this.sequence;
          const result = await this.request({version: 1, command: "recording.chunk", recording_id: this.id, sequence, data: base64(part)});
          if (result.recording_id !== this.id || result.sequence !== sequence || result.bytes !== this.bytes + part.length) throw new Error("A confirmação dos dados não corresponde à gravação.");
          this.sequence++; this.bytes += part.length; this.onProgress(this.bytes);
        }
      }).catch(error => this.fail(error)).finally(() => {this.pendingBytes -= blob.size;});
    }
    async finish() {
      await this.tail;
      if (this.failed) throw this.failed;
      const result = await this.request({version: 1, command: "recording.finish", recording_id: this.id, sequence: this.sequence, bytes: this.bytes});
      if (result.recording_id !== this.id || result.status !== "ready" || result.bytes !== this.bytes || !/^[a-f0-9]{64}$/.test(result.sha256)) throw new Error("O aplicativo não confirmou o arquivo completo.");
      this.disconnect(); return result;
    }
    disconnect() {
      this.closed = true;
      if (this.waiter) {clearTimeout(this.waiter.timer); this.waiter.reject(new Error("Conexão encerrada; os dados enviados foram preservados.")); this.waiter = null;}
      try {this.port?.disconnect();} catch {}
    }
    abort() {
      if (this.closed) return;
      // Disconnect is authoritative too: the host retains owned partial data on EOF.
      if (this.id && !this.waiter) {try {this.port.postMessage({version: 1, command: "recording.abort", recording_id: this.id});} catch {}}
      this.disconnect();
    }
  }
  class CaptureSession {
    constructor({mediaDevices, Recorder, runtime, onState = () => {}, onProgress = () => {}, onDiagnostic = () => {}, onStream = () => {}, uploaderOptions = {}, timing = {now: () => performance.now(), setTimeout: (fn, delay) => setTimeout(fn, delay), clearTimeout: id => clearTimeout(id)}}) {
      this.mediaDevices = mediaDevices; this.Recorder = Recorder; this.runtime = runtime;
      this.onState = onState; this.onProgress = onProgress; this.onDiagnostic = onDiagnostic; this.onStream = onStream;
      this.uploaderOptions = uploaderOptions; this.state = "idle"; this.timing = timing; this.activeMs = 0; this.runningSince = null; this.maxDurationMs = 0;
    }
    elapsed() {return this.activeMs + (this.runningSince === null ? 0 : Math.max(0, this.timing.now() - this.runningSince));}
    preview(stream) {
      // UI preview is an observer, never the owner of capture or native cleanup.
      try {this.onStream(stream);}
      catch {try {this.onDiagnostic("preview-unavailable", "A prévia local não está disponível. A captura e o recebimento dos dados continuam independentes dela.");} catch {}}
    }
    change(state, detail) {
      if (this.runningSince !== null) {this.activeMs = this.elapsed(); this.runningSince = null;}
      if (this.durationTimer !== undefined) {this.timing.clearTimeout(this.durationTimer); this.durationTimer = undefined;}
      this.state = state;
      if (state === "recording") {
        this.runningSince = this.timing.now();
        if (this.maxDurationMs) this.durationTimer = this.timing.setTimeout(() => {
          if (this.state !== "recording") return;
          this.onDiagnostic("duration-limit", "Tempo máximo de gravação atingido. Salvando os dados finais."); this.stop();
        }, Math.max(0, this.maxDurationMs - this.elapsed()));
      }
      this.onState(state, detail);
    }
    async capture(quality) {
      if (this.state !== "idle") throw new Error("A captura já foi iniciada.");
      this.change("choosing");
      try {
        // Must be invoked directly from the visible start click. No getUserMedia/microphone.
        this.stream = await this.mediaDevices.getDisplayMedia(captureOptions(quality));
        if (!this.stream.getVideoTracks().length || this.state === "interrupted") throw new Error("Nenhuma superfície de vídeo disponível.");
        const audio = this.stream.getAudioTracks().filter(track => track.readyState !== "ended");
        for (const track of this.stream.getTracks()) track.addEventListener("ended", () => {
          if (["ready", "interrupted", "finalizing"].includes(this.state)) return;
          if (track.kind === "audio") this.onDiagnostic("audio-ended", "O compartilhamento do áudio foi encerrado. A gravação será finalizada com os dados já recebidos.");
          this.stop();
        });
        this.preview(this.stream);
        if (!audio.length) this.onDiagnostic("audio-missing", "Nenhuma faixa de áudio foi compartilhada. Escolha continuar sem áudio ou cancele para compartilhar outra aba.");
        this.change("selected"); return audio.length > 0;
      } catch (error) {this.release(); this.change("interrupted", error); throw error;}
    }
    async begin(mime, title, {bitrate = "auto", maxDurationMs = 0} = {}) {
      if (this.state !== "selected") throw new Error("Selecione uma aba ou janela antes de gravar.");
      if (!Object.hasOwn(BITRATES, bitrate) || !Number.isSafeInteger(maxDurationMs) || maxDurationMs < 0 || maxDurationMs > 86400000) throw new Error("Qualidade ou duração da gravação inválida.");
      this.maxDurationMs = maxDurationMs;
      this.change("connecting");
      try {
        if (!this.Recorder.isTypeSupported(mime)) throw new Error("O formato escolhido não é suportado por este navegador.");
        const audio = this.stream.getAudioTracks().some(track => track.readyState !== "ended");
        const options = {mimeType: mime}, preset = BITRATES[bitrate];
        if (preset && "videoBitsPerSecond" in this.Recorder.prototype) options.videoBitsPerSecond = preset.video;
        if (preset && audio && "audioBitsPerSecond" in this.Recorder.prototype) options.audioBitsPerSecond = preset.audio;
        try {this.recorder = new this.Recorder(this.recordingStream || this.stream, options);}
        catch (error) {
          if (Object.keys(options).length === 1 || !["TypeError", "NotSupportedError"].includes(error.name)) throw error;
          this.recorder = new this.Recorder(this.recordingStream || this.stream, {mimeType: mime});
          this.onDiagnostic("bitrate-fallback", "O navegador recusou a taxa escolhida. A gravação usará a qualidade automática.");
        }
        if (preset && Object.keys(options).length === 1) this.onDiagnostic("bitrate-unavailable", "Este navegador não informa suporte às taxas de gravação. A qualidade será automática.");
        this.uploader = new NativeUploader(this.runtime, {...this.uploaderOptions, onProgress: this.onProgress, onFailure: error => this.interrupt(error)});
        await this.uploader.start(this.recorder.mimeType || mime, title, audio);
        if (this.state !== "connecting") {this.uploader.abort(); return;}
        this.recorder.addEventListener("dataavailable", event => this.uploader.enqueue(event.data));
        this.recorder.addEventListener("error", event => this.interrupt(event.error || new Error("O navegador interrompeu a gravação.")));
        this.recorder.addEventListener("stop", () => this.finalize());
        this.recorder.start(1000); this.change("recording");
      } catch (error) {this.interrupt(error); throw error;}
    }
    setVideoOutput(output) {
      if (this.state !== "selected") {output?.release(); throw new Error("Ajuste a área antes de iniciar a gravação.");}
      if (!output?.stream?.getVideoTracks().length || output.stream.getAudioTracks().some(track => !this.stream.getAudioTracks().includes(track))) {output?.release(); throw new Error("Saída de recorte inválida.");}
      this.videoOutput?.release(); this.videoOutput = output; this.recordingStream = output.stream;
    }
    pause() {if (this.state === "recording") {try {this.recorder.pause(); this.change("paused");} catch (error) {this.interrupt(error);}}}
    resume() {if (this.state === "paused") {try {this.recorder.resume(); this.change("recording");} catch (error) {this.interrupt(error);}}}
    stop() {
      if (["recording", "paused"].includes(this.state)) {
        this.change("finalizing");
        try {this.recorder.stop();} catch (error) {this.interrupt(error);} finally {this.release();}
      }
      else if (["selected", "choosing", "connecting"].includes(this.state)) this.interrupt(new Error("A seleção da captura foi encerrada."));
    }
    async finalize() {
      if (this.state === "interrupted" || this.finalizing) return;
      this.finalizing = true; this.change("finalizing"); this.release();
      try {const result = await this.uploader.finish(); if (this.state !== "interrupted") this.change("ready", result);}
      catch (error) {this.interrupt(error);}
    }
    release() {
      if (this.durationTimer !== undefined) {this.timing.clearTimeout(this.durationTimer); this.durationTimer = undefined;}
      this.videoOutput?.release(); this.videoOutput = null; this.recordingStream = null;
      for (const track of this.stream?.getTracks() || []) {try {track.stop();} catch {}}
      if (this.stream) {this.stream = null; this.preview(null);}
    }
    interrupt(error = new Error("A captura foi encerrada. Os dados já enviados permanecem como arquivo parcial.")) {
      if (["interrupted", "ready"].includes(this.state)) return;
      this.change("interrupted", error);
      if (this.recorder && this.recorder.state !== "inactive") {try {this.recorder.stop();} catch {}}
      this.release(); this.uploader?.abort();
    }
  }
  const api = {CHUNK_BYTES, MAX_PENDING_BYTES, BITRATES, mimeChoices, captureOptions, NativeUploader, CaptureSession};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.OpenDownloaderRecording = api;
})(globalThis);
