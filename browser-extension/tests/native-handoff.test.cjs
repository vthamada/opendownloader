// Public extension event boundary tests. These do not prove Chrome/Edge UI behavior.
"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const root=path.join(__dirname,"..");
function event(){const listeners=[];return {addListener:fn=>listeners.push(fn),removeListener:fn=>{const i=listeners.indexOf(fn);if(i>=0)listeners.splice(i,1);},emit:(...args)=>listeners.map(fn=>fn(...args)),listeners};}
const copy=value=>JSON.parse(JSON.stringify(value));
function harness(){
  const calls=[],local={},session={},permissions=new Set(),scripts=[],documents=new Map();
  let tab={id:7,url:"https://example.org/watch"},native={ok:true,id:"6f4bdcb1-4cbb-428d-908e-94f5fa90f694"};
  const initialDoc={frameId:0,documentId:"doc-7-main",result:{url:tab.url}};documents.set(7,[initialDoc]);
  function area(data){return {get:async key=>Object.fromEntries((Array.isArray(key)?key:[key]).map(k=>[k,data[k]===undefined?undefined:copy(data[k])])),set:item=>{Object.assign(data,copy(item));return Promise.resolve();}};}
  const chrome={
    runtime:{id:"abcdefghijklmnopabcdefghijklmnop",lastError:undefined,getURL:file=>`chrome-extension://abcdefghijklmnopabcdefghijklmnop/${file}`,onInstalled:event(),onStartup:event(),onMessage:event(),sendNativeMessage:(host,body,callback)=>{calls.push({host,body:copy(body)});callback(native);}},
    action:{setBadgeText(){},setBadgeBackgroundColor(){},setTitle(){}},storage:{local:area(local),session:area(session)},contextMenus:{update:(_,value,callback)=>{calls.push({menu:value});callback();},create:(value,callback)=>{calls.push({menu:value});callback();},onClicked:event()},
    tabs:{query:async()=>[copy(tab)],sendMessage:async(id,message,options)=>{calls.push({tabMessage:{id,message,options}});},onRemoved:event(),onUpdated:event()},
    permissions:{contains:async({origins})=>origins.every(origin=>permissions.has(origin)),remove:async({origins})=>{origins.forEach(origin=>permissions.delete(origin));return true;},onRemoved:event()},
    scripting:{executeScript:async config=>{calls.push({injection:config});if(!config.func)return [];const docs=documents.get(config.target.tabId)||[];return copy(config.target.documentIds?docs.filter(d=>config.target.documentIds.includes(d.documentId)):docs);},getRegisteredContentScripts:async()=>copy(scripts),registerContentScripts:async configs=>{scripts.push(...copy(configs));},updateContentScripts:async configs=>{scripts.splice(0,scripts.length,...copy(configs));},unregisterContentScripts:async()=>{scripts.length=0;}}
  };
  const context=vm.createContext({chrome,URL});context.importScripts=file=>vm.runInContext(fs.readFileSync(path.join(root,file),"utf8"),context);vm.runInContext(fs.readFileSync(path.join(root,"service-worker.js"),"utf8"),context);
  const popup={id:chrome.runtime.id,url:chrome.runtime.getURL("popup.html")},content={id:chrome.runtime.id,url:tab.url,frameId:0,documentId:initialDoc.documentId,documentLifecycle:"active",tab:copy(tab)};
  function message(body,sender=popup){return new Promise(resolve=>{const accepted=chrome.runtime.onMessage.listeners[0](body,sender,resolve);if(!accepted)resolve({rejected:true});});}
  return {calls,local,session,permissions,scripts,documents,chrome,popup,content,message,setNative:value=>{native=value;},setTab:value=>{tab=value;}};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function hello(h,sender){assert.equal((await h.message({type:"content-hello"},sender)).ok,true);}
const item={kind:"video",url:"https://cdn.example.org/v.mp4",title:"Clip"};
(async()=>{
 const diagnostic=harness();
 for(const sender of [diagnostic.content,{id:"foreign",url:diagnostic.popup.url},{...diagnostic.popup,tab:{id:7}}])
   assert.deepEqual(await diagnostic.message({type:"bridge-status"},sender),{rejected:true});
 assert.equal(diagnostic.calls.filter(call=>call.host).length,0);
 diagnostic.setNative({ok:true,version:1,status:"ready",app_version:"1.6.0"});
 assert.match((await diagnostic.message({type:"bridge-status"})).message,/1\.6\.0 respondendo/);
 assert.deepEqual(diagnostic.calls.filter(call=>call.host),[{host:"app.lume.downloads",body:{version:1,command:"status"}}]);
 assert.equal(diagnostic.local.lastResult,undefined);
 diagnostic.chrome.runtime.lastError={message:"Specified native messaging host not found."};
 assert.match((await diagnostic.message({type:"bridge-status"})).error,/host not found/);
 diagnostic.chrome.runtime.lastError=undefined;
 diagnostic.setNative({ok:true,id:"download-receipt"});assert.equal((await diagnostic.message({type:"bridge-status"})).ok,false);
 const h=harness(),nativeCalls=()=>h.calls.filter(call=>call.host);
 h.chrome.runtime.onInstalled.emit();await tick();assert.equal(h.calls.find(call=>call.menu).menu.title,"Baixar com OpenDownloader");
 assert.deepEqual(await h.message({type:"queue-url",url:item.url},{id:"foreign",url:"https://example.org"}),{rejected:true});
 assert.deepEqual(await h.message({type:"queue-url",url:item.url},h.content),{rejected:true});
 for(const url of ["file:///C:/secret","javascript:alert(1)","https://user:secret@example.org","https://example.org\nInjected","https://example.org/"+"a".repeat(8192)])h.chrome.contextMenus.onClicked.emit({menuItemId:"lume-download-link",linkUrl:url});assert.equal(nativeCalls().length,0);
 const original="https://example.org/file?caller=value#fragment";h.chrome.contextMenus.onClicked.emit({menuItemId:"lume-download-link",linkUrl:original});assert.deepEqual(nativeCalls()[0],{host:"app.lume.downloads",body:{url:original}});assert.match(h.local.lastResult.message,/Confirme as opções/);assert.ok(Number.isFinite(h.local.lastResult.at));
 h.chrome.runtime.lastError={message:"host unavailable"};assert.match((await h.message({type:"queue-url",url:original})).error,/host unavailable/);h.chrome.runtime.lastError=undefined;
 h.setNative({ok:true,id:"id",warning:"Link salvo; abra o OpenDownloader manualmente."});h.chrome.contextMenus.onClicked.emit({menuItemId:"lume-download-link",linkUrl:original});assert.equal(h.local.lastResult.message,"Link salvo; abra o OpenDownloader manualmente.");h.setNative({ok:false,error:"Cannot persist inbox"});h.chrome.contextMenus.onClicked.emit({menuItemId:"lume-download-link",linkUrl:original});assert.equal(h.local.lastResult.message,"Cannot persist inbox");h.setNative({ok:true,id:"id"});
 assert.equal((await h.message({type:"content-queue",item},h.content)).code,"grant");assert.equal((await h.message({type:"content-hello"},h.content)).code,"grant");
 assert.equal((await h.message({type:"scan-page"})).ok,true);await hello(h,h.content);assert.equal(h.session.manualGrants[7][0].documentId,h.content.documentId);
 const count=nativeCalls().length;const blob=await h.message({type:"content-queue",item:{kind:"video",url:"blob:https://example.org/id"}},h.content);assert.equal(blob.ok,false);assert.match(blob.error,/blob/);assert.equal(nativeCalls().length,count);
 await h.message({type:"content-queue",item:{kind:"audio",url:"https://cdn.example.org/a.mp3",title:"Audio\nname"}},h.content);assert.deepEqual(nativeCalls().at(-1).body,{version:2,url:"https://cdn.example.org/a.mp3",title:"Audio name",media:true});
 await h.message({type:"content-queue",item:{kind:"video",url:"https://cdn.example.org/master.m3u8",title:"Stream"}},h.content);assert.deepEqual(nativeCalls().at(-1).body,{version:3,url:"https://cdn.example.org/master.m3u8",title:"Stream",media:true,source:"hls"});
 await h.message({type:"content-queue",item:{kind:"video",url:"https://cdn.example.org/manifest",mimetype:"application/dash+xml",title:"DASH"}},h.content);assert.equal(nativeCalls().at(-1).body.version,3);assert.equal(nativeCalls().at(-1).body.source,"dash");
 await h.message({type:"queue-url",url:"https://cdn.example.org/manifest",title:"Popup choice",media:true,source:"hls"});assert.equal(nativeCalls().at(-1).body.version,3);assert.equal(nativeCalls().at(-1).body.source,"hls");
 await h.message({type:'content-report',items:[{url:'https://cdn.example.org/proxy?u=original&t=caller',kind:'video',title:'Component',mimetype:'application/vnd.apple.mpegurl',discovery:'component',component:'HLSJS-VIDEO'}]},h.content);
 const componentChoice=(await h.message({type:'media-list'})).items[0];assert.equal(componentChoice.source,'hls');assert.equal(componentChoice.discovery,'component');
 await h.message({type:'content-queue',item:componentChoice},h.content);assert.equal(nativeCalls().at(-1).body.version,3);assert.equal(nativeCalls().at(-1).body.source,'hls');assert.equal(nativeCalls().at(-1).body.url,'https://cdn.example.org/proxy?u=original&t=caller');
 h.chrome.contextMenus.onClicked.emit({menuItemId:"lume-download-link",mediaType:"video",srcUrl:"blob:https://youtube.com/id",pageUrl:"https://www.youtube.com/watch?v=aGCdLKXNF3w&list=x"});assert.equal(nativeCalls().at(-1).body.url,"https://www.youtube.com/watch?v=aGCdLKXNF3w");
 await h.message({type:"content-report",items:[item,{kind:"audio",url:"blob:unsupported"}]},h.content);let list=await h.message({type:"media-list"});assert.equal(list.items.length,2);assert.equal(list.items[1].supported,false);
 h.setTab({id:7,url:"https://example.org/changed"});h.chrome.tabs.onUpdated.emit(7,{url:"https://example.org/changed"});await tick();assert.equal((await h.message({type:"media-list"})).items.length,0);
 const changed={...h.content,url:"https://example.org/changed"};assert.equal((await h.message({type:"content-report",items:[item]},changed)).ok,true);
 h.setTab(h.content.tab);assert.equal((await h.message({type:"site-enable",origin:"https://example.org/*"})).ok,false);
 h.permissions.add("https://example.org/*");await h.message({type:"site-enable",origin:"https://example.org/*"});assert.deepEqual(h.scripts[0].matches,["https://example.org/*"]);
 const other={...h.content,documentId:"doc-8-main",tab:{id:8,url:h.content.url}};h.documents.set(8,[{frameId:0,documentId:other.documentId,result:{url:other.url}}]);await hello(h,other);await h.message({type:"content-report",items:[item]},other);
 // Disable must invalidate BOTH manually and automatically authorized Documents.
 await h.message({type:"site-disable",origin:"https://example.org/*"});assert.equal(h.scripts.length,0);assert.equal(h.permissions.size,0);
 for(const sender of [h.content,other]){assert.equal((await h.message({type:"content-report",items:[item]},sender)).code,"grant");const before=nativeCalls().length;assert.equal((await h.message({type:"content-queue",item},sender)).code,"grant");assert.equal(nativeCalls().length,before);}
 assert.ok(h.calls.some(call=>call.tabMessage?.id===7));assert.ok(h.calls.some(call=>call.tabMessage?.id===8));assert.equal(h.session.mediaTabs[8].items.length,0);
 // Only an explicit manual scan may reacquire its exact current Documents.
 await h.message({type:"scan-page"});await hello(h,h.content);assert.equal((await h.message({type:"content-queue",item},h.content)).ok,true);assert.equal((await h.message({type:"content-queue",item},other)).code,"grant");
 // New automatic activation creates no implicit manual authority.
 const a=harness();a.permissions.add("https://example.org/*");await a.message({type:"site-enable",origin:"https://example.org/*"});assert.equal(Object.keys(a.session.manualGrants).length,0);await hello(a,a.content);
 const aOther={...a.content,documentId:"doc-8",tab:{id:8,url:a.content.url}};a.documents.set(8,[{frameId:0,documentId:aOther.documentId,result:{url:aOther.url}}]);await hello(a,aOther);
 for(const sender of [a.content,aOther])await a.message({type:"content-report",items:[item]},sender);
 a.permissions.clear();a.chrome.permissions.onRemoved.emit({origins:["https://example.org/*"]});await tick();
 for(const sender of [a.content,aOther]){assert.equal((await a.message({type:"content-report",items:[item]},sender)).code,"grant");assert.equal((await a.message({type:"content-queue",item},sender)).code,"grant");}
 assert.ok(a.calls.some(call=>call.tabMessage?.id===7));assert.ok(a.calls.some(call=>call.tabMessage?.id===8));assert.equal(a.calls.filter(call=>call.host).length,0);
 // A late unload from an older iframe cannot clear its newer Document.
 a.permissions.add("https://example.org/*");await a.message({type:"site-enable",origin:"https://example.org/*"});
 const oldFrame={...a.content,frameId:2,documentId:"old-frame"},newFrame={...oldFrame,documentId:"new-frame"};
 a.documents.set(7,[{frameId:0,documentId:a.content.documentId,result:{url:a.content.url}},{frameId:2,documentId:oldFrame.documentId,result:{url:oldFrame.url}}]);await hello(a,oldFrame);
 a.documents.set(7,[{frameId:0,documentId:a.content.documentId,result:{url:a.content.url}},{frameId:2,documentId:newFrame.documentId,result:{url:newFrame.url}}]);await hello(a,newFrame);await a.message({type:"content-report",items:[item]},newFrame);
 assert.equal((await a.message({type:"content-report",items:[item]},oldFrame)).code,"grant");assert.equal((await a.message({type:"content-hello"},oldFrame)).code,"grant");
 await a.message({type:"content-clear"},oldFrame);assert.equal(a.session.mediaTabs[7].frames[2].documentId,"new-frame");assert.ok(a.session.mediaTabs[7].items.some(value=>value.frameId===2));
 await a.message({type:"content-clear"},newFrame);assert.equal(a.session.mediaTabs[7].frames[2],undefined);assert.equal(a.session.mediaTabs[7].items.some(value=>value.frameId===2),false);
 // BFCache release/resume for an authorized automatic site; no stale entries.
 await hello(a,a.content);await a.message({type:"content-report",items:[item]},a.content);await a.message({type:"content-clear"},a.content);assert.equal(a.session.mediaTabs[7].items.length,0);await hello(a,a.content);assert.equal((await a.message({type:"content-report",items:[item]},a.content)).ok,true);
 // Manual navigation clears document authority and requires an explicit rescan.
 h.chrome.tabs.onUpdated.emit(7,{status:"loading"});await tick();assert.equal((await h.message({type:"content-hello"},h.content)).code,"grant");await h.message({type:"scan-page"});await hello(h,h.content);
 const tooMany=Array.from({length:33},(_,i)=>({kind:"video",url:`https://example.org/${i}.mp4`}));assert.equal((await h.message({type:"content-report",items:tooMany},h.content)).ok,false);
 for(let id=0;id<10;id++){const sender={...a.content,documentId:`doc-${id}`,tab:{id,url:a.content.url}};a.documents.set(id,[{frameId:0,documentId:sender.documentId,result:{url:sender.url}}]);await hello(a,sender);await a.message({type:"content-report",items:[item]},sender);}assert.equal(Object.keys(a.session.mediaTabs).length,8);
 console.log("Native handoff, document grants, two-tab revocation, iframe ownership, BFCache and bounds tests passed (browser API simulation).");
})().catch(error=>{console.error(error);process.exitCode=1;});
