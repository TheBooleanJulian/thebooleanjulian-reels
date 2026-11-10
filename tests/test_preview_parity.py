"""The studio preview re-implements the edit arithmetic and the colour maths in JavaScript. This runs those
functions (taken straight from studio/index.html) in Node and checks they agree with edl.py / longform.py,
so the preview keeps showing what the export renders. Skipped when Node isn't installed."""
import json, os, re, shutil, subprocess, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from edl import Edl  # noqa: E402
import longform  # noqa: E402

NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='node not installed')
FUNCS = ['buildEdl', 'clipAt', 'srcAt', 'toEdit', 'toEditFwd', 'remapWords', 'gradeMatrix', 'rotZoom']

CASES = [
    [{'in': 0, 'out': 2}, {'in': 5, 'out': 6.5}],
    [{'in': 10, 'out': 12.4}, {'in': 0.0333, 'out': 1.9, 'transition': {'type': 'fade', 'dur': 0.5}},
     {'in': 3, 'out': 3.2, 'transition': {'type': 'dip_black', 'dur': 1.0}}, {'in': 20, 'out': 25, 'transition': {'type': 'zoom'}}],
    [{'in': 1, 'out': 1.03}, {'in': 4, 'out': 8, 'transition': {'type': 'bogus', 'dur': 0.5}}],
]
WORDS = [{'w': f'w{i}', 'start': i * 0.37, 'end': i * 0.37 + 0.3} for i in range(70)]
GRADES = [{}, {'exposure': 0.5, 'temperature': -0.4, 'tint': 0.3, 'saturation': 1.4}, {'saturation': 0}]


def page_functions():
    src = open(os.path.join(ROOT, 'studio', 'index.html'), encoding='utf-8').read()
    out = [re.search(r'^const TRANS = .*?;$', src, re.M).group(0), re.search(r'^const clamp = .*?;$', src, re.M).group(0)]
    for name in FUNCS:
        m = re.search(r'^function ' + name + r'\([^\n]*\}[ \t]*$', src, re.M)          # one-line functions
        if not m: m = re.search(r'^function ' + name + r'\(.*?^}', src, re.M | re.S)   # multi-line, up to the closing brace
        assert m, f'{name} not found in studio/index.html'
        out.append(m.group(0))
    return '\n'.join(out)


def run_js():
    code = page_functions() + f"""
const cases = {json.dumps(CASES)}, words = {json.dumps(WORDS)}, grades = {json.dumps(GRADES)};
const res = cases.map(c => {{
  const E = buildEdl(c, 30);
  const probes = [];
  for (let t = 0; t < E.dur; t += 0.137) probes.push([clipAt(E, t), srcAt(E, t)[1]]);
  const back = [0.5, 1.0, 3.1, 5.5, 11, 22, 30].map(s => [toEdit(E, s), toEditFwd(E, s)]);
  return {{ dur: E.dur, sf: E.clips.map(x => x.sf), ov: E.clips.map(x => x.ov), probes, back,
           words: remapWords(E, words).map(w => [w.w, w.start]) }};
}});
res.push(grades.map(g => gradeMatrix(g)), [rotZoom(3, 1920, 1080), rotZoom(-7.5, 1080, 1920)]);
console.log(JSON.stringify(res));
"""
    r = subprocess.run([NODE, '-e', code], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_preview_matches_engine():
    js = run_js()
    for case, got in zip(CASES, js):
        e = Edl(case, 30)
        assert got['dur'] == pytest.approx(e.dur)
        assert got['sf'] == [c['sf'] for c in e.clips] and got['ov'] == [c['ov'] for c in e.clips]
        k = 0
        t = 0.0
        while t < e.dur:
            assert got['probes'][k][0] == e.clip_at(t), (case, t)
            assert got['probes'][k][1] == pytest.approx(e.src_time(t)), (case, t)
            k += 1; t += 0.137
        for (a, b), s in zip(got['back'], [0.5, 1.0, 3.1, 5.5, 11, 22, 30]):
            want = e.to_edit(s)
            assert (a is None) == (want is None) and (a is None or a == pytest.approx(want))
            assert b == pytest.approx(e.to_edit_forward(s))
        py_words = [[w['w'], w['start']] for w in e.remap_words(WORDS)]
        assert [w[0] for w in got['words']] == [w[0] for w in py_words]
        assert [w[1] for w in got['words']] == pytest.approx([w[1] for w in py_words])
    for g, m in zip(GRADES, js[len(CASES)]):
        assert sum(m, []) == pytest.approx(sum(longform.grade_matrix(g), []))
    rz = js[len(CASES) + 1]
    assert rz[0] == pytest.approx(longform.rotate_zoom(3, 1920, 1080))
    assert rz[1] == pytest.approx(longform.rotate_zoom(-7.5, 1080, 1920))
