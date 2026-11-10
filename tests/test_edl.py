"""Edit decision list arithmetic, look maths and text overlay events. studio/index.html mirrors the Edl
arithmetic for its live preview; the expected numbers here are the contract both sides follow."""
import math, os, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from edl import Edl, cut_range, split_at  # noqa: E402
import longform, subs  # noqa: E402

F = 30


def test_plain_cuts_lay_end_to_end():
    e = Edl([{'in': 0, 'out': 2}, {'in': 5, 'out': 6}], F)
    assert e.starts == [0.0, 2.0] and e.dur == pytest.approx(3.0)
    assert e.monotonic() and e.runs() == [[0, 1]]


def test_transition_overlaps_and_shortens():
    e = Edl([{'in': 0, 'out': 2}, {'in': 5, 'out': 7, 'transition': {'type': 'fade', 'dur': 0.5}}], F)
    assert e.starts == [0.0, 1.5] and e.dur == pytest.approx(3.5)
    assert not e.monotonic() and e.runs() == [[0], [1]]
    assert e.clip_at(1.6) == 1                       # during the overlap the incoming clip is "current"
    assert e.src_time(1.6) == pytest.approx(5.1)


def test_transition_is_clamped_to_half_the_shorter_clip():
    e = Edl([{'in': 0, 'out': 0.4}, {'in': 1, 'out': 3, 'transition': {'type': 'dip_black', 'dur': 2}}], F)
    assert e.clips[1]['ov'] == 6                     # 0.4 s clip -> at most 0.2 s overlap


def test_unknown_or_first_transition_is_a_cut():
    e = Edl([{'in': 0, 'out': 1, 'transition': {'type': 'fade', 'dur': 0.5}},
             {'in': 2, 'out': 3, 'transition': {'type': 'nonsense', 'dur': 0.5}}], F)
    assert [c['ov'] for c in e.clips] == [0, 0]


def test_reordered_clips_map_source_to_edit():
    e = Edl([{'in': 10, 'out': 12}, {'in': 0, 'out': 1}], F)
    assert e.to_edit(10.5) == pytest.approx(0.5) and e.to_edit(0.5) == pytest.approx(2.5)
    assert e.to_edit(5) is None and e.to_edit_forward(5) == pytest.approx(0.0)
    assert not e.monotonic()


def test_frames_are_whole():
    e = Edl([{'in': 0.011, 'out': 1.019}], F)
    assert (e.clips[0]['ka'], e.clips[0]['kb']) == (0, 31)


def test_cut_range_splits_and_keeps_order():
    clips = [{'in': 10, 'out': 20, 'zoom': 1.2}, {'in': 0, 'out': 5}]
    out = cut_range(clips, 12, 14)
    assert [(c['in'], c['out']) for c in out] == [(10, 12), (14, 20), (0, 5)]
    assert out[1]['zoom'] == 1.2


def test_split_at_playhead():
    clips = [{'in': 0, 'out': 4, 'transition': {'type': 'fade', 'dur': 0.3}}]
    out = split_at(clips, 1.5, F)
    assert [(c['in'], c['out']) for c in out] == [(0, 1.5), (1.5, 4)]
    assert 'transition' not in out[1]


def test_neutral_grade_adds_no_filter():
    assert longform.grade_filter({}) == ''
    assert longform.grade_filter({'exposure': 0, 'contrast': 1, 'saturation': 1}) == ''
    m = longform.grade_matrix({'saturation': 0})
    assert all(abs(m[r][0] - 0.2126) < 1e-9 for r in range(3))       # greyscale: every row = luma weights


def test_rotate_zoom_covers_corners():
    assert longform.rotate_zoom(0, 1920, 1080) == pytest.approx(1.0)
    s = longform.rotate_zoom(3, 1920, 1080)
    a = math.radians(3)
    assert s * 1920 >= 1920 * math.cos(a) + 1080 * math.sin(a) - 1e-6
    assert s * 1080 >= 1920 * math.sin(a) + 1080 * math.cos(a) - 1e-6


@pytest.mark.parametrize('ain, aout', [(i, o) for i in subs.ANIM_IN for o in subs.ANIM_OUT])
def test_text_events_cover_the_whole_span(ain, aout):
    item = {'text': 'Hello there', 'x': 0.5, 'y': 0.3, 'anim_in': ain, 'anim_out': aout}
    ev = subs.text_events(0, item, 2.0, 3.0, 1080, 1920)
    times = [l.split(',')[1:3] for l in ev]

    def sec(s): h, m, x = s.split(':'); return int(h) * 3600 + int(m) * 60 + float(x)
    spans = [(sec(a), sec(b)) for a, b in times]
    assert spans[0][0] == pytest.approx(2.0) and spans[-1][1] == pytest.approx(5.0)
    for (a0, b0), (a1, _b1) in zip(spans, spans[1:]):
        assert b0 == pytest.approx(a1, abs=0.011)     # in -> hold -> out, no gap, no overlap


def test_build_ass_with_texts_only():
    ass = subs.build_ass([], 'off', 1080, 1920, None, longform.COLORS,
                         texts=[({'text': 'Hi', 'font': 'mono', 'color': 'cyan', 'box': 0.5}, 0.0, 2.0)])
    assert 'Style: T0,Share Tech Mono' in ass and 'Style: Cap' not in ass and ass.count('Dialogue:') == 3
