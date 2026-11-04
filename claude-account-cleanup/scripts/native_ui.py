"""Bounded native accessibility bridge for an Agent-confirmed OLD app window.

No global window discovery, process launch, keystrokes, clipboard, database edits,
or credential reads. Existing Agent UI tools remain preferred when available.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
from cleanup_guard import GuardError, read_bytes, parse_json, require

ACTIONS = {'inspect', 'open-menu', 'logout', 'confirm-logout', 'remove-site'}
NAMES = {
    'logout': ('Log out', 'Logout', 'Sign out', '退出登录', '退出账号', '登出'),
    'confirm-logout': ('Log out', 'Logout', 'Sign out', '退出登录', '退出账号', '登出'),
    'open-menu': ('Settings', 'Account', 'Menu', '设置', '账户', '账号', '菜单'),
    'remove-site': ('Delete', 'Remove', 'Clear', '删除', '移除', '清除'),
}


def validate_spec(spec):
    require(spec.get('schema') == 1 and spec.get('scope') == 'old-only'
            and spec.get('profile_confirmed') is True and spec.get('drafts_saved') is True,
            'Confirmed old-only original profile and saved work required')
    require(spec.get('entry_kind') in ('browser', 'desktop', 'vscode'), 'Unsupported UI entrypoint')
    require(type(spec.get('pid')) is int and spec['pid'] > 0, 'Explicit running process required')
    action = spec.get('action')
    require(action in ACTIONS, 'Unsupported UI action')
    if spec['entry_kind'] == 'browser':
        require(action in ('inspect', 'remove-site'), 'Browser profile/SSO logout is outside site-data cleanup')
    if action != 'inspect':
        require(spec.get('control_name') in NAMES[action], 'Use a known scoped account/menu action')
    if action == 'remove-site':
        require(spec['entry_kind'] == 'browser' and spec.get('origin') == 'https://claude.ai'
                and isinstance(spec.get('site_container_runtime_id'), list),
                'Exact site container and Claude web origin required; global deletion refused')
    if sys.platform == 'win32':
        require(type(spec.get('window_handle')) is int and spec['window_handle'] > 0,
                'Exact original window handle required')
        if action != 'inspect':
            require(isinstance(spec.get('runtime_id'), list) and 0 < len(spec['runtime_id']) < 20
                    and all(type(x) is int for x in spec['runtime_id']), 'Previously inspected control identity required')
    return spec


def operate(spec, runner=subprocess.run):
    validate_spec(spec)
    here = Path(__file__).resolve().parent
    if sys.platform == 'win32':
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-File',
                   str(here / 'native_ui_windows.ps1')]
        data = json.dumps(spec, ensure_ascii=False)
    elif sys.platform == 'darwin':
        if spec['action'] == 'remove-site':
            return {'status': 'needs-agent-ui', 'reason': 'Use an existing browser UI tool to verify the exact macOS site row'}
        command = ['osascript', str(here / 'native_ui_macos.applescript'), str(spec['pid']),
                   spec['action'], spec.get('control_name', '')]
        data = None
    else:
        return {'status': 'unsupported-platform'}
    try:
        process = runner(command, input=data, text=True, encoding='utf-8', errors='replace',
                         capture_output=True, timeout=25)
    except subprocess.TimeoutExpired:
        return {'status': 'incomplete' if spec['action'] != 'inspect' else 'needs-agent-ui',
                'reason': 'UI operation timed out; reinspect this window before attempting another action'}
    if process.returncode:
        denial = sys.platform == 'darwin' and re.search(
            r'-1743|-25211|not allowed assistive access|not authorized to send apple events',
            process.stderr.lower())
        return {'status': 'needs-system-authorization' if denial else 'needs-agent-ui',
                'reason': 'Grant native accessibility/automation permission in macOS' if denial else
                          'Exact native control is missing or changed; use existing UI control'}
    try:
        if sys.platform == 'win32':
            return json.loads(process.stdout)
        # macOS output is a list of bounded role/name labels, never field values.
        lines = [x[:180] for x in process.stdout.splitlines()[:80]]
        return {'status': 'inspected' if spec['action'] == 'inspect' else 'action-invoked',
                'controls': lines, 'logout_independently_verified': False}
    except (ValueError, TypeError):
        return {'status': 'needs-agent-ui', 'reason': 'Native UI response cannot be verified'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', required=True)
    args = parser.parse_args()
    try:
        result = operate(parse_json(read_bytes(args.spec, 1024 * 1024)))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] in ('inspected', 'action-invoked') else 2
    except (GuardError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print(json.dumps({'status': 'blocked', 'reason': 'Exact original old-only UI scope is unavailable'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
