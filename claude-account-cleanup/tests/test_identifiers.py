"""Identifier policy and real synthetic JSON transformations. No account discovery."""
import argparse
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cleanup_guard as g
from identifier_policy import BUILDS, reset_fields
ARTIFACTS = None


class IdentifierTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='ids-', dir=ARTIFACTS))
        self.state = self.base / '.claude.json'
        self.data = {'oauthAccount': {'accountUuid': 'FAKE-OLD'}, 'userID': 'FAKE-OLD-ID',
            'machineID': 'FAKE-OLD-MACHINE', 'remoteControlMachineId': 'FAKE-KEEP-REMOTE',
            'projects': {'demo': {'work': 'FAKE-KEEP'}}, 'mcpServers': {'demo': {'auth': 'FAKE-KEEP-MCP'}}}
        self.state.write_bytes(g.encode(self.data))
        self.work = self.base / 'session.jsonl'
        self.work.write_bytes(b'{"work":"FAKE_KEEP_SESSION"}\n')
        version, fingerprint = list(BUILDS)[1]
        self.evidence = {'version': version, 'binary_sha256': fingerprint, 'config_scope': 'old-only', 'reset_requested': True}
        self.spec = {'schema': 1, 'account_state': 'old-only', 'config_dirs': [],
            'state_files': [str(self.state)], 'preserve_paths': [str(self.work)], 'operations': [
            {'kind':'state-json', 'path':str(self.state),'remove_fields':['oauthAccount'],
             'old_account_evidence':'confirmed', 'identifier_reset':self.evidence}]}

    def test_known_build_resets_both_ids_preserves_projects_mcp_and_remote_id(self):
        plan=g.prepare(self.spec)
        self.assertEqual(set(plan['operations'][0]['remove_fields']), {'oauthAccount','userID','machineID'})
        self.assertEqual(g.apply(plan,True)['status'],'verified')
        self.assertEqual(g.verify(plan)['status'],'verified')
        self.assertEqual(g.parse_json(self.state.read_bytes()),{k:v for k,v in self.data.items() if k not in {'oauthAccount','userID','machineID'}})
        self.assertEqual(self.work.read_bytes(),b'{"work":"FAKE_KEEP_SESSION"}\n')

    def test_older_build_does_not_assume_machine_id(self):
        self.evidence.update(dict(zip(('version','binary_sha256'),list(BUILDS)[0])))
        self.assertEqual(reset_fields(self.data,self.evidence),['userID'])

    def test_missing_ids_are_not_created(self):
        self.assertEqual(reset_fields({'projects':{}},self.evidence),[])

    def test_wrong_binary_hash_same_version_is_not_trusted(self):
        self.evidence['binary_sha256']='0'*64
        with self.assertRaises(ValueError):reset_fields(self.data,self.evidence)

    def test_unknown_build_needs_explicit_reviewed_code_locations(self):
        self.evidence.update(version='9.9.9',binary_sha256='0'*64,basis='local-code-reviewed',
            verified_random_id_fields=['userID'],code_locations=['fixture-generator:123'])
        self.assertEqual(reset_fields(self.data,self.evidence),['userID'])
        self.evidence['verified_random_id_fields']=['remoteControlMachineId']
        with self.assertRaises(ValueError):reset_fields(self.data,self.evidence)

    def test_new_account_scope_and_unrequested_reset_are_refused(self):
        for delta in ({'config_scope':'mixed'},{'reset_requested':False}):
            with self.assertRaises(ValueError):reset_fields(self.data,{**self.evidence,**delta})

    def test_new_login_after_plan_prevents_identifier_reset(self):
        plan=g.prepare(self.spec)
        raw=g.encode({**self.data,'oauthAccount':{'accountUuid':'FAKE-NEW'}})
        self.state.write_bytes(raw)
        with self.assertRaises(g.GuardError):g.apply(plan,True)
        self.assertEqual(self.state.read_bytes(),raw)

    def test_matching_backup_is_processed_only_when_explicitly_selected(self):
        selected=self.base/'.claude.json.backup-1';selected.write_bytes(self.state.read_bytes())
        other=self.base/'.claude.json.backup-2';other.write_bytes(g.encode({'userID':'FAKE-OTHER-KEEP'}))
        original=other.read_bytes()
        self.spec['state_files'].append(str(selected))
        op=copy.deepcopy(self.spec['operations'][0]);op['path']=str(selected);self.spec['operations'].append(op)
        self.spec['preserve_paths'].append(str(other))
        self.assertEqual(g.apply(g.prepare(self.spec),True)['status'],'verified')
        self.assertNotIn('userID',g.parse_json(selected.read_bytes()))
        self.assertEqual(other.read_bytes(),original)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifact-dir');args,rest=p.parse_known_args()
    if args.artifact_dir:
        ARTIFACTS=Path(args.artifact_dir).absolute();ARTIFACTS.mkdir(parents=True,exist_ok=True)
    unittest.main(argv=[sys.argv[0],*rest],verbosity=2)
