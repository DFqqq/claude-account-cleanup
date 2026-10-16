"""Scoped local JSON cleanup. No discovery, network, login, process killing or raw backups.

All paths come from an explicit plan. Plan/account assertions are NOT proof of user
authorization; the invoking agent must establish authorization and old-account scope.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from types import SimpleNamespace
from identifier_policy import reset_fields

sys.dont_write_bytecode = True
VERSION = '1.2.0'
STATE_FIELDS = frozenset({'oauthAccount', 'userID', 'machineID', 'deviceId'})
CREDENTIAL_FIELDS = frozenset({'claudeAiOauth'})
REPARSE = 0x400


class GuardError(Exception):
    """Messages must be constants, never secret-bearing external exceptions."""


def require(condition, message):
    if not condition:
        raise GuardError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def parse_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON keys; source not changed')
            result[key] = value
        return result
    def invalid(_):
        raise GuardError('Non-finite JSON number; source not changed')
    try:
        value = json.loads(data.decode('utf-8-sig'), object_pairs_hook=pairs, parse_constant=invalid)
        require(isinstance(value, dict), 'Expected a JSON object')
        return value
    except (UnicodeError, ValueError):
        raise GuardError('Invalid JSON; source not changed') from None


def absolute(value):
    path = Path(value)
    require(path.is_absolute() and '..' not in path.parts, 'Explicit absolute path required')
    if os.name == 'nt':
        raw = str(path)
        require(not raw.startswith('\\\\.\\'), 'Windows device path refused')
        if raw.startswith('\\\\?\\'):
            require(bool(re.match(r'^\\\\\?\\(?:[A-Za-z]:\\|UNC\\[^\\]+\\[^\\]+(?:\\|$))', raw)),
                    'Unsupported Windows extended path')
        else:
            path = Path('\\\\?\\UNC\\' + raw[2:] if raw.startswith('\\\\') else '\\\\?\\' + raw)
    return path


def linked(meta):
    return stat.S_ISLNK(meta.st_mode) or bool(getattr(meta, 'st_file_attributes', 0) & REPARSE)


def guard_path(path, missing_leaf=False):
    path = absolute(path)
    for part in reversed([path, *path.parents]):
        try:
            meta = part.lstat()
        except FileNotFoundError:
            require(missing_leaf and part == path, 'Path or parent is missing')
            continue
        require(not linked(meta), 'Linked or reparse path requires a separately verified real root')
    return path


def stable(meta):
    return (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)


def handle_stat(fd):
    """Use native Windows ChangeTime, independent of Python's ctime semantics."""
    meta = os.fstat(fd)
    if os.name != 'nt':
        return meta
    import msvcrt
    from ctypes import wintypes as w
    class BasicInfo(ctypes.Structure):
        _fields_ = [('creation', ctypes.c_longlong), ('access', ctypes.c_longlong),
                    ('write', ctypes.c_longlong), ('change', ctypes.c_longlong),
                    ('attributes', w.DWORD)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.GetFileInformationByHandleEx.restype = w.BOOL
    info = BasicInfo()
    require(kernel.GetFileInformationByHandleEx(msvcrt.get_osfhandle(fd), 0,
            ctypes.byref(info), ctypes.sizeof(info)), 'Cannot read native Windows change time')
    require(not info.attributes & REPARSE, 'Reparse handle refused')
    # Windows time is in 100ns ticks from 1601; comparisons need a consistent epoch only.
    return SimpleNamespace(st_dev=meta.st_dev, st_ino=meta.st_ino, st_size=meta.st_size,
        st_mtime_ns=meta.st_mtime_ns, st_atime_ns=meta.st_atime_ns,
        st_ctime_ns=info.change * 100, st_mode=meta.st_mode)


def checked_file_stat(path):
    """Check pathname identity separately from native handle change time."""
    initial = path.lstat()
    if os.name != 'nt':
        return initial
    require(stat.S_ISREG(initial.st_mode) and not linked(initial), 'Expected a regular file')
    fd = os.open(path, os.O_RDONLY | os.O_BINARY)
    try:
        opened = handle_stat(fd)
        final = path.lstat()
        require(stable(initial) == stable(final), 'Source changed during metadata read')
        require(stable(initial)[:4] == stable(opened)[:4], 'Source identity changed during metadata read')
        return opened
    finally:
        os.close(fd)


def read_bytes(path, maximum=None):
    path = guard_path(path)
    initial = checked_file_stat(path)
    require(stat.S_ISREG(initial.st_mode), 'Expected a regular file')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        before = handle_stat(stream.fileno())
        require(stable(before) == stable(initial), 'Source changed before read')
        if maximum is not None:
            require(before.st_size <= maximum, 'JSON file exceeds supported size')
        data = stream.read()
        after = handle_stat(stream.fileno())
    require(stable(before) == stable(after) == stable(checked_file_stat(path)), 'Source changed during read')
    return data


def file_hash(path):
    path = guard_path(path)
    initial = checked_file_stat(path)
    require(stat.S_ISREG(initial.st_mode), 'Expected a regular file')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    value = hashlib.sha256()
    with os.fdopen(fd, 'rb') as stream:
        before = handle_stat(stream.fileno())
        require(stable(before) == stable(initial), 'Source changed before hashing')
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
        after = handle_stat(stream.fileno())
    require(stable(before) == stable(after) == stable(checked_file_stat(path)), 'Source changed during hashing')
    return value.hexdigest()


def win_libraries():
    from ctypes import wintypes as w
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    adv.GetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    adv.GetFileSecurityW.restype = w.BOOL
    adv.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p]
    adv.SetFileSecurityW.restype = w.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return adv, kernel, w


