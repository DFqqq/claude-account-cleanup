"""Isolated installation tests; no real user paths or account data."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode=True
SKILL=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SKILL/"scripts"))
import install_skill as setup
ARTIFACTS=None

class InstallTests(unittest.TestCase):
    def setUp(self):
        self.base=Path(tempfile.mkdtemp(prefix="install-case-",dir=ARTIFACTS))
        self.source=self.base/"unpacked"/setup.NAME
        for rel in setup.FILES:
            p=self.source/rel;p.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(SKILL/rel,p)
        self.parent=self.base/"virtual-user"/".agents"/"skills"
        self.accounts=self.base/"virtual-user"/".claude"
        self.accounts.mkdir(parents=True)
        self.secret=self.accounts/".credentials.json"
        self.secret.write_text('{"claudeAiOauth":"FAKE_NEW_ACCOUNT"}',encoding="utf-8")
    def test_install_and_repeat_leave_account_untouched(self):
        original=self.secret.read_bytes()
        first=setup.install(self.source,self.parent)
        after={str(p.relative_to(self.parent)):p.read_bytes() for p in self.parent.rglob("*") if p.is_file()}
        second=setup.install(self.source,self.parent)
        self.assertEqual(first["status"],"installed")
        self.assertEqual(second["status"],"already-installed")
        self.assertEqual(after,{str(p.relative_to(self.parent)):p.read_bytes() for p in self.parent.rglob("*") if p.is_file()})
        self.assertEqual(self.secret.read_bytes(),original)
    def test_existing_modified_skill_is_preserved(self):
        setup.install(self.source,self.parent)
        custom=self.parent/setup.NAME/"SKILL.md"
        custom.write_text("USER CUSTOMIZATION",encoding="utf-8")
        result=setup.install(self.source,self.parent)
        self.assertEqual(result["status"],"existing-version-preserved")
        self.assertEqual(custom.read_text(encoding="utf-8"),"USER CUSTOMIZATION")
    def test_authorized_update_preserves_old_skill_and_account(self):
        setup.install(self.source,self.parent)
        old=self.parent/setup.NAME/'SKILL.md'
        old.write_text(old.read_text(encoding='utf-8')+'\nLocal prior customization\n',encoding='utf-8')
        previous=old.read_bytes();account=self.secret.read_bytes()
        result=setup.install(self.source,self.parent,True)
        self.assertEqual(result['status'],'updated')
        self.assertEqual((Path(result['previous_skill_preserved'])/'SKILL.md').read_bytes(),previous)
        self.assertTrue(setup.installed_matches(self.parent/setup.NAME,setup.source_manifest(self.source)))
        self.assertEqual(self.secret.read_bytes(),account)
    def test_update_failure_restores_previous_skill(self):
        from unittest.mock import patch
        setup.install(self.source,self.parent)
        old=self.parent/setup.NAME/'SKILL.md';old.write_text(old.read_text(encoding='utf-8')+'\nOriginal local text\n',encoding='utf-8')
        raw=old.read_bytes();real=setup.os.rename;calls=[]
        def injected(a,b):
            calls.append(1)
            if len(calls)==2:raise OSError('fixture publish failure')
            return real(a,b)
        with patch.object(setup.os,'rename',side_effect=injected):
            with self.assertRaises(OSError):setup.install(self.source,self.parent,True)
        self.assertEqual(old.read_bytes(),raw)
    def test_missing_source_has_no_install_side_effect(self):
        (self.source/"agents"/"openai.yaml").unlink()
        with self.assertRaises(setup.InstallError):setup.install(self.source,self.parent)
        self.assertFalse(self.parent.exists())
    def test_bad_python_has_no_install_side_effect(self):
        (self.source/"scripts"/"keychain_guard.py").write_text("def broken(",encoding="utf-8")
        with self.assertRaises(SyntaxError):setup.install(self.source,self.parent)
        self.assertFalse(self.parent.exists())
    def test_path_traversal_refused(self):
        with self.assertRaises(setup.InstallError):setup.install(self.source,self.base/"a"/".."/"b")
    def test_overlapping_destination_refused(self):
        with self.assertRaises(setup.InstallError):setup.install(self.source,self.source/"inside")
    def test_extra_source_payload_not_installed(self):
        (self.source/"unexpected.txt").write_text("not shipped",encoding="utf-8")
        setup.install(self.source,self.parent)
        self.assertFalse((self.parent/setup.NAME/"unexpected.txt").exists())
    def test_partial_copy_does_not_publish_skill(self):
        from unittest.mock import patch
        with patch.object(setup.shutil,"copyfile",side_effect=OSError("fixture deny")):
            with self.assertRaises(OSError):setup.install(self.source,self.parent)
        self.assertFalse((self.parent/setup.NAME).exists())
        self.assertEqual(len(list(self.parent.glob(".cc-*"))),1)
    def test_destination_file_not_replaced(self):
        self.parent.mkdir(parents=True)
        target=self.parent/setup.NAME;target.write_text("keep",encoding="utf-8")
        with self.assertRaises(setup.InstallError):setup.install(self.source,self.parent)
        self.assertEqual(target.read_text(encoding="utf-8"),"keep")
    @unittest.skipUnless(os.name=="nt","Windows extended paths")
    def test_windows_long_destination_without_manual_prefix(self):
        parent=self.parent/("nested-"+"x"*72)/("nested-"+"y"*72)
        self.assertGreater(len(str(parent/setup.NAME/"scripts"/"install_skill.py")),260)
        result=setup.install(self.source,parent)
        self.assertEqual(result["status"],"installed")
        self.assertEqual(setup.install(self.source,parent)["status"],"already-installed")

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--artifact-dir");args,remaining=p.parse_known_args()
    if args.artifact_dir:
        ARTIFACTS=Path(args.artifact_dir).absolute();ARTIFACTS.mkdir(parents=True,exist_ok=True)
    unittest.main(argv=[sys.argv[0],*remaining],verbosity=2)

