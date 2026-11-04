/* Actual Chromium + installed helper, isolated synthetic profiles. No real account. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const root=path.resolve(process.argv[2]||path.join(__dirname,'../test-runs/browser-'+Date.now()));fs.mkdirSync(root,{recursive:true});
const helper=path.resolve(process.argv[3]||path.join(__dirname,'../claude-account-cleanup/scripts/browser_cleanup.mjs'));
const profile=path.join(root,'fictional-browser-profile');
const OLD='https://claude.ai',CHILD='https://auth.claude.ai',KEEP='https://company.example',SHARED='https://console.anthropic.com';
const checks=[];let intercepted=0;
async function run(args){return new Promise((resolve,reject)=>{
 const p=spawn(process.execPath,[helper,...args],{windowsHide:true});let out='',err='';
 p.stdout.on('data',x=>out+=x);p.stderr.on('data',x=>err+=x);
 p.on('error',reject);p.on('close',code=>resolve({code,out,err}));
});}
async function prepare(ctx){await ctx.setOffline(true);await ctx.route('**/*',r=>{
 intercepted++;const u=new URL(r.request().url());assert.ok([OLD,CHILD,KEEP,SHARED].includes(u.origin));
 return r.fulfill({status:200,contentType:'text/html',body:'<title>Synthetic fixture only</title><p>No real account</p>'});
});}
async function seed(ctx,o,marker){const p=await ctx.newPage();await p.goto(o);await p.evaluate(async marker=>{
 localStorage.setItem('fake-login',marker);sessionStorage.setItem('draft','FAKE_DRAFT_'+marker);
 const c=await caches.open('fixture-cache');await c.put('/fake',new Response(marker));
 await new Promise((resolve,reject)=>{const r=indexedDB.open('fixture-db',1);r.onupgradeneeded=()=>r.result.createObjectStore('store');r.onerror=()=>reject(r.error);r.onsuccess=()=>{const db=r.result,t=db.transaction('store','readwrite');t.objectStore('store').put(marker,'key');t.oncomplete=()=>{db.close();resolve();};};});
 },marker);return p;}
