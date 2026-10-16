/** Scoped browser cleanup through an EXISTING local CDP connection, Node 22+.
 * No browser discovery/launch, profile copying, database edits, page navigation,
 * messages or external requests. Cookies and storage contents stay in memory.
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';

export const VERSION = '1.2.1';
export const TYPES = 'local_storage,indexeddb,cache_storage,service_workers,file_systems,websql,storage_buckets';
export class BrowserError extends Error {}
const fail = message => { throw new BrowserError(message); };
const require = (ok, message) => { if (!ok) fail(message); };
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value;
const hash = value => crypto.createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
const sorted = items => [...items].sort((a,b) => JSON.stringify(canonical(a)).localeCompare(JSON.stringify(canonical(b))));

export function origin(value) {
  const u = new URL(value);
  require(u.protocol === 'https:' && !u.username && !u.password && !u.port &&
    u.href === u.origin + '/', 'Use exact HTTPS origins without paths or credentials');
  return u.origin;
}

export function validate(spec) {
  require(spec?.schema === 1 && spec.account_state === 'old-only' && spec.profile_confirmed === true &&
    spec.drafts_saved === true && spec.shared_login_excluded === true, 'Old-only profile, saved drafts and shared-login scope must be confirmed');
  require(typeof spec.profile_path === 'string' && path.isAbsolute(spec.profile_path), 'Confirmed original profile path required');
  require(typeof spec.anchor_target_id === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(spec.anchor_target_id), 'Existing anchor target required');
  const ws = new URL(spec.endpoint);
  require(ws.protocol === 'ws:' && ['127.0.0.1','[::1]'].includes(ws.hostname) && ws.port &&
    !ws.username && !ws.password && !ws.search && !ws.hash && /^\/devtools\/browser\/[A-Za-z0-9-]+$/.test(ws.pathname), 'Only an explicit loopback browser CDP endpoint is supported');
  require(Array.isArray(spec.origins) && spec.origins.length > 0 && spec.origins.length <= 30, 'Explicit selected origins required');
  const origins = [...new Set(spec.origins.map(origin))];
  require(origins.length === spec.origins.length, 'Duplicate origins refused');
  require(origins.every(x => new URL(x).hostname === 'claude.ai' || new URL(x).hostname.endsWith('.claude.ai')),
    'Only Claude web origins are supported; shared Anthropic/SSO domains require separate UI verification');
  require(origins.includes('https://claude.ai'), 'Include the parent Claude origin to resolve parent cookies explicitly');
  require(Array.isArray(spec.protected_origins), 'Explicit protected-origin list required, even if empty');
  const protectedOrigins = spec.protected_origins.map(origin);
  require(protectedOrigins.length <= 100 && protectedOrigins.every(x => !origins.includes(x)), 'Protected and selected origins overlap');
  return {origins, protectedOrigins, hosts: origins.map(x => new URL(x).hostname)};
}

export class CDP {
  constructor(endpoint) { this.endpoint=endpoint; this.seq=0; this.pending=new Map(); }
  async open() {
    require(typeof WebSocket === 'function', 'Node 22+ is required for the browser helper');
    this.socket = new WebSocket(this.endpoint);
    await new Promise((resolve,reject) => {
      const timer=setTimeout(()=>{this.socket.close();reject(new Error('Local browser connection timed out'));},10000);
      this.socket.addEventListener('open',()=>{clearTimeout(timer);resolve();},{once:true});
      this.socket.addEventListener('error',()=>{clearTimeout(timer);reject(new Error('Local browser connection unavailable'));},{once:true});
    });
    this.socket.addEventListener('message', e => {
      if (typeof e.data !== 'string' || e.data.length > 32*1024*1024) { this.close(); return; }
      let msg; try {msg=JSON.parse(e.data);} catch {this.close();return;}
      const entry=this.pending.get(msg.id); if(!entry)return;
      this.pending.delete(msg.id);clearTimeout(entry.timer);
      if(msg.error)entry.reject(new BrowserError('Browser protocol operation failed: '+entry.method+' ('+Number(msg.error.code)+')'));
      else entry.resolve(msg.result || {});
    });
    this.socket.addEventListener('close',()=>this.rejectPending());
    return this;
  }
  rejectPending() { for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(new Error('Browser connection closed'));}this.pending.clear(); }
  call(method, params={}, sessionId) {
    const id=++this.seq;
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error('Browser operation timed out: '+method));},15000);
      this.pending.set(id,{resolve,reject,timer,method});
      try { this.socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})})); }
      catch { clearTimeout(timer);this.pending.delete(id);reject(new Error('Browser connection unavailable')); }
    });
  }
  close() { this.rejectPending();this.socket?.close(); }
}

export class BrowserScope {
  constructor(cdp,spec) { this.cdp=cdp;this.spec=spec;Object.assign(this,validate(spec)); }
  async attach() {
    const info=(await this.cdp.call('Target.getTargetInfo',{targetId:this.spec.anchor_target_id})).targetInfo;
    require(info?.type==='page', 'Anchor must be an existing page in the confirmed profile');
    this.context=info.browserContextId || '';
    require(this.context === (this.spec.browser_context_id || ''), 'Browser context does not match confirmed profile');
    // Chromium may report a nonempty ID for the persistent default context but
    // Storage.getCookies accepts IDs only for contexts returned by this method.
    const contexts=(await this.cdp.call('Target.getBrowserContexts')).browserContextIds;
    this.cookieContext=contexts.includes(this.context)?this.context:'';
    let a;try {a=new URL(info.url).origin;}catch {a='';}
    require(!this.origins.includes(a), 'Anchor must be a neutral page, not a Claude page');
    this.session=(await this.cdp.call('Target.attachToTarget',{targetId:info.targetId,flatten:true})).sessionId;
  }
  async detach() { if(this.session)await this.cdp.call('Target.detachFromTarget',{sessionId:this.session}).catch(()=>{}); }
  page(method,params={}) { return this.cdp.call(method,params,this.session); }
  async tabs() {
    const current=(await this.cdp.call('Target.getTargetInfo',{targetId:this.spec.anchor_target_id})).targetInfo;
    require(current && (current.browserContextId||'')===this.context, 'Profile context changed');
    const {targetInfos}=await this.cdp.call('Target.getTargets');
    const tabs=[];
    for(const target of targetInfos) {
      if((target.browserContextId||'')!==this.context || target.type==='service_worker')continue;
      let u;try {u=new URL(target.url);}catch {continue;}
      if(!this.origins.includes(u.origin))continue;
      require(target.type==='page' && this.spec.close_target_tabs===true,
        'Target workers/frames or unsaved tabs require browser UI handling');
      tabs.push({id:target.targetId,origin:u.origin});
    }
    return sorted(tabs);
  }
  async tabSession(tab, clear=false) {
    const {sessionId}=await this.cdp.call('Target.attachToTarget',{targetId:tab.id,flatten:true});
    const storageId={securityOrigin:tab.origin,isLocalStorage:false};
    try {
      if(clear)await this.cdp.call('DOMStorage.clear',{storageId},sessionId);
      const {entries}=await this.cdp.call('DOMStorage.getDOMStorageItems',{storageId},sessionId);
      require(!clear || entries.length===0,'Target session storage did not clear');
      return {count:entries.length,hash:hash(sorted(entries))};
    }finally{await this.cdp.call('Target.detachFromTarget',{sessionId}).catch(()=>{});}
  }
  async cookies() {
    return (await this.cdp.call('Storage.getCookies',this.cookieContext?{browserContextId:this.cookieContext}:{})).cookies;
  }
  selected(cookie) { return this.hosts.includes(cookie.domain.replace(/^\./,'')); }
  async state(o) {
    const storageId={securityOrigin:o,isLocalStorage:true};
    const {targetInfos}=await this.cdp.call('Target.getTargets');
    const target=targetInfos.find(t=>{
      try{return t.type==='page'&&(t.browserContextId||'')===this.context&&new URL(t.url).origin===o;}catch{return false;}
    });
    let ownSession;
    if(target)ownSession=(await this.cdp.call('Target.attachToTarget',{targetId:target.targetId,flatten:true})).sessionId;
    const call=(m,p)=>this.cdp.call(m,p,ownSession||this.session);
    // DOMStorage/IndexedDB require an inspected frame in some Chromium builds.
    // A successful clear command alone is not independent verification.
    const optional=async(m,p,key)=>{try{return (await call(m,p))[key];}catch(e){if(e instanceof BrowserError)return null;throw e;}};
    try {
      const local=await optional('DOMStorage.getDOMStorageItems',{storageId},'entries');
      const quota=await call('Storage.getUsageAndQuota',{origin:o});
      const db=await optional('IndexedDB.requestDatabaseNames',{securityOrigin:o},'databaseNames');
      const caches=await optional('CacheStorage.requestCacheNames',{securityOrigin:o},'caches');
      return {local_hash:local===null?null:hash(sorted(local)), local_count:local?.length??null,
        database_names_hash:db===null?null:hash(sorted(db)), database_count:db?.length??null,
        cache_names_hash:caches===null?null:hash(sorted(caches.map(x=>x.cacheName))), cache_count:caches?.length??null,
        usage:sorted(quota.usageBreakdown.map(x=>({type:x.storageType,bytes:x.usage}))) };
    }finally{if(ownSession)await this.cdp.call('Target.detachFromTarget',{sessionId:ownSession}).catch(()=>{});}
  }
  async snapshot() {
    const tabs=await this.tabs();
    const tabStates=[];for(const t of tabs)tabStates.push({...t,session:await this.tabSession(t)});
    const cookies=await this.cookies();
    require(!cookies.some(c=>this.selected(c)&&c.partitionKeyOpaque), 'Opaque partitioned target cookie requires browser UI cleanup');
    const states={};for(const o of [...this.origins,...this.protectedOrigins])states[o]=await this.state(o);
    return {tabs:tabStates,cookie_hash:hash(sorted(cookies)),
      protected_cookies_hash:hash(sorted(cookies.filter(c=>!this.selected(c)))),
      target_cookie_count:cookies.filter(c=>this.selected(c)).length, states};
  }
  async plan() {
    return {schema:1, version:VERSION, spec:this.spec, before:await this.snapshot(),
      coverage:'Selected original-profile origins and selected open-tab session storage; historical closed-tab restore records and global HTTP cache are not verified'};
  }
  async waitClosed(planned) {
    const ids=new Set(planned.map(t=>t.id));
    for(let attempt=0;attempt<12;attempt++) {
      const remaining=await this.tabs();
      require(remaining.every(t=>ids.has(t.id)), 'A new target tab appeared during cleanup');
      if(remaining.length===0)return;
      await new Promise(r=>setTimeout(r,150));
    }
    fail('Selected target tabs have not finished closing');
  }
  async apply(plan, execute=false) {
    require(plan.schema===1&&plan.version===VERSION&&hash(plan.spec)===hash(this.spec), 'Invalid browser plan');
    require(hash(await this.snapshot())===hash(plan.before), 'Browser state changed; old plan refused');
    if(!execute)return {status:'dry-run',selected_origins:this.origins.length};
    let started=false;const frozen=[];
    try {
      // Cookies read only into memory. Partition identity is preserved in exact deletions.
      const cookies=await this.cookies();
      require(hash(sorted(cookies))===plan.before.cookie_hash,'Browser login changed before cleanup');
      for(const tab of plan.before.tabs) {
        // Retain the session through closure so unload/pagehide handlers cannot
        // repopulate storage. This override is temporary, on selected old tabs only.
        const {sessionId}=await this.cdp.call('Target.attachToTarget',{targetId:tab.id,flatten:true});
        frozen.push(sessionId);
        await this.cdp.call('Emulation.setScriptExecutionDisabled',{value:true},sessionId);
        started=true;await this.tabSession(tab,true);
      }
      require(hash(await this.tabs())===hash(plan.before.tabs.map(({id,origin})=>({id,origin}))),'Target tabs changed during cleanup');
      for(const o of this.origins) { started=true;await this.page('Storage.clearDataForOrigin',{origin:o,storageTypes:TYPES}); }
      for(const c of cookies.filter(c=>this.selected(c))) {
        started=true;
        await this.page('Network.deleteCookies',{name:c.name,domain:c.domain,path:c.path,...(c.partitionKey?{partitionKey:c.partitionKey}:{})});
      }
      let after;
      for(let attempt=0;attempt<12;attempt++) {
        after=await this.snapshot();
        if(this.empty(after))break;
        await new Promise(r=>setTimeout(r,150));
      }
      require(this.empty(after),'Selected site data is not empty after cleanup');
      require(after.protected_cookies_hash===plan.before.protected_cookies_hash,'Unrelated cookie state changed; inspect partial result');
      for(const o of this.protectedOrigins)require(hash(after.states[o])===hash(plan.before.states[o]),'Protected site state changed; inspect partial result');
      for(const tab of plan.before.tabs) {
        require(after.tabs.find(t=>t.id===tab.id)?.session.count===0,'Session storage was recreated');
        const closed=await this.cdp.call('Target.closeTarget',{targetId:tab.id});
        require(closed.success,'Target tab did not close');
      }
      await this.waitClosed(plan.before.tabs);
      // A closing renderer can flush writes. Clear again after it has gone.
      // Closed origins cannot always be read through DOMStorage/IndexedDB CDP;
      // report that limitation instead of claiming an independent final read.
      for(const o of this.origins)await this.page('Storage.clearDataForOrigin',{origin:o,storageTypes:TYPES});
      const finalCookies=await this.cookies();
      require(!finalCookies.some(c=>this.selected(c)),'Target cookies were recreated');
      require(hash(sorted(finalCookies.filter(c=>!this.selected(c))))===plan.before.protected_cookies_hash,'Protected cookies changed');
      for(const o of this.origins) {
        let empty=false;
        for(let attempt=0;attempt<12;attempt++) {
          const quota=await this.page('Storage.getUsageAndQuota',{origin:o});
          empty=quota.usageBreakdown.filter(x=>TYPES.split(',').includes(x.storageType)).every(x=>x.usage===0);
          if(empty)break;
          await new Promise(r=>setTimeout(r,150));
        }
        require(empty,'Closed origin storage quota is not empty');
      }
      for(const o of this.protectedOrigins)require(hash(await this.state(o))===hash(plan.before.states[o]),'Protected site state changed after closing');
      require((await this.tabs()).length===0,'A target tab appeared after final cleanup');
      return {status:'cleared-selected-origins',origins:this.origins,
        removed_cookie_count:plan.before.target_cookie_count,protected_cookie_fingerprint:after.protected_cookies_hash,
        protected_origins_verified:this.protectedOrigins.length,session_storage_cleared_tabs:plan.before.tabs.length,
        verification:{before_close:'localStorage, IndexedDB, CacheStorage, sessionStorage, cookies and quota checked empty',
          after_close:'persistent stores cleared again; cookies, quota and protected sites verified',
          limit:'No final independent localStorage/IndexedDB/CacheStorage read after closing; use existing browser site-settings UI if that proof is required'},
        not_covered:['closed-tab session-restore records','global HTTP cache','unselected origins','other browser profiles'],
        network_or_account_settings_changed:false};
    } catch(error) { return {status:started?'partial':'blocked',reason:error instanceof BrowserError?error.message:'Browser state changed, storage is unavailable, or verification failed; inspect remaining scope before retrying'}; }
    finally { for(const sessionId of frozen) {
      await this.cdp.call('Emulation.setScriptExecutionDisabled',{value:false},sessionId).catch(()=>{});
      await this.cdp.call('Target.detachFromTarget',{sessionId}).catch(()=>{});
    } }
  }
  empty(s) { return s.target_cookie_count===0 && this.origins.every(o=>{
    const v=s.states[o];return v.local_count===0&&v.database_count===0&&v.cache_count===0&&
      v.usage.filter(x=>TYPES.split(',').includes(x.type)).every(x=>x.bytes===0);
  }); }
}

// Reports contain hashes and counts, never raw cookies, localStorage or database contents.
function load(file) { const raw=fs.readFileSync(file);require(raw.length<=2*1024*1024,'Input too large');return JSON.parse(raw); }
async function main() {
  const args=process.argv.slice(2),command=args.shift();
  const flags={};for(let i=0;i<args.length;i++){if(args[i]==='--execute')flags.execute=true;else {require(['--spec','--plan','--out'].includes(args[i]),'Unknown option');flags[args[i].slice(2)]=args[++i];}}
  require(['plan','apply'].includes(command)&&flags.out,'Use plan --spec or apply --plan, with --out');
  const plan=command==='apply'?load(flags.plan):null,spec=plan?plan.spec:load(flags.spec);
  validate(spec);
  require(path.isAbsolute(flags.out)&&!fs.existsSync(flags.out),'A fresh absolute report path is required');
  const out=path.resolve(flags.out),profile=path.resolve(spec.profile_path);
  const rel=path.relative(profile,out);
  require(rel.startsWith('..'+path.sep)||rel==='..'||path.isAbsolute(rel),'Report must be outside browser profile');
  // Spec/report has no credentials. On Windows, Agent stores reports in its user-private work directory.
  const fd=fs.openSync(out,'wx',0o600);let cdp,scope,result;
  try {
    cdp=await new CDP(spec.endpoint).open();scope=new BrowserScope(cdp,spec);await scope.attach();
    result=command==='plan'?await scope.plan():await scope.apply(plan,!!flags.execute);
    fs.writeFileSync(fd,JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify(command==='plan'?{status:'plan-ready'}:result));
    if(command==='apply'&&!['dry-run','cleared-selected-origins'].includes(result.status))process.exitCode=2;
  } catch (error) {
    result={status:flags.execute?'incomplete':'blocked',reason:error instanceof BrowserError ? error.message : 'Browser control or verified scope unavailable; no claim of completed cleanup'};
    fs.writeFileSync(fd,JSON.stringify(result)+'\n');console.log(JSON.stringify(result));process.exitCode=2;
  } finally {await scope?.detach();cdp?.close();fs.closeSync(fd);}
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(()=>{console.log(JSON.stringify({status:'blocked',reason:'Invalid browser scope or report path'}));process.exitCode=2;});
