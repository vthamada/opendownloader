/* Shared interpretation of page/player candidates; no browser or DOM access. */
"use strict";
(() => {
  function httpUrl(value) {
    if (typeof value !== "string" || !value || value.length > 8192 || /[\s\u0000-\u001f\u007f-\u009f\\]/u.test(value)) throw new Error("Informe um link HTTP ou HTTPS válido de até 8192 caracteres.");
    if (!/^https?:\/\//i.test(value)) throw new Error("Use um link HTTP ou HTTPS.");
    const parsed = new URL(value);
    if (!parsed.hostname || parsed.username || parsed.password || !["https:", "http:"].includes(parsed.protocol)) throw new Error("Use um link HTTP ou HTTPS sem usuário e senha na URL.");
    if (parsed.port === "0" || [...value].some((character) => character.length === 1 && /[\ud800-\udfff]/u.test(character))) throw new Error("O link contém uma porta ou caractere inválido.");
    return value;
  }
  function title(value) {
    if (typeof value !== "string") return "";
    let result = "", count = 0;
    for (const character of value) {
      const point = character.codePointAt(0);
      if (point >= 0xd800 && point <= 0xdfff) continue;
      result += point < 32 || (point >= 127 && point <= 159) ? " " : character;
      if (++count >= 256) break;
    }
    return result.trim();
  }
  function youtubePage(value) {
    try {
      const page = new URL(httpUrl(value));
      if (page.port && !["80", "443"].includes(page.port)) return null;
      let id;
      if (["youtu.be", "www.youtu.be"].includes(page.hostname)) id = page.pathname.slice(1);
      else if (["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"].includes(page.hostname)) {
        if (page.pathname.replace(/\/$/, "") === "/watch") {
          const ids = page.searchParams.getAll("v"); id = ids.length === 1 ? ids[0] : null;
        } else if (page.pathname.startsWith("/shorts/")) id = page.pathname.slice(8).replace(/\/$/, "");
      }
      return /^[A-Za-z0-9_-]{11}$/.test(id || "") ? `https://www.youtube.com/watch?v=${id}` : null;
    } catch { return null; }
  }
  function mimeType(value) {return typeof value === "string" && value.length <= 128 ? value.split(";",1)[0].trim().toLowerCase() : "";}
  function componentSource(tag) {return {"HLSJS-VIDEO":"hls","HLS-VIDEO":"hls","DASH-VIDEO":"dash"}[typeof tag === "string" ? tag.toUpperCase() : ""] || null;}
  function sourceFor(value, mimetype = "") {
    let path;
    try {path = new URL(httpUrl(value)).pathname.toLowerCase();} catch {return null;}
    const mime = mimeType(mimetype);
    if (path.endsWith(".m3u8") || ["application/vnd.apple.mpegurl","application/x-mpegurl","audio/mpegurl","audio/x-mpegurl"].includes(mime)) return "hls";
    if (path.endsWith(".mpd") || mime === "application/dash+xml") return "dash";
    if (/\.(?:mp4|m4v|webm|mov|mp3|m4a|ogg|oga|wav|flac|aac)$/u.test(path) || /^(?:video|audio)\//u.test(mime)) return "direct";
    return null;
  }
  function resourceCandidate(entry, pageTitle = "") {
    if (!entry || typeof entry.name !== "string") return null;
    const source = sourceFor(entry.name, entry.contentType);
    if (!source) return null;
    const url = httpUrl(entry.name), path = new URL(url).pathname.toLowerCase();
    const audio = source === "direct" && (/\.(?:mp3|m4a|ogg|oga|wav|flac|aac)$/u.test(path) || /^audio\//u.test(mimeType(entry.contentType)));
    return {url, kind:audio ? "audio" : "video", title:title(pageTitle) || "Fonte detectada na página",
      discovery:"resource", mimetype:mimeType(entry.contentType)};
  }
  function candidate(raw, pageUrl) {
    if (!raw || typeof raw !== "object" || !["audio", "video"].includes(raw.kind)) return null;
    const cleanTitle = title(raw.title);
    // This is an observation of the player API, never a classification of an
    // unrelated manifest, licence policy, key system or downloadable title.
    const protection = raw.protection === "eme-observed" ? {protection:"eme-observed"} : {};
    const youtube = raw.kind === "video" ? youtubePage(pageUrl) : null;
    if (youtube) return {url: youtube, title: cleanTitle || "Vídeo da página no YouTube", kind: "video", supported: true, source: "youtube", ...protection};
    try {
      const url = httpUrl(raw.url), source = sourceFor(url,raw.mimetype) || "direct";
      const component = componentSource(raw.component) ? raw.component.toUpperCase() : null;
      const discovery = raw.discovery === "resource" ? "resource" : raw.discovery === "component" && component ? "component" : "element";
      return {url, title: cleanTitle || (raw.kind === "audio" ? "Áudio da página" : "Vídeo da página"), kind: raw.kind, supported:true, source, discovery, ...protection,
        mimetype:source === "hls" ? "application/vnd.apple.mpegurl" : source === "dash" ? "application/dash+xml" : "",
        ...(discovery === "component" ? {component} : {}),
        evidence:discovery === "resource" ? "Requisição observada neste frame; associação ao player não confirmada" : discovery === "component" ? `Fonte exposta pelo componente ${component}` : "Fonte exposta pelo player"};
    } catch {
      return {url: null, title: cleanTitle || "Player da página", kind: raw.kind, supported: false, source: "unresolved", ...protection, reason: protection.protection
        ? "Este player usa a API de mídia criptografada (EME). Isso não identifica o DRM nem garante download ou gravação. Use o modo offline oficial ou uma fonte autorizada."
        : typeof raw.url === "string" && raw.url.startsWith("blob:")
        ? "Este player usa blob. Inicie a reprodução e procure manifests HLS/DASH ou fontes detectadas neste frame."
        : "Inicie a reprodução. Se o player não expuser um arquivo HTTP(S), este modo não poderá baixá-lo."};
    }
  }
  globalThis.LumeMedia = Object.freeze({httpUrl, title, youtubePage, sourceFor, componentSource, resourceCandidate, candidate});
})();
