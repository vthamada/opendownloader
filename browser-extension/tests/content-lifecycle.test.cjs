// Production content-script lifecycle with deterministic DOM/API doubles.
// Not a claim of real browser rendering or extension installation.
"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const root=path.join(__dirname,"..");
function events(){const listeners=new Map();return {addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,new Set());listeners.get(type).add(fn);},removeEventListener(type,fn){listeners.get(type)?.delete(fn);},dispatch(type,event={}){for(const fn of listeners.get(type)||[])fn(event);},listeners};}
function harness(options={}){
 const sent=[],timeouts=new Map(),intervals=new Map(),rafs=new Map(),shadows=[],roots=[],probes={mediaQueries:0,sourceReads:0},lifecycle=events();let next=1,allowed=true,observer;
 class Element{
  constructor(tag){this.tagName=tag.toUpperCase();this.nodeType=1;this.isConnected=true;this.children=[];this.style={setProperty(){}};this.events=events();this.src="";this.currentSrc="";this.attributes={};}
  append(...nodes){this.children.push(...nodes);for(const node of nodes)node.parent=this;}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  remove(){this.isConnected=false;if(this.parent)this.parent.children=this.parent.children.filter(item=>item!==this);}
  attachShadow(){const shadow=new Element("shadow");shadows.push(shadow);return shadow;}
  addEventListener(...args){this.events.addEventListener(...args);}setAttribute(){}
  removeEventListener(...args){this.events.removeEventListener(...args);}getAttribute(name){return this.attributes[name]??null;}getRootNode(){return this.root||document;}
  getBoundingClientRect(){return {left:10,top:10,right:330,bottom:210,width:320,height:200};}
  matches(selector){return selector.split(",").includes(this.tagName.toLowerCase());}
  querySelector(selector){return selector==="source[src]"?{src:"https://cdn.example.org/from-source.mp4"}:null;}
  querySelectorAll(){return boundedList(100000,8,i=>{probes.sourceReads++;return {src:`https://cdn.example.org/alternative-${i}.mp4`};});}
 }
 function boundedList(length,cap,get){return new Proxy({length},{get(target,key){if(key===Symbol.iterator)throw new Error("Unbounded NodeList expansion");if(/^\d+$/.test(String(key))){const index=Number(key);if(index>=cap)throw new Error(`Read beyond ${cap} retained entries`);return get(index);}return target[key];}});}
 const players=Array.from({length:64},(_,i)=>{const player=new Element(i===1?"audio":"video");player.currentSrc=options.blob?`blob:https://example.org/player-${i}`:i===2?"":`https://cdn.example.org/player-${i}.mp4`;return player;});
 const document={...events(),title:"a".repeat(255)+"😀",documentElement:new Element("html"),createElement:tag=>new Element(tag),querySelectorAll(){probes.mediaQueries++;return boundedList(100000,64,i=>players[i]);}};
 let shadowHost=null,skinRoot=null,mediaRoot=null;
 if(options.openShadow){
  const skin=new Element('video-minimal-skin');skinRoot=new Element('shadow-root');skin.shadowRoot=skinRoot;skinRoot.host=skin;
  shadowHost=new Element('hlsjs-video');shadowHost.root=skinRoot;shadowHost.attributes.src=options.componentUrl||'https://cdn.example.org/manifest?u=caller&t=value';
  mediaRoot=new Element('shadow-root');mediaRoot.host=shadowHost;shadowHost.shadowRoot=mediaRoot;players[0].root=mediaRoot;
  document.createTreeWalker=root=>{const nodes=root===document?[skin]:root===skinRoot?[shadowHost]:root===mediaRoot?[players[0]]:[];let index=0;return{nextNode:()=>nodes[index++]||null};};
  document.querySelectorAll=()=>[];
 }
 if(options.treeOverflow)document.createTreeWalker=()=>({nextNode(){probes.walkReads=(probes.walkReads||0)+1;if(probes.walkReads>4097)throw new Error('DOM traversal exceeds4096 inspectednodes');return new Element('div');}});
 const messages={listeners:new Set(),addListener(fn){this.listeners.add(fn);},removeListener(fn){this.listeners.delete(fn);}};
 const chrome={runtime:{id:"self",onMessage:messages,sendMessage:async message=>{sent.push(message);return allowed?{ok:true,message:"Confirme no OpenDownloader"}:{ok:false,code:"grant"};}}};
 let performanceObserver;const performanceEntries=options.resources||[];
 const context=vm.createContext({chrome,document,location:{href:"https://example.org/watch"},window:lifecycle,innerWidth:1200,innerHeight:800,
  performance:{getEntriesByType:()=>performanceEntries,now:()=>10000},PerformanceObserver:class{constructor(callback){this.callback=callback;performanceObserver=this;}observe(config){this.active=true;this.config=config;}disconnect(){this.active=false;}},
  setTimeout:(fn,delay)=>{const id=next++;timeouts.set(id,{fn,delay});return id;},clearTimeout:id=>timeouts.delete(id),setInterval:(fn,delay)=>{const id=next++;intervals.set(id,{fn,delay});return id;},clearInterval:id=>intervals.delete(id),requestAnimationFrame:fn=>{const id=next++;rafs.set(id,fn);return id;},cancelAnimationFrame:id=>rafs.delete(id),
  MutationObserver:class{constructor(callback){this.callback=callback;this.roots=[];observer=this;}observe(root){this.active=true;this.roots.push(root);}disconnect(){this.active=false;this.roots=[];}},ResizeObserver:class{observe(){}unobserve(){}disconnect(){}},URL});
 vm.runInContext(fs.readFileSync(path.join(root,"media-core.js"),"utf8"),context);
 return {context,document,players,sent,timeouts,intervals,shadows,probes,lifecycle,messages,shadowHost,skinRoot,mediaRoot,getObserver:()=>observer,getPerformanceObserver:()=>performanceObserver,deny:()=>{allowed=false;},run:()=>vm.runInContext(fs.readFileSync(path.join(root,"content-script.js"),"utf8"),context)};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const h=harness();await h.run();
 assert.equal(h.probes.mediaQueries,1);assert.equal(h.document.documentElement.children.length,64);assert.equal(h.probes.sourceReads,64*8);
 const report=h.sent.find(message=>message.type==="content-report");assert.equal(report.items.length,32);assert.equal(report.truncated,true);assert.equal(report.items[2].url,"https://cdn.example.org/alternative-1.mp4");
 assert.ok(report.items.every(item=>![...item.title].some(char=>char.length===1&&/[\ud800-\udfff]/u.test(char))));
 const beforeTimers=h.timeouts.size;
 h.getObserver().callback([{type:"attributes",target:{matches:()=>false}}]);assert.equal(h.timeouts.size,beforeTimers);
 h.getObserver().callback([{type:"childList",addedNodes:[{nodeType:1,matches:()=>false,querySelector:()=>null}],removedNodes:[]}]);assert.equal(h.timeouts.size,beforeTimers);
 h.getObserver().callback([{type:"attributes",target:h.players[0]}]);assert.equal(h.timeouts.size,beforeTimers+1);
 h.getObserver().callback([{type:"attributes",target:h.players[0]}]);assert.equal(h.timeouts.size,beforeTimers+1);
 const button=h.shadows[0].children.find(node=>node.tagName==="BUTTON");
 button.events.dispatch("click",{isTrusted:false});await tick();assert.equal(h.sent.some(message=>message.type==="content-queue"),false);
 button.events.dispatch("click",{isTrusted:true,preventDefault(){},stopPropagation(){}});await tick();assert.equal(h.sent.filter(message=>message.type==="content-queue").length,1);
 h.lifecycle.dispatch("pagehide",{persisted:true});await tick();assert.equal(h.document.documentElement.children.length,0);assert.equal(h.intervals.size,0);assert.equal(h.timeouts.size,0);assert.equal(h.getObserver().active,false);assert.equal(h.sent.at(-1).type,"content-clear");
 h.lifecycle.dispatch("pageshow",{persisted:true});await tick();assert.equal(h.document.documentElement.children.length,64);assert.equal(h.intervals.size,1);assert.equal(h.getObserver().active,true);assert.equal(h.sent.filter(message=>message.type==="content-hello").length,2);
 await h.run();assert.equal(h.document.documentElement.children.length,64);assert.equal(h.intervals.size,1);
 h.lifecycle.dispatch("pagehide",{persisted:true});h.deny();h.lifecycle.dispatch("pageshow",{persisted:true});await tick();assert.equal(h.document.documentElement.children.length,0);assert.equal(h.context.__lumeMediaController,undefined);assert.equal(h.messages.listeners.size,0);
 // A blob is never guessed to belong to one request. The button requires an
 // explicit source choice, with frame-level warning, even for a single source.
 const streaming=harness({blob:true,resources:[{name:"https://cdn.example.org/master.m3u8",startTime:1},{name:"https://ads.example.org/ad.mpd",startTime:2},{name:"https://example.org/secret.json",startTime:3}]});await streaming.run();
 const sourceReport=streaming.sent.find(message=>message.type==="content-report");assert.equal(sourceReport.items[0].discovery,"resource");assert.equal(sourceReport.items[0].url,"https://cdn.example.org/master.m3u8");
 assert.equal(sourceReport.items.some(item=>item.url==="https://example.org/secret.json"),false);
 const sourceButton=streaming.shadows[0].children.find(node=>node.tagName==="BUTTON");assert.equal(sourceButton.textContent,"↓ Escolher fonte detectada");assert.equal(sourceButton.disabled,false);
 sourceButton.events.dispatch("click",{isTrusted:true,preventDefault(){},stopPropagation(){}});await tick();assert.equal(streaming.sent.some(message=>message.type==="content-queue"),false);
 const chooser=streaming.shadows[0].children.at(-1);assert.match(chooser.children[0].textContent,/Podem incluir anúncios/);assert.equal(chooser.children.filter(node=>node.tagName==="BUTTON").length,2);
 const sourceChoice=chooser.children.find(node=>node.tagName==="BUTTON");sourceChoice.events.dispatch("click",{isTrusted:true,preventDefault(){},stopPropagation(){}});await tick();
 assert.equal(streaming.sent.find(message=>message.type==="content-queue").item.source,"hls");
 assert.equal(streaming.getPerformanceObserver().config.type,"resource");assert.equal(streaming.getPerformanceObserver().config.buffered,true);
 const pObserver=streaming.getPerformanceObserver();pObserver.callback({getEntries:()=>[{name:"https://cdn.example.org/late.mpd",startTime:4}]});assert.ok(streaming.timeouts.size);
 streaming.lifecycle.dispatch("pagehide",{persisted:true});assert.equal(pObserver.active,false);
 const countAfterHide=streaming.sent.length;pObserver.callback({getEntries:()=>[{name:"https://cdn.example.org/stale.mpd",startTime:5}]});assert.equal(streaming.sent.length,countAfterHide);
 const manyResources=Array.from({length:700},(_,i)=>({name:`https://cdn.example.org/playlist-${i}.m3u8`,startTime:i}));
 const boundedResources=new Proxy(manyResources,{get(target,key){if(/^\d+$/.test(String(key))&&Number(key)<188)throw new Error("Resource batch expanded beyond newest512");return target[key];}});
 const bounded=harness({blob:true,resources:boundedResources});await bounded.run();const boundedReport=bounded.sent.find(message=>message.type==="content-report");assert.equal(boundedReport.items.filter(item=>item.discovery==="resource").length,24);assert.equal(boundedReport.truncated,true);assert.equal(boundedReport.items[0].url,"https://cdn.example.org/playlist-676.m3u8");
 // Same-document route changes release old observed URLs. Old resource timing
 // entries cannot repopulate the newly displayed route's candidates.
 bounded.context.location.href="https://example.org/next";for(const interval of bounded.intervals.values())interval.fn();await tick();
 await bounded.context.__lumeMediaController.resume();assert.equal(bounded.sent.findLast(message=>message.type==="content-report").items.some(item=>item.discovery==="resource"),false);
 // Real user structure: VIDEO-MINIMAL-SKIN -> open root -> HLSJS-VIDEO
 // extensionless public src -> open root -> VIDEO blob. No global qSA hit.
 const shadow=harness({openShadow:true,blob:true});await shadow.run();assert.equal(shadow.document.querySelectorAll('video,audio').length,0);assert.equal(shadow.document.documentElement.children.length,1);
 assert.equal(shadow.getObserver().roots.length,3);assert.equal(shadow.mediaRoot.events.listeners.get('loadedmetadata').size,1);
 const componentReport=shadow.sent.find(message=>message.type==='content-report');assert.equal(componentReport.items[0].source,'hls');assert.equal(componentReport.items[0].url,'https://cdn.example.org/manifest?u=caller&t=value');assert.equal(componentReport.items[0].discovery,'component');assert.match(componentReport.items[0].evidence,/HLSJS-VIDEO/);
 const shadowButton=shadow.shadows[0].children.find(node=>node.tagName==='BUTTON');assert.equal(shadowButton.disabled,false);shadowButton.events.dispatch('click',{isTrusted:true,preventDefault(){},stopPropagation(){}});await tick();assert.equal(shadow.sent.find(message=>message.type==='content-queue').item.source,'hls');
 shadow.shadowHost.attributes.src='https://cdn.example.org/new-manifest?exact=query';shadow.getObserver().callback([{type:'attributes',target:shadow.shadowHost}]);assert.ok(shadow.timeouts.size);
 shadow.lifecycle.dispatch('pagehide',{persisted:true});assert.equal(shadow.mediaRoot.events.listeners.get('loadedmetadata').size,0);assert.equal(shadow.getObserver().roots.length,0);
 shadow.lifecycle.dispatch('pageshow',{persisted:true});await tick();assert.equal(shadow.getObserver().roots.length,3);assert.equal(shadow.mediaRoot.events.listeners.get('loadedmetadata').size,1);
 // EME evidence stays with the exact player; observed requests elsewhere in
 // the frame never acquire that protection classification. No event payload
 // (initData) or licence data is read, and listeners are released on BFCache.
 const protectedPlayer=harness({blob:true,resources:[{name:'https://cdn.example.org/ad.m3u8',startTime:4}]});
 protectedPlayer.players[0].mediaKeys={};await protectedPlayer.run();
 let protectedReport=protectedPlayer.sent.findLast(message=>message.type==='content-report');
 assert.equal(protectedReport.items[0].protection,undefined);
 assert.equal(protectedReport.items[1].protection,'eme-observed');
 assert.equal(protectedPlayer.players[0].events.listeners.get('encrypted').size,1);
 protectedPlayer.lifecycle.dispatch('pagehide',{persisted:true});
 assert.equal(protectedPlayer.players[0].events.listeners.get('encrypted').size,0);
 protectedPlayer.players[0].mediaKeys=null;protectedPlayer.lifecycle.dispatch('pageshow',{persisted:true});await tick();
 const noRead={isTrusted:true,get initData(){throw new Error('Do not inspect encryption payload');}};
 protectedPlayer.players[0].events.dispatch('encrypted',noRead);
 Object.defineProperty(protectedPlayer.players[0],'mediaKeys',{configurable:true,get(){throw new Error('Getter unavailable in this context');}});
 await protectedPlayer.context.__lumeMediaController.refresh();
 protectedReport=protectedPlayer.sent.findLast(message=>message.type==='content-report');
 assert.equal(protectedReport.items[1].protection,'eme-observed');
 protectedPlayer.players[0].events.dispatch('emptied',{isTrusted:false});await protectedPlayer.context.__lumeMediaController.refresh();
 assert.equal(protectedPlayer.sent.findLast(message=>message.type==='content-report').items[1].protection,'eme-observed');
 protectedPlayer.players[0].events.dispatch('emptied',{isTrusted:true});await protectedPlayer.context.__lumeMediaController.refresh();
 assert.equal(protectedPlayer.sent.findLast(message=>message.type==='content-report').items[1].protection,undefined);
 protectedPlayer.players[0].events.dispatch('encrypted',{isTrusted:false});await protectedPlayer.context.__lumeMediaController.refresh();
 assert.equal(protectedPlayer.sent.findLast(message=>message.type==='content-report').items[1].protection,undefined);
 protectedPlayer.context.__lumeMediaController.teardown();
 assert.equal(protectedPlayer.players[0].events.listeners.get('emptied').size,0);
 const treeBound=harness({treeOverflow:true});await treeBound.run();assert.equal(treeBound.probes.walkReads,4097);assert.equal(treeBound.sent.find(message=>message.type==='content-report').truncated,true);assert.equal(treeBound.document.documentElement.children.length,0);
 console.log("Content script bounded producer, relevant mutations, trusted click and BFCache lifecycle tests passed (DOM/API simulation).");
})().catch(error=>{console.error(error);process.exitCode=1;});