def windows_sd(path):
    adv, _, w = win_libraries()
    size = w.DWORD()
    adv.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(size))
    require(size.value > 0, 'Cannot read Windows ACL')
    buf = ctypes.create_string_buffer(size.value)
    require(adv.GetFileSecurityW(str(path), 4, buf, size.value, ctypes.byref(size)), 'Cannot read Windows ACL')
    return bytes(buf.raw[:size.value])


def private_permissions(path):
    if os.name != 'nt':
        os.chmod(path, 0o600)
        return
    adv, kernel, w = win_libraries()
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.OpenProcessToken.restype = w.BOOL
    adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    adv.GetTokenInformation.restype = w.BOOL
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = w.BOOL
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    token, text, sd = w.HANDLE(), w.LPWSTR(), ctypes.c_void_p()
    try:
        require(adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)), 'Cannot obtain current Windows identity')
        size = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        require(size.value > 0, 'Cannot obtain current Windows identity')
        buf = ctypes.create_string_buffer(size.value)
        require(adv.GetTokenInformation(token, 1, buf, size.value, ctypes.byref(size)), 'Cannot obtain current Windows identity')
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        require(adv.ConvertSidToStringSidW(sid, ctypes.byref(text)), 'Cannot obtain current Windows identity')
        sddl = 'D:P(A;;FA;;;SY)(A;;FA;;;' + text.value + ')'
        require(adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(sd), None), 'Cannot build private ACL')
        require(adv.SetFileSecurityW(str(path), 4 | 0x80000000, sd), 'Cannot set private ACL')
    finally:
        if token:
            kernel.CloseHandle(token)
        if text:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        if sd:
            kernel.LocalFree(sd)


