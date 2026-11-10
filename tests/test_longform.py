"""Edit decisions for interviews/presentations: no ffmpeg needed (probe is stubbed)."""
import json, os, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import cuts, subs, longform  # noqa: E402
from make_reel import concat_line  # noqa: E402


def W(w, s, e): return {'w': w, 'start': s, 'end': e}


WORDS = [W('Hi,', 0.0, 0.4), W("I'm", 0.5, 0.7), W('Jane.', 0.7, 1.0),           # pause 3 s next
         W('Um,', 4.0, 4.4), W('the', 4.8, 5.0), W('best', 5.0, 5.3), W('part.', 5.3, 5.8),
         W('Okay', 6.0, 6.3), W('so', 6.3, 6.5), W('anyway.', 6.5, 7.0),           # to be cut by hand
         W('That', 7.2, 7.4), W('works.', 7.4, 7.9)]


# ---------------------------------------------------------------- cuts.segments_from_words(breaks=)
def test_breaks_split_a_short_gap():
    ws = [W('a', 0, 0.5), W('c', 0.9, 1.2)]                  # 0.4 s gap: normally kept
    assert len(cuts.segments_from_words(ws, max_gap=0.6)) == 1
    segs = cuts.segments_from_words(ws, max_gap=0.6, breaks=[(0.55, 0.85)])
    assert len(segs) == 2


def test_breaks_default_keeps_old_behaviour():
    assert cuts.segments_from_words(WORDS) == cuts.segments_from_words(WORDS, breaks=())


# ---------------------------------------------------------------- subs
def test_word_events_never_overlap():
    ws = [W(f'w{i}', i * 0.3, i * 0.3 + 0.25) for i in range(20)]
    ev = subs.word_events(ws, max_words=3, max_chars=22, hold_after=0.25)
    for (a0, b0, *_), (a1, *_r) in zip(ev, ev[1:]):
        assert b0 <= a1 + 1e-9                               # libass would stack overlapping lines
    assert all(b > a for a, b, *_ in ev)
    assert sum(1 for *_x, first in ev if first) == len(subs.group_words(ws, 3, 22))


def test_line_cues_respect_max_chars_and_order():
    ws = [W(f'word{i}', i * 0.4, i * 0.4 + 0.3) for i in range(40)]
    cues = subs.line_cues(ws, max_chars=20, max_lines=2)
    for c in cues:
        assert 1 <= len(c['lines']) <= 2
        assert all(len(l) <= 20 for l in c['lines'])
    for c0, c1 in zip(cues, cues[1:]):
        assert c0['end'] <= c1['start']


def test_ass_color_and_timestamps():
    assert subs.ass_color((0, 229, 255)) == '&H00FFE500'
    assert subs._ts_ass(3725.456) == '1:02:05.46'
    assert subs._ts_srt(61.5) == '00:01:01,500'


def test_build_ass_has_one_style_and_pinned_words():
    st = {'family': 'Chakra Petch', 'size': 0.05, 'y': 0.7, 'color': 'white', 'highlight': 'cyan', 'bold': True}
    out = subs.build_ass(WORDS[:3], 'words', 1080, 1920, st, {'white': (255, 255, 255), 'cyan': (0, 229, 255)})
    assert out.count('Style: Cap,') == 1
    dialogue = [l for l in out.splitlines() if l.startswith('Dialogue:')]
    assert dialogue and all('\\pos(540,' in l for l in dialogue)
    assert '{\\c&HFFE500&}' in out


def test_chapters_pin_first_to_zero():
    assert subs.chapter_lines([(12.0, 'Intro'), (75.0, 'Q&A')]) == '00:00 Intro\n01:15 Q&A\n'
    assert subs.chapter_lines([]) == ''


# ---------------------------------------------------------------- longform.Edit
@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(longform, 'probe', lambda p: {'duration': 9.0, 'width': 1920, 'height': 1080, 'fps': 30.0, 'audio': True})
    proj = tmp_path / 'projects' / 'demo'
    (proj / 'src').mkdir(parents=True)
    (proj / 'raw.mp4').write_bytes(b'')
    (proj / 'src' / 'raw.words.json').write_text(json.dumps({'words': WORDS}), encoding='utf-8')

    def make(**over):
        cfg = {'format': 'interview', 'name': 'demo', 'video': 'raw.mp4', 'transcript': 'src/raw.words.json'}
        cfg.update(over)
        (proj / 'preset.json').write_text(json.dumps(cfg), encoding='utf-8')
        return longform.Edit(str(proj / 'preset.json'))
    return make


