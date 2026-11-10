"""Studio server: path guard, argument validation for jobs (no jobs are actually run)."""
import os, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import studio  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(studio, 'submit', lambda title, argv, kind, outputs=(), project=None:
                        calls.append({'title': title, 'argv': argv, 'kind': kind, 'outputs': list(outputs)}) or calls[-1])
    return calls


def test_inside_does_not_match_sibling_prefix(tmp_path):
    a = tmp_path / 'proj'; b = tmp_path / 'proj-evil'
    a.mkdir(); b.mkdir()
    assert studio.inside(str(a / 'x.mp4'), str(a))
    assert not studio.inside(str(b / 'x.mp4'), str(a))


def test_media_guard_blocks_unpicked_files(tmp_path):
    f = tmp_path / 'secret.txt'; f.write_text('x')
    assert not studio.is_allowed(str(f))
    studio.allow(str(tmp_path))
    assert studio.is_allowed(str(f))


def test_polaroid_builds_argv(tmp_path, captured):
    photos = tmp_path / 'Event'; photos.mkdir()
    studio.build_polaroid({'template': 2, 'photos': str(photos), 'duration': 60, 'size': '1080x1920',
                           'per_page': 6, 'draft': True, 'order': 'name'})
    argv = captured[0]['argv']
    assert argv[1].endswith('polaroid_pile.py') and argv[2] == str(photos)
    assert argv[argv.index('-o') + 1] == str(photos) + '_pile.mp4'
    for flag in ('-d', '--size', '--per-page', '--preview', '--order'): assert flag in argv
    assert '--watch' not in argv


@pytest.mark.parametrize('bad', [
    {'photos': ''},
    {'photos': 'Z:/definitely/not/here'},
    {'size': '1920x1080; del *'},
    {'duration': -5},
])
def test_polaroid_rejects_bad_input(tmp_path, captured, bad):
    photos = tmp_path / 'p'; photos.mkdir()
    with pytest.raises(ValueError):
        studio.build_polaroid({'template': 1, 'photos': str(photos), **bad})
    assert not captured


@pytest.mark.parametrize('name', ['../x', 'a b', '', 'x' * 100, '..\\..\\windows'])
def test_project_names_are_validated(name):
    with pytest.raises(ValueError):
        studio.project_dir(name)


def test_new_project_rejects_unknown_template(captured):
    with pytest.raises(ValueError):
        studio.build_new({'name': 'ok-name', 'template': '../../evil'})
    studio.build_new({'name': 'ok-name', 'template': 'interview'})
    assert captured[0]['argv'][-2:] == ['--template', 'interview']
