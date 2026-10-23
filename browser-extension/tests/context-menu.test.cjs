// Chrome API simulation: recovery proof, not a real context-menu UI test.
"use strict";
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.join(__dirname, '..'), menuId = 'lume-download-link';
function event() {const listeners=[]; return {addListener:fn=>listeners.push(fn), emit:(...args)=>listeners.forEach(fn=>fn(...args)), listeners};}
function harness() {
  const menus = new Map(), calls = [], local = {}, session = {};
  let createFailure = false;
  const area = data => ({get:async key=>Object.fromEntries((Array.isArray(key)?key:[key]).map(k=>[k,data[k]])), set:async value=>{Object.assign(data,value);}});
  const chrome = {
    runtime: {id:'abcdefghijklmnopabcdefghijklmnop',getURL:file=>'chrome-extension://abcdefghijklmnopabcdefghijklmnop/'+file,
      onInstalled:event(),onStartup:event(),onMessage:event(),lastError:undefined,sendNativeMessage(){calls.push({native:true});}},
    contextMenus: {onClicked:event(),
      update(id,props,callback) {calls.push({update:id}); queueMicrotask(()=>{if(!menus.has(id)) chrome.runtime.lastError={message:'Cannot find menu item with id '+id}; else Object.assign(menus.get(id),props); callback?.(); chrome.runtime.lastError=undefined;});},
      create(props,callback) {calls.push({create:props.id}); queueMicrotask(()=>{if(createFailure)chrome.runtime.lastError={message:'Menu creation refused'};else if(menus.has(props.id))chrome.runtime.lastError={message:'Duplicate menu id'};else menus.set(props.id,{...props}); callback?.(); chrome.runtime.lastError=undefined;});return props.id;},
      remove(id,callback) {calls.push({remove:id});menus.delete(id);callback?.();}},
    storage:{local:area(local),session:area(session)}, action:{setBadgeText(){},setBadgeBackgroundColor(){},setTitle(){}},
    tabs:{onRemoved:event(),onUpdated:event()},permissions:{onRemoved:event(),contains:async()=>false},
    scripting:{getRegisteredContentScripts:async()=>[],unregisterContentScripts:async()=>{}}
  };
  const context=vm.createContext({chrome,URL});context.importScripts=file=>vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(root,'service-worker.js'),'utf8'),context);
  function message(body) {return new Promise(resolve=>{const accepted=chrome.runtime.onMessage.listeners[0](body,{id:chrome.runtime.id,url:chrome.runtime.getURL('popup.html')},resolve);if(!accepted)resolve({rejected:true});});}
  return {chrome,menus,calls,local,message,failCreate:value=>{createFailure=value;}};
}
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
  const h=harness();
  // A profile can wake with no menu, even without an install/update event.
  h.chrome.runtime.onStartup.emit(); await flush();
  assert.equal(h.menus.has(menuId),true,'startup must recover a missing menu');
  assert.deepEqual(Array.from(h.menus.get(menuId).contexts),['link','video','audio']);
  h.menus.set('unrelated-menu',{title:'Preserve me'});
  h.menus.set(menuId,{title:'Old title',contexts:['link'],visible:false,enabled:false});
  h.calls.length=0;h.chrome.runtime.onStartup.emit();await flush();
  assert.equal(h.menus.get(menuId).title,'Baixar com OpenDownloader');assert.equal(h.menus.get(menuId).visible,true);assert.equal(h.menus.get(menuId).enabled,true);
  assert.equal(h.calls.filter(c=>c.create||c.remove).length,0,'existing menu must be updated without destructive replacement');assert.equal(h.menus.get('unrelated-menu').title,'Preserve me');
  h.menus.delete(menuId);h.calls.length=0;
  const result=await Promise.all([h.message({type:'menu-status'}),h.message({type:'menu-status'})]);
  assert.ok(result.every(r=>r.ok===true));assert.equal(h.calls.filter(c=>c.create).length,1,'concurrent popup checks share one recovery');
  h.menus.delete(menuId);h.failCreate(true);
  const failed=await h.message({type:'menu-status'});assert.equal(failed.ok,false);assert.match(failed.error,/Menu creation refused/);assert.equal(h.menus.has(menuId),false);
  h.failCreate(false);assert.equal((await h.message({type:'menu-status'})).ok,true,'later popup must retry after transient failure');
  assert.equal(h.calls.some(c=>c.native),false,'menu recovery must not send any download');
  h.chrome.runtime.onInstalled.emit();await flush();assert.equal(h.menus.size,2);
  console.log('Context menu startup/popup recovery, non-destructive update, callback errors and concurrency passed (Chrome API simulation).');
})().catch(error=>{console.error(error);process.exitCode=1;});
