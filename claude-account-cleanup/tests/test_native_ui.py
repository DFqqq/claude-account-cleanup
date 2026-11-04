"""Validation and error mapping only; never open host UI or native Keychain."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cleanup_guard as g
import native_ui as ui
import keychain_guard as k


class NativeTests(unittest.TestCase):
    def spec(self):
        return {'schema':1,'scope':'old-only','profile_confirmed':True,'drafts_saved':True,
                'entry_kind':'desktop','pid':123,'window_handle':456,'action':'inspect'}

    def test_new_mixed_unknown_and_unsaved_scopes_never_call_os(self):
        for delta in ({'scope':'new-account'},{'scope':'mixed'},{'scope':'unknown'},{'drafts_saved':False}):
            with self.assertRaises(g.GuardError):
                ui.operate({**self.spec(),**delta},runner=lambda *a,**kw:self.fail('OS must not be called'))

    def test_wholesale_delete_and_unknown_action_refused(self):
        for name in ['Delete account','Clear all browsing data','Reset app','Uninstall']:
            with self.assertRaises(g.GuardError):
                ui.validate_spec({**self.spec(),'action':'logout','control_name':name,'runtime_id':[1]})

    def test_browser_sso_and_profile_logout_not_mistaken_for_site_cleanup(self):
        with self.assertRaises(g.GuardError):
            ui.validate_spec({**self.spec(),'entry_kind':'browser','action':'logout','control_name':'Sign out','runtime_id':[1]})

    def test_action_timeout_requires_reinspection_without_replay(self):
        import subprocess
        def timed_out(*a,**kw):raise subprocess.TimeoutExpired('fixture',25)
        with patch.object(ui.sys,'platform','win32'):
            r=ui.operate({**self.spec(),'action':'logout','control_name':'Log out','runtime_id':[1]},timed_out)
        self.assertEqual(r['status'],'incomplete')

    def test_site_deletion_requires_exact_web_origin_and_container(self):
        for delta in ({'entry_kind':'desktop','origin':'https://claude.ai','site_container_runtime_id':[1]},
                      {'entry_kind':'browser','origin':'https://accounts.google.com','site_container_runtime_id':[1]},
                      {'entry_kind':'browser','origin':'https://claude.ai'}):
            with self.assertRaises(g.GuardError):
                ui.validate_spec({**self.spec(),'action':'remove-site','control_name':'Delete','runtime_id':[1],**delta})

    def test_windows_backend_uses_input_json_and_never_claims_logout_verified(self):
        def runner(argv,**kw):
            self.assertNotIn('123',argv)
            self.assertEqual(json.loads(kw['input'])['pid'],123)
            return SimpleNamespace(returncode=0,stdout='{"status":"action-invoked","logout_independently_verified":false}')
        with patch.object(ui.sys,'platform','win32'):
            r=ui.operate({**self.spec(),'action':'logout','control_name':'Log out','runtime_id':[1]},runner)
        self.assertFalse(r['logout_independently_verified'])

    def test_macos_missing_accessibility_is_structured_system_action(self):
        with patch.object(ui.sys,'platform','darwin'):
            r=ui.operate(self.spec(),lambda *a,**kw:SimpleNamespace(returncode=1,stdout='',stderr='not authorized to send Apple events (-1743)'))
        self.assertEqual(r['status'],'needs-system-authorization')
        self.assertNotIn('FAKE_ACCESS_DENIED',json.dumps(r))

    def test_mac_missing_control_does_not_ask_for_irrelevant_permission(self):
        with patch.object(ui.sys,'platform','darwin'):
            r=ui.operate(self.spec(),lambda *a,**kw:SimpleNamespace(returncode=1,stdout='',stderr='Exact unique control required (-1728)'))
        self.assertEqual(r['status'],'needs-agent-ui')

    def test_keychain_permission_errors_are_not_absence(self):
        for code in (-128,-25293,-25308):
            with self.assertRaises(k.KeychainError) as raised:k.check_osstatus(code,'read')
            self.assertEqual(raised.exception.status,'needs-system-authorization')
        with self.assertRaises(k.KeychainError) as raised:k.check_osstatus(-25291,'open')
        self.assertEqual(raised.exception.status,'needs-system-unlock')

    def test_keychain_unknown_error_never_becomes_success(self):
        with self.assertRaises(k.KeychainError) as raised:k.check_osstatus(-1,'read')
        self.assertEqual(raised.exception.status,'blocked')
        k.check_osstatus(0,'read')

    def test_selected_metadata_omits_password_flags_and_unrelated_attributes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            path=Path(base)/'fixture.keychain-db';path.write_bytes(b'FAKE')
            def runner(argv,**kw):
                self.assertNotIn('-w',argv);self.assertNotIn('-g',argv)
                return SimpleNamespace(returncode=0,stdout='"acct"<blob>="FAKE_ACCOUNT"\n"svce"<blob>="Claude Code-credentials"\n"other"<blob>="FAKE_NOT_OUTPUT"',stderr='')
            with patch.object(k.sys,'platform','darwin'):
                r=k.resolve_account(path,'Claude Code-credentials',runner)
            self.assertEqual(r,{'status':'candidate-account','account':'FAKE_ACCOUNT'})
            self.assertNotIn('FAKE_NOT_OUTPUT',json.dumps(r))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifact-dir');args,rest=p.parse_known_args()
    unittest.main(argv=[sys.argv[0],*rest],verbosity=2)
