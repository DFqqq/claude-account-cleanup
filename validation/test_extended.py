"""Additional failure/recovery checks. All account material is synthetic and local."""
import argparse
import copy
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / 'claude-account-cleanup'
sys.path.insert(0, str(SKILL / 'scripts'))
import cleanup_guard as g
import keychain_guard as k

ARTIFACTS = None
class ExtendedTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='probe-',dir=ARTIFACTS))
        self.config=self.base/'旧登录 测试😀'; self.config.mkdir()
        self.state=self.base/'.claude.json'
        self.cred=self.config/'.credentials.json'
        self.work=self.config/'项目'/'session.jsonl';self.work.parent.mkdir()
        self.network=self.base/'company-network.json'
        self.original={'oauthAccount':{'accountUuid':'FAKE-OLD'},'userID':'FAKE-ID','projects':{'项目':{'count':9007199254740993}},'mcpServers':{'keep':{'secret':'FAKE-MCP-KEEP'}}}
        self.creds={'claudeAiOauth':{'accessToken':'FAKE-OLD-SECRET'},'mcpOAuth':{'token':'FAKE-MCP-KEEP'},'apiKey':'FAKE-GATEWAY-KEEP'}
        self.state.write_bytes(g.encode(self.original));self.cred.write_bytes(g.encode(self.creds))
        self.work.write_bytes(b'{"session":"FAKE","text":"keep"}\n')
        self.network.write_bytes(b'{"dns":"keep","proxy":"keep"}')
        self.spec={'schema':1,'account_state':'old-only','config_dirs':[str(self.config)],'state_files':[str(self.state)],'preserve_paths':[str(self.network)],'operations':[
            {'kind':'state-json','path':str(self.state),'remove_fields':['oauthAccount','userID'],'old_account_evidence':'confirmed'},
            {'kind':'credential-json','path':str(self.cred),'remove_fields':['claudeAiOauth'],'old_account_evidence':'confirmed'}]}
    def plan(self):return g.prepare(copy.deepcopy(self.spec))
    def test_bom_crlf_chinese_emoji_and_large_integer_preserved(self):
        self.state.write_bytes(b'\xef\xbb\xbf'+g.encode(self.original).replace(b'\n',b'\r\n'))
        p=self.plan();self.assertEqual(g.apply(p,True)['status'],'verified')
        self.assertEqual(g.parse_json(self.state.read_bytes())['projects'],self.original['projects'])
    def test_second_run_old_plan_refused_and_new_scope_empty(self):
        p=self.plan();g.apply(p,True);before=self.cred.read_bytes()
        with self.assertRaises(g.GuardError):g.apply(p,True)
        self.assertEqual(self.cred.read_bytes(),before)
        self.spec['operations']=[]
        self.assertEqual(g.apply(self.plan(),True)['changed_files'],0)
    def test_failure_before_second_write_then_resume_only_remaining(self):
        p=self.plan();original=g.atomic_replace;calls=[]
        def inject(path,raw,expected):
            calls.append(str(path))
            if len(calls)==2:raise OSError('simulated full disk')
            return original(path,raw,expected)
        with patch.object(g,'atomic_replace',side_effect=inject):r=g.apply(p,True)
        self.assertEqual(r['status'],'partial');self.assertEqual(r['verified_completed_files'],1)
        state_after=self.state.read_bytes();self.spec['operations']=self.spec['operations'][1:]
        self.assertEqual(g.apply(self.plan(),True)['status'],'verified')
        self.assertEqual(self.state.read_bytes(),state_after)
    def test_failure_after_commit_is_not_reported_all_done(self):
        original=g.atomic_replace
        def inject(path,raw,expected):
            original(path,raw,expected);raise OSError('post-commit check failed')
        p=self.plan()
        with patch.object(g,'atomic_replace',side_effect=inject):r=g.apply(p,True)
        self.assertEqual(r['status'],'partial');self.assertEqual(r['verified_completed_files'],0)
        self.assertNotIn('oauthAccount',g.parse_json(self.state.read_bytes()))
        self.assertEqual(g.parse_json(self.cred.read_bytes()),self.creds)
    def test_new_account_arrives_between_writes_remaining_credential_protected(self):
        p=self.plan();original=g.atomic_replace
        new=g.encode({'claudeAiOauth':{'accessToken':'FAKE-NEW-KEEP'},'mcpOAuth':self.creds['mcpOAuth']})
        def inject(path,raw,expected):
            original(path,raw,expected);self.cred.write_bytes(new)
        with patch.object(g,'atomic_replace',side_effect=inject):r=g.apply(p,True)
        self.assertEqual(r['status'],'partial');self.assertEqual(self.cred.read_bytes(),new)
    def test_project_deleted_after_plan_blocks_all_mutation(self):
        p=self.plan();self.work.unlink();old=self.state.read_bytes()
        with self.assertRaises(g.GuardError):g.apply(p,True)
        self.assertEqual(self.state.read_bytes(),old)
    def test_source_read_only_file_is_not_forced(self):
        if os.name!='nt':self.skipTest('Windows readonly semantics')
        self.state.chmod(0o444);p=self.plan();old=self.state.read_bytes()
        try:
            r=g.apply(p,True)
            self.assertEqual(r['status'],'partial');self.assertEqual(self.state.read_bytes(),old)
        finally:self.state.chmod(0o666)
    def test_actual_windows_sharing_lock_is_not_forced(self):
        if os.name!='nt':self.skipTest('Windows share lock')
        from ctypes import wintypes as w
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE];api.CreateFileW.restype=w.HANDLE
        api.CloseHandle.argtypes=[w.HANDLE];api.CloseHandle.restype=w.BOOL
        handle=api.CreateFileW(str(self.state),0x80000000,1,None,3,0,None)
        self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
        old=self.state.read_bytes()
        try:
            p=self.plan();r=g.apply(p,True)
            self.assertEqual(r['status'],'partial');self.assertEqual(self.state.read_bytes(),old)
        finally:api.CloseHandle(handle)
    def test_cli_existing_receipt_blocks_before_source_change(self):
        plan=self.base/'plan.json';plan.write_bytes(g.encode(self.plan()))
        receipt=self.base/'receipt.json';receipt.write_bytes(b'KEEP EXISTING RECEIPT')
        old=self.state.read_bytes()
        run=subprocess.run([sys.executable,'-B','-X','utf8',str(SKILL/'scripts/cleanup_guard.py'),'apply','--plan',str(plan),'--out',str(receipt),'--execute'],capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(run.returncode,0);self.assertEqual(self.state.read_bytes(),old);self.assertEqual(receipt.read_bytes(),b'KEEP EXISTING RECEIPT')
        self.assertNotIn('FAKE-OLD-SECRET',run.stdout+run.stderr)
    def test_many_sessions_remain_byte_identical(self):
        snapshots={}
        for i in range(120):
            p=self.work.parent/f'session-{i}.jsonl';p.write_bytes((f'{{"id":{i},"text":"虚构工作记录"}}\n'*4).encode());snapshots[p]=p.read_bytes()
        self.assertEqual(g.apply(self.plan(),True)['status'],'verified')
        self.assertTrue(all(p.read_bytes()==v for p,v in snapshots.items()))
    def test_long_windows_source_and_receipt_paths(self):
        if os.name!='nt':self.skipTest('Windows extended paths')
        deep=self.base/('深路径-'+('x'*64))/('y'*70)
        while len(str(deep))<300:deep=deep/('z'*40)
        deep=Path(str(deep) if str(deep).startswith('\\\\?\\') else '\\\\?\\'+str(deep));deep.mkdir(parents=True)
        state=deep/'.claude.json';state.write_bytes(g.encode(self.original))
        self.spec['state_files']=[str(state)];self.spec['operations']=self.spec['operations'][:1];self.spec['operations'][0]['path']=str(state)
        self.assertGreater(len(str(state)),280)
        self.assertEqual(g.apply(self.plan(),True)['status'],'verified')
        report=deep/'output.json';g.private_write(report,b'{}');self.assertEqual(report.read_bytes(),b'{}')
    def test_long_windows_source_without_manual_prefix(self):
        if os.name!='nt':self.skipTest('Windows extended paths')
        plain=self.base/('deep-'+('x'*64))/('y'*70)
        while len(str(plain))<300:plain=plain/('z'*40)
        extended=Path(str(plain) if str(plain).startswith('\\\\?\\') else '\\\\?\\'+str(plain));extended.mkdir(parents=True)
        (extended/'.claude.json').write_bytes(g.encode(self.original));state=Path(str(plain).removeprefix('\\\\?\\'))/'.claude.json'
        self.spec['state_files']=[str(state)];self.spec['operations']=self.spec['operations'][:1];self.spec['operations'][0]['path']=str(state)
        self.assertEqual(g.apply(self.plan(),True)['status'],'verified')
    def test_keychain_changed_between_inspect_and_write_preserved(self):
        class Backend:
            def __init__(b):b.raw=g.encode(self.creds);b.writes=0
            def read(b):return b.raw
            def compare_replace(b,expected,output):
                b.raw=g.encode({'claudeAiOauth':'FAKE_NEW','mcpOAuth':'FAKE_KEEP'})
                g.require(g.digest(b.raw)==expected,'Changed');b.writes+=1
        b=Backend();fp=k.inspect(b)['fingerprint']
        with self.assertRaises(g.GuardError):k.clean(b,fp,True)
        self.assertEqual(b.writes,0);self.assertEqual(g.parse_json(b.raw)['claudeAiOauth'],'FAKE_NEW')
    def test_keychain_denied_not_absent(self):
        class Backend:
            def read(b):raise g.GuardError('Denied')
        with self.assertRaises(g.GuardError):k.inspect(Backend())
    def test_malformed_keychain_keeps_original(self):
        class Backend:
            raw=b'{"claudeAiOauth":1,"claudeAiOauth":2,"mcpOAuth":"FAKE_KEEP"}'
            def read(b):return b.raw
            def compare_replace(b,*a):raise AssertionError('must never write')
        b=Backend();raw=b.raw
        with self.assertRaises(g.GuardError):k.clean(b,g.digest(raw),True)
        self.assertEqual(b.raw,raw)
    def test_report_does_not_include_fake_secrets(self):
        p=self.plan();r=g.apply(p,True);text=json.dumps({'plan':p,'receipt':r})
        for secret in ['FAKE-OLD-SECRET','FAKE-MCP-KEEP','FAKE-GATEWAY-KEEP']:self.assertNotIn(secret,text)
    def test_verify_rejects_missing_operations(self):
        p=self.plan();p['operations']=[]
        with self.assertRaises(g.GuardError):g.verify(p)
    def test_verify_rejects_faked_no_change_hashes(self):
        p=self.plan()
        for op in p['operations']:op['after_sha256']=op['before_sha256']
        with self.assertRaises(g.GuardError):g.verify(p)
    def test_verify_rejects_cross_platform_plan(self):
        p=self.plan();g.apply(p,True);p['platform']='fixture-other-platform'
        with self.assertRaises(g.GuardError):g.verify(p)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--artifact-dir',required=True);options,rest=parser.parse_known_args()
    ARTIFACTS=str(Path(options.artifact_dir).absolute());Path(ARTIFACTS).mkdir(parents=True,exist_ok=True)
    unittest.main(argv=[sys.argv[0],*rest],verbosity=2)