async function view(p){return p.evaluate(async()=>({local:localStorage.getItem('fake-login'),session:sessionStorage.getItem('draft'),caches:await caches.keys(),db:(await indexedDB.databases()).map(x=>x.name)}));}
(async()=>{
 const ctx=await chromium.launchPersistentContext(profile,{headless:true,...(process.env.FIXTURE_BROWSER_EXECUTABLE?{executablePath:process.env.FIXTURE_BROWSER_EXECUTABLE}:{}),args:['--remote-debugging-port=0','--host-resolver-rules=MAP * ~NOTFOUND']});
 try{
  await prepare(ctx);const browser=ctx.browser();const next=await browser.newContext();await prepare(next);
  const old=await seed(ctx,OLD,'FAKE_OLD'),child=await seed(ctx,CHILD,'FAKE_CHILD');
  await old.evaluate(()=>{for(const e of ['pagehide','unload','beforeunload'])window.addEventListener(e,()=>localStorage.setItem(e,'FAKE_RECREATED'));document.addEventListener('visibilitychange',()=>{if(document.hidden)localStorage.setItem('hidden','FAKE_RECREATED');});});
  const company=await seed(ctx,KEEP,'FAKE_COMPANY_KEEP'),shared=await seed(ctx,SHARED,'FAKE_SHARED_KEEP');
  const fresh=await seed(next,OLD,'FAKE_NEW_KEEP');
  await ctx.addCookies([{name:'login',value:'FAKE_OLD',domain:'.claude.ai',path:'/',secure:true},
   {name:'child',value:'FAKE_CHILD',domain:'auth.claude.ai',path:'/',secure:true},
   {name:'company',value:'FAKE_COMPANY_KEEP',domain:'company.example',path:'/',secure:true},
   {name:'shared',value:'FAKE_SHARED_KEEP',domain:'.anthropic.com',path:'/',secure:true}]);
  await next.addCookies([{name:'login',value:'FAKE_NEW_KEEP',domain:'.claude.ai',path:'/',secure:true}]);
  const companyBefore=await view(company),sharedBefore=await view(shared),newBefore=await view(fresh),newCookies=await next.cookies();
  fs.writeFileSync(path.join(root,'saved-drafts.json'),JSON.stringify([(await view(old)).session,(await view(child)).session]));
  const anchor=await ctx.newPage(),cdp=await ctx.newCDPSession(anchor);
  const {targetInfo}=await cdp.send('Target.getTargetInfo');await cdp.detach();
  const active=fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').trim().split(/\r?\n/);
  const spec={schema:1,endpoint:'ws://127.0.0.1:'+active[0]+active[1],profile_path:profile,profile_confirmed:true,
   anchor_target_id:targetInfo.targetId,browser_context_id:targetInfo.browserContextId||'',account_state:'old-only',
   drafts_saved:true,shared_login_excluded:true,close_target_tabs:true,origins:[OLD,CHILD],protected_origins:[KEEP,SHARED]};
  const specPath=path.join(root,'spec.json'),plan=path.join(root,'plan.json'),receipt=path.join(root,'receipt.json');fs.writeFileSync(specPath,JSON.stringify(spec));
  let r=await run(['plan','--spec',specPath,'--out',plan]);assert.equal(r.code,0,r.out+r.err);checks.push('installed helper creates a redacted plan from original fixture context');
  r=await run(['apply','--plan',plan,'--out',path.join(root,'dry.json')]);
  // Chromium can settle quota usage between snapshots. A dry-run must either
  // preview or safely reject the stale plan; neither path may change data.
  if(r.code!==0){assert.equal(r.code,2,r.out+r.err);const refused=JSON.parse(r.out);assert.equal(refused.status,'blocked');assert.equal(refused.reason,'Browser state changed; old plan refused');}
  assert.equal(old.isClosed(),false);assert.equal((await view(old)).local,'FAKE_OLD');checks.push('dry run preserves fake login and open drafts');
  // Exercise the actual installed completion runner with real file and browser
  // adapters, not a mock browser adapter or only a directly invoked helper.
  const fakeHome=path.join(root,'fictional-home'),config=path.join(fakeHome,'.claude');
  fs.mkdirSync(config,{recursive:true});
  const state=path.join(fakeHome,'.claude.json'),cred=path.join(config,'.credentials.json');
  fs.writeFileSync(state,JSON.stringify({oauthAccount:{accountUuid:'FAKE_OLD'},userID:'a'.repeat(64),machineID:'b'.repeat(64),projects:{keep:true}}));
  fs.writeFileSync(cred,JSON.stringify({claudeAiOauth:{accessToken:'FAKE_OLD_ONLY'},mcpOAuth:{keep:'FAKE_MCP_KEEP'}}));
  const work=path.join(config,'session.jsonl'),network=path.join(fakeHome,'company-network.json');
  fs.writeFileSync(work,'FAKE_KEEP_SESSION');fs.writeFileSync(network,'FAKE_KEEP_VPN_DNS');
  const fileSpec={schema:1,account_state:'old-only',config_dirs:[config],state_files:[state],preserve_paths:[work,network],operations:[
    {kind:'state-json',path:state,remove_fields:['oauthAccount'],old_account_evidence:'confirmed'},
    {kind:'credential-json',path:cred,remove_fields:['claudeAiOauth'],old_account_evidence:'confirmed'}]};
  const idSpec={...fileSpec,operations:[{kind:'state-json',path:state,remove_fields:[],old_account_evidence:'confirmed',
    identifier_reset:{config_scope:'old-only',reset_requested:true,version:'2.1.314',binary_sha256:'66e00df32e6981f686a91f67287fcc86e6e1b80ba4973a7b7c4aa43c59986cfa'}}]};
  const fileSpecPath=path.join(root,'files.json'),idSpecPath=path.join(root,'ids.json');
  fs.writeFileSync(fileSpecPath,JSON.stringify(fileSpec));fs.writeFileSync(idSpecPath,JSON.stringify(idSpec));
  const inventory={schema:1,inventory_complete:true,entries:[
    {id:'files',kind:'files',scope:'old-only',shared_scope:'default',spec:fileSpecPath,identity_state:state,old_account_uuid:'FAKE_OLD'},
    {id:'identifiers',kind:'identifiers',scope:'old-only',shared_scope:'default',spec:idSpecPath,identity_state:state,old_account_uuid:'FAKE_OLD'},
    {id:'browser',kind:'browser',scope:'old-only',shared_scope:'browser',spec:specPath,profile_path:profile,node:process.execPath},
    {id:'desktop',kind:'desktop',scope:'not-installed',shared_scope:'desktop'}]};
  const inventoryPath=path.join(root,'inventory.json'),completionOut=path.join(root,'completion-run');
  fs.writeFileSync(inventoryPath,JSON.stringify(inventory));
  r=await new Promise((resolve,reject)=>{
    const p=spawn(process.env.FIXTURE_PYTHON||'python',['-B','-X','utf8',path.join(path.dirname(helper),'completion.py'),
      '--inventory',inventoryPath,'--out',completionOut,'--execute'],{windowsHide:true});
    let out='',err='';p.stdout.on('data',b=>out+=b);p.stderr.on('data',b=>err+=b);p.on('error',reject);p.on('close',code=>resolve({code,out,err}));
  });
  assert.equal(r.code,0,r.out+r.err);
  const completion=JSON.parse(fs.readFileSync(path.join(completionOut,'completion.json')));
  assert.equal(completion.overall_status,'complete-selected-old-scope');
  const report=completion.entries.find(x=>x.id==='browser');assert.equal(report.status,'completed');assert.equal(report.session_storage_cleared_tabs,2);
  assert.deepEqual(JSON.parse(fs.readFileSync(state)),{projects:{keep:true}});
  assert.deepEqual(JSON.parse(fs.readFileSync(cred)),{mcpOAuth:{keep:'FAKE_MCP_KEEP'}});
  assert.equal(fs.readFileSync(work,'utf8'),'FAKE_KEEP_SESSION');assert.equal(fs.readFileSync(network,'utf8'),'FAKE_KEEP_VPN_DNS');
  checks.push('installed completion runner executes file, identifier and original-context browser cleanup in one run');
  checks.push('actual runner preserves MCP, project/session and company-network fixture data');
  assert.ok(old.isClosed()&&child.isClosed());checks.push('selected open-tab sessionStorage cleared and only selected tabs closed');
  for(const o of [OLD,CHILD]){const p=await ctx.newPage();await p.goto(o);assert.deepEqual(await view(p),{local:null,session:null,caches:[],db:[]});assert.equal(await p.evaluate(()=>localStorage.length),0);await p.close();}
  checks.push('target and child localStorage IndexedDB CacheStorage are empty after reopening');
  checks.push('pagehide unload and visibilitychange cannot leave recreated storage after closure');
  const cookies=await ctx.cookies();assert.deepEqual(cookies.map(c=>c.name).sort(),['company','shared']);checks.push('exact target cookies removed; company and shared parent cookies retained');
  assert.deepEqual(await view(company),companyBefore);assert.deepEqual(await view(shared),sharedBefore);checks.push('company and shared Console storage preserved');
  assert.deepEqual(await view(fresh),newBefore);assert.deepEqual(await next.cookies(),newCookies);checks.push('separate new-account context unchanged');
  assert.equal(JSON.parse(fs.readFileSync(path.join(root,'saved-drafts.json'))).length,2);checks.push('saved work drafts retained');
  for(const name of ['plan.json'])assert.ok(!/FAKE_OLD|FAKE_COMPANY_KEEP|FAKE_NEW_KEEP/.test(fs.readFileSync(path.join(root,name),'utf8')));
  assert.ok(!/FAKE_OLD_ONLY|FAKE_COMPANY_KEEP|FAKE_NEW_KEEP/.test(JSON.stringify(completion)));checks.push('no credential values in plans or receipts');
  // New account between planning and applying must block, including before tab closure.
  const p2=path.join(root,'stale-plan.json');r=await run(['plan','--spec',specPath,'--out',p2]);assert.equal(r.code,0,r.out);
  await ctx.addCookies([{name:'login',value:'FAKE_NEW_AFTER_PLAN',domain:'.claude.ai',path:'/',secure:true}]);
  r=await run(['apply','--plan',p2,'--out',path.join(root,'stale-receipt.json'),'--execute']);assert.notEqual(r.code,0);assert.ok((await ctx.cookies()).some(c=>c.value==='FAKE_NEW_AFTER_PLAN'));checks.push('new login after plan blocks stale cleanup');
  const result={version:'1.3.0',passed:true,checks,engine:browser.version(),intercepted_local_requests:intercepted,
   real_account_access:false,real_skill_directory_modified:false,company_network_modified:false,
   boundary:'Real Chromium with isolated synthetic profile and locally fulfilled pages; not a real Claude login or macOS UI test.'};
  fs.writeFileSync(path.join(root,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({passed:true,checks:checks.length,result:path.join(root,'result.json')}));
 } finally{await ctx.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
