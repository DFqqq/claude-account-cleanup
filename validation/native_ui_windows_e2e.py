"""Real Windows UIAutomation on a fabricated app in a PRIVATE desktop.

Requires existing pywin32 in the TEST Python runtime only. Never opens Claude,
browser profiles, Keychain, real credentials, or the user's visible desktop.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True
FORM = r'''
param([string]$FixtureRoot)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$cache=Join-Path $FixtureRoot 'fake-auth.json'
$f=New-Object System.Windows.Forms.Form
$f.Text='Synthetic Cleanup UI';$f.Width=400;$f.Height=200
$button=New-Object System.Windows.Forms.Button
$button.Text='Log out';$button.AccessibleName='Log out';$button.Width=200
$button.Add_Click({
  $d=Get-Content -LiteralPath $cache -Raw | ConvertFrom-Json
  $d.PSObject.Properties.Remove('oldLogin')
  $d | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $cache -Encoding UTF8
  $button.Text='Sign in';$button.AccessibleName='Sign in'
})
$f.Controls.Add($button)
$timer=New-Object System.Windows.Forms.Timer
$timer.Interval=30000;$timer.Add_Tick({$timer.Stop();$f.Close()});$timer.Start()
$f.Add_Shown({
  @{pid=$PID;handle=$f.Handle.ToInt64()} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $FixtureRoot 'ready.json') -Encoding UTF8
})
[void]$f.ShowDialog()
'''


def child(root, skill):
    sys.path.insert(0, str(skill / 'scripts'))
    import native_ui
    before = {name: hashlib.sha256((root/name).read_bytes()).hexdigest()
              for name in ('project.txt','session.jsonl','company-network.json')}
    form = root/'form.ps1'; form.write_text(FORM, encoding='utf-8-sig')
    errors=(root/'fixture-errors.log').open('wb')
    app = subprocess.Popen(['powershell.exe','-NoProfile','-STA','-File',str(form),'-FixtureRoot',str(root)],
                           stdout=subprocess.DEVNULL,stderr=errors)
    handle = None
    try:
        deadline=time.monotonic()+12
        while not (root/'ready.json').exists() and time.monotonic()<deadline:
            if app.poll() is not None:raise RuntimeError('Fixture app stopped before ready')
            time.sleep(.1)
        ready=json.loads((root/'ready.json').read_text(encoding='utf-8-sig'))
        assert ready['pid']==app.pid
        handle=ready['handle']
        spec={'schema':1,'scope':'old-only','profile_confirmed':True,'drafts_saved':True,
              'entry_kind':'desktop','pid':app.pid,'window_handle':handle,'action':'inspect'}
        inspect=native_ui.operate(spec)
        assert inspect['status']=='inspected',inspect
        controls=[x for x in inspect['controls'] if x['name']=='Log out']
        assert len(controls)==1,inspect
        action=native_ui.operate({**spec,'action':'logout','control_name':'Log out','runtime_id':controls[0]['runtime_id']})
        assert action['status']=='action-invoked',action
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            cache=json.loads((root/'fake-auth.json').read_text(encoding='utf-8-sig'))
            if 'oldLogin' not in cache:break
            time.sleep(.1)
        assert cache=={'mcp':'FAKE_MCP_KEEP'},cache
        after=native_ui.operate(spec)
        assert any(x['name']=='Sign in' for x in after['controls']),after
        assert before=={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in before}
        report={'passed':True,'native_windows_bridge':True,'backend':action.get('backend','UIAutomation'),'test_app':'fabricated Windows Forms app',
                'official_claude_desktop_login_tested':False,'real_account_accessed':False,
                'checks':['exact process/window inspected','logout invoked through packaged native bridge',
                          'independent fake auth removal','independent sign-in control visible',
                          'MCP preserved','project/session/company-network hashes preserved']}
    except Exception as exc:
        report={'passed':False,'error':type(exc).__name__,'detail':str(exc)[:500],
                'real_account_accessed':False}
    finally:
        if handle:
            ctypes.windll.user32.PostMessageW(ctypes.c_void_p(handle),0x0010,0,0)
        try:app.wait(timeout=5)
        except subprocess.TimeoutExpired:pass  # Fixture self-closes after 30s.
        errors.close()
    (root/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return 0 if report['passed'] else 2


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--skill',required=True);p.add_argument('--child',action='store_true')
    args=p.parse_args();root=Path(args.root).absolute();skill=Path(args.skill).absolute()
    if args.child:return child(root,skill)
    if os.name!='nt':return 2
    root.mkdir(parents=True,exist_ok=False)
    for name,value in [('project.txt','FAKE_KEEP'),('session.jsonl','FAKE_KEEP'),('company-network.json','FAKE_KEEP')]:
        (root/name).write_text(value,encoding='utf-8')
    (root/'fake-auth.json').write_text(json.dumps({'oldLogin':'FAKE_OLD','mcp':'FAKE_MCP_KEEP'}),encoding='utf-8')
    # Test infrastructure dependency, never installed by this script.
    candidate=Path(sys.executable).parent/'Lib/site-packages/pywin32_system32'
    if candidate.exists():os.add_dll_directory(str(candidate))
    import win32con,win32process
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.CreateDesktopW.argtypes=[ctypes.c_wchar_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p]
    user.CreateDesktopW.restype=ctypes.c_void_p
    user.CloseDesktop.argtypes=[ctypes.c_void_p]
    name='CleanupSkillFixture_'+uuid.uuid4().hex[:10]
    desktop=user.CreateDesktopW(name,None,None,0,0x10000000,None)
    if not desktop:raise ctypes.WinError(ctypes.get_last_error())
    env=os.environ.copy()
    for key in list(env):
        if key.startswith(('ANTHROPIC_','CLAUDE_','CODEX_','OPENAI_')):env.pop(key,None)
    for key,suffix in [('HOME','home'),('USERPROFILE','home'),('APPDATA','home/roaming'),('LOCALAPPDATA','home/local'),('TEMP','tmp'),('TMP','tmp')]:
        folder=root/suffix;folder.mkdir(parents=True,exist_ok=True);env[key]=str(folder)
    startup=win32process.STARTUPINFO();startup.lpDesktop='winsta0\\'+name
    command=[sys.executable,'-B','-X','utf8',str(Path(__file__).resolve()),'--root',str(root),'--skill',str(skill),'--child']
    hp=ht=None
    try:
        hp,ht,pid,tid=win32process.CreateProcess(sys.executable,subprocess.list2cmdline(command),None,None,False,
            win32con.CREATE_UNICODE_ENVIRONMENT,env,str(root),startup)
        ht.Close();ht=None
        deadline=time.monotonic()+40
        while not (root/'result.json').exists() and time.monotonic()<deadline:time.sleep(.2)
        report=json.loads((root/'result.json').read_text(encoding='utf-8'))
        print(json.dumps({'passed':report['passed'],'result':str(root/'result.json')}))
        return 0 if report['passed'] else 2
    finally:
        if ht:ht.Close()
        if hp:hp.Close()
        user.CloseDesktop(desktop)


if __name__=='__main__':raise SystemExit(main())
