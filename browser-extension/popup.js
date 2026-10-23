"use strict";
const form = document.getElementById("link-form"), input = document.getElementById("url"), button = document.getElementById("send"), status = document.getElementById("status");
const media = document.getElementById("media-list"), scan = document.getElementById("scan"), site = document.getElementById("site"), siteLabel = document.getElementById("site-label");
let siteState = null;
document.getElementById("record").addEventListener("click", () => {
  chrome.tabs.create({url: chrome.runtime.getURL("recorder.html")}, () => {
    if (chrome.runtime.lastError) render("Não foi possível abrir a aba de gravação. Tente novamente.", false);
  });
});
document.getElementById("bridge").addEventListener("click", async () => {
  const control = document.getElementById("bridge"), label = document.getElementById("bridge-status");
  control.disabled = true; label.textContent = "Verificando a ponte local…"; label.classList.remove("error");
  try {
    const result = await message({type:"bridge-status"});
    label.textContent = result.ok ? result.message : result.error; label.classList.toggle("error", !result.ok);
  } catch {
    label.textContent = "O diagnóstico não respondeu. Reabra a extensão e tente novamente.";
    label.classList.toggle("error", true);
  } finally {control.disabled = false;}
});
function render(message, ok = true) {status.textContent = message; status.classList.toggle("error", !ok);}
function message(body) {
  return new Promise((resolve) => chrome.runtime.sendMessage(body, (response) => {
    resolve(chrome.runtime.lastError || !response ? {ok: false, error: "A extensão não respondeu. Reabra o botão do OpenDownloader."} : response);
  }));
}
async function send(url, title = null, source = null) {
  button.disabled = true; render("Enviando o pedido. A transferência só começa após confirmar no OpenDownloader…");
  const result = await message(title === null ? {type: "queue-url", url} : {type: "queue-url", url, title, source, media: true});
  button.disabled = false; render(result.ok ? result.message : result.error, result.ok); return result;
}
function showMedia(result) {
  media.replaceChildren();
  if (!result.ok) {render(`A busca de mídia não foi concluída: ${result.error}`, false); return;}
  if (!result.items?.length) {
    const text = document.createElement("p"); text.textContent = "Nenhum player encontrado. Inicie o vídeo e clique em Procurar nesta página."; media.append(text); return;
  }
  for (const item of result.items) {
    const entry = document.createElement("li"), title = document.createElement("strong"), detail = document.createElement("p");
    const protocol = item.source === "hls" ? "HLS" : item.source === "dash" ? "DASH" : item.kind === "audio" ? "Áudio" : "Vídeo";
    const sourceUrl = item.supported ? new URL(item.url) : null;
    title.textContent = item.title; detail.textContent = item.supported ? `${protocol} • ${sourceUrl.hostname}${item.discovery === "resource" ? ` • ${sourceUrl.pathname.slice(-100)} • observado neste frame; pode incluir publicidade ou segmentos` : item.discovery === "component" ? ` • ${item.component} • ${sourceUrl.pathname.slice(-100)}` : ""}` : item.reason;
    if (item.supported && item.protection === "eme-observed") detail.textContent += " • API EME observada no player: download/gravação podem ser bloqueados. Isso não identifica o DRM desta fonte.";
    entry.append(title, detail);
    if (item.supported) {
      const select = document.createElement("button"); select.type = "button"; select.textContent = "Escolher opções no OpenDownloader ↗";
      select.addEventListener("click", async () => {select.disabled = true; await send(item.url, item.title,item.source); select.disabled = false;}); entry.append(select);
    }
    media.append(entry);
  }
  if (result.truncated) {const text = document.createElement("p"); text.textContent = "A lista mostra até 24 candidatos da página. Os botões nos players podem oferecer outros itens."; media.append(text);}
}
async function updateSite() {
  siteState = await message({type: "site-state"}); site.disabled = !siteState.ok;
  siteLabel.textContent = siteState.ok ? siteState.label : "Abra uma página HTTP(S).";
  site.textContent = siteState.enabled ? "Desativar botões neste site" : "Ativar botões neste site";
}
chrome.storage.local.get("lastResult", ({lastResult}) => {
  if (lastResult?.message) {
    const when = Number.isFinite(lastResult.at) ? new Date(lastResult.at).toLocaleString() : "horário não registrado";
    render(`Último envio (${when}): ${lastResult.message}`, lastResult.ok);
  }
});
form.addEventListener("submit", async (event) => {event.preventDefault(); if ((await send(input.value)).ok) input.value = "";});
scan.addEventListener("click", async () => {
  scan.disabled = true; render("Procurando vídeos e áudios nesta página…");
  try {
    const result = await message({type: "scan-page"}); showMedia(result);
    if (result.ok) {
      const count = result.items?.filter(item => item.supported).length || 0;
      render(count ? `Busca concluída: ${count} ${count === 1 ? "fonte disponível" : "fontes disponíveis"}. Escolha no player ou na lista abaixo.` : "Busca concluída. Nenhuma fonte disponível; inicie o vídeo e procure novamente.");
    }
  } catch {render("A busca não respondeu. Reabra a extensão e tente novamente.", false);}
  finally {scan.disabled = false;}
});
site.addEventListener("click", async () => {
  if (!siteState?.ok) return; site.disabled = true;
  if (siteState.enabled) {
    const result = await message({type: "site-disable", origin: siteState.origin});
    if (result.ok) {media.replaceChildren(); render("Botões automáticos desativados neste site.");} else render(result.error, false);
    await updateSite(); return;
  }
  // This permission request is directly initiated by the user's click, for this origin only.
  chrome.permissions.request({origins: [siteState.origin]}, async (granted) => {
    if (chrome.runtime.lastError || !granted) {render("Permissão não concedida. Você ainda pode usar Procurar nesta página.", false); await updateSite(); return;}
    const result = await message({type: "site-enable", origin: siteState.origin}); showMedia(result);
    if (result.ok) render("Botões automáticos ativados somente neste site. Escolha um vídeo para enviar ao OpenDownloader.");
    await updateSite();
  });
});
async function checkMenu() {
  const result = await message({type:"menu-status"}), label = document.getElementById("menu-status");
  label.classList.toggle("error", !result.ok);
  const version = chrome.runtime.getManifest().version;
  label.textContent = `Extensão ${version} • ${result.ok ? "Menu de links configurado." : result.error}`;
}
(async () => {await checkMenu(); await updateSite(); showMedia(await message({type: "media-list"}));})();
