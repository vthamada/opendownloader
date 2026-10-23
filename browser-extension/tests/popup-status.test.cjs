// Public popup events and rendered text. Simulated DOM/APIs, not browser UI proof.
"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'..'),version=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8')).version;
class Element {
  constructor(){this.textContent='';this.disabled=false;this.children=[];this.events=new Map();this.classes=new Set();this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name),remove:name=>this.classes.delete(name)};}
  addEventListener(name,handler){this.events.set(name,handler);}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(){this.children=[];}
  fire(name,event={}){return this.events.get(name)?.(event);}
}
function harness(){
  const elements=new Map(),pending=[],bridgePending=[],calls=[],tabs=[];let failScan=false;
  const document={getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element()};
  const chrome={tabs:{create:(options,callback)=>{tabs.push(options);callback();}},runtime:{getURL:file=>`chrome-extension://test/${file}`,getManifest:()=>({version}),sendMessage:(body,callback)=>{
    calls.push(body);
    if(body.type==='bridge-status'){bridgePending.push(callback);return;}
    if(body.type==='scan-page'){if(failScan)throw new Error('Extension context invalidated');pending.push(callback);return;}
    callback(body.type==='site-state'?{ok:true,origin:'https://example.org/*',label:'example.org',enabled:false}:body.type==='media-list'?{ok:true,items:[]}:{ok:true});
  }},storage:{local:{get:(_key,callback)=>callback({lastResult:{ok:false,message:'Old host error',at:1}})}}};
  vm.runInNewContext(fs.readFileSync(path.join(root,'popup.js'),'utf8'),{document,chrome,URL,Date});
  return {elements,calls,tabs,resolveBridge:value=>bridgePending.shift()(value),resolveScan:value=>pending.shift()(value),failScan:()=>{failScan=true;}};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const h=harness();await tick();
  h.elements.get('record').fire('click');assert.equal(h.tabs[0].url,'chrome-extension://test/recorder.html');
  assert.match(h.elements.get('menu-status').textContent,new RegExp(version.replaceAll('.','\\.')),'popup identifies the running extension version');
  assert.equal(h.calls.some(call=>call.type==='bridge-status'),false,'only an explicit click starts the native diagnostic');
  const bridge=h.elements.get('bridge');
  let diagnosis=bridge.fire('click');const bridgeStatus=h.elements.get('bridge-status');
  assert.equal(bridge.disabled,true);assert.match(bridgeStatus.textContent,/Verificando/);
  h.resolveBridge({ok:true,message:'Ponte local 1.6.0 respondendo.'});await diagnosis;
  assert.equal(bridge.disabled,false);assert.match(bridgeStatus.textContent,/respondendo/);assert.equal(bridgeStatus.classes.has('error'),false);
  diagnosis=bridge.fire('click');h.resolveBridge({ok:false,error:'Specified native messaging host not found.'});await diagnosis;
  assert.equal(bridge.disabled,false);assert.match(bridgeStatus.textContent,/host not found/);assert.equal(bridgeStatus.classes.has('error'),true);
  const scan=h.elements.get('scan'),status=h.elements.get('status');
  const first=scan.fire('click');
  assert.equal(scan.disabled,true);assert.match(status.textContent,/Procurando/);assert.equal(status.classes.has('error'),false,'new scan clears stale native failure');
  h.resolveScan({ok:true,items:[{supported:true,kind:'video',title:'Test',url:'https://cdn.example.org/master.m3u8',source:'hls'}]});await first;
  assert.equal(scan.disabled,false);assert.match(status.textContent,/1 fonte/);assert.equal(status.classes.has('error'),false);
  const empty=scan.fire('click');h.resolveScan({ok:true,items:[]});await empty;
  assert.match(status.textContent,/Busca concluída/);assert.match(h.elements.get('media-list').children[0].textContent,/Nenhum player/);
  const rejected=scan.fire('click');h.resolveScan({ok:false,error:'Access denied for this document'});await rejected;
  assert.match(status.textContent,/A busca de mídia não foi concluída: Access denied for this document/);assert.equal(status.classes.has('error'),true);assert.equal(scan.disabled,false);
  h.failScan();await scan.fire('click');assert.match(status.textContent,/busca não respondeu/);assert.equal(status.classes.has('error'),true);assert.equal(scan.disabled,false);
  console.log('Popup running version and scan start/success/empty/error status passed (DOM/API simulation).');
})().catch(error=>{console.error(error);process.exitCode=1;});
