"""Actual completion runner against fabricated paths/backends only."""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cleanup_guard as g
import completion as flow
import keychain_guard as k
ARTIFACTS = None


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='completion-', dir=ARTIFACTS))
        self.config = self.base / '.claude'; self.config.mkdir()
        self.state = self.base / '.claude.json'
        self.credentials = self.config / '.credentials.json'
        self.keep = self.base / 'company-network.json'
        self.state.write_bytes(g.encode({'oauthAccount': {'accountUuid': 'FAKE_OLD'},
            'userID': 'a'*64, 'machineID': 'b'*64, 'projects': {'keep': True}, 'remoteControlMachineId': 'KEEP'}))
        self.credentials.write_bytes(g.encode({'claudeAiOauth': {'accessToken': 'FAKE_OLD_SECRET'}, 'mcpOAuth': {'keep': 'FAKE_KEEP'}}))
        self.keep.write_text('FAKE_VPN_DNS_PROXY_KEEP', encoding='utf-8')
        self.spec = {'schema': 1, 'account_state': 'old-only', 'config_dirs': [str(self.config)],
            'state_files': [str(self.state)], 'preserve_paths': [str(self.keep)], 'operations': [
                {'kind': 'state-json', 'path': str(self.state), 'remove_fields': ['oauthAccount'], 'old_account_evidence': 'confirmed'},
                {'kind': 'credential-json', 'path': str(self.credentials), 'remove_fields': ['claudeAiOauth'], 'old_account_evidence': 'confirmed'}]}
        self.specpath = self.base/'spec.json'; self.specpath.write_bytes(g.encode(self.spec))
        self.ids = copy.deepcopy(self.spec)
        self.ids['operations'] = [{'kind': 'state-json', 'path': str(self.state), 'remove_fields': [],
            'old_account_evidence': 'confirmed', 'identifier_reset': {
                'version': '2.1.314', 'binary_sha256': '66e00df32e6981f686a91f67287fcc86e6e1b80ba4973a7b7c4aa43c59986cfa',
                'config_scope': 'old-only', 'reset_requested': True}}]
        self.idpath = self.base/'ids.json'; self.idpath.write_bytes(g.encode(self.ids))
        self.entries = [
            {'id': 'files', 'kind': 'files', 'scope': 'old-only', 'shared_scope': 'default', 'spec': str(self.specpath)},
            {'id': 'ids', 'kind': 'identifiers', 'scope': 'old-only', 'shared_scope': 'default', 'spec': str(self.idpath)},
            {'id': 'browser', 'kind': 'browser', 'scope': 'old-only', 'shared_scope': 'browser'},
            {'id': 'desktop', 'kind': 'desktop', 'scope': 'not-installed', 'shared_scope': 'desktop'}]
        for e in self.entries[:2]:
            e.update(identity_state=str(self.state), old_account_uuid='FAKE_OLD')

    def run_flow(self, adapters=None, **kwargs):
        return flow.run({'schema': 1, 'inventory_complete': True, 'entries': self.entries},
            self.base/('records-'+next(tempfile._get_candidate_names())), True, adapters, **kwargs)

    def test_unknown_ids_leave_auth_cleanup_and_independent_browser_running(self):
        self.ids['operations'][0]['identifier_reset']['binary_sha256']='0'*64
        self.idpath.write_bytes(g.encode(self.ids))
        r=self.run_flow({'browser': lambda *a: {'status': 'completed'}})
        self.assertEqual(r['overall_status'], 'partial')
        self.assertEqual([x['status'] for x in r['entries']], ['completed','needs-build-evidence','completed','not-applicable'])
        self.assertNotIn('oauthAccount', flow.load(self.state))
        self.assertIn('userID', flow.load(self.state))

    def test_new_build_auth_ids_and_preservation_complete(self):
        r=self.run_flow({'browser': lambda *a: {'status': 'completed'}})
        self.assertEqual(r['overall_status'],'complete-selected-old-scope')
        state=flow.load(self.state)
        self.assertFalse({'oauthAccount','userID','machineID'} & set(state))
        self.assertEqual(state['remoteControlMachineId'],'KEEP')
        self.assertEqual(flow.load(self.credentials), {'mcpOAuth':{'keep':'FAKE_KEEP'}})
        self.assertEqual(self.keep.read_text(),'FAKE_VPN_DNS_PROXY_KEEP')

    def test_current_account_protects_every_shared_entry(self):
        state=flow.load(self.state);state['oauthAccount']['accountUuid']='FAKE_NEW';self.state.write_bytes(g.encode(state))
        original=(self.state.read_bytes(),self.credentials.read_bytes())
        r=self.run_flow({'browser':lambda *a:{'status':'completed'}})
        self.assertEqual(original,(self.state.read_bytes(),self.credentials.read_bytes()))
        self.assertTrue(all(x['status']=='protected-current-account' for x in r['entries'][:2]))

    def test_explicit_shared_new_account_blocks_related_old_file_scope(self):
        self.entries.append({'id':'new-vscode','kind':'vscode','scope':'new-account','shared_scope':'default'})
        before=self.state.read_bytes()
        r=self.run_flow()
        self.assertEqual(self.state.read_bytes(),before)
        self.assertEqual(r['entries'][0]['status'],'protected-current-account')

    def test_missing_ui_is_agent_task_not_employee_json_work(self):
        r=self.run_flow()
        self.assertEqual(r['overall_status'],'partial')
        self.assertEqual(r['agent_tasks'][0]['id'],'browser')
        self.assertEqual(r['user_actions'],[])

    def test_system_authorization_collected_while_other_entries_continue(self):
        self.entries.insert(2,{'id':'keychain','kind':'keychain','scope':'old-only','shared_scope':'independent-keychain'})
        def denied(*a): raise k.KeychainError('needs-system-authorization','FAKE_SYSTEM_DIALOG')
        def denied_result(*a):
            try:denied()
            except k.KeychainError as e:return {'status':e.status,'reason':e.reason}
        r=self.run_flow({'keychain':denied_result,'browser':lambda *a:{'status':'completed'}})
        self.assertEqual(len(r['user_actions']),1)
        self.assertEqual(r['entries'][3]['status'],'completed')

    def test_second_run_reinspects_and_does_not_replay_old_plan(self):
        self.run_flow({'browser':lambda *a:{'status':'completed'}})
        before=self.state.read_bytes(),self.credentials.read_bytes()
        r=self.run_flow({'browser':lambda *a:{'status':'already-clean'}})
        self.assertEqual(before,(self.state.read_bytes(),self.credentials.read_bytes()))
        self.assertEqual([x['status'] for x in r['entries'][:2]],['already-clean','already-clean'])

    def test_partial_shared_scope_pauses_only_related_remaining_entries(self):
        r=self.run_flow({'files':lambda *a:{'status':'partial'},'browser':lambda *a:{'status':'completed'}})
        self.assertEqual([x['status'] for x in r['entries'][:3]],['partial','needs-reinspection','completed'])
        self.assertEqual(r['overall_status'],'partial')

    def test_ui_evidence_must_match_entry_and_prove_preservation(self):
        p=self.base/'ui-proof.json';p.write_bytes(g.encode({'entry_id':'wrong','profile_confirmed':True}))
        self.entries[-1].update(scope='old-only',ui_evidence=str(p))
        r=self.run_flow()
        self.assertEqual(r['entries'][-1]['status'],'blocked')

    def test_valid_independent_ui_evidence_finishes_matching_entry(self):
        p=self.base/'ui-proof.json';p.write_bytes(g.encode({'entry_id':'desktop','profile_confirmed':True,
            'old_logout_verified':True,'work_preserved':True,'mcp_preserved':True}))
        self.entries[-1].update(scope='old-only',ui_evidence=str(p))
        r=self.run_flow({'browser':lambda *a:{'status':'completed'}})
        self.assertEqual(r['overall_status'],'complete-selected-old-scope')

    def test_output_inside_work_scope_refused_before_mutation(self):
        before=self.state.read_bytes()
        with self.assertRaises(g.GuardError):
            flow.run({'schema':1,'inventory_complete':True,'entries':self.entries},self.config/'records',True)
        self.assertEqual(self.state.read_bytes(),before)

    def test_receipts_never_include_fake_credentials(self):
        self.run_flow()
        for p in self.base.glob('records-*/**/*.json'):
            self.assertNotIn('FAKE_OLD_SECRET',p.read_text(encoding='utf-8'))

    def test_missing_one_scope_does_not_abort_independent_entry(self):
        self.entries[0]['spec']=str(self.base/'missing-spec.json')
        before=self.credentials.read_bytes()
        r=self.run_flow({'browser':lambda *a:{'status':'completed'}})
        self.assertEqual([x['status'] for x in r['entries'][:3]],['needs-scope-evidence','needs-scope-evidence','completed'])
        self.assertEqual(self.credentials.read_bytes(),before)

    def test_unreadable_identity_does_not_abort_independent_browser(self):
        self.entries[0]['identity_state']=str(self.base/'missing-identity.json')
        r=self.run_flow({'browser':lambda *a:{'status':'completed'}})
        self.assertEqual(r['entries'][0]['status'],'needs-scope-evidence')
        self.assertEqual(r['entries'][2]['status'],'completed')


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--artifact-dir')
    args,rest=parser.parse_known_args()
    if args.artifact_dir:
        ARTIFACTS=Path(args.artifact_dir);ARTIFACTS.mkdir(parents=True,exist_ok=True)
    unittest.main(argv=[sys.argv[0],*rest],verbosity=2)
