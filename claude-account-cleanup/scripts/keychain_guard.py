"""macOS exact-item Keychain adapter. Secrets remain in memory, never argv or reports.

Only removes claudeAiOauth from a verified JSON bundle. Other fields and the item
itself remain. No default-keychain search, enumeration, unlock or item deletion.
"""
import argparse
import ctypes
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from cleanup_guard import GuardError, digest, encode, guard_path, parse_json, require


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
        require(self.security.SecKeychainOpen(str(self.path).encode('utf-8'), ctypes.byref(self.handle)) == 0, 'Cannot open exact Keychain')

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
        require(code == 0, 'Keychain access denied or unavailable')
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
            require(code == 0, 'Keychain update failed')
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
    p.add_argument('command', choices=['inspect', 'clean'])
    p.add_argument('--keychain', required=True)
    p.add_argument('--service', required=True)
    p.add_argument('--account', required=True)
    p.add_argument('--expected-sha256')
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    backend = None
    try:
        backend = MacKeychain(args.keychain, args.service, args.account)
        result = inspect(backend) if args.command == 'inspect' else clean(backend, args.expected_sha256, args.execute)
        print(json.dumps(result))
        return 0
    except (GuardError, OSError, ValueError, TypeError):
        print(json.dumps({'status': 'unverified', 'reason': 'Keychain denied, changed, unsupported, or post-write verification failed; do not retry blindly'}))
        return 2
    finally:
        if backend:
            backend.close()


if __name__ == '__main__':
    raise SystemExit(main())
