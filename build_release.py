"""Reproducible local package build. Does not install or inspect any real account."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'claude-account-cleanup'/'scripts'))
from install_skill import FILES, VERSION, source_manifest


def sha(raw):return hashlib.sha256(raw).hexdigest()
def write_json(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
def archive(output,entries):
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name in sorted(entries):
            p=ROOT/name
            if p.is_symlink() or not p.is_file():raise ValueError('Unexpected package path')
            info=zipfile.ZipInfo(name,date_time=(2026,10,14,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            z.writestr(info,p.read_bytes())
    with zipfile.ZipFile(output) as z:
        assert sorted(z.namelist())==sorted(entries)
        assert all(z.read(name)==(ROOT/name).read_bytes() for name in entries)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--manifest-only',action='store_true');args=parser.parse_args()
    source_manifest(ROOT/'claude-account-cleanup')
    skill=['claude-account-cleanup/'+rel for rel in FILES]
    extras=['run_validation.py','开始测试.txt','validation/test_extended.py','validation/test_browser.mjs','validation/browser_e2e.cjs']
    manifest={'version':VERSION,'files':{p:sha((ROOT/p).read_bytes()) for p in sorted(skill+extras)}}
    write_json(ROOT/'bundle-manifest.json',manifest)
    if args.manifest_only:
        print(json.dumps({'version':VERSION,'manifest_files':len(manifest['files'])}));return
    dist=ROOT/'dist';dist.mkdir(exist_ok=True)
    skill_zip=dist/f'claude-account-cleanup-v{VERSION}.zip'
    bundle_zip=dist/f'Claude清理Skill-闲置Windows测试包-v{VERSION}.zip'
    archive(skill_zip,skill)
    archive(bundle_zip,skill+extras+['bundle-manifest.json','README.md'])
    result={'version':VERSION,'source_files':manifest['files'],
            'artifacts':{p.name:{'sha256':sha(p.read_bytes()),'bytes':p.stat().st_size} for p in [skill_zip,bundle_zip]}}
    write_json(dist/f'release-manifest-v{VERSION}.json',result)
    print(json.dumps({'version':VERSION,'artifacts':result['artifacts']},ensure_ascii=False))


if __name__=='__main__':main()
