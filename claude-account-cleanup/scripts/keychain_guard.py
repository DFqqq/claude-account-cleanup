"""macOS exact-item Keychain adapter. Secrets remain in memory, never argv or reports.

Only removes claudeAiOauth from a verified JSON bundle. Other fields and the item
itself remain. No default-keychain search, enumeration, unlock or item deletion.
"""
import argparse
import ctypes
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
from cleanup_guard import GuardError, digest, encode, guard_path, parse_json, require


class KeychainError(GuardError):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


def check_osstatus(code, operation):
    if code == 0:
        return
    if code in (-128, -25293, -25308):
        raise KeychainError('needs-system-authorization', 'Allow access to the selected Claude Keychain item in the macOS system dialog')
    if code == -25291:
        raise KeychainError('needs-system-unlock', 'Selected Keychain is unavailable; unlock it in macOS before continuing')
    raise KeychainError('blocked', 'Selected Keychain operation is unavailable: ' + operation)


def resolve_account(keychain, service, runner=subprocess.run):
    """Selected service metadata only. No -g/-w, no password or full dump."""
    require(sys.platform == 'darwin', 'Selected Keychain metadata requires macOS')
    require(service.startswith('Claude Code-credentials'), 'Verified Claude service required')
    path = guard_path(Path(keychain))
    result = runner(['security', 'find-generic-password', '-s', service, str(path)],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
    if result.returncode:
        return {'status': 'needs-scope-evidence', 'reason': 'Exact service metadata unavailable; inspect the selected namespace with existing tools'}
    text = result.stdout + result.stderr
    account = re.search(r'\"acct\"<blob>=\"([^\r\n\"]{1,200})\"', text)
    found_service = re.search(r'\"svce\"<blob>=\"([^\r\n\"]{1,200})\"', text)
    if account and found_service and found_service.group(1) == service:
        return {'status': 'candidate-account', 'account': account.group(1)}
    return {'status': 'needs-scope-evidence', 'reason': 'No exact account metadata could be verified'}


class MacKeychain:
    def __init__(self, keychain, service, account):
        require(sys.platform == 'darwin', 'Native Keychain adapter requires macOS')
        require(service.startswith('Claude Code-credentials') and service and account, 'Exact Claude namespace and account required')
        self.path = guard_path(Path(keychain))
        self.service, self.account = service.encode('utf-8'), account.encode('utf-8')
        self.security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]
        self.cf.CFRelease.restype = None
        self.security.SecKeychainOpen.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        self.security.SecKeychainOpen.restype = ctypes.c_int32
        self.security.SecKeychainFindGenericPassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
        self.security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self.security.SecKeychainItemModifyAttributesAndData.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        self.security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self.handle = ctypes.c_void_p()
        check_osstatus(self.security.SecKeychainOpen(str(self.path).encode('utf-8'), ctypes.byref(self.handle)), 'open')

    def close(self):
        if self.handle:
            self.cf.CFRelease(self.handle)
            self.handle = None

    def fetch(self):
        length, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        code = self.security.SecKeychainFindGenericPassword(self.handle, len(self.service), self.service,
            len(self.account), self.account, ctypes.byref(length), ctypes.byref(data), ctypes.byref(item))
        if code == -25300:
            return None, None  # errSecItemNotFound; other errors are not "absent".
        check_osstatus(code, 'read')
        try:
            require(length.value <= 32 * 1024 * 1024, 'Keychain bundle exceeds supported size')
            raw = ctypes.string_at(data, length.value)
            return raw, item
        except Exception:
            self.release(item)
            raise
        finally:
            if data:
                self.security.SecKeychainItemFreeContent(None, data)

    def release(self, item):
        if item:
            self.cf.CFRelease(item)

    def read(self):
        raw, item = self.fetch()
        self.release(item)
        return raw

    def compare_replace(self, expected, output):
        raw, item = self.fetch()
        try:
            require(raw is not None and digest(raw) == expected, 'Keychain changed; stale plan refused')
            buffer = ctypes.create_string_buffer(output)
            code = self.security.SecKeychainItemModifyAttributesAndData(item, None, len(output), buffer)
            check_osstatus(code, 'update')
        finally:
            self.release(item)


def inspect(backend):
    raw = backend.read()
    if raw is None:
        return {'status': 'absent'}
    value = parse_json(raw)
    return {'status': 'inspected', 'fingerprint': digest(raw),
            'has_claude_oauth': 'claudeAiOauth' in value,
            'has_mcp_oauth': 'mcpOAuth' in value, 'other_field_count': len(value) - int('claudeAiOauth' in value)}


def clean(backend, expected, execute=False):
    raw = backend.read()
    require(raw is not None and digest(raw) == expected, 'Keychain changed; stale plan refused')
    value = parse_json(raw)
    require('claudeAiOauth' in value, 'Claude OAuth is absent; nothing to remove')
    retained = {k: v for k, v in value.items() if k != 'claudeAiOauth'}
    if not execute:
        return {'status': 'dry-run', 'preserved_fields': len(retained)}
    backend.compare_replace(expected, encode(retained))
    observed = backend.read()
    require(observed is not None and parse_json(observed) == retained, 'Keychain verification failed after write; inspect this item')
    return {'status': 'verified', 'removed_field': 'claudeAiOauth', 'preserved_fields': len(retained)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['resolve', 'inspect', 'clean'])
    p.add_argument('--keychain', required=True)
    p.add_argument('--service', required=True)
    p.add_argument('--account')
    p.add_argument('--expected-sha256')
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    backend = None
    try:
        if args.command == 'resolve':
            result = resolve_account(args.keychain, args.service)
            print(json.dumps(result))
            return 0 if result['status'] == 'candidate-account' else 2
        require(args.account, 'Exact selected account required')
        backend = MacKeychain(args.keychain, args.service, args.account)
        result = inspect(backend) if args.command == 'inspect' else clean(backend, args.expected_sha256, args.execute)
        print(json.dumps(result))
        return 0
    except KeychainError as error:
        print(json.dumps({'status': error.status, 'reason': error.reason}))
        return 2
    except (GuardError, OSError, ValueError, TypeError):
        print(json.dumps({'status': 'unverified', 'reason': 'Keychain denied, changed, unsupported, or post-write verification failed; do not retry blindly'}))
        return 2
    finally:
        if backend:
            backend.close()


if __name__ == '__main__':
    raise SystemExit(main())
