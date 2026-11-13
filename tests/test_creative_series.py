import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agent_core.creative_series import add_reference, get_template, list_templates, save_template


def settings(tmp_path):
    return SimpleNamespace(project_root=tmp_path, assets=tmp_path / 'assets')


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b'')


def test_builtin_snapshot_and_versioned_save(tmp_path):
    s = settings(tmp_path)
    first = get_template(s, 'suri-family')
    assert first['characters'][0]['age'] == 4
    assert first['reference_assets'] == []
    first['label'] = 'Owner version'
    saved = save_template(s, first)
    assert saved['version'] == 2
    first['label'] = 'mutated'
    assert get_template(s, 'suri-family')['label'] == 'Owner version'
    assert len(list_templates(s)) == 1


def test_reference_immutable_deduplicated_and_preserved_on_save(tmp_path):
    s = settings(tmp_path)
    before = get_template(s, 'suri-family')
    result = add_reference(s, 'suri-family', 'suri', 'photo.png', png(), rights='permission')
    ref = result['reference_assets'][0]
    assert result['version'] == 2
    assert before['reference_assets'] == []
    assert add_reference(s, 'suri-family', 'suri', 'again.png', png())['version'] == 2
    result['reference_assets'] = []  # cannot erase durable refs by posting an old snapshot
    saved = save_template(s, result)
    assert len(saved['reference_assets']) == 1
    assert ref['rights'] == 'permission'
    assert not list(tmp_path.rglob('*.tmp'))


@pytest.mark.parametrize('tid,cid,name,data,rights', [
    ('../evil', 'suri', 'x.png', png(), 'owned'),
    ('suri-family', 'missing', 'x.png', png(), 'owned'),
    ('suri-family', 'suri', '../x.png', png(), 'owned'),
    ('suri-family', 'suri', 'x.png', b'not an image', 'owned'),
    ('suri-family', 'suri', 'x.png', png(), 'unknown'),
])
def test_invalid_reference_rejected(tmp_path, tid, cid, name, data, rights):
    with pytest.raises(ValueError):
        add_reference(settings(tmp_path), tid, cid, name, data, rights)
    assert not list(tmp_path.rglob('*.png'))


def test_threaded_saves_increment_versions_atomically(tmp_path):
    s = settings(tmp_path)
    template = get_template(s, 'suri-family')
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda _: save_template(s, template), range(8)))
    assert sorted(x['version'] for x in snapshots) == list(range(2, 10))
    assert get_template(s, 'suri-family')['version'] == 9
