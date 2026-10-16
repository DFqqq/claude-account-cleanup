import {test} from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {validate,BrowserScope,BrowserError,CDP} from '../claude-account-cleanup/scripts/browser_cleanup.mjs';

const base=()=>({schema:1,endpoint:'ws://127.0.0.1:12345/devtools/browser/fixture-id',
  profile_path:path.resolve('fictional-profile'),profile_confirmed:true,browser_context_id:'old',anchor_target_id:'anchor',
  account_state:'old-only',drafts_saved:true,shared_login_excluded:true,close_target_tabs:true,
  origins:['https://claude.ai'],protected_origins:['https://company.example']});
class FakeCDP {
  constructor(){this.calls=[];this.login='FAKE_OLD';this.protectedLogin='FAKE_KEEP';this.local=1;this.protocolFails=false;this.tabs=[];this.partition=true;}
  async call(method,p={},sid){
    this.calls.push({method,p,sid});
    if(method==='Target.getTargetInfo')return {targetInfo:{type:'page',targetId:'anchor',browserContextId:'old',url:'about:blank'}};
    if(method==='Target.getBrowserContexts')return {browserContextIds:['old','new']};
    if(method==='Target.attachToTarget')return {sessionId:'fixture-session'};
    if(method==='Emulation.setScriptExecutionDisabled')return {};
    if(method==='DOMStorage.clear'){this.session=0;return {};}
    if(method==='Target.closeTarget'){this.tabs=this.tabs.filter(t=>t.targetId!==p.targetId);this.local=1;return {success:true};}
    if(method==='Target.detachFromTarget')return {};
    if(method==='Target.getTargets')return {targetInfos:this.tabs};
    if(method==='Storage.getCookies')return {cookies:[...(this.login?[{name:'session',value:this.login,domain:'.claude.ai',path:'/',...(this.partition?{partitionKey:{topLevelSite:'https://claude.ai',hasCrossSiteAncestor:false}}:{})}]:[]),
      {name:'SSO',value:this.protectedLogin,domain:'.anthropic.com',path:'/'}]};
    const own=(p.origin||p.securityOrigin||p.storageId?.securityOrigin)==='https://claude.ai';
    if(method==='DOMStorage.getDOMStorageItems')return {entries:own?((p.storageId.isLocalStorage?this.local:this.session)?[['fake','value']]:[]):[['keep','FAKE_KEEP']]};
    if(method==='Storage.getUsageAndQuota')return {usageBreakdown:[{storageType:'indexeddb',usage:own?this.local:4}]};
    if(method==='IndexedDB.requestDatabaseNames')return {databaseNames:own?(this.local?['fake']:[]):['company']};
    if(method==='CacheStorage.requestCacheNames')return {caches:own?(this.local?[{cacheName:'fake'}]:[]):[{cacheName:'company'}]};
    if(method==='Storage.clearDataForOrigin'){if(this.protocolFails)throw Error('synthetic');this.local=0;return {};}
    if(method==='Network.deleteCookies'){assert.equal(p.domain,'.claude.ai');if(this.partition)assert.ok(p.partitionKey);this.login=null;return {};}
    throw Error('Unexpected fixture method: '+method);
  }
}
test('new account, mixed ownership and unsaved drafts rejected',()=>{
  for(const d of [{account_state:'new'},{profile_confirmed:false},{drafts_saved:false},{shared_login_excluded:false}])assert.throws(()=>validate({...base(),...d}));
});
test('external, credential-bearing and non-browser endpoints rejected',()=>{
  for(const endpoint of ['ws://example.org:80/devtools/browser/id','ws://localhost:80/devtools/browser/id','ws://127.0.0.1:80/page/1','ws://user:password@127.0.0.1:80/devtools/browser/id','ws://127.0.0.1:80/devtools/browser/id?token=x'])assert.throws(()=>validate({...base(),endpoint}));
});
test('arbitrary domains, paths and overlapping protected sites rejected',()=>{
  for(const origins of [['https://accounts.google.com'],['https://claude.ai/path'],['https://claude.ai.evil.test'],['https://sub.claude.ai']])assert.throws(()=>validate({...base(),origins}));
  assert.throws(()=>validate({...base(),protected_origins:['https://claude.ai']}));
});
test('dry run sends no clear/close/navigation or global mutation commands',async()=>{
  const c=new FakeCDP(),s=new BrowserScope(c,base());await s.attach();const p=await s.plan();
  assert.equal((await s.apply(p)).status,'dry-run');
  assert.ok(c.calls.every(x=>!/(clear|delete|closeTarget|navigate|setCookie)/i.test(x.method)));
});
test('exact cookie partition and unrelated parent SSO preserved',async()=>{
  const c=new FakeCDP(),s=new BrowserScope(c,base());await s.attach();
  assert.equal((await s.apply(await s.plan(),true)).status,'cleared-selected-origins');
  assert.equal(c.protectedLogin,'FAKE_KEEP');assert.equal(c.login,null);
  assert.ok(c.calls.every(x=>!['Storage.clearCookies','Network.clearBrowserCookies','Network.clearBrowserCache','Page.navigate'].includes(x.method)));
});
test('new login after plan blocks all modifications',async()=>{
  const c=new FakeCDP(),s=new BrowserScope(c,base());await s.attach();const p=await s.plan();c.login='FAKE_NEW';
  await assert.rejects(s.apply(p,true));assert.equal(c.login,'FAKE_NEW');assert.equal(c.local,1);
});
test('active embedded Claude frame is protected',async()=>{
  const c=new FakeCDP();c.tabs=[{type:'iframe',url:'https://claude.ai',browserContextId:'old',targetId:'frame'}];
  const s=new BrowserScope(c,base());await s.attach();await assert.rejects(s.plan());
});
test('another context with a new Claude tab is not cleared or closed',async()=>{
  const c=new FakeCDP();c.tabs=[{type:'page',url:'https://claude.ai',browserContextId:'new',targetId:'newtab'}];
  const s=new BrowserScope(c,base());await s.attach();assert.equal((await s.apply(await s.plan(),true)).status,'cleared-selected-origins');
  assert.ok(c.calls.every(x=>x.method!=='Target.closeTarget'));
});
test('protocol failure is reported partial, not success',async()=>{
  const c=new FakeCDP(),s=new BrowserScope(c,base());await s.attach();const p=await s.plan();c.protocolFails=true;
  assert.equal((await s.apply(p,true)).status,'partial');assert.equal(c.login,'FAKE_OLD');
});
test('plans and receipts contain no cookie or local storage values',async()=>{
  const c=new FakeCDP(),s=new BrowserScope(c,base());await s.attach();const p=await s.plan();const result=await s.apply(p,true);
  for(const marker of ['FAKE_OLD','FAKE_KEEP','"value"'])assert.ok(!JSON.stringify([p,result]).includes(marker));
});
test('uninspectable local storage cannot be reported fully verified',async()=>{
  const c=new FakeCDP(),real=c.call.bind(c);
  c.call=async(m,p,sid)=>{if(m==='DOMStorage.getDOMStorageItems'&&p.storageId.isLocalStorage)throw new BrowserError('Unavailable in fixture');return real(m,p,sid);};
  const s=new BrowserScope(c,base());await s.attach();const p=await s.plan();
  assert.equal(p.before.states['https://claude.ai'].local_count,null);
  assert.equal((await s.apply(p,true)).status,'partial');
});
test('context mismatch is blocked before reading cookies',async()=>{
  const c=new FakeCDP(),s=new BrowserScope(c,{...base(),browser_context_id:'different'});
  await assert.rejects(s.attach());assert.ok(c.calls.every(x=>x.method!=='Storage.getCookies'));
});


