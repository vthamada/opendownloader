"use strict";
const {CaptureSession, mimeChoices} = OpenDownloaderRecording;
const elements = Object.fromEntries(["start", "pause", "stop", "status", "format", "quality", "bitrate", "duration", "title", "elapsed", "bytes", "receipt", "silent-choice", "silent-continue", "silent-cancel", "preview", "preview-panel", "diagnostic", "crop-overlay", "crop-selection", "crop-x", "crop-y", "crop-width", "crop-height", "crop-reset", "crop-status", "record", "capture-cancel"].map(id => [id, document.getElementById(id)]));
let session = null;
let region = {x: 0, y: 0, width: 1, height: 1}, audioApproved = false, drag = null;
const cropFields = ["crop-x", "crop-y", "crop-width", "crop-height"];
function renderRegion() {
  const {videoWidth: width, videoHeight: height} = elements.preview;
  const full = region.x === 0 && region.y === 0 && region.width === 1 && region.height === 1;
  const rect = {x: region.x * width, y: region.y * height, width: region.width * width, height: region.height * height};
  for (const [index, name] of ["x", "y", "width", "height"].entries()) elements[cropFields[index]].value = String(Math.floor(rect[name] || 0));
  Object.assign(elements["crop-selection"].style, {left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.width * 100}%`, height: `${region.height * 100}%`, right: "auto", bottom: "auto"});
  elements["crop-status"].textContent = width && height ? `${Math.floor(rect.width)} × ${Math.floor(rect.height)} pixels${full ? " • superfície inteira, sem recorte" : " • recorte selecionado; saída usa dimensões pares"}` : "Aguardando as dimensões da superfície…";
  elements.record.disabled = session?.state !== "selected" || !audioApproved || !width || !height;
}
elements.preview.addEventListener("loadedmetadata", renderRegion);
elements["crop-reset"].addEventListener("click", () => {region = {x: 0, y: 0, width: 1, height: 1}; renderRegion();});
for (const id of cropFields) elements[id].addEventListener("change", () => {
  if (session?.state !== "selected") return;
  const width = elements.preview.videoWidth, height = elements.preview.videoHeight;
  const values = cropFields.map(key => Number(elements[key].value));
  const candidate = {x: values[0] / width, y: values[1] / height, width: values[2] / width, height: values[3] / height};
  try {OpenDownloaderCrop.regionPixels(candidate, width, height); region = candidate; renderRegion();}
  catch (error) {diagnostic("crop-invalid", error.message); elements.record.disabled = true;}
});
function point(event) {const bounds = elements["crop-overlay"].getBoundingClientRect(); return {x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height))};}
elements["crop-overlay"].addEventListener("pointerdown", event => {
  if (session?.state !== "selected" || event.button !== 0) return;
  drag = {point: point(event), pointer: event.pointerId, original: region}; elements["crop-overlay"].setPointerCapture(event.pointerId); event.preventDefault();
});
elements["crop-overlay"].addEventListener("pointermove", event => {
  if (!drag || drag.pointer !== event.pointerId) return;
  const end = point(event), start = drag.point;
  region = {x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y)}; renderRegion();
});
function finishDrag(event) {
  if (!drag || drag.pointer !== event.pointerId) return;
  try {OpenDownloaderCrop.regionPixels(region, elements.preview.videoWidth, elements.preview.videoHeight);}
  catch {region = drag.original;}
  drag = null; renderRegion();
}
elements["crop-overlay"].addEventListener("pointerup", finishDrag);
elements["crop-overlay"].addEventListener("pointercancel", event => {if (drag?.pointer === event.pointerId) {region = drag.original; drag = null; renderRegion();}});
function size(bytes) {if (bytes < 1024) return `${bytes} B`; if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KiB`; return `${(bytes / 1048576).toFixed(1)} MiB`;}
function clock() {const seconds = Math.floor((session?.elapsed() || 0) / 1000); elements.elapsed.textContent = `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;}
function diagnostic(_code, message) {elements.diagnostic.hidden = false; elements.diagnostic.textContent = message;}
function showPreview(stream) {
  try {elements.preview.pause();} catch { /* A detached preview cannot own capture cleanup. */ }
  elements["preview-panel"].hidden = !stream;
  const unavailable = () => {
    if (elements.preview.srcObject === stream) diagnostic("preview-unavailable", "Não foi possível mostrar a prévia local. A captura pode continuar; confira a superfície selecionada no navegador.");
  };
  try {elements.preview.srcObject = stream;}
  catch {if (stream) diagnostic("preview-unavailable", "Não foi possível conectar a prévia local. A captura pode continuar."); return;}
  if (stream) {
    try {elements.preview.muted = true; elements.preview.play().catch(unavailable);}
    catch {unavailable();}
  }
}
function stateChanged(state, detail) {
  elements.start.disabled = !["idle", "ready", "interrupted"].includes(state);
  elements.pause.disabled = !["recording", "paused"].includes(state);
  elements.pause.textContent = state === "paused" ? "Continuar" : "Pausar";
  elements.stop.disabled = !["recording", "paused"].includes(state);
  const editable = state === "selected";
  for (const key of [...cropFields, "crop-reset", "capture-cancel"]) elements[key].disabled = !editable;
  elements["crop-overlay"].classList.toggle("locked", !editable);
  elements.record.disabled = !editable || !audioApproved;
  for (const key of ["format", "quality", "bitrate", "duration", "title"]) elements[key].disabled = !["idle", "ready", "interrupted"].includes(state);
  elements["silent-choice"].hidden = true;
  const labels = {choosing: "Escolha uma aba ou janela e compartilhe o áudio no seletor.", selected: "Ajuste a área na prévia e clique em Gravar área selecionada.", connecting: "Preparando o arquivo no aplicativo…", recording: "Gravando a reprodução. Você pode pausar ou parar.", paused: "Gravação pausada. O intervalo pausado não será incluído.", finalizing: "Enviando os últimos dados e aguardando a confirmação do aplicativo…", ready: "Gravação recebida. Confira o arquivo no OpenDownloader.", interrupted: detail?.message || "A gravação foi interrompida."};
  elements.status.textContent = labels[state] || "Pronto para gravar.";
  elements.status.classList.toggle("error", state === "interrupted");
  if (state === "ready") {
    if (detail.desktop === "unavailable") elements.status.textContent = "Gravação recebida. Abra o OpenDownloader para escolher a pasta e salvar o vídeo.";
    elements.receipt.hidden = false;
    elements.receipt.textContent = `Recebimento confirmado\n${detail.bytes} bytes • SHA-256: ${detail.sha256}\nIdentificador: ${detail.recording_id}\nO aplicativo permite escolher a pasta de destino. Esse recibo confirma o recebimento dos dados, não a qualidade da imagem ou do áudio.`;
  }
  clock();
}
async function begin() {
  if (!audioApproved || session?.state !== "selected") return;
  try {
    if (!(region.x === 0 && region.y === 0 && region.width === 1 && region.height === 1)) {
      const output = OpenDownloaderCrop.createCrop(elements.preview, session.stream, region, {onError: error => session.interrupt(error)});
      session.setVideoOutput(output);
    }
    await session.begin(elements.format.value, elements.title.value.trim() || "Gravação de vídeo", {bitrate: elements.bitrate.value, maxDurationMs: Number(elements.duration.value) * 60000});
  } catch (error) {diagnostic("capture-start", error.message);}
}
const formats = typeof MediaRecorder === "undefined" ? [] : mimeChoices(MediaRecorder);
for (const mime of formats) {const option = document.createElement("option"); option.value = mime; option.textContent = mime.replace("video/", "").replace(";codecs=", " • "); elements.format.append(option);}
if (!formats.length || !navigator.mediaDevices?.getDisplayMedia) {elements.start.disabled = true; elements.status.textContent = "Este navegador não oferece a captura e os formatos necessários. Use uma versão atual do Chrome ou Edge.";}
elements.start.addEventListener("click", async () => {
  audioApproved = false; region = {x: 0, y: 0, width: 1, height: 1}; drag = null;
  elements.bytes.textContent = "0 B"; elements.receipt.hidden = true; elements.diagnostic.hidden = true;
  session = new CaptureSession({mediaDevices: navigator.mediaDevices, Recorder: MediaRecorder, runtime: chrome.runtime, onState: stateChanged, onDiagnostic: diagnostic, onStream: showPreview, onProgress: bytes => {elements.bytes.textContent = size(bytes);}});
  try {
    const hasAudio = await session.capture(elements.quality.value);
    if (hasAudio) {audioApproved = true; renderRegion();}
    else {elements["silent-choice"].hidden = false; elements.status.textContent = "Aguardando sua escolha: continuar sem áudio ou cancelar.";}
  } catch { /* Picker cancellation never creates a native recording. */ }
});
elements["silent-continue"].addEventListener("click", () => {audioApproved = true; elements["silent-choice"].hidden = true; renderRegion(); elements.status.textContent = "Sem áudio confirmado. Ajuste a área e clique em Gravar área selecionada.";});
elements.record.addEventListener("click", begin);
elements["capture-cancel"].addEventListener("click", () => session?.interrupt(new Error("Seleção cancelada. Nenhuma gravação foi iniciada.")));
elements["silent-cancel"].addEventListener("click", () => session?.interrupt(new Error("Captura cancelada. Nenhuma gravação foi iniciada.")));
elements.pause.addEventListener("click", () => {if (session?.state === "paused") session.resume(); else session?.pause();});
elements.stop.addEventListener("click", () => session?.stop());
window.addEventListener("pagehide", () => session?.interrupt());
window.addEventListener("beforeunload", event => {if (session && !["idle", "ready", "interrupted"].includes(session.state)) {event.preventDefault(); event.returnValue = "";}});
setInterval(clock, 500);
