"""Portable offline validation. Only package files and generated fixtures are read.

Run after extracting the test bundle. Never discovers, installs into, or cleans a
real user account. A real Codex application's skill discovery is a separate check.
"""
import argparse
import shutil
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import tempfile
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
if os.name == 'nt' and not str(ROOT).startswith('\\\\?\\'):
    raw = str(ROOT)
    ROOT = Path('\\\\?\\UNC\\' + raw[2:] if raw.startswith('\\\\') else '\\\\?\\' + raw)

def node_path(value):
    value = str(value)
    if value.startswith('\\\\?\\UNC\\'):
        return '\\\\' + value[8:]
    return value[4:] if value.startswith('\\\\?\\') else value

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--node')
    parser.add_argument('--browser',action='store_true')
    parser.add_argument('--browser-artifact-dir')
    options=parser.parse_args()
    if sys.version_info < (3, 10):
        print('Python 3.10+ is required. Nothing was installed or cleaned.')
        return 2
    manifest=json.loads((ROOT/'bundle-manifest.json').read_text(encoding='utf-8'))
    for rel,expected in manifest['files'].items():
        p=ROOT/rel
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            print('Package integrity check failed; no tests started.')
            return 2
    out=ROOT/'test-runs'/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    out.mkdir(parents=True)
    skill=ROOT/'claude-account-cleanup'
    results=[]
    def run(label,args,expected_status=None,executable=None):
        start=time.monotonic()
        try:
            command=([executable,*map(node_path,args)] if executable else [sys.executable,'-B','-X','utf8',*map(str,args)])
            process=subprocess.run(command,cwd=node_path(ROOT) if executable else ROOT,
                                   capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=240)
            text=process.stdout+'\n'+process.stderr
            passed=process.returncode==0
            if expected_status is not None:
                try:passed=passed and json.loads(process.stdout)['status']==expected_status
                except (json.JSONDecodeError,KeyError):passed=False
            count=re.search(r'Ran (\d+) tests?',text) or re.search(r'(?:#|ℹ) tests (\d+)',text)
            result={'name':label,'passed':passed,'returncode':process.returncode,'tests':int(count[1]) if count else None}
        except subprocess.TimeoutExpired:
            text='Isolated child exceeded the test timeout; only this child was stopped.'
            result={'name':label,'passed':False,'reason':'timeout'}
        (out/(label+'.log')).write_text(text,encoding='utf-8')
        result['seconds']=round(time.monotonic()-start,2);results.append(result)
        print(label+': '+('PASS' if result['passed'] else 'NEEDS REVIEW'),flush=True)
    fake_skills=out/'virtual-user'/'.agents'/'skills'
    run('install',[skill/'scripts/install_skill.py','--dest-root',fake_skills],'installed')
    run('repeat-install',[skill/'scripts/install_skill.py','--dest-root',fake_skills],'already-installed')
    # Exercise the installed copy, not only the source files.
    installed=fake_skills/'claude-account-cleanup'
    run('cleanup-core',[installed/'tests/test_cleanup.py','--artifact-dir',out/'core-fixtures'])
    run('installation',[installed/'tests/test_install.py','--artifact-dir',out/'install-fixtures'])
    run('identifiers',[installed/'tests/test_identifiers.py','--artifact-dir',out/'identifier-fixtures'])
    run('extended',[ROOT/'validation/test_extended.py','--artifact-dir',out/'extended-fixtures'])
    node=options.node or shutil.which('node')
    if node:
        run('browser-unit',['--test',ROOT/'validation/test_browser.mjs'],executable=node)
    if options.browser:
        if not node:raise RuntimeError('Browser tests require an existing Node.js runtime')
        browser_out=Path(options.browser_artifact_dir).absolute() if options.browser_artifact_dir else Path(tempfile.mkdtemp(prefix='cc-browser-fixture-'))
        run('browser-e2e',[ROOT/'validation/browser_e2e.cjs',browser_out,installed/'scripts/browser_cleanup.mjs'],executable=node)
    report={'version':'1.2.0','passed':all(x['passed'] for x in results),'system':platform.system(),
            'python':platform.python_version(),'results':results,'real_account_access':False,
            'real_skill_directory_modified':False,'client_processes_operated':False,'network_used':False,
            'browser_unit_run':bool(node),'browser_e2e_run':options.browser,
            'not_covered':['real Claude accounts and browser UI control connection availability','native macOS Keychain and native macOS browser UI']}
    (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Result: '+str(out/'result.json'))
    print('Real accounts, client windows and company network settings were not accessed or changed.')
    return 0 if report['passed'] else 2

if __name__=='__main__':
    raise SystemExit(main())