test('closing writes are cleared a second time and receipt scopes final proof',async()=>{
  const c=new FakeCDP();c.tabs=[{type:'page',url:'https://claude.ai',browserContextId:'old',targetId:'oldtab'}];c.session=1;
  const s=new BrowserScope(c,base());await s.attach();const receipt=await s.apply(await s.plan(),true);
  assert.equal(receipt.status,'cleared-selected-origins');assert.equal(c.local,0);assert.equal(c.tabs.length,0);
  const close=c.calls.findIndex(x=>x.method==='Target.closeTarget');
  assert.ok(c.calls.slice(close+1).some(x=>x.method==='Storage.clearDataForOrigin'));
  assert.match(receipt.verification.limit,/No final independent/);
  assert.ok(c.calls.some(x=>x.method==='Emulation.setScriptExecutionDisabled'&&x.p.value===false));
});


test('waits for known closing targets but refuses a new target immediately',async()=>{
  const s=new BrowserScope(new FakeCDP(),base());let calls=0;
  s.tabs=async()=>++calls<3?[{id:'known',origin:'https://claude.ai'}]:[];
  await s.waitClosed([{id:'known'}]);assert.equal(calls,3);
  s.tabs=async()=>[{id:'new-login-tab',origin:'https://claude.ai'}];
  await assert.rejects(s.waitClosed([{id:'known'}]),/new target/);
});
