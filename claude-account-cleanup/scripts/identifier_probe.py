"""Inspect explicitly selected PROGRAM files, never Claude account data."""
import argparse
import hashlib
import json
import mmap
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
from cleanup_guard import GuardError, guard_path, require
from identifier_policy import BUILDS


def probe(binary, version):
    path = guard_path(Path(binary))
    require(path.is_absolute() and path.is_file(), 'Explicit installed program path required')
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    sha = digest.hexdigest()
    fields = BUILDS.get((version, sha))
    result = {'version': version, 'binary_sha256': sha, 'binary_executed': False}
    if fields is not None:
        return {**result, 'status': 'verified-build', 'verified_random_id_fields': list(fields)}
    # Candidate locations reduce Agent investigation; strings alone never authorize reset.
    locations = []
    size = path.stat().st_size
    if size:
        with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            for field in (b'userID', b'machineID', b'deviceId'):
                pos = 0
                for _ in range(5):
                    pos = data.find(field, pos)
                    if pos < 0:
                        break
                    locations.append({'field': field.decode(), 'byte_offset': pos})
                    pos += len(field)
    return {**result, 'status': 'needs-code-review', 'candidate_locations': locations,
            'required_evidence': ['random generation', 'configuration read/write',
                                  'field use', 'actual profile scope']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    try:
        require(re.fullmatch(r'[0-9A-Za-z.+_-]{1,80}', args.version), 'Invalid version')
        print(json.dumps(probe(args.binary, args.version)))
        return 0
    except (GuardError, OSError, ValueError, RuntimeError):
        print(json.dumps({'status': 'unverified', 'reason': 'Selected program cannot be inspected'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
