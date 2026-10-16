"""Install this skill only; never discover or read Claude account data."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import uuid

NAME = "claude-account-cleanup"
VERSION = "1.2.1"
FILES = ("SKILL.md", "LICENSE", "agents/openai.yaml", "scripts/cleanup_guard.py",
         "scripts/keychain_guard.py", "scripts/install_skill.py",
         "references/platforms.md", "references/operations.md",
         "tests/test_cleanup.py", "tests/test_install.py",
         "scripts/identifier_policy.py", "scripts/browser_cleanup.mjs",
         "references/identifiers.md", "references/browser.md", "tests/test_identifiers.py")

class InstallError(Exception):
    pass

def require(ok, reason):
    if not ok:
        raise InstallError(reason)

def safe_path(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "absolute-path-required")
    if os.name == "nt":
        raw = str(path)
        require(not raw.startswith("\\\\.\\"), "device-path-refused")
        if not raw.startswith("\\\\?\\"):
            path = Path("\\\\?\\UNC\\" + raw[2:] if raw.startswith("\\\\") else "\\\\?\\" + raw)
    for part in reversed([path, *path.parents]):
        try:
            meta = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(meta.st_mode) and not (getattr(meta, "st_file_attributes", 0) & 0x400), "linked-path-refused")
    return path

def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def source_manifest(source):
    source = safe_path(source)
    require(source.is_dir(), "source-missing")
    entries = {}
    for rel in FILES:
        p = safe_path(source / rel)
        require(p.is_file(), "package-incomplete")
        raw = p.read_bytes()
        text = raw.decode("utf-8")
        if rel.endswith(".py"):
            ast.parse(text, filename=rel, feature_version=(3, 10))
        entries[rel] = hashlib.sha256(raw).hexdigest()
    front = (source / "SKILL.md").read_text(encoding="utf-8").split("---", 2)
    require(len(front) == 3 and "name: "+NAME in front[1], "wrong-skill-package")
    return entries

def installed_matches(target, expected):
    return installed_manifest(target) == expected


def installed_manifest(target):
    target = safe_path(target)
    found = {}
    for current, dirs, files in os.walk(target, followlinks=False):
        for name in dirs + files:
            p = safe_path(Path(current) / name)
            if p.is_file():
                found[p.relative_to(target).as_posix()] = hash_file(p)
    return found

def install(source, parent, upgrade=False):
    require(sys.version_info >= (3, 10), "python-3.10-required")
    source = safe_path(Path(source))
    parent = safe_path(Path(parent))
    require(source != parent and source not in parent.parents, "destination-overlaps-source")
    expected = source_manifest(source)
    parent.mkdir(parents=True, exist_ok=True)
    safe_path(parent)
    target = safe_path(parent / NAME)
    previous = None
    if target.exists():
        require(target.is_dir(), "target-not-directory")
        if installed_matches(target, expected):
            return {"status":"already-installed", "version":VERSION, "files":len(expected)}
        if not upgrade:
            return {"status":"existing-version-preserved", "reason":"Different existing skill; update was not authorized", "installed":False}
        front = (target / 'SKILL.md').read_text(encoding='utf-8').split('---', 2)
        require(len(front) == 3 and 'name: '+NAME in front[1], 'wrong-existing-skill')
        previous = installed_manifest(target)
    stage = parent / (".cc-" + uuid.uuid4().hex[:12])
    safe_path(stage)
    stage.mkdir()
    # Only package files are copied. On failure, retain this uniquely named staging folder.
    for rel, fingerprint in expected.items():
        item = safe_path(source / rel)
        require(hash_file(item) == fingerprint, "package-changed")
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, dest)
        require(hash_file(dest) == fingerprint, "copy-verification-failed")
    require(installed_matches(stage, expected), "staging-verification-failed")
    safe_path(target)
    backup = None
    if previous is not None:
        require(installed_manifest(target) == previous, 'existing-skill-changed')
        backup = safe_path(parent / ('.cc-previous-' + uuid.uuid4().hex[:12]))
        require(backup.parent == parent and not backup.exists(), 'invalid-backup-target')
        os.rename(target, backup)
    require(not target.exists(), "target-created-concurrently")
    # Both absolute paths are verified immediate children of the requested skill root.
    require(stage.parent == parent and target.parent == parent, "move-outside-install-root")
    # macOS rename may replace an empty directory if one appears concurrently; never replace a populated skill.
    try:
        os.rename(stage, target)
    except OSError:
        if backup is not None and not target.exists():
            os.rename(backup, target)
        raise
    require(installed_matches(target, expected), "installed-verification-failed")
    return {"status":"updated" if backup else "installed", "version":VERSION, "files":len(expected),
            **({'previous_skill_preserved':str(backup)} if backup else {})}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest-root",required=True)
    parser.add_argument("--upgrade",action='store_true',help='Explicitly update this skill, retaining the previous folder')
    args=parser.parse_args()
    try:
        result=install(Path(__file__).resolve().parents[1],Path(args.dest_root),args.upgrade)
        print(json.dumps(result))
        return 0 if result["status"] in ("installed","already-installed","updated") else 2
    except InstallError as exc:
        print(json.dumps({"status":"blocked","reason":str(exc)}))
    except (OSError,ValueError,SyntaxError,UnicodeError):
        print(json.dumps({"status":"blocked","reason":"package-or-install-permission-error"}))
    return 2

if __name__=="__main__":
    raise SystemExit(main())

