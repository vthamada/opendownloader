/* Runs in the extension's isolated world only after activeTab/site permission. */
"use strict";
(async () => {
  const previous = globalThis.__lumeMediaController;
  if (previous) return previous.resume();
  const MAX_PLAYERS = 64, MAX_REPORT = 32, MAX_RESOURCES = 24, MAX_RESOURCE_BATCH = 512, MAX_ROOTS = 32, MAX_DOM_NODES = 4096;
  const players = new Map();
  const encryptedPlayers = new WeakSet();
  const timers = new Set();
  const ownHosts = new WeakSet();
  const observedRoots = new Set();
  const resources = new Map();
  let resourceObserver = null, resourceTruncated = false, resourceStart = 0;
  let stopped = false, suspended = true, navigation = null, frame = null, scanTimer = null, lastScan = 0, lastUrl = location.href, lastReport = "";
  function playerTitle(index) {
    const value = LumeMedia.title(document.title) || "Mídia da página";
    return LumeMedia.title(index ? `${value} • ${index}` : value);
  }
  function candidateFor(player, index = 0) {
    const url = player.currentSrc || player.src || player.querySelector("source[src]")?.src || "";
    const direct = LumeMedia.candidate(rawCandidate(player,url,index), location.href);
    if (direct?.supported) return direct;
    // Public component attributes structurally enclose this exact player. No
    // app-state scraping, proxy decoding, main-world injection or cross-root guess.
    let inner = player;
    for (let depth = 0; depth < 8; depth++) {
      const host = inner.getRootNode?.().host;
      if (!host) break;
      const protocol = LumeMedia.componentSource(host.tagName), source = host.getAttribute?.("src");
      if (protocol && typeof source === "string" && source.length > 0 && source.length <= 8192) {
        try {
          const candidate = LumeMedia.candidate({url:new URL(source,document.baseURI || location.href).href,kind:player.tagName.toLowerCase(),title:playerTitle(index),
            mimetype:protocol === "hls" ? "application/vnd.apple.mpegurl" : "application/dash+xml",discovery:"component",component:host.tagName,...protectionFor(player)},location.href);
          if (candidate?.supported) return candidate;
        } catch {}
      }
      inner = host;
    }
    return direct;
  }
  function watchRoots(roots) {
    observer.disconnect();
    const active = new Set(roots.filter(root=>root !== document));
    for (const root of observedRoots) if (!active.has(root)) {root.removeEventListener("loadedmetadata",requestScan,true);root.removeEventListener("play",requestScan,true);observedRoots.delete(root);}
    for (const root of active) if (!observedRoots.has(root)) {root.addEventListener("loadedmetadata",requestScan,true);root.addEventListener("play",requestScan,true);observedRoots.add(root);}
    for (const root of roots) observer.observe(root === document ? document.documentElement : root,{subtree:true,childList:true,attributes:true,attributeFilter:["src","type"]});
  }
  function collectPlayers() {
    const selected=[],roots=[document],seen=new Set(roots);let examined=0,truncated=false;
    if (typeof document.createTreeWalker !== "function") {
      const nodes=document.querySelectorAll("video,audio");for(let i=0;i<Math.min(nodes.length,MAX_PLAYERS);i++)selected.push(nodes[i]);
      watchRoots(roots);return {selected,truncated:nodes.length>MAX_PLAYERS};
    }
    outer:for (let index=0;index<roots.length;index++) {
      const walker=document.createTreeWalker(roots[index],1); // NodeFilter.SHOW_ELEMENT.
      for(let node=walker.nextNode();node;node=walker.nextNode()) {
        if(++examined>MAX_DOM_NODES){truncated=true;break outer;}
        if(ownHosts.has(node))continue;
        if(node.matches?.("video,audio")) {if(selected.length>=MAX_PLAYERS){truncated=true;break outer;}selected.push(node);}
        if(node.shadowRoot&&!seen.has(node.shadowRoot)) {if(roots.length>=MAX_ROOTS){truncated=true;continue;}seen.add(node.shadowRoot);roots.push(node.shadowRoot);}
      }
    }
    watchRoots(roots);return {selected,truncated};
  }
  function rawCandidate(player, url, index) {
    const fallback = player.querySelector("source[src]");
    const type = fallback?.src === url ? fallback.type || "" : player.getAttribute?.("type") || "";
    return {url:typeof url === "string" && url.length <= 8192 ? url : "", kind: player.tagName.toLowerCase(), title: playerTitle(index),
      mimetype:typeof type === "string" && type.length <= 128 ? type : "",...protectionFor(player)};
  }
  function protectionFor(player) {
    // Do not read initData, key IDs, sessions, messages or license responses.
    // Absence is deliberately left unknown, including failed API access.
    if (encryptedPlayers.has(player)) return {protection:"eme-observed"};
    try {if (player.mediaKeys != null) return {protection:"eme-observed"};} catch {}
    return {};
  }
  function releasePlayer(player, record) {
    resize?.unobserve(player); player.removeEventListener("encrypted", record.encrypted);
    player.removeEventListener("emptied", record.emptied); encryptedPlayers.delete(player); record.host.remove();
  }
  function resourceEntries(entries) {
    if (stopped || suspended) return;
    let changed = false;
    if (entries.length > MAX_RESOURCE_BATCH) resourceTruncated = true;
    for (let i = Math.max(0,entries.length - MAX_RESOURCE_BATCH); i < entries.length; i++) {
      const entry = entries[i];
      if (Number.isFinite(entry?.startTime) && entry.startTime < resourceStart) continue;
      let item;
      try {item = LumeMedia.resourceCandidate(entry, document.title);} catch {continue;}
      if (!item || resources.has(item.url)) continue;
      resources.set(item.url,item); changed = true;
      if (resources.size > MAX_RESOURCES) {resources.delete(resources.keys().next().value); resourceTruncated = true;}
    }
    if (changed) requestScan();
  }
  function resourceSnapshot() {
    // Browser-owned snapshot allocation is native; processing and retained state
    // are capped. Never alter the page's Resource Timing buffer or fetch bodies.
    try {if (typeof globalThis.performance?.getEntriesByType === "function") resourceEntries(performance.getEntriesByType("resource"));} catch {}
  }
  function startResources() {
    resourceSnapshot();
    if (typeof PerformanceObserver !== "function") return;
    try {
      resourceObserver = new PerformanceObserver(list => {if (!stopped && !suspended) resourceEntries(list.getEntries());});
      resourceObserver.observe({type:"resource",buffered:true});
    } catch {resourceObserver?.disconnect(); resourceObserver = null;}
  }
  function position() {
    frame = null;
    for (const [player, record] of players) {
      const box = player.getBoundingClientRect();
      const visible = player.isConnected && box.width >= 100 && box.height >= 35 && box.bottom > 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth;
      record.host.style.setProperty("display", visible ? "block" : "none", "important");
      if (!visible) continue;
      const top = player.tagName === "AUDIO" && box.top > 38 ? box.top - 34 : box.top + 8;
      record.host.style.setProperty("left", `${Math.max(4, Math.min(innerWidth - 220, box.left + 8))}px`, "important");
      record.host.style.setProperty("top", `${Math.max(4, Math.min(innerHeight - 50, top))}px`, "important");
    }
  }
  function requestPosition() {if (!stopped && !suspended && frame === null) frame = requestAnimationFrame(position);}
  function showButton(player) {
    const host = document.createElement("div");
    for (const [name, value] of Object.entries({all: "initial", position: "fixed", "z-index": "2147483647", "pointer-events": "none", display: "none", "max-width": "220px"})) host.style.setProperty(name, value, "important");
    ownHosts.add(host);
    const shadow = host.attachShadow({mode: "closed"});
    const style = document.createElement("style");
    style.textContent = ":host{font:13px system-ui,sans-serif}button{pointer-events:auto;font:600 13px system-ui,sans-serif;background:#264a3e;color:white;border:1px solid #d9e8dd;border-radius:7px;padding:7px 11px;cursor:pointer;box-shadow:0 2px 7px #0005;max-width:220px}button:hover{background:#35624f}button:focus-visible{outline:3px solid #e7f0ba;outline-offset:2px}button:disabled{background:#394c44;color:#fff;cursor:default}span{display:block;pointer-events:none;max-width:220px;background:#f7f7f1;color:#263433;font:12px system-ui,sans-serif;padding:5px;border-radius:4px;margin-top:3px;overflow-wrap:anywhere}span:empty{display:none}";
    const button = document.createElement("button"); button.type = "button";
    const status = document.createElement("span"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    shadow.append(style, button, status); document.documentElement.append(host);
    const choices = document.createElement("div"); shadow.append(choices);
    const record = {host, button, status, choices, item: null, options:[]};
    record.encrypted = event => {if (!stopped && !suspended && event.isTrusted === true) {encryptedPlayers.add(player);requestScan();}};
    record.emptied = event => {if (event.isTrusted === true) {encryptedPlayers.delete(player);requestScan();}};
    player.addEventListener("encrypted",record.encrypted);player.addEventListener("emptied",record.emptied);
    async function queue(item, target) {
      if (stopped || suspended) return;
      target.disabled = true; status.textContent = "Enviando ao OpenDownloader…";
      try {
        const response = await chrome.runtime.sendMessage({type:"content-queue",item});
        if (response?.code === "grant") {teardown(); return;}
        status.textContent = response?.ok ? (response.message || "Confirme as opções no OpenDownloader.") : (response?.error || "O OpenDownloader não confirmou o recebimento.");
      } catch {status.textContent = "A extensão foi atualizada ou perdeu a conexão. Recarregue a página.";}
      finally {if (!stopped) target.disabled = false;}
    }
    function showChoices() {
      if (choices.children.length) {choices.replaceChildren(); return;}
      const warning = document.createElement("span");
      warning.textContent = "Fontes deste frame, sem associação confirmada ao player. Podem incluir anúncios ou segmentos. Escolha a correta:";
      choices.append(warning);
      for (const item of record.options.slice(0,8)) {
        const select = document.createElement("button"), url = new URL(item.url);
        select.type = "button"; select.textContent = `${item.source.toUpperCase()} • ${url.hostname} • ${url.pathname.split("/").pop().slice(0,48) || "mídia"}`;
        select.title = "Enviar esta fonte para conferir formatos e opções no OpenDownloader";
        select.addEventListener("click",event=>{if (!event.isTrusted || stopped || suspended) return; event.preventDefault();event.stopPropagation();queue(item,select);});
        choices.append(select);
      }
      if (record.options.length > 8) {const note=document.createElement("span");note.textContent="Outras fontes estão no popup da extensão.";choices.append(note);}
    }
    button.addEventListener("click", async (event) => {
      if (!event.isTrusted || stopped || suspended) return;
      event.preventDefault(); event.stopPropagation();
      if (!record.item?.supported) {if (record.options.length) showChoices(); return;}
      await queue(record.item,button);
      if (!stopped && !suspended) {const timer = setTimeout(() => {timers.delete(timer); if (!stopped) status.textContent = "";}, 8000); timers.add(timer);}
    });
    players.set(player, record); resize?.observe(player); return record;
  }
  async function refresh() {
    if (stopped || suspended) return {ok: false};
    lastScan = Date.now();
    const {selected,truncated:domTruncated} = collectPlayers();
    const current = new Set(selected);
    for (const [player, record] of players) if (!current.has(player)) {releasePlayer(player,record); players.delete(player);}
    const raw = []; let truncated = domTruncated || resourceTruncated;
    const observed = [];
    for (const rawItem of resources.values()) {
      const parsed = LumeMedia.candidate(rawItem,location.href);
      if (parsed?.supported && !observed.some(item=>item.url===parsed.url)) observed.push(parsed);
    }
    function reportItem(item) {if (raw.length < MAX_REPORT) raw.push(item); else truncated = true;}
    // Manifest candidates must remain visible even on pages with many players
    // or source alternatives; retain their bounded provenance before DOM rows.
    for (const item of resources.values()) reportItem(item);
    for (const [index, player] of selected.entries()) {
      const record = players.get(player) || showButton(player), item = candidateFor(player, selected.length > 1 ? index + 1 : 0);
      record.item = item; record.options = observed; record.choices.replaceChildren(); record.button.disabled = !item?.supported && !observed.length;
      record.button.textContent = item?.supported ? item?.source === "youtube" ? "↓ Baixar vídeo da página" : player.tagName === "AUDIO" ? "↓ Baixar áudio" : "↓ Baixar vídeo" : observed.length ? "↓ Escolher fonte detectada" : "↓ Procurando fonte de vídeo";
      record.button.title = item?.protection === "eme-observed" ? "API EME observada neste player. As fontes listadas podem ser de anúncios; download e gravação podem ser bloqueados." : item?.supported ? "Escolher opções no OpenDownloader antes de baixar" : observed.length ? "Fontes deste frame: escolha a correta; anúncios podem aparecer" : item?.reason || "Mídia indisponível";
      reportItem(item?.supported ? item : rawCandidate(player, player.currentSrc || player.src || player.querySelector("source[src]")?.src || "", selected.length > 1 ? index + 1 : 0));
      if (!LumeMedia.youtubePage(location.href)) {
        const sources = player.querySelectorAll("source[src]");
        if (sources.length > 8) truncated = true;
        for (let i = 0; i < Math.min(sources.length, 8); i++) {
          const source = sources[i];
          if (source.src && !raw.some((entry) => entry.url === source.src)) reportItem({...rawCandidate(player, source.src, selected.length > 1 ? index + 1 : 0),mimetype:typeof source.type === "string" && source.type.length <= 128 ? source.type : ""});
        }
      }
    }
    requestPosition();
    const report = {type: "content-report", items: raw, truncated};
    const signature = JSON.stringify({page: location.href, ...report});
    if (signature !== lastReport) {
      lastReport = signature;
      try {const accepted = await chrome.runtime.sendMessage(report); if (!accepted?.ok) teardown();} catch {teardown();}
    }
    return {ok: true, examined: selected.length, truncated: report.truncated};
  }
  function requestScan() {
    if (stopped || suspended || scanTimer !== null) return;
    scanTimer = setTimeout(() => {scanTimer = null; refresh();}, Math.max(100, 500 - (Date.now() - lastScan)));
  }
  function received(message, sender, reply) {
    if (sender.id !== chrome.runtime.id || message?.type !== "lume-teardown") return false;
    teardown(); reply?.({ok: true}); return false;
  }
  function suspend() {
    suspended = true; observer.disconnect(); resize?.disconnect(); resourceObserver?.disconnect(); resourceObserver = null; clearInterval(navigation); navigation = null;
    if (scanTimer !== null) clearTimeout(scanTimer); if (frame !== null) cancelAnimationFrame(frame);
    for (const timer of timers) clearTimeout(timer); timers.clear();
    scanTimer = null; frame = null; lastReport = "";
    for (const [player,record] of players) releasePlayer(player,record); players.clear(); resources.clear(); resourceTruncated = false;
    for(const root of observedRoots){root.removeEventListener("loadedmetadata",requestScan,true);root.removeEventListener("play",requestScan,true);}observedRoots.clear();
  }
  function clearDocument() {chrome.runtime.sendMessage({type: "content-clear"}).catch(() => {});}
  function pageHidden(event) {clearDocument(); if (event.persisted) suspend(); else teardown();}
  function pageShown(event) {if (event.persisted) resume();}
  function teardown() {
    if (stopped) return; stopped = true; suspend();
    window.removeEventListener("scroll", requestPosition, true); window.removeEventListener("resize", requestPosition);
    window.removeEventListener("pagehide", pageHidden); window.removeEventListener("pageshow", pageShown); document.removeEventListener("loadedmetadata", requestScan, true);
    document.removeEventListener("play", requestScan, true); chrome.runtime.onMessage.removeListener(received);
    delete globalThis.__lumeMediaController;
  }
  const resize = typeof ResizeObserver === "function" ? new ResizeObserver(requestPosition) : null;
  const observer = new MutationObserver((changes) => {
    // Inspect a bounded mutation prefix without expanding NodeLists. A large
    // wave conservatively schedules one coalesced, bounded player scan.
    let examined = 0;
    for (let i = 0; i < Math.min(changes.length, 32); i++) {
      const change = changes[i];
      if (change.type === "attributes") {
        if (change.target.matches?.("video,audio,source,hlsjs-video,hls-video,dash-video")) {requestScan(); return;}
        continue;
      }
      for (const nodes of [change.addedNodes, change.removedNodes]) {
        for (let j = 0; j < nodes.length; j++) {
          if (++examined > 32) {requestScan(); return;}
          const node = nodes[j];
          if (node.nodeType !== 1 || ownHosts.has(node)) continue;
          if (node.shadowRoot || node.matches?.("video,audio,source,hlsjs-video,hls-video,dash-video") || node.querySelector?.("video,audio")) {requestScan(); return;}
        }
      }
    }
    if (changes.length > 32) requestScan();
  });
  async function resume() {
    if (stopped) return {ok: false};
    try {const accepted = await chrome.runtime.sendMessage({type: "content-hello"}); if (!accepted?.ok) {teardown(); return accepted;}}
    catch {teardown(); return {ok: false};}
    if (stopped) return {ok: false};
    if (suspended) {
      suspended = false; observer.observe(document.documentElement, {subtree: true, childList: true, attributes: true, attributeFilter: ["src"]});
      startResources();
      navigation = setInterval(() => {if (location.href !== lastUrl) {lastUrl = location.href; lastReport = ""; resources.clear();resourceTruncated=false;resourceStart=typeof globalThis.performance?.now === "function" ? performance.now() : 0;refresh();}else if(Date.now()-lastScan>=3000)requestScan();}, 1000);
    }
    else resourceSnapshot();
    return refresh();
  }
  window.addEventListener("scroll", requestPosition, true); window.addEventListener("resize", requestPosition);
  window.addEventListener("pagehide", pageHidden); window.addEventListener("pageshow", pageShown); document.addEventListener("loadedmetadata", requestScan, true); document.addEventListener("play", requestScan, true);
  chrome.runtime.onMessage.addListener(received);
  globalThis.__lumeMediaController = {refresh, resume, teardown};
  return resume();
})();
