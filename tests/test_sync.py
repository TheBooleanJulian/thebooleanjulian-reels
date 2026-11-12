"""Projects in your account: a project leaves one device as small files, arrives on another, and finds that device's
own copy of the footage by file name + size (or asks for it once)."""
import json, os, shutil, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import studio  # noqa: E402


def device(monkeypatch, root):
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(studio, 'PROJECTS', str(root / 'projects'))
    monkeypatch.setattr(studio, 'MEDIA_INDEX', str(root / 'media_index.json'))
    os.makedirs(studio.PROJECTS, exist_ok=True)


def make_project(root, footage, name='talk'):
    d = root / 'projects' / name
    (d / 'src').mkdir(parents=True)
    cfg = {'format': 'presentation', 'name': name, 'video': str(footage / 'C0001.MP4'), 'transcript': 'src/talk.words.json',
           'broll': [{'file': str(footage / 'crowd.jpg'), 'at_src': 3}], 'music': {'file': ''}}
    (d / 'preset.json').write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    (d / 'src' / 'talk.words.json').write_text(json.dumps({'words': [{'w': 'hello', 'start': 0, 'end': 0.4}]}), encoding='utf-8')
    (d / 'notes.md').write_text('client wants it short', encoding='utf-8')
    return d


@pytest.fixture
def footage(tmp_path):
    f = tmp_path / 'J' / 'Shoot'
    f.mkdir(parents=True)
    (f / 'C0001.MP4').write_bytes(b'v' * 1000)
    (f / 'crowd.jpg').write_bytes(b'j' * 50)
    return f


def test_round_trip_finds_footage_by_name_and_size(tmp_path, monkeypatch, footage):
    device(monkeypatch, tmp_path / 'pc')
    make_project(tmp_path / 'pc', footage)
    out = studio.project_bundle('talk')
    b = out['bundle']
    assert b['kind'] == 'presentation' and b['notes'] == 'client wants it short'
    assert b['transcript']['file'] == 'talk.words.json' and 'hello' in b['transcript']['text']
    assert {m['size'] for m in b['media']} == {1000, 50}
    wire = json.loads(json.dumps(b))                         # what the website stores and sends back
    shutil.rmtree(footage)                                   # the PC's drive isn't on the laptop

    # The laptop has the same footage in another place (its own drive letter / folder) and has used it before.
    lap = tmp_path / 'laptop'
    copy = tmp_path / 'E' / 'Footage'
    copy.mkdir(parents=True)
    (copy / 'C0001.MP4').write_bytes(b'v' * 1000)
    (copy / 'crowd.jpg').write_bytes(b'j' * 50)
    device(monkeypatch, lap)
    studio.remember_media([str(copy / 'C0001.MP4'), str(copy / 'crowd.jpg')])
    r = studio.apply_bundle({'bundle': wire, 'account': 'julian@example.com', 'updated_at': '2026-11-12T10:00:00Z'})
    assert r['missing'] == []
    cfg = json.loads((lap / 'projects' / 'talk' / 'preset.json').read_text(encoding='utf-8'))
    assert cfg['video'] == str(copy / 'C0001.MP4') and cfg['broll'][0]['file'] == str(copy / 'crowd.jpg')
    assert (lap / 'projects' / 'talk' / 'src' / 'talk.words.json').exists()
    assert (lap / 'projects' / 'talk' / 'notes.md').read_text(encoding='utf-8') == 'client wants it short'
    sync = studio.read_sync(str(lap / 'projects' / 'talk'))['julian@example.com']
    assert sync == {'updated_at': '2026-11-12T10:00:00Z', 'hash': studio.project_bundle('talk')['hash']}


