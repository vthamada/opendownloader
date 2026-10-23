"use strict";
importScripts("media-core.js");
const HOST = "app.lume.downloads", MENU_ID = "lume-download-link", AUTO_SCRIPT = "lume-media-auto";
const PENDING = "Link recebido. Confirme as opções na janela do OpenDownloader para baixar.";
// One owner serializes grant changes, discovery and native sends. Session-only,
// bounded to 8 tabs, 32 frame identities/tab and 24 candidate entries/tab.
const MAX_TABS = 8, MAX_TAB_MEDIA = 24, MAX_TAB_FRAMES = 32;
let stateWrites = Promise.resolve();
let menuSync = null;
function menuOperation(operation, ...args) {
  // create() returns an ID before completion; only its callback owns success.
  return new Promise(resolve => {
    try {
      chrome.contextMenus[operation](...args, () => {
        const error = chrome.runtime.lastError;
        resolve(error ? (error.message || "O navegador recusou a configuração do menu.") : null);
      });
    } catch (error) {resolve(error.message || "A API de menus não respondeu.");}
  });
}
function ensureMenu() {
  if (menuSync) return menuSync;
  menuSync = (async () => {
    const properties = {title:"Baixar com OpenDownloader",contexts:["link","video","audio"],
      documentUrlPatterns:["http://*/*","https://*/*"],visible:true,enabled:true};
    // Update our fixed identity first. If it is absent, create it. No remove
    // window, duplicate IDs or changes to any other menu entry.
    const updateError = await menuOperation("update", MENU_ID, properties);
    const error = updateError ? await menuOperation("create", {id:MENU_ID,...properties}) : null;
    return error ? {ok:false,error:`Não foi possível configurar “Baixar com OpenDownloader”: ${error}`} : {ok:true};
  })().finally(() => {menuSync = null;});
  return menuSync;
}
function showResult(result) {
  const message = result.ok ? (result.message || PENDING) : result.error;
  chrome.action.setBadgeText({text: result.ok ? "✓" : "!"});
  chrome.action.setBadgeBackgroundColor({color: result.ok ? "#24745a" : "#a14236"});
  chrome.action.setTitle({title: `OpenDownloader: ${message}`});
  chrome.storage.local.set({lastResult: {ok: result.ok, message, id: result.id || null, at: Date.now()}});
}
function nativeMessage(request, callback) {
  chrome.runtime.sendNativeMessage(HOST, request, (response) => {
    const error = chrome.runtime.lastError;
    callback(response, error ? `A ponte local não respondeu: ${error.message || "motivo não informado pelo navegador"}` : null);
  });
}
function checkBridge(callback) {
  nativeMessage({version:1,command:"status"}, (response, error) => {
    if(error) {callback({ok:false,error});return;}
    if(response?.ok===true && response.version===1 && response.status==="ready"
      && typeof response.app_version==="string" && /^\d+\.\d+\.\d+$/.test(response.app_version) && response.app_version.length<=32) {
      callback({ok:true,message:`Ponte local ${response.app_version} respondendo. Envie um link para confirmar o download no aplicativo.`});
    } else callback({ok:false,error:typeof response?.error==="string" ? response.error : "A ponte não confirmou o diagnóstico. Confira a versão instalada do OpenDownloader."});
  });
}
function sendLink(value, callback, hints = null) {
  let request;
  try {
    const url = LumeMedia.httpUrl(value), source = ["hls","dash"].includes(hints?.source) ? hints.source : null;
    request = hints ? {version:source ? 3 : 2,url,title:LumeMedia.title(hints.title),media:true,...(source ? {source} : {})} : {url};
  }
  catch (error) {const result = {ok: false, error: error.message}; showResult(result); callback?.(result); return;}
  nativeMessage(request, (response, error) => {
    let result;
    if (error) result = {ok: false, error};
    else if (response?.ok === true && typeof response.id === "string") result = {ok: true, id: response.id, message: response.warning || PENDING};
    else result = {ok: false, error: typeof response?.error === "string" ? response.error : "O OpenDownloader não confirmou o recebimento do link."};
    showResult(result); callback?.(result);
  });
}
function originPattern(value) {return new URL(LumeMedia.httpUrl(value)).origin + "/*";}
function emptyTab(url) {return {pageUrl: url, items: [], frames: {}, at: Date.now()};}
function grantError() {return Object.assign(new Error("O acesso a esta página foi desativado. Use Procurar nesta página para autorizar uma nova análise."), {code: "grant"});}
async function activeTab() {
  const [tab] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
  if (!tab || !Number.isInteger(tab.id) || tab.id < 0 || !tab.url) throw new Error("Abra uma página HTTP(S) para procurar vídeos.");
  try {originPattern(tab.url);} catch {throw new Error("Abra uma página HTTP(S) para procurar vídeos. Páginas internas do navegador não podem ser analisadas.");}
  return tab;
}
function inspectedFrame(value) {
  // Frame/document identity belongs to the browser. Special-scheme documents
  // do not acquire their parent's origin, even when the browser can inspect them.
  if (!value || !Number.isInteger(value.frameId) || value.frameId < 0 || !validDocumentId(value.documentId)) return null;
  try {
    const url=LumeMedia.httpUrl(value.result?.url);
    return {frameId:value.frameId,documentId:value.documentId,url,origin:new URL(url).origin+"/*"};
  } catch {return null;}
}
function withState(change) {
  const job = stateWrites.then(async () => {
    const {mediaTabs = {}, manualGrants = {}} = await chrome.storage.session.get(["mediaTabs", "manualGrants"]);
    const result = await change(mediaTabs, manualGrants);
    const newest = Object.entries(mediaTabs).sort((a,b) => b[1].at-a[1].at).slice(0, MAX_TABS);
    const kept = Object.fromEntries(newest), keptManual = {};
    for (const [id] of newest) if (manualGrants[id]) keptManual[id] = manualGrants[id];
    await chrome.storage.session.set({mediaTabs: kept, manualGrants: keptManual}); return result;
  });
  stateWrites = job.catch(() => {}); return job;
}
async function authorize(sender, manualGrants) {
  if(sender.documentLifecycle!==undefined&&sender.documentLifecycle!=="active")throw grantError();
  const origin = originPattern(sender.url), grant = manualGrants[sender.tab.id]?.[sender.frameId];
  if (grant?.documentId === sender.documentId && grant.origin === origin) return "manual";
  const {autoSites = []} = await chrome.storage.local.get("autoSites");
  if (autoSites.includes(origin) && await chrome.permissions.contains({origins:[origin]})) return "automatic";
  throw grantError();
}
function ownedFrame(tabs, sender) {
  let tab = tabs[sender.tab.id];
  if (!tab) tab = emptyTab(sender.frameId === 0 ? sender.url : sender.tab.url);
  tab.frames ||= {};
  if (!(sender.frameId in tab.frames) && Object.keys(tab.frames).length >= MAX_TAB_FRAMES) throw new Error("Limite de frames nesta aba atingido.");
  const previous = tab.frames[sender.frameId];
  if (previous?.documentId !== sender.documentId) tab.items = tab.items.filter(item => item.frameId !== sender.frameId);
  tab.frames[sender.frameId] = {documentId: sender.documentId, url: sender.url, origin: originPattern(sender.url)};
  if (sender.frameId === 0) tab.pageUrl = sender.url;
  tab.at = Date.now(); tabs[sender.tab.id] = tab; return tab;
}
function requireOwner(tabs,sender) {
  if(tabs[sender.tab.id]?.frames?.[sender.frameId]?.documentId!==sender.documentId)throw grantError();
}
function contentHello(sender) {return withState(async (tabs, manual) => {
  await authorize(sender, manual);
  // The browser resolves this exact current Document, preventing a delayed hello
  // from an unloaded frame from replacing a newer frame owner.
  const current=await chrome.scripting.executeScript({target:{tabId:sender.tab.id,documentIds:[sender.documentId]},func:()=>({url:location.href})});
  const identity=Array.isArray(current)&&current.length===1 ? inspectedFrame(current[0]) : null;
  if(!identity||identity.documentId!==sender.documentId||identity.frameId!==sender.frameId||identity.origin!==originPattern(sender.url))throw grantError();
  ownedFrame(tabs,sender);return {ok:true};
});}
function recordMedia(message, sender) {
  return withState(async (tabs, manual) => {
    await authorize(sender, manual);
    requireOwner(tabs,sender);
    if (!Array.isArray(message.items) || message.items.length > 32) throw new Error("Lista de mídias inválida.");
    const tab = ownedFrame(tabs, sender), items = [];
    for (const raw of message.items) {const item = LumeMedia.candidate(raw,sender.url); if(item) items.push({...item,frameId:sender.frameId});}
    const combined = [...items,...tab.items.filter(item=>item.frameId!==sender.frameId)];
    tab.items = combined.slice(0,MAX_TAB_MEDIA); tab.truncated = combined.length>MAX_TAB_MEDIA || Boolean(message.truncated); return {ok:true};
  });
}
function queueContent(message,sender) {
  return withState(async (tabs,manual) => {
    await authorize(sender,manual); requireOwner(tabs,sender);
    const item = LumeMedia.candidate(message.item,sender.url);
    if(!item?.supported) return {ok:false,error:item?.reason||"Mídia inválida."};
    return new Promise(resolve=>sendLink(item.url,resolve,item));
  });
}
function clearFrame(sender) {
  // Clear ownership only; no live grant is needed to release one's old document.
  return withState(async(tabs,manual)=>{
    const tab=tabs[sender.tab.id], record=tab?.frames?.[sender.frameId];
    if(record?.documentId===sender.documentId) {
      delete tab.frames[sender.frameId]; tab.items=tab.items.filter(item=>item.frameId!==sender.frameId);
    }
    if(manual[sender.tab.id]?.[sender.frameId]?.documentId===sender.documentId) delete manual[sender.tab.id][sender.frameId];
    return {ok:true};
  });
}
async function mediaList() {
  const tab = await activeTab(); return withState(async(tabs)=>{
    const found=tabs[tab.id]; if(!found||found.pageUrl!==tab.url) return {ok:true,items:[],truncated:false};
    // URL identity alone cannot collapse evidence from different frames or
    // discovery paths. An earlier resource row must not hide the EME signal
    // attached to a specific player, nor acquire that signal by merging.
    const unique=new Map(); for(const item of found.items) {
      const key=JSON.stringify([item.url,item.frameId,item.kind,item.discovery,item.component,item.protection,item.reason]);
      if(!unique.has(key))unique.set(key,item);
    }
    return {ok:true,items:[...unique.values()],truncated:Boolean(found.truncated)};
  });
}
async function injectFiles(tab) {
  const files=["media-core.js","content-script.js"];
  try {await chrome.scripting.executeScript({target:{tabId:tab.id,allFrames:true},files});}
  catch {await chrome.scripting.executeScript({target:{tabId:tab.id,frameIds:[0]},files});}
}
async function scanPage() {
  const tab=await activeTab(); let documents;
  const inspect={func:()=>({url:location.href})};
  try {documents=await chrome.scripting.executeScript({target:{tabId:tab.id,allFrames:true},...inspect});}
  catch {documents=await chrome.scripting.executeScript({target:{tabId:tab.id,frameIds:[0]},...inspect});}
  const frames=[],frameIds=new Set(),documentIds=new Set();
  const identityError=()=>new Error("O navegador não confirmou a identidade de documentos HTTP(S) nesta página. Recarregue-a e use Procurar nesta página novamente.");
  if(!Array.isArray(documents))throw identityError();
  for(const result of documents) {
    const frame=inspectedFrame(result);if(!frame)continue;
    if(frameIds.has(frame.frameId)||documentIds.has(frame.documentId))throw identityError();
    frames.push(frame);frameIds.add(frame.frameId);documentIds.add(frame.documentId);
    if(frames.length===MAX_TAB_FRAMES)break;
  }
  if(!frames.length)throw identityError();
  // Parse the complete selected batch before publishing any grant. A malformed
  // or unsupported subframe cannot abort a valid page or acquire inherited access.
  await withState(async(tabs,manual)=>{
    tabs[tab.id] ||= emptyTab(tab.url); tabs[tab.id].at=Date.now();
    manual[tab.id]=Object.fromEntries(frames.map(frame=>[frame.frameId,{documentId:frame.documentId,origin:frame.origin}]));
  });
  // Target only the Documents that received this explicit grant. If navigation
  // removes one, never retry against frame 0 or fresh/unvalidated allFrames.
  try {await chrome.scripting.executeScript({target:{tabId:tab.id,documentIds:frames.map(frame=>frame.documentId)},files:["media-core.js","content-script.js"]});}
  catch {throw new Error("A página mudou ou o navegador recusou o acesso aos documentos identificados. Recarregue-a e use Procurar nesta página novamente.");}
  return mediaList();
}
async function syncAutomaticScripts() {
  const {autoSites=[]}=await chrome.storage.local.get("autoSites"),permitted=[];
  for(const origin of autoSites.slice(0,128)) {try{if(originPattern(origin.slice(0,-1))===origin&&await chrome.permissions.contains({origins:[origin]}))permitted.push(origin);}catch{}}
  const existing=await chrome.scripting.getRegisteredContentScripts({ids:[AUTO_SCRIPT]});
  if(!permitted.length){if(existing.length)await chrome.scripting.unregisterContentScripts({ids:[AUTO_SCRIPT]});}
  else {const config={id:AUTO_SCRIPT,matches:permitted,js:["media-core.js","content-script.js"],allFrames:true,runAt:"document_idle",persistAcrossSessions:true};if(existing.length)await chrome.scripting.updateContentScripts([config]);else await chrome.scripting.registerContentScripts([config]);}
  await chrome.storage.local.set({autoSites:permitted});return permitted;
}
async function siteState() {
  const tab=await activeTab(),origin=originPattern(tab.url),{autoSites=[]}=await chrome.storage.local.get("autoSites");
  return {ok:true,origin,enabled:autoSites.includes(origin)&&await chrome.permissions.contains({origins:[origin]}),label:new URL(tab.url).host};
}
async function enableSite(message) {
  const tab=await activeTab(),origin=originPattern(tab.url);
  await withState(async()=>{
    if(message.origin!==origin||!await chrome.permissions.contains({origins:[origin]}))throw new Error("O site mudou ou a permissão não foi concedida. Reabra a extensão.");
    const {autoSites=[]}=await chrome.storage.local.get("autoSites");
    if(!autoSites.includes(origin)&&autoSites.length>=128)throw new Error("Limite de 128 sites automáticos atingido.");
    await chrome.storage.local.set({autoSites:[...new Set([...autoSites,origin])]});await syncAutomaticScripts();
  });
  // Automatic activation never creates an activeTab/manual grant.
  await injectFiles(tab);return mediaList();
}
function patternMatches(pattern,url) {
  if(pattern==="<all_urls>")return true;
  const match=/^(\*|https?):\/\/([^/]+)\/.*$/.exec(pattern);if(!match)return false;
  const value=new URL(url),scheme=value.protocol.slice(0,-1);if(match[1]!=="*"&&match[1]!==scheme)return false;
  let authority=match[2],port="*",host=authority;
  const split=authority.lastIndexOf(":");if(split>=0&&(!authority.startsWith("[")||split>authority.indexOf("]"))){host=authority.slice(0,split);port=authority.slice(split+1);}
  if(port!=="*"&&port!==(value.port||(scheme==="https"?"443":"80")))return false;
  return host==="*"||host===value.hostname||(host.startsWith("*.")&&(value.hostname===host.slice(2)||value.hostname.endsWith(host.slice(1))));
}
function invalidateOrigins(tabs,manual,matches) {
  const targets=[];
  for(const [id,tab]of Object.entries(tabs)){
    for(const [frame,record]of Object.entries(tab.frames||{}))if(matches(record.url)){
      targets.push({id:Number(id),frame:Number(frame),documentId:record.documentId});delete tab.frames[frame];tab.items=tab.items.filter(item=>item.frameId!==Number(frame));
    }
  }
  for(const [id,frames]of Object.entries(manual))for(const [frame,record]of Object.entries(frames))if(matches(record.origin.slice(0,-1)))delete manual[id][frame];
  return targets;
}
async function teardownTargets(targets) {
  for(const target of targets)try{await chrome.tabs.sendMessage(target.id,{type:"lume-teardown"},{documentId:target.documentId});}catch{}
}
async function disableSite(message) {
  const tab=await activeTab(),origin=originPattern(tab.url);if(message.origin!==origin)throw new Error("O site mudou. Reabra a extensão.");
  const targets=await withState(async(tabs,manual)=>{
    const targets=invalidateOrigins(tabs,manual,url=>originPattern(url)===origin);
    const {autoSites=[]}=await chrome.storage.local.get("autoSites");await chrome.storage.local.set({autoSites:autoSites.filter(item=>item!==origin)});await syncAutomaticScripts();
    await chrome.permissions.remove({origins:[origin]});return targets;
  });await teardownTargets(targets);return {ok:true};
}
async function revokeRemovedSites(event) {
  const targets=await withState(async(tabs,manual)=>{
    const {autoSites=[]}=await chrome.storage.local.get("autoSites"),remaining=await syncAutomaticScripts();
    const removed=autoSites.filter(origin=>!remaining.includes(origin)),patterns=Array.isArray(event?.origins)?event.origins:[];
    return invalidateOrigins(tabs,manual,url=>removed.includes(originPattern(url))||patterns.some(pattern=>patternMatches(pattern,url)));
  });await teardownTargets(targets);
}
function validDocumentId(value){return typeof value==="string"&&value.length>0&&value.length<=128&&!/[\u0000-\u0020]/u.test(value);}
function isPopup(sender){return sender.id===chrome.runtime.id&&sender.url===chrome.runtime.getURL("popup.html")&&!sender.tab;}
function isContent(sender){
  if(sender.id!==chrome.runtime.id||!Number.isInteger(sender.tab?.id)||sender.tab.id<0||!Number.isInteger(sender.frameId)||sender.frameId<0||!validDocumentId(sender.documentId))return false;
  try{LumeMedia.httpUrl(sender.url);return true;}catch{return false;}
}
chrome.runtime.onInstalled.addListener(()=>{
  ensureMenu();
  withState(async()=>{await syncAutomaticScripts();}).catch(()=>{});
});
chrome.runtime.onStartup.addListener(()=>{ensureMenu();withState(async()=>{await syncAutomaticScripts();}).catch(()=>{});});
chrome.permissions.onRemoved.addListener(event=>{revokeRemovedSites(event).catch(()=>{});});
chrome.tabs.onRemoved.addListener(id=>{withState(async(tabs,manual)=>{delete tabs[id];delete manual[id];}).catch(()=>{});});
chrome.tabs.onUpdated.addListener((id,change)=>{
  if(change.status!=="loading"&&!change.url)return;
  withState(async(tabs,manual)=>{
    if(change.status==="loading"||!tabs[id]?.pageUrl||originPattern(change.url||tabs[id].pageUrl)!==originPattern(tabs[id].pageUrl)){delete manual[id];delete tabs[id];}
    else {tabs[id].items=[];tabs[id].pageUrl=change.url||tabs[id].pageUrl;tabs[id].at=Date.now();}
  }).catch(()=>{});
});
chrome.contextMenus.onClicked.addListener(info=>{
  if(info.menuItemId!==MENU_ID)return;
  if(["video","audio"].includes(info.mediaType)){const item=LumeMedia.candidate({url:info.srcUrl,title:"",kind:info.mediaType},info.pageUrl);if(item?.supported)sendLink(item.url,null,item);else showResult({ok:false,error:item?.reason||"O player não oferece um arquivo direto."});}
  else sendLink(info.linkUrl);
});
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(!message||typeof message!=="object")return false;
  const popup=isPopup(sender),content=isContent(sender);let job;
  if(popup&&message.type==="menu-status")job=ensureMenu();
  else if(popup&&message.type==="bridge-status"){checkBridge(sendResponse);return true;}
  else if(popup&&message.type==="queue-url"){sendLink(message.url,sendResponse,message.media===true?{title:message.title,source:message.source}:null);return true;}
  else if(content&&message.type==="content-hello")job=contentHello(sender);
  else if(content&&message.type==="content-report")job=recordMedia(message,sender);
  else if(content&&message.type==="content-queue")job=queueContent(message,sender);
  else if(content&&message.type==="content-clear")job=clearFrame(sender);
  else if(popup&&message.type==="media-list")job=mediaList();
  else if(popup&&message.type==="scan-page")job=scanPage();
  else if(popup&&message.type==="site-state")job=siteState();
  else if(popup&&message.type==="site-enable")job=enableSite(message);
  else if(popup&&message.type==="site-disable")job=disableSite(message);
  else return false;
  Promise.resolve(job).then(sendResponse,error=>sendResponse({ok:false,error:error.message||"A operação não pôde ser concluída.",code:error.code}));return true;
});
