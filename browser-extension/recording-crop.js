"use strict";
(function (root) {
  function regionPixels(region, width, height) {
    if (!Number.isInteger(width) || !Number.isInteger(height) || width < 2 || height < 2 || !region ||
        ![region.x, region.y, region.width, region.height].every(Number.isFinite) ||
        region.x < 0 || region.y < 0 || region.width <= 0 || region.height <= 0 ||
        region.x + region.width > 1.000001 || region.y + region.height > 1.000001) throw new Error("Escolha uma área válida dentro da prévia.");
    const x = Math.floor(region.x * width), y = Math.floor(region.y * height);
    const w = Math.floor(Math.min(width - x, region.width * width) / 2) * 2;
    const h = Math.floor(Math.min(height - y, region.height * height) / 2) * 2;
    if (w < 2 || h < 2 || w > 4096 || h > 4096 || w * h > 16777216) throw new Error("O recorte deve ter pelo menos 2 × 2 e no máximo 4096 × 4096 pixels. Escolha uma área menor ou capture até 1080p.");
    return {x, y, width: w, height: h};
  }
  function createCrop(video, source, region, {document: doc = document, Stream = MediaStream, onError = () => {}, timing = {setTimeout: (fn, delay) => setTimeout(fn, delay), clearTimeout: id => clearTimeout(id)}} = {}) {
    const area = regionPixels(region, video.videoWidth, video.videoHeight);
    const canvas = doc.createElement("canvas"); canvas.width = area.width; canvas.height = area.height;
    const context = canvas.getContext("2d", {alpha: false});
    if (!context || typeof canvas.captureStream !== "function") throw new Error("Este navegador não oferece recorte de gravação por canvas.");
    let stopped = false, callback, output;
    // Video-frame callbacks may be throttled when the preview scrolls out of
    // view even while this document is visible. A bounded draw timer keeps the
    // recording independent of preview visibility. Hidden tabs may still be
    // suspended by the browser; no security/power policy is changed here.
    function schedule() {callback = timing.setTimeout(draw, 1000 / 30);}
    function draw() {
      if (stopped) return;
      try {
        if (!video.videoWidth || !video.videoHeight || video.videoWidth < area.x + area.width || video.videoHeight < area.y + area.height) throw new Error("A resolução da superfície mudou e não comporta o recorte. Selecione a área novamente.");
        context.drawImage(video, area.x, area.y, area.width, area.height, 0, 0, canvas.width, canvas.height);
        schedule();
      } catch (error) {release(); onError(error);}
    }
    function release() {
      if (stopped) return; stopped = true;
      timing.clearTimeout(callback);
      for (const track of output?.getVideoTracks() || []) track.stop();
      // Shared audio belongs to the original consented capture, not the crop.
      canvas.width = 0; canvas.height = 0;
    }
    try {
      context.drawImage(video, area.x, area.y, area.width, area.height, 0, 0, canvas.width, canvas.height);
      output = canvas.captureStream(30);
      const stream = new Stream([...output.getVideoTracks(), ...source.getAudioTracks()]);
      schedule(); return {stream, release, width: area.width, height: area.height};
    } catch (error) {release(); throw error;}
  }
  const api = {regionPixels, createCrop};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.OpenDownloaderCrop = api;
})(globalThis);
