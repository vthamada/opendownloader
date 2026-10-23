// Public message boundary with browser-owned frame/document/API simulations.
// These regressions do not establish real Chrome/Edge popup or site behavior.
"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const root=path.join(__dirname,".."),copy=value=>JSON.parse(JSON.stringify(value));
function event(){const listeners=[];return{listeners,addListener:fn=>listeners.push(fn),emit:(...args)=>listeners.map(fn=>fn(...args))};}
const frame=(frameId,documentId,url)=>({frameId,documentId,result:{url}});
function harness(initial){
  const calls=[],session={},local={},permissions=new Set();let documents=initial,tab={id:7,url:"https://example.org/watch"};
  let beforeFiles=null,failSession=false;
  function area(data){return{get:async keys=>Object.fromEntries((Array.isArray(keys)?keys:[keys]).map(key=>[key,data[key]===undefined?undefined:copy(data[key])])),
    set:async value=>{if(data===session&&failSession)throw new Error("fixture storage write refused");Object.assign(data,copy(value));}};}
  const chrome={runtime:{id:"self",getURL:name=>`chrome-extension://self/${name}`,onInstalled:event(),onStartup:event(),onMessage:event(),
      sendNativeMessage:()=>{throw new Error("Scanning must not send a native download");}},
    storage:{session:area(session),local:area(local)},action:{setBadgeText(){},setBadgeBackgroundColor(){},setTitle(){}},
    contextMenus:{update:(_id,_data,callback)=>callback(),create:(_data,callback)=>callback(),onClicked:event()},
    permissions:{contains:async({origins})=>origins.every(origin=>permissions.has(origin)),onRemoved:event(),remove:async()=>true},
    tabs:{query:async()=>[copy(tab)],onRemoved:event(),onUpdated:event(),sendMessage:async()=>{}},
    scripting:{executeScript:async config=>{
      calls.push(config);
      if(config.files){if(beforeFiles)await beforeFiles();if(config.target.documentIds?.some(id=>!documents.some(document=>document.documentId===id)))throw new Error("No document with id");return [];}
      const result=config.target.documentIds?documents.filter(document=>config.target.documentIds.includes(document.documentId)):
        config.target.frameIds?documents.filter(document=>config.target.frameIds.includes(document.frameId)):documents;
      return copy(result);
    },getRegisteredContentScripts:async()=>[],unregisterContentScripts:async()=>{}}
  };
  const context=vm.createContext({chrome,URL});context.importScripts=name=>vm.runInContext(fs.readFileSync(path.join(root,name),"utf8"),context);
  vm.runInContext(fs.readFileSync(path.join(root,"service-worker.js"),"utf8"),context);
  const popup={id:"self",url:chrome.runtime.getURL("popup.html")};
  function message(body,sender=popup){return new Promise(resolve=>{if(!chrome.runtime.onMessage.listeners[0](body,sender,resolve))resolve({rejected:true});});}
  return{chrome,calls,session,message,setDocuments:value=>{documents=value;},setTab:value=>{tab=value;},beforeFiles:fn=>{beforeFiles=fn;},failSession:()=>{failSession=true;},
    sender:document=>({id:"self",tab:copy(tab),frameId:document.frameId,documentId:document.documentId,url:document.result.url,documentLifecycle:"active"})};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const main=frame(0,"main-document","https://example.org/watch"),child=frame(4,"media-document","https://player.example.org/embed");
  const mixed=harness([main,frame(1,"blank-document","about:blank"),frame(2,"javascript-document","javascript:;"),
    frame(3,"blob-document","blob:https://example.org/source"),child]);
  const scanned=await mixed.message({type:"scan-page"});
  assert.equal(scanned.ok,true,`Unsupported subframes must not abort the valid page scan: ${scanned.error}`);
  assert.deepEqual(Object.keys(mixed.session.manualGrants[7]),["0","4"]);
  const injection=mixed.calls.find(call=>call.files);
  assert.deepEqual(copy(injection.target),{tabId:7,documentIds:["main-document","media-document"]});
  assert.equal(injection.target.allFrames,undefined);assert.equal(injection.target.frameIds,undefined);
  for(const document of [main,child])assert.equal((await mixed.message({type:"content-hello"},mixed.sender(document))).ok,true);
  assert.equal((await mixed.message({type:"content-report",items:[{url:"https://cdn.example.org/test.mp4",kind:"video",title:"Test"}]},mixed.sender(child))).ok,true);
  assert.equal((await mixed.message({type:"media-list"})).items[0].frameId,4);
  const sameUrl='https://cdn.example.org/test.mp4';
  await mixed.message({type:'content-report',items:[
    {url:sameUrl,kind:'video',discovery:'resource'},
    {url:sameUrl,kind:'video',protection:'eme-observed'},
    {url:sameUrl,kind:'video',protection:'eme-observed'}]},mixed.sender(child));
  const observations=(await mixed.message({type:'media-list'})).items;
  assert.equal(observations.length,2,'deduplicate identical observations, preserve distinct player/resource evidence');
  assert.equal(observations[0].discovery,'resource');assert.equal(observations[0].protection,undefined);
  assert.equal(observations[1].protection,'eme-observed');
  const blankSender=mixed.sender(frame(1,"blank-document","about:blank"));
  assert.deepEqual(await mixed.message({type:"content-hello"},blankSender),{rejected:true});

  const invalid=harness([frame(-1,"negative","https://example.org/"),frame("2","string-id","https://example.org/"),
    frame(2,"","https://example.org/"),frame(3,"bad document","https://example.org/"),
    frame(4,"bad-url","https://user:password@example.org/"),frame(5,"newline-url","https://example.org/\nInjected"),
    frame(6,"bad-protocol","data:text/plain,hello"),main]);
  assert.equal((await invalid.message({type:"scan-page"})).ok,true);
  assert.deepEqual(Object.keys(invalid.session.manualGrants[7]),["0"]);
  assert.deepEqual(copy(invalid.calls.find(call=>call.files).target.documentIds),["main-document"]);
  const saved=copy(invalid.session);
  invalid.setDocuments([frame(1,"only-blank","about:blank"),frame(2,"only-script","javascript:;"),null]);
  const unsupported=await invalid.message({type:"scan-page"});assert.equal(unsupported.ok,false);
  assert.match(unsupported.error,/identidade|Recarregue|HTTP\(S\)/);assert.doesNotMatch(unsupported.error,/Use um link HTTP ou HTTPS/);
  assert.deepEqual(invalid.session,saved,"A failed inspection must not publish an empty or partial grant snapshot");

  const internal=harness([]);internal.setTab({id:7,url:"chrome://extensions/"});
  const internalResult=await internal.message({type:"scan-page"});assert.equal(internalResult.ok,false);
  assert.match(internalResult.error,/Abra uma página HTTP\(S\)/);assert.equal(internal.calls.length,0);

  const bounded=harness([frame(1,"blank-prefix","about:blank"),...Array.from({length:40},(_,id)=>frame(id+2,`document-${id}`,`https://example.org/frame-${id}`))]);
  assert.equal((await bounded.message({type:"scan-page"})).ok,true);
  assert.equal(Object.keys(bounded.session.manualGrants[7]).length,32);
  assert.equal(bounded.calls.find(call=>call.files).target.documentIds.length,32);

  const navigation=harness([main]);navigation.beforeFiles(()=>{navigation.setDocuments([frame(0,"new-document",main.result.url)]);});
  const navigated=await navigation.message({type:"scan-page"});assert.equal(navigated.ok,false);
  assert.equal(navigation.calls.filter(call=>call.files).length,1,"Navigation cannot retry against a newly occupying frame");
  assert.deepEqual(copy(navigation.calls.find(call=>call.files).target),{tabId:7,documentIds:["main-document"]});
  assert.equal((await navigation.message({type:"content-hello"},navigation.sender(frame(0,"new-document",main.result.url)))).code,"grant");

  const revoked=harness([main]);revoked.beforeFiles(async()=>{revoked.chrome.permissions.onRemoved.emit({origins:["https://example.org/*"]});await tick();});
  await revoked.message({type:"scan-page"});
  assert.equal((await revoked.message({type:"content-hello"},revoked.sender(main))).code,"grant");
  assert.equal(revoked.session.manualGrants[7]?.[0],undefined);

  const storage=harness([main]);storage.failSession();assert.equal((await storage.message({type:"scan-page"})).ok,false);
  assert.deepEqual(storage.session,{});assert.equal(storage.calls.some(call=>call.files),false);
  console.log("Mixed HTTP/non-HTTP frame scan, exact-document injection, malformed inputs, bounds, navigation/revocation and atomic storage regressions passed (browser API simulation).");
})().catch(error=>{console.error(error);process.exitCode=1;});
