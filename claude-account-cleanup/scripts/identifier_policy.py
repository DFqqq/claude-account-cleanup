"""Version-scoped local identifier selection; never reads account data by itself."""
import re

# Evidence is recorded in references/identifiers.md. Hashes identify exact builds,
# not usernames, account IDs or hardware identities.
BUILDS = {
    ('2.1.178', '8e82516a82511cb321f3aabe696e5fa6a318c43c2a74d657b867d70adf5e699b'): ('userID',),
    ('2.1.223', 'a708ba811c4cc46907df358e22f2aa6da3dbc28192747e4d3c4a0869752fe722'): ('userID', 'machineID'),
}


def reset_fields(state, evidence):
    if evidence.get('config_scope') != 'old-only' or evidence.get('reset_requested') is not True:
        raise ValueError('Identifier reset requires an explicitly selected old-only config')
    key = (evidence.get('version'), evidence.get('binary_sha256'))
    fields = BUILDS.get(key)
    if fields is None:
        # A different build needs new static-code evidence, not a version/name guess.
        if not (evidence.get('basis') == 'local-code-reviewed' and
                re.fullmatch(r'[0-9a-f]{64}', key[1] or '') and
                isinstance(evidence.get('code_locations'), list) and evidence['code_locations'] and
                all(isinstance(x, str) and 0 < len(x) < 200 for x in evidence['code_locations'])):
            raise ValueError('Unverified identifier implementation')
        fields = evidence.get('verified_random_id_fields')
        if not (isinstance(fields, list) and fields and len(set(fields)) == len(fields) and
                set(fields) <= {'userID', 'machineID', 'deviceId'}):
            raise ValueError('Unsupported identifier fields')
    # Absence is normal. Never create a made-up field or change machine/system IDs.
    return [field for field in fields if field in state]
