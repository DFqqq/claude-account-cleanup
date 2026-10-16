"""Synthetic fixtures only. No discovery, live user paths, network or native Keychain.
Artifacts are kept; specify --artifact-dir to choose the parent directory.
"""
import argparse
import copy
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
import cleanup_guard as guard
import keychain_guard as keychain

ARTIFACTS = None


class FakeKeychain:
    def __init__(self, data):
        self.raw = data
        self.writes = 0
    def read(self):
        return self.raw
    def compare_replace(self, expected, output):
        guard.require(guard.digest(self.raw) == expected, 'Changed')
        self.raw = output
        self.writes += 1


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='case-', dir=ARTIFACTS))
        self.config = self.base / '.claude-test'
        self.config.mkdir()
        self.state = self.base / '.claude.json'
        self.credentials = self.config / '.credentials.json'
        self.network = self.base / 'company-network.json'
        self.original = {'oauthAccount': {'accountUuid': 'FAKE_OLD_ACCOUNT'}, 'userID': 'FAKE_OLD_ID',
                         'projects': {'synthetic': {'allowedTools': ['Read']}},
                         'mcpServers': {'local': {'env': {'API_KEY': 'FAKE_PRESERVE_KEY'}}}}
        self.credential_value = {'claudeAiOauth': {'accessToken': 'FAKE_SECRET_DO_NOT_PRINT'},
                                 'mcpOAuth': {'fake': {'accessToken': 'FAKE_MCP_PRESERVE'}},
                                 'apiKey': 'FAKE_UPSTREAM_PRESERVE'}
        self.state.write_bytes(guard.encode(self.original))
        self.credentials.write_bytes(guard.encode(self.credential_value))
        self.network.write_text('{"proxy":"127.0.0.1:7897","vpn":"keep","dns":"keep"}', encoding='utf-8')
        for name in ['projects/demo/session.jsonl', 'projects/demo/memory/MEMORY.md',
                     'skills/local/SKILL.md', 'plugins/installed.json', 'file-history/demo/snapshot',
                     'history.jsonl', 'settings.json', 'CLAUDE.md']:
            p = self.config / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('synthetic work content; must remain identical\n', encoding='utf-8')
        self.spec = {'schema': 1, 'account_state': 'old-only', 'config_dirs': [str(self.config)],
                     'state_files': [str(self.state)], 'preserve_paths': [str(self.network)],
                     'operations': [
                         {'kind': 'state-json', 'path': str(self.state), 'remove_fields': ['oauthAccount', 'userID'], 'old_account_evidence': 'confirmed'},
                         {'kind': 'credential-json', 'path': str(self.credentials), 'remove_fields': ['claudeAiOauth'], 'old_account_evidence': 'confirmed'}]}

    def test_cleanup_preserves_projects_sessions_mcp_network_and_permissions(self):
        original_meta = guard.metadata(self.state)
        plan = guard.prepare(self.spec)
        result = guard.apply(plan, execute=True)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(guard.metadata(self.state), original_meta)
        self.assertEqual(guard.parse_json(self.state.read_bytes()), {k: v for k, v in self.original.items() if k not in ('oauthAccount', 'userID')})
        self.assertEqual(guard.parse_json(self.credentials.read_bytes()), {k: v for k, v in self.credential_value.items() if k != 'claudeAiOauth'})
        self.assertEqual(guard.verify(plan)['status'], 'verified')

    def test_dry_run_does_not_change_sources(self):
        plan = guard.prepare(self.spec)
        self.assertEqual(guard.apply(plan)['status'], 'dry-run')
        roots, _ = guard.validated_spec(self.spec)
        self.assertEqual(guard.snapshot(roots), plan['before'])

    def test_macos_acl_absence_requires_enoent_on_valid_descriptor(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        def no_acl(fd, kind):
            os.fstat(fd)
            ctypes_error = errno.ENOENT
            guard.ctypes.set_errno(ctypes_error)
            return None
        libc = SimpleNamespace(acl_get_fd_np=Mock(side_effect=no_acl),
                               acl_to_text=Mock(), acl_free=Mock())
        before = self.state.read_bytes()
        with patch.object(guard.ctypes, 'CDLL', return_value=libc):
            self.assertIsNone(guard.mac_acl_fingerprint(self.state, self.state.lstat()))
        self.assertEqual(self.state.read_bytes(), before)
        libc.acl_free.assert_not_called()

    def test_macos_acl_permission_or_unknown_failure_is_not_absence(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        for error in [errno.EACCES, errno.EPERM, errno.ENOTSUP, 0]:
            def failed(fd, kind):
                guard.ctypes.set_errno(error)
                return None
            libc = SimpleNamespace(acl_get_fd_np=Mock(side_effect=failed),
                                   acl_to_text=Mock(), acl_free=Mock())
            before = self.state.read_bytes()
            with patch.object(guard.ctypes, 'CDLL', return_value=libc):
                with self.assertRaises(guard.GuardError):
                    guard.mac_acl_fingerprint(self.state, self.state.lstat())
            self.assertEqual(self.state.read_bytes(), before)

    @unittest.skipUnless(sys.platform == 'darwin', 'Native macOS empty ACL')
    def test_macos_absent_extended_acl_is_not_a_read_failure(self):
        subprocess.run(['chmod', '-N', str(self.state)], check=True)
        self.assertIsNone(guard.metadata(self.state)['acl'])
        plan = guard.prepare(self.spec)
        self.assertEqual(guard.apply(plan, True)['status'], 'verified')
        self.assertIsNone(guard.metadata(self.state)['acl'])

    @unittest.skipUnless(sys.platform == 'darwin', 'Native macOS ACL and xattr')
    def test_macos_nonempty_acl_and_xattr_are_preserved(self):
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
        acl = 'user:' + user + ' allow read,write,readattr,writeattr,readextattr,writeextattr,readsecurity'
        subprocess.run(['chmod', '+a', acl, str(self.state)], check=True)
        subprocess.run(['xattr', '-w', 'com.example.claude-cleanup-fixture', 'FAKE_KEEP', str(self.state)], check=True)
        before = guard.metadata(self.state)
        self.assertIsNotNone(before['acl'])
        self.assertEqual(before['xattrs']['com.example.claude-cleanup-fixture'], guard.digest(b'FAKE_KEEP'))
        plan = guard.prepare(self.spec)
        self.assertEqual(guard.apply(plan, True)['status'], 'verified')
        self.assertEqual(guard.metadata(self.state), before)

    @unittest.skipUnless(os.name == 'nt', 'Windows timestamp compatibility')
    def test_windows_existing_file_metadata_uses_consistent_handle_timestamps(self):
        import time
        time.sleep(0.02)
        raw = self.state.read_bytes()
        self.state.write_bytes(raw)
        self.assertEqual(guard.read_bytes(self.state), raw)
        self.assertEqual(guard.file_hash(self.state), guard.digest(raw))
        before = guard.checked_file_stat(self.state)
        time.sleep(0.02)
        # Even restoring mtime and keeping size/content unchanged must retain
        # change-time detection through native handle metadata.
        self.state.write_bytes(raw)
        os.utime(self.state, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = guard.checked_file_stat(self.state)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertNotEqual(guard.stable(before), guard.stable(after))

    def test_new_login_after_plan_rejects_all_changes(self):
        plan = guard.prepare(self.spec)
        self.state.write_text('{"oauthAccount":{"accountUuid":"FAKE_NEW"}}')
        unchanged = self.credentials.read_bytes()
        with self.assertRaises(guard.GuardError):
            guard.apply(plan, True)
        self.assertEqual(self.credentials.read_bytes(), unchanged)

    def test_new_account_scope_is_rejected(self):
        self.spec['account_state'] = 'new-account'
        with self.assertRaises(guard.GuardError):
            guard.prepare(self.spec)

    def test_unknown_ownership_is_rejected(self):
        self.spec['operations'][0]['old_account_evidence'] = 'unknown'
        with self.assertRaises(guard.GuardError):
            guard.prepare(self.spec)

    def test_active_session_append_refuses_stale_plan(self):
        plan = guard.prepare(self.spec)
        with (self.config / 'projects/demo/session.jsonl').open('a') as stream:
            stream.write('new session line\n')
        with self.assertRaises(guard.GuardError):
            guard.apply(plan, True)
        self.assertIn('oauthAccount', guard.parse_json(self.state.read_bytes()))

    def test_duplicate_json_keys_rejected(self):
        self.state.write_text('{"oauthAccount":{},"oauthAccount":{},"userID":"fake"}')
        with self.assertRaises(guard.GuardError):
            guard.prepare(self.spec)

    def test_invalid_and_nonfinite_json_rejected(self):
        for raw in [b'{broken', b'{"x":NaN}', b'{"nested":{"a":1,"a":2}}']:
            with self.assertRaises(guard.GuardError):
                guard.parse_json(raw)

    def test_absent_device_field_is_not_created(self):
        self.spec['operations'][0]['remove_fields'] = ['deviceId']
        original = self.state.read_bytes()
        with self.assertRaises(guard.GuardError):
            guard.prepare(self.spec)
        self.assertEqual(self.state.read_bytes(), original)

    def test_mcp_field_cannot_be_selected(self):
        self.spec['operations'][1]['remove_fields'] = ['mcpOAuth']
        with self.assertRaises(guard.GuardError):
            guard.prepare(self.spec)

    def test_network_file_cannot_be_selected(self):
        self.spec['operations'][0]['path'] = str(self.network)
        with self.assertRaises(guard.GuardError):
            guard.prepare(self.spec)

    def test_directory_deletion_not_supported(self):
        self.spec['operations'][0] = {'kind': 'delete-tree', 'path': str(self.config), 'old_account_evidence': 'confirmed'}
        with self.assertRaises(guard.GuardError):
            guard.prepare(self.spec)

    def test_preserve_overlap_refused(self):
        self.spec['preserve_paths'].append(str(self.state))
        with self.assertRaises(guard.GuardError):
            guard.prepare(self.spec)

    def test_hardlinked_target_refused(self):
        os.link(self.state, self.base / 'state-other-link')
        with self.assertRaises(guard.GuardError):
            guard.prepare(self.spec)

    def test_verify_rejects_plan_missing_operations(self):
        plan = guard.prepare(self.spec)
        plan['operations'] = []
        with self.assertRaises(guard.GuardError):
            guard.verify(plan)

    def test_verify_rejects_forged_unchanged_fingerprints(self):
        plan = guard.prepare(self.spec)
        for op in plan['operations']:
            op['after_sha256'] = op['before_sha256']
        with self.assertRaises(guard.GuardError):
            guard.verify(plan)

    def test_verify_rejects_other_platform(self):
        plan = guard.prepare(self.spec)
        guard.apply(plan, True)
        plan['platform'] = 'fixture-other-platform'
        with self.assertRaises(guard.GuardError):
            guard.verify(plan)

    @unittest.skipUnless(os.name == 'nt', 'Windows extended paths')
    def test_long_source_paths_are_handled_without_user_reformatting(self):
        plain = self.base / ('long-' + 'x' * 100) / ('y' * 100)
        native = guard.absolute(plain)
        native.mkdir(parents=True)
        (native / '.claude.json').write_bytes(guard.encode(self.original))
        self.spec['state_files'] = [str(plain / '.claude.json')]
        self.spec['operations'] = [dict(self.spec['operations'][0], path=str(plain / '.claude.json'))]
        plan = guard.prepare(self.spec)
        self.assertEqual(guard.apply(plan, True)['status'], 'verified')

    def test_output_overlap_and_existing_file_refused(self):
        with self.assertRaises(guard.GuardError):
            guard.output_outside(self.config / 'report.json', self.spec)
        with self.assertRaises(guard.GuardError):
            guard.private_write(self.network, b'overwrite')

    def test_plan_and_receipt_do_not_contain_secrets(self):
        plan = guard.prepare(self.spec)
        rendered = guard.encode(plan)
        self.assertNotIn(b'FAKE_SECRET_DO_NOT_PRINT', rendered)
        self.assertNotIn(b'FAKE_MCP_PRESERVE', rendered)
        self.assertNotIn(b'FAKE_OLD_ACCOUNT', rendered)
        self.assertNotIn(b'FAKE_UPSTREAM_PRESERVE', rendered)
        self.assertEqual(guard.apply(plan, True)['status'], 'verified')
        self.assertEqual(list(self.base.rglob('.claude-cleanup-*.tmp')), [])

    def test_plan_tampering_refused(self):
        plan = guard.prepare(self.spec)
        plan['operations'][0]['remove_fields'] = ['projects']
        with self.assertRaises(guard.GuardError):
            guard.apply(plan, True)

    def test_mid_execution_failure_reports_partial(self):
        plan = guard.prepare(self.spec)
        original = guard.atomic_replace
        calls = []
        def fail_second(*args):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('synthetic failure')
            return original(*args)
        with patch.object(guard, 'atomic_replace', side_effect=fail_second):
            result = guard.apply(plan, True)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['verified_completed_files'], 1)
        self.assertEqual(guard.parse_json(self.credentials.read_bytes()), self.credential_value)

    def test_stale_plan_cli_writes_blocked_receipt(self):
        plan_path, receipt = self.base / 'plan.json', self.base / 'receipt.json'
        guard.private_write(plan_path, guard.encode(guard.prepare(self.spec)))
        self.state.write_text('{"newAccount":true}')
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(SKILL / 'scripts/cleanup_guard.py'),
            'apply', '--plan', str(plan_path), '--out', str(receipt), '--execute'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(receipt.read_text())['status'], 'blocked')

    def test_keychain_mixed_bundle_keeps_mcp_and_api_key(self):
        backend = FakeKeychain(guard.encode(self.credential_value))
        fingerprint = keychain.inspect(backend)['fingerprint']
        self.assertEqual(keychain.clean(backend, fingerprint)['status'], 'dry-run')
        self.assertEqual(backend.writes, 0)
        self.assertEqual(keychain.clean(backend, fingerprint, True)['status'], 'verified')
        self.assertEqual(guard.parse_json(backend.raw), {k: v for k, v in self.credential_value.items() if k != 'claudeAiOauth'})

    def test_keychain_new_login_rejected(self):
        backend = FakeKeychain(guard.encode(self.credential_value))
        fingerprint = keychain.inspect(backend)['fingerprint']
        backend.raw = b'{"claudeAiOauth":{"accessToken":"FAKE_NEW_ACCOUNT"}}'
        with self.assertRaises(guard.GuardError):
            keychain.clean(backend, fingerprint, True)
        self.assertEqual(backend.writes, 0)

    def test_keychain_absent_not_confused_with_permission_failure(self):
        self.assertEqual(keychain.inspect(FakeKeychain(None))['status'], 'absent')
        backend = FakeKeychain(b'{}')
        with patch.object(backend, 'read', side_effect=guard.GuardError('denied')):
            with self.assertRaises(guard.GuardError):
                keychain.inspect(backend)

    def test_keychain_mcp_only_is_not_modified(self):
        backend = FakeKeychain(b'{"mcpOAuth":{"fake":"keep"}}')
        fingerprint = keychain.inspect(backend)['fingerprint']
        with self.assertRaises(guard.GuardError):
            keychain.clean(backend, fingerprint, True)
        self.assertEqual(backend.writes, 0)

    def test_streaming_hash_matches_bytes(self):
        p = self.base / 'large-synthetic.bin'
        p.write_bytes(b'synthetic' * 300000)
        self.assertEqual(guard.file_hash(p), guard.digest(p.read_bytes()))

    @unittest.skipUnless(os.name == 'nt', 'Windows-specific ACL test')
    def test_windows_private_report_acl_is_protected(self):
        from ctypes import wintypes as w
        import ctypes
        report = self.base / 'private-report.json'
        guard.private_write(report, b'{"test":"synthetic"}')
        adv, kernel, _ = guard.win_libraries()
        adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), ctypes.c_void_p]
        adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = w.BOOL
        buf = ctypes.create_string_buffer(guard.windows_sd(report))
        value = w.LPWSTR()
        self.assertTrue(adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(buf, 1, 4, ctypes.byref(value), None))
        try:
            self.assertTrue(value.value.startswith('D:P'))
            self.assertNotIn(';;;WD)', value.value)
            self.assertNotIn(';;;BU)', value.value)
        finally:
            kernel.LocalFree(ctypes.cast(value, ctypes.c_void_p))

    @unittest.skipUnless(os.name == 'nt', 'Windows native DACL preservation')
    def test_existing_protected_dacl_is_preserved_during_cleanup(self):
        guard.private_permissions(self.state)
        guard.private_permissions(self.credentials)
        before = {p: guard.windows_sd(p) for p in (self.state, self.credentials)}
        self.assertEqual(guard.apply(guard.prepare(self.spec), True)['status'], 'verified')
        for p, expected in before.items():
            self.assertEqual(guard.windows_sd(p), expected)


    @unittest.skipUnless(os.name == 'nt', 'Windows replacement retry')
    def test_transient_replacement_lock_is_retried_without_forcing(self):
        real = guard.windows_replace_once
        calls = []
        def transient(path, candidate):
            calls.append(1)
            return (False, 1175) if len(calls) == 1 else real(path, candidate)
        with patch.object(guard, 'windows_replace_once', side_effect=transient):
            self.assertEqual(guard.apply(guard.prepare(self.spec), True)['status'], 'verified')
        self.assertEqual(len(calls), 3)

    @unittest.skipUnless(os.name == 'nt', 'Windows replacement retry')
    def test_new_login_during_replacement_wait_is_not_overwritten(self):
        plan = guard.prepare(self.spec)
        untouched = self.credentials.read_bytes()
        new = guard.encode({'oauthAccount': {'id': 'FAKE_NEW'}})
        def changed(path, candidate):
            path.write_bytes(new)
            return False, 1175
        with patch.object(guard, 'windows_replace_once', side_effect=changed):
            result = guard.apply(plan, True)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(self.state.read_bytes(), new)
        self.assertEqual(self.credentials.read_bytes(), untouched)

    @unittest.skipUnless(os.name == 'nt', 'Windows replacement recovery')
    def test_native_failure_after_original_disappears_keeps_work_candidate(self):
        plan = guard.prepare(self.spec)
        def interrupted(path, candidate):
            path.unlink()  # ONLY this test's fresh synthetic source.
            return False, 1176
        with patch.object(guard, 'windows_replace_once', side_effect=interrupted):
            result = guard.apply(plan, True)
        self.assertEqual(result['status'], 'partial')
        recovery = Path(result['recovery_file'])
        self.assertEqual(recovery.parent, guard.guard_path(self.state.parent))
        self.assertEqual(guard.parse_json(recovery.read_bytes()),
                         {k:v for k,v in self.original.items() if k not in ('oauthAccount','userID')})
        self.assertNotIn('FAKE_SECRET_DO_NOT_PRINT', json.dumps(result))


    @unittest.skipUnless(os.name == 'nt', 'Windows candidate link protection')
    def test_recovery_candidate_hardlink_does_not_change_protected_acl(self):
        plan = guard.prepare(self.spec)
        before = self.network.read_bytes(), guard.metadata(self.network)
        def swapped(path, candidate):
            path.unlink()  # ONLY fresh synthetic fixtures.
            candidate.unlink()
            os.link(self.network, candidate)
            return False, 1176
        with patch.object(guard, 'windows_replace_once', side_effect=swapped):
            result = guard.apply(plan, True)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual((self.network.read_bytes(), guard.metadata(self.network)), before)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifact-dir')
    options = parser.parse_args()
    if options.artifact_dir:
        ARTIFACTS = str(Path(options.artifact_dir).absolute())
        Path(ARTIFACTS).mkdir(parents=True, exist_ok=True)
    print('Synthetic fixtures only; native macOS Keychain is never called.')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CleanupTests))
    raise SystemExit(not result.wasSuccessful())