def test_same_name_different_size_is_not_trusted(tmp_path, monkeypatch, footage):
    device(monkeypatch, tmp_path / 'pc')
    make_project(tmp_path / 'pc', footage)
    wire = studio.project_bundle('talk')['bundle']
    shutil.rmtree(footage)
    device(monkeypatch, tmp_path / 'laptop')
    other = tmp_path / 'E' / 'OtherShoot'
    other.mkdir(parents=True)
    (other / 'C0001.MP4').write_bytes(b'x' * 999)            # cameras reuse names: a different clip
    studio.remember_media([str(other / 'C0001.MP4')])
    r = studio.apply_bundle({'bundle': wire})
    assert str(footage / 'C0001.MP4') in r['missing']


def test_find_files_relinks_the_rest_of_the_folder(tmp_path, monkeypatch, footage):
    device(monkeypatch, tmp_path / 'pc')
    make_project(tmp_path / 'pc', footage)
    wire = studio.project_bundle('talk')['bundle']
    shutil.rmtree(footage)
    device(monkeypatch, tmp_path / 'laptop')
    r = studio.apply_bundle({'bundle': wire})
    assert len(r['missing']) == 2
    copy = tmp_path / 'E' / 'Footage'
    copy.mkdir(parents=True)
    (copy / 'C0001.MP4').write_bytes(b'v' * 1000)
    (copy / 'crowd.jpg').write_bytes(b'j' * 50)
    r = studio.relink({'name': 'talk', 'ref': r['missing'][0], 'path': str(copy / 'C0001.MP4')})
    assert r == {'ok': True, 'fixed': 2, 'missing': []}
    assert studio.find_media('anything/C0001.MP4', 1000) == str(copy / 'C0001.MP4')


def test_absolute_transcript_moves_into_the_project_and_old_preset_is_kept(tmp_path, monkeypatch, footage):
    device(monkeypatch, tmp_path / 'pc')
    d = make_project(tmp_path / 'pc', footage)
    cfg = json.loads((d / 'preset.json').read_text(encoding='utf-8'))
    cfg['transcript'] = str(footage / 'talk.words.json')
    (footage / 'talk.words.json').write_text('{"words": []}', encoding='utf-8')
    (d / 'preset.json').write_text(json.dumps(cfg), encoding='utf-8')
    wire = studio.project_bundle('talk')['bundle']
    device(monkeypatch, tmp_path / 'laptop')
    make_project(tmp_path / 'laptop', footage)                # an older local copy gets replaced
    studio.apply_bundle({'bundle': wire})
    lp = tmp_path / 'laptop' / 'projects' / 'talk'
    assert json.loads((lp / 'preset.json').read_text(encoding='utf-8'))['transcript'] == 'src/talk.words.json'
    assert (lp / 'src' / 'talk.words.json').read_text(encoding='utf-8') == '{"words": []}'
    assert len(os.listdir(lp / 'work' / 'backup')) == 1


def test_hash_ignores_media_sizes_but_not_edits(tmp_path, monkeypatch, footage):
    device(monkeypatch, tmp_path / 'pc')
    d = make_project(tmp_path / 'pc', footage)
    h1 = studio.project_bundle('talk')['hash']
    (footage / 'C0001.MP4').write_bytes(b'v' * 10)
    assert studio.project_bundle('talk')['hash'] == h1
    (d / 'notes.md').write_text('changed', encoding='utf-8')
    assert studio.project_bundle('talk')['hash'] != h1


def test_refuses_bad_bundles_and_reels(tmp_path, monkeypatch):
    device(monkeypatch, tmp_path / 'pc')
    with pytest.raises(ValueError): studio.apply_bundle({'bundle': {'v': 1, 'name': '../evil', 'preset': '{}'}})
    with pytest.raises(ValueError): studio.apply_bundle({'bundle': {'v': 1, 'name': 'x', 'preset': 'not json'}})
    with pytest.raises(ValueError): studio.apply_bundle({'bundle': {'v': 1, 'name': 'x', 'preset': '{"scenes": []}'}})
    reel = tmp_path / 'pc' / 'projects' / 'r'
    reel.mkdir(parents=True)
    (reel / 'preset.json').write_text('{"scenes": []}', encoding='utf-8')
    with pytest.raises(ValueError): studio.project_bundle('r')
