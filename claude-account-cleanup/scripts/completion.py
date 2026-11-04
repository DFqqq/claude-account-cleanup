"""Execute an Agent-confirmed cleanup inventory; no user-profile discovery.

Every installed entrypoint must be accounted for. File success is not global
success. Inventories contain paths/evidence, never credential values. Native UI
and system authorization remain independently verified entrypoints.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

sys.dont_write_bytecode = True
import cleanup_guard as g
import keychain_guard as keychain
from identifier_policy import reset_fields

VERSION = '1.3.0'
KINDS = {'files', 'identifiers', 'keychain', 'browser', 'desktop', 'vscode'}
FINISHED = {'completed', 'already-clean', 'not-applicable', 'protected-current-account'}


def load(path):
    return g.parse_json(g.read_bytes(g.guard_path(Path(path)), 8 * 1024 * 1024))


def validate_inventory(value):
    g.require(value.get('schema') == 1 and value.get('inventory_complete') is True,
              'Agent must complete entrypoint discovery before running')
    entries = value.get('entries')
    g.require(isinstance(entries, list) and 0 < len(entries) <= 100, 'Explicit entrypoint list required')
    seen = set()
    for item in entries:
        g.require(isinstance(item, dict) and item.get('kind') in KINDS, 'Unsupported entrypoint')
        ident = item.get('id')
        g.require(isinstance(ident, str) and 0 < len(ident) <= 100 and ident not in seen, 'Unique entrypoint id required')
        seen.add(ident)
        g.require(item.get('scope') in ('old-only', 'new-account', 'mixed', 'unknown', 'not-installed'), 'Scope required')
        g.require(isinstance(item.get('shared_scope'), str) and item['shared_scope'], 'Credential sharing scope required')
    return entries


def protect_changed_identity(item):
    path = item.get('identity_state')
    expected = item.get('old_account_uuid')
    if not path or not expected:
        return False
    current = load(path).get('oauthAccount', {}).get('accountUuid')
    return current is not None and current != expected


def file_step(item, work, execute):
    spec = copy.deepcopy(load(item['spec']))
    # Fresh planning on every run. No previously generated plan is replayed.
    operations = []
    for op in spec.get('operations', []):
        state = load(op['path'])
        op['remove_fields'] = [field for field in op.get('remove_fields', []) if field in state]
        if op['remove_fields'] or op.get('identifier_reset'):
            operations.append(op)
    if not operations:
        return {'status': 'already-clean'}
    spec['operations'] = operations
    plan = g.prepare(spec)
    g.private_write(work / 'file-plan.json', g.encode(plan))
    result = g.apply(plan, execute)
    return {**result, 'status': 'completed' if result['status'] == 'verified' else result['status']}


def identifier_step(item, work, execute):
    spec = copy.deepcopy(load(item['spec']))
    # This is a separate step: missing identifier evidence cannot block logout.
    for op in spec.get('operations', []):
        g.require(op.get('kind') == 'state-json' and not op.get('remove_fields'),
                  'Identifier step may remove only identifiers selected by policy')
        state = load(op['path'])
        if not any(field in state for field in ('userID', 'machineID', 'deviceId')):
            continue
        try:
            reset_fields(state, op.get('identifier_reset', {}))
        except ValueError:
            return {'status': 'needs-build-evidence', 'reason': 'Inspect the actual program with identifier_probe.py; Agent resolves fields'}
    spec['operations'] = [op for op in spec.get('operations', [])
                          if any(k in load(op['path']) for k in ('userID', 'machineID', 'deviceId'))]
    if not spec['operations']:
        return {'status': 'already-clean'}
    target = work / 'identifier-spec.json'
    g.private_write(target, g.encode(spec))
    return file_step({**item, 'spec': str(target)}, work, execute)


def keychain_step(item, work, execute):
    backend = None
    try:
        account = item.get('account')
        if not account:
            g.require(item.get('namespace_verified') is True, 'Agent must verify this old-only credential namespace')
            metadata = keychain.resolve_account(item['keychain'], item['service'])
            if metadata['status'] != 'candidate-account':
                return metadata
            account = metadata['account']
        backend = keychain.MacKeychain(item['keychain'], item['service'], account)
        info = keychain.inspect(backend)
        if info['status'] == 'absent' or not info.get('has_claude_oauth'):
            return {'status': 'already-clean'}
        result = keychain.clean(backend, info['fingerprint'], execute)
        return {**result, 'status': 'completed' if result['status'] == 'verified' else result['status']}
    except keychain.KeychainError as error:
        return {'status': error.status, 'reason': error.reason}
    finally:
        if backend is not None:
            backend.close()


def browser_step(item, work, execute):
    if not item.get('spec'):
        return {'status': 'needs-agent-ui', 'reason': 'Use the original browser site-data UI; no existing CDP connection is confirmed'}
    node = item.get('node') or shutil.which('node')
    if not node:
        return {'status': 'needs-agent-ui', 'reason': 'Use available UI control; existing Node 22+ unavailable'}
    helper = Path(__file__).with_name('browser_cleanup.mjs')
    plan = work / 'browser-plan.json'
    for command in ([node, str(helper), 'plan', '--spec', item['spec'], '--out', str(plan)],
                    [node, str(helper), 'apply', '--plan', str(plan), '--out', str(work / 'browser-result.json'),
                     *(['--execute', '--retry-transient'] if execute else [])]):
        process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=240)
        try:
            result = json.loads(process.stdout)
        except (ValueError, TypeError):
            return {'status': 'needs-agent-ui', 'reason': 'Confirmed browser helper is unavailable; inspect original-profile UI'}
        if process.returncode:
            # Never copy arbitrary native stderr into a receipt.
            return {'status': result.get('status', 'blocked'), 'reason': result.get('reason', 'Browser verification incomplete')}
    return {**result, 'status': 'completed' if result.get('status') == 'cleared-selected-origins' else result['status']}


def ui_step(item, work, execute):
    evidence_path = item.get('ui_evidence')
    if evidence_path:
        evidence = load(evidence_path)
        g.require(evidence.get('entry_id') == item['id'] and evidence.get('profile_confirmed') is True,
                  'UI evidence must match this entrypoint')
        g.require(all(evidence.get(k) is True for k in
                      ('old_logout_verified', 'work_preserved', 'mcp_preserved')), 'Independent UI/preservation evidence required')
        return {'status': 'completed', 'evidence': str(Path(evidence_path).absolute())}
    # The Agent uses its UI tools or the packaged native bridge, then submits
    # evidence and reruns this inventory. This is an Agent task, not an employee task.
    return {'status': 'needs-agent-ui', 'reason': 'Use existing UI tools or native_ui.py on the confirmed original entrypoint; verify logout and preservation'}


ADAPTERS = {'files': file_step, 'identifiers': identifier_step, 'keychain': keychain_step,
            'browser': browser_step, 'desktop': ui_step, 'vscode': ui_step}


def run(inventory, output, execute=False, adapters=None):
    entries = validate_inventory(inventory)
    output = g.guard_path(Path(output), missing_leaf=True)
    g.require(output.is_absolute() and not output.exists(), 'Fresh private work directory required')
    uncertain_groups = set()
    # Receipts must not enter any source/protected tree and invalidate preservation.
    for entry in entries:
        if entry.get('spec') and entry['kind'] in ('files', 'identifiers'):
            try:
                spec = load(entry['spec'])
                sources = [g.guard_path(Path(raw)) for key in ('config_dirs', 'state_files', 'preserve_paths')
                           for raw in spec.get(key, [])]
            except (g.GuardError, OSError, ValueError, KeyError, TypeError):
                uncertain_groups.add(entry['shared_scope'])
                sources = []
            for source in sources:
                g.require(output != source and output not in source.parents and source not in output.parents,
                          'Receipt overlaps source or preservation scope')
        if entry.get('profile_path'):
            source = g.guard_path(Path(entry['profile_path']))
            g.require(output != source and output not in source.parents and source not in output.parents,
                      'Receipt overlaps browser/app profile')
    output.mkdir(parents=True)
    # Fail before any credential change if receipts cannot be stored privately.
    g.private_write(output / 'inventory.json', g.encode(inventory))
    protected_groups = {e['shared_scope'] for e in entries if e['scope'] in ('new-account', 'mixed', 'unknown')}
    blocked_groups = set()
    for entry in entries:
        try:
            if entry['scope'] == 'old-only' and protect_changed_identity(entry):
                protected_groups.add(entry['shared_scope'])
        except (g.GuardError, OSError, ValueError, KeyError, TypeError):
            uncertain_groups.add(entry['shared_scope'])
    results = []
    adapter_map = ADAPTERS if adapters is None else {**ADAPTERS, **adapters}
    for number, item in enumerate(entries):
        work = output / ('entry-' + str(number))
        work.mkdir()
        group = item['shared_scope']
        if item['scope'] == 'not-installed':
            result = {'status': 'not-applicable'}
        elif group in blocked_groups:
            result = {'status': 'needs-reinspection', 'reason': 'A shared entrypoint was partially modified; recheck before continuing'}
        elif group in uncertain_groups:
            result = {'status': 'needs-scope-evidence', 'reason': 'Selected scope metadata is unavailable; Agent reinspects this scope'}
        elif group in protected_groups:
            result = {'status': 'protected-current-account' if item['scope'] != 'unknown' else 'needs-scope-evidence'}
        else:
            try:
                if protect_changed_identity(item):
                    protected_groups.add(group)
                    result = {'status': 'protected-current-account'}
                else:
                    result = adapter_map[item['kind']](item, work, execute)
            except (g.GuardError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
                # No raw error/credential output. Continue independent entries.
                result = {'status': 'blocked', 'reason': 'Entrypoint changed, is inaccessible, or lacks verified scope; reinspect this entrypoint'}
            try:
                if protect_changed_identity(item):
                    protected_groups.add(group)
            except (g.GuardError, OSError, ValueError, KeyError, TypeError):
                uncertain_groups.add(group)
            if result.get('status') == 'partial':
                blocked_groups.add(group)
        result = {'id': item['id'], 'kind': item['kind'], **result}
        g.private_write(work / 'result.json', g.encode(result))
        results.append(result)
    agent_tasks = [r for r in results if r['status'] in ('needs-agent-ui', 'needs-build-evidence', 'needs-scope-evidence', 'needs-reinspection', 'blocked', 'partial', 'incomplete')]
    user_actions = [r for r in results if r['status'] in ('needs-system-authorization', 'needs-system-unlock')]
    pending = [r for r in results if r['status'] not in FINISHED]
    receipt = {'schema': 1, 'version': VERSION, 'executed': execute,
               'overall_status': 'complete-selected-old-scope' if execute and not pending else 'dry-run' if not execute else 'partial',
               'entries': results, 'agent_tasks': agent_tasks, 'user_actions': user_actions,
               'next_action': 'Agent continues remaining tasks; consolidate system actions once' if pending else 'No remaining applicable old-scope tasks',
               'does_not_mean_all_accounts_logged_out': True}
    g.private_write(output / 'completion.json', g.encode(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    try:
        result = run(load(args.inventory), args.out, args.execute)
        print(json.dumps(result))
        return 0 if result['overall_status'] in ('complete-selected-old-scope', 'dry-run') else 2
    except (g.GuardError, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'overall_status': 'blocked', 'reason': 'Inventory or private receipt directory is invalid'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