def test_interview_cuts_pause_and_filler(project):
    e = project()
    assert 3 in e.filler_idx                                 # 'Um,'
    kept = [w['w'] for w in e.words]
    assert 'Um,' not in kept and kept[0] == 'Hi,'
    assert e.dur < 9.0 - 2.5                                 # the 3 s pause is gone
    # whole frames only, so audio slices line up with video exactly
    for a, b in e.edl.segs:
        assert abs(a * 30 - round(a * 30)) < 1e-6 and abs(b * 30 - round(b * 30)) < 1e-6
    assert abs(e.dur - sum(c['kb'] - c['ka'] for c in e.edl.clips) / 30) < 1e-9


def test_hand_cut_removes_words_and_audio(project):
    e = project(cuts=[[6.0, 7.0]])
    assert 'anyway.' not in [w['w'] for w in e.words]
    assert all(not (a < 6.5 < b) for a, b in e.edl.segs)        # middle of the cut is not in any kept segment


def test_fillers_kept_when_disabled(project):
    e = project(trim={'fillers': False})
    assert e.filler_idx == [] and 'Um,' in [w['w'] for w in e.words]


def test_edit_t_maps_cut_moments_forward(project):
    e = project(cuts=[[6.0, 7.0]])
    t_cut = e.edit_t(6.5)
    assert t_cut == pytest.approx(e.edit_t(7.2) - (7.2 - [a for a, _ in e.edl.segs if a > 6.5][0]), abs=0.05)
    assert e.edit_t(0.0) == 0.0


def test_alternate_framing_toggles_on_long_segments_only(project):
    zooms = [lk['zoom'] for lk in project(punch={'mode': 'alternate', 'min_seg': 0.5, 'scale': 1.2}).clip_looks()]
    assert len(zooms) >= 2 and zooms[0] == 1.0 and zooms[1] == pytest.approx(1.2)
    assert all(z == 1.0 for z in (lk['zoom'] for lk in project(punch={'mode': 'off'}).clip_looks()))


def test_clip_overrides_beat_project_look(project):
    e = project(output={'zoom': 1.1, 'rotate': 2}, grade={'exposure': 0.5},
                clips=[{'in': 0, 'out': 1.0}, {'in': 4.8, 'out': 5.8, 'zoom': 1.5, 'rotate': -1, 'grade': {'saturation': 0}}])
    a, b = e.clip_looks()
    assert a['zoom'] == pytest.approx(1.1) and a['rotate'] == 2 and a['grade'] == {'exposure': 0.5}
    assert b['zoom'] == pytest.approx(1.65) and b['rotate'] == -1 and b['grade'] == {'exposure': 0.5, 'saturation': 0}


def test_manual_clips_keep_order_and_words(project):
    e = project(clips=[{'in': 4.8, 'out': 5.8}, {'in': 0.0, 'out': 1.0, 'transition': {'type': 'fade', 'dur': 0.2}}])
    assert e.manual and [w['w'] for w in e.words][:2] == ['the', 'best']
    assert e.dur == pytest.approx(2.0 - 0.2)


def test_screen_layout_ranges(project):
    e = project(format='presentation', screen={'video': 'raw.mp4', 'layouts': [{'at_src': 4.8, 'layout': 'screen'},
                                                                               {'at_src': 7.2, 'layout': 'speaker'}]})
    assert e.layout_ranges_src() == [(4.8, 7.2)]
    c = next(c for c in e.edl.clips if c['ka'] / 30 <= 5.0 <= c['kb'] / 30)
    (r,) = e._clip_screen_ranges(c)
    assert r[0] == pytest.approx(max(0.0, 4.8 - c['ka'] / 30), abs=1e-6)


def test_presentation_defaults_keep_more_air(project):
    i, p = project(), project(format='presentation')
    assert p.dur > i.dur
    assert p.captions_mode() == 'lines' and i.captions_mode() == 'words'


def test_graphics_follow_source_time(project):
    e = project(lower_thirds=[{'name': 'Jane', 'title': 'Lead', 'at_src': 4.8, 'dur': 2}],
                intro={'title': 'Hello', 'dur': 1.5}, end={'lines': ['Bye']})
    kinds = {k: (t, d) for k, _, t, d, *_ in e.graphics()}
    assert kinds['intro'] == (0.0, 1.5)
    assert kinds['lower_third'][0] == pytest.approx(e.edit_t(4.8))
    assert e.end()['dur'] > 0


def test_plan_is_json_serialisable(project):
    json.dumps(project().plan())


def test_nothing_left_is_an_error(project):
    with pytest.raises(SystemExit):
        project(cuts=[[0, 9]])


# ---------------------------------------------------------------- make_reel
def test_concat_line_uses_forward_slashes_and_escapes_quotes():
    line = concat_line(r"C:\Users\x\it's\a.mp4" if os.name == 'nt' else "/tmp/it's/a.mp4")
    assert '\\' not in line.replace("'\\''", '')
    assert "it'\\''s" in line and line.startswith("file '") and line.endswith("'\n")