def metadata(path):
    meta = path.lstat()
    if os.name == 'nt':
        return {'acl': digest(windows_sd(path)), 'readonly': bool(meta.st_file_attributes & 1)}
    attrs = {}
    if hasattr(os, 'listxattr'):
        attrs = {name: digest(os.getxattr(path, name, follow_symlinks=False))
                 for name in os.listxattr(path, follow_symlinks=False)}
    result = {'mode': stat.S_IMODE(meta.st_mode), 'uid': meta.st_uid, 'gid': meta.st_gid, 'xattrs': attrs}
    if sys.platform == 'darwin':
        libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        libc.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
        libc.acl_get_file.restype = ctypes.c_void_p
        libc.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
        libc.acl_to_text.restype = ctypes.c_void_p
        libc.acl_free.argtypes = [ctypes.c_void_p]
        libc.acl_free.restype = ctypes.c_int
        acl = libc.acl_get_file(str(path).encode('utf-8'), 0x100)
        require(acl, 'Cannot inspect macOS ACL')
        rendered = None
        try:
            size = ctypes.c_ssize_t()
            rendered = libc.acl_to_text(acl, ctypes.byref(size))
            require(rendered, 'Cannot inspect macOS ACL')
            result['acl'] = digest(ctypes.string_at(rendered, size.value))
        finally:
            if rendered:
                libc.acl_free(rendered)
            libc.acl_free(acl)
    return result


def private_open(path):
    path = guard_path(path, missing_leaf=True)
    require(not path.exists(), 'Output already exists')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        private_permissions(path)  # File is still empty until protection succeeds.
        return fd
    except BaseException:
        os.close(fd)
        raise


def private_write(path, data):
    fd = private_open(path)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(roots):
    """Hash regular files; record links without following them or reading their targets."""
    result = {}
    def visit(path):
        info = path.lstat()
        if linked(info):
            result[str(path)] = {'kind': 'link', 'target_hash': digest(os.readlink(path).encode('utf-8'))}
        elif stat.S_ISDIR(info.st_mode):
            guard_path(path)
            result[str(path)] = {'kind': 'directory', 'metadata': metadata(path)}
            for child in sorted(path.iterdir()):
                visit(child)
        elif stat.S_ISREG(info.st_mode):
            result[str(path)] = {'kind': 'file', 'sha256': file_hash(path), 'metadata': metadata(path), 'links': info.st_nlink}
        else:
            raise GuardError('Special file in selected scope')
    for root in roots:
        visit(guard_path(root))
    return result


def validated_spec(spec):
    require(spec.get('schema') == 1 and spec.get('account_state') == 'old-only', 'Old-account-only scope must be established before planning')
    configs = [guard_path(absolute(p)) for p in spec.get('config_dirs', [])]
    states = [guard_path(absolute(p)) for p in spec.get('state_files', [])]
    preserves = [guard_path(absolute(p)) for p in spec.get('preserve_paths', [])]
    require(all(p.is_dir() for p in configs), 'Config root must be a directory')
    require(all(p.is_file() and (p.name == '.claude.json' or re.fullmatch(r'\.claude\.json\.backup(?:[.-][\w-]+)?', p.name)) for p in states), 'Unsupported state-file name')
    candidates = configs + states + preserves
    require(candidates, 'No explicit scope provided')
    roots = []
    for p in sorted(set(candidates), key=lambda p: len(p.parts)):
        if not any(p == root or root in p.parents for root in roots):
            roots.append(p)
    ops = spec.get('operations', [])
    require(isinstance(ops, list), 'Invalid operations')
    seen = set()
    for op in ops:
        p = guard_path(absolute(op['path']))
        require(p not in seen and p.is_file(), 'Duplicate target or non-file target')
        seen.add(p)
        require(p.lstat().st_nlink == 1, 'Hard-linked target cannot be changed')
        require(op.get('old_account_evidence') == 'confirmed', 'Target ownership must be established')
        require(not any(p == x or x in p.parents for x in preserves), 'Operation overlaps explicit preserve scope')
        if 'identifier_reset' in op:
            require(op.get('kind') == 'state-json' and p in states, 'Identifier reset requires a state file')
            try:
                selected = reset_fields(parse_json(read_bytes(p, 32 * 1024 * 1024)), op['identifier_reset'])
            except (ValueError, TypeError, AttributeError):
                raise GuardError('Unverified identifier reset scope or implementation') from None
            # Resolution happens only at plan construction. Validation of an applied
            # plan uses the exact resolved fields even after those fields are absent.
            if '_resolved_identifier_fields' not in op:
                op['_resolved_identifier_fields'] = selected
                op['remove_fields'] = list(dict.fromkeys(op.get('remove_fields', []) + selected))
            else:
                require(set(op['_resolved_identifier_fields']) <= STATE_FIELDS - {'oauthAccount'}, 'Invalid resolved identifiers')
        fields = op.get('remove_fields')
        require(isinstance(fields, list) and fields and len(fields) == len(set(fields)), 'Specify unique exact fields')
        if op.get('kind') == 'state-json':
            require(p in states and set(fields) <= STATE_FIELDS, 'Unsupported state field or target')
        elif op.get('kind') == 'credential-json':
            require(p.name == '.credentials.json' and p.parent in configs and set(fields) <= CREDENTIAL_FIELDS, 'Unsupported credential operation')
        else:
            raise GuardError('Unsupported operation; no directory or file deletion is provided')
        op['path'] = str(p)
    return roots, ops


def transformation(raw, fields):
    original = parse_json(raw)
    require(all(field in original for field in fields), 'Selected field is absent; no invented identifier or no-op cleanup')
    preserved = {k: v for k, v in original.items() if k not in fields}
    output = encode(preserved)
    require(parse_json(output) == preserved, 'JSON preservation check failed')
    return output


def prepare(spec):
    roots, ops = validated_spec(spec)
    before = snapshot(roots)
    prepared = []
    for op in ops:
        raw = read_bytes(op['path'], 32 * 1024 * 1024)
        after = transformation(raw, op['remove_fields'])
        require(digest(raw) == before[op['path']]['sha256'], 'Source changed during planning')
        prepared.append({**op, 'before_sha256': digest(raw), 'after_sha256': digest(after)})
    require(snapshot(roots) == before, 'Source changed during planning')
    return {'schema': 1, 'tool_version': VERSION, 'platform': sys.platform,
            'spec': spec, 'before': before, 'operations': prepared}


def restore_metadata(source, target):
    if sys.platform == 'darwin':
        libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        libc.fcopyfile.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        libc.fcopyfile.restype = ctypes.c_int
        src = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        dst = os.open(target, os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            require(libc.fcopyfile(src, dst, None, 7) == 0, 'Cannot preserve macOS ACL and extended attributes')
        finally:
            os.close(src)
            os.close(dst)
    elif os.name == 'posix':
        info = source.stat()
        os.chown(target, info.st_uid, info.st_gid)
        os.chmod(target, stat.S_IMODE(info.st_mode))
        for name in os.listxattr(source):
            os.setxattr(target, name, os.getxattr(source, name))


def windows_replace_once(path, temporary):
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.ReplaceFileW.argtypes = [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    kernel.ReplaceFileW.restype = w.BOOL
    ok = kernel.ReplaceFileW(str(path), str(temporary), None, 0, None, None)
    return bool(ok), 0 if ok else ctypes.get_last_error()


def windows_replace_checked(path, temporary, expected, outgoing, previous_meta):
    # Only sharing/lock and unable-to-remove errors keep both original names.
    # Verify content and permissions before a bounded retry, never force handles.
    for attempt in range(4):
        ok, code = windows_replace_once(path, temporary)
        if ok:
            return
        require(code in (32, 33, 1175) and attempt < 3, 'Windows atomic replacement failed')
        time.sleep(0.1)
        guard_path(path)
        guard_path(temporary)
        require(path.lstat().st_nlink == temporary.lstat().st_nlink == 1, 'Replacement path changed')
        require(digest(read_bytes(path)) == expected and metadata(path) == previous_meta,
                'Target changed while waiting for replacement')
        require(digest(read_bytes(temporary)) == outgoing, 'Replacement candidate changed')


def atomic_replace(path, raw, expected):
    path = guard_path(path)
    require(path.lstat().st_nlink == 1, 'Hard-linked target cannot be changed')
    require(digest(read_bytes(path)) == expected, 'Target changed; stale plan refused')
    previous_meta = metadata(path)
    previous_sd = windows_sd(path) if os.name == 'nt' else None
    fd, temporary = tempfile.mkstemp(prefix='.claude-cleanup-', suffix='.tmp', dir=path.parent)
    temporary = Path(temporary)
    retain_temporary = False
    try:
        private_permissions(temporary)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        os.close(fd)
        fd = None
        restore_metadata(path, temporary)
        guard_path(path)
        require(path.lstat().st_nlink == 1 and digest(read_bytes(path)) == expected, 'Target changed before replacement')
        require(metadata(path) == previous_meta, 'Target permissions changed before replacement')
        if os.name == 'nt':
            windows_replace_checked(path, temporary, expected, digest(raw), previous_meta)
            # ReplaceFileW can normalize inheritance flags even while retaining
            # effective access. Restore the exact saved DACL, then verify it.
            if windows_sd(path) != previous_sd:
                require(digest(read_bytes(path)) == digest(raw), 'Replacement changed before ACL restoration')
                adv, _, _ = win_libraries()
                saved_sd = ctypes.create_string_buffer(previous_sd)
                require(adv.SetFileSecurityW(str(path), 4, saved_sd), 'Cannot restore original Windows ACL')
        else:
            os.replace(temporary, path)
        require(metadata(path) == previous_meta, 'Replacement permissions did not match; inspect partial result')
        require(digest(read_bytes(path)) == digest(raw), 'Replacement content did not match; inspect partial result')
    except (GuardError, OSError) as error:
        # Some native replacement failures remove/rename the original before
        # returning. Never discard the only remaining candidate with work data.
        if temporary.exists():
            try:
                safe_to_discard = digest(read_bytes(path)) == expected and metadata(path) == previous_meta
            except (GuardError, OSError):
                safe_to_discard = False
            if not safe_to_discard:
                retain_temporary = True
                error.recovery_file = str(temporary)
                guard_path(temporary)
                require(temporary.lstat().st_nlink == 1, 'Recovery candidate link changed')
                private_permissions(temporary)
        raise
    finally:
        if fd is not None:
            os.close(fd)
        # Only this function's exact temporary file is removed; never a source tree.
        if temporary.exists() and not retain_temporary:
            guard_path(temporary)
            require(temporary.is_file() and temporary.lstat().st_nlink == 1, 'Unexpected temporary file')
            temporary.unlink()


def validated_plan(plan):
    require(plan.get('schema') == 1 and plan.get('tool_version') == VERSION, 'Unsupported plan version')
    require(plan.get('platform') == sys.platform, 'Plan belongs to another platform')
    roots, ops = validated_spec(plan['spec'])
    require(isinstance(plan.get('operations'), list) and len(ops) == len(plan['operations']), 'Plan operation mismatch')
    for selected, op in zip(ops, plan['operations']):
        require(all(op.get(k) == v for k, v in selected.items()), 'Plan operation mismatch')
        require(all(isinstance(op.get(k), str) and re.fullmatch(r'[0-9a-f]{64}', op[k])
                    for k in ('before_sha256', 'after_sha256')), 'Invalid plan fingerprint')
        require(op['before_sha256'] == plan['before'][op['path']]['sha256'], 'Plan source mismatch')
    return roots, ops


def verify(plan):
    roots, _ = validated_plan(plan)
    expected = json.loads(json.dumps(plan['before']))
    for op in plan['operations']:
        expected[op['path']]['sha256'] = op['after_sha256']
    require(snapshot(roots) == expected, 'Preservation or cleanup verification failed')
    for op in plan['operations']:
        current = parse_json(read_bytes(op['path'], 32 * 1024 * 1024))
        require(all(field not in current for field in op['remove_fields']), 'Selected old fields are still present')
    return {'status': 'verified', 'changed_files': len(plan['operations']),
            'preserved_entries': len(expected) - len(plan['operations'])}


def apply(plan, execute=False):
    roots, ops = validated_plan(plan)
    require(snapshot(roots) == plan['before'], 'Environment changed; stale plan refused')
    outputs = []
    for selected, op in zip(ops, plan['operations']):
        require(all(op.get(k) == v for k, v in selected.items()), 'Plan operation mismatch')
        raw = read_bytes(op['path'], 32 * 1024 * 1024)
        transformed = transformation(raw, op['remove_fields'])
        require(digest(raw) == op['before_sha256'] == plan['before'][op['path']]['sha256'], 'Target changed; stale plan refused')
        require(digest(transformed) == op['after_sha256'], 'Plan result mismatch')
        outputs.append(transformed)
    if not execute:
        return {'status': 'dry-run', 'planned_changes': len(ops)}
    completed = 0
    expected = json.loads(json.dumps(plan['before']))
    try:
        for op, content in zip(plan['operations'], outputs):
            require(snapshot(roots) == expected, 'Environment changed during cleanup')
            atomic_replace(Path(op['path']), content, op['before_sha256'])
            completed += 1
            expected[op['path']]['sha256'] = op['after_sha256']
        return verify(plan)
    except (GuardError, OSError) as error:
        # The current operation may have committed before its post-check failed.
        return {'status': 'partial', 'verified_completed_files': completed,
                'attempted_operation': completed + 1, 'reason': 'Execution or verification stopped; inspect exact plan targets before retrying',
                **({'recovery_file': error.recovery_file} if hasattr(error, 'recovery_file') else {})}


def output_outside(path, spec):
    roots, _ = validated_spec(spec)
    path = guard_path(absolute(path), missing_leaf=True)
    require(not path.exists(), 'Output already exists')
    require(all(path != root and root not in path.parents and path not in root.parents for root in roots), 'Output overlaps source scope')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan')
    p.add_argument('--spec', required=True)
    p.add_argument('--out', required=True)
    p = sub.add_parser('apply')
    p.add_argument('--plan', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--execute', action='store_true')
    p = sub.add_parser('verify')
    p.add_argument('--plan', required=True)
    args = parser.parse_args()
    receipt = None
    receipt_fd = None
    result = None

    def write_receipt(value):
        # Write through the reserved descriptor, never reopen a path that another
        # process could have replaced with a link or unrelated file.
        checked = guard_path(receipt)
        opened, current = os.fstat(receipt_fd), checked.lstat()
        require(stat.S_ISREG(opened.st_mode) and opened.st_nlink == current.st_nlink == 1,
                'Unexpected receipt file')
        require((opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino),
                'Receipt was replaced')
        os.lseek(receipt_fd, 0, os.SEEK_SET)
        os.ftruncate(receipt_fd, 0)
        with os.fdopen(receipt_fd, 'wb', closefd=False) as stream:
            stream.write(encode(value))
            stream.flush()
            os.fsync(receipt_fd)
    try:
        if args.command == 'plan':
            spec = parse_json(read_bytes(args.spec, 8 * 1024 * 1024))
            out = output_outside(args.out, spec)
            plan = prepare(spec)
            private_write(out, encode(plan))
            result = {'status': 'plan-ready', 'planned_changes': len(plan['operations'])}
        else:
            plan = parse_json(read_bytes(args.plan, 128 * 1024 * 1024))
            if args.command == 'verify':
                result = verify(plan)
            else:
                out = output_outside(args.out, plan['spec'])
                # Reserve private output before mutation; do not discover an unwritable receipt afterward.
                receipt_fd = private_open(out)
                receipt = out
                result = apply(plan, args.execute)
                write_receipt(result)
        print(json.dumps(result))
        return 2 if result['status'] == 'partial' else 0
    except (GuardError, OSError, ValueError, KeyError, TypeError):
        result = ({'status': 'partial', 'operation_result': result,
                   'reason': 'Receipt could not be saved; inspect current targets before retrying'}
                  if result is not None and getattr(args, 'execute', False) else
                  {'status': 'blocked', 'reason': 'Invalid scope, changed data, unsupported fields, or insufficient filesystem permissions; no clean bill of health'})
        if receipt is not None:
            try:
                write_receipt(result)
            except (GuardError, OSError):
                pass
        print(json.dumps(result))
        return 2
    finally:
        if receipt_fd is not None:
            os.close(receipt_fd)


if __name__ == '__main__':
    raise SystemExit(main())
