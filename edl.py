"""Edit decision list for interviews and presentations: ordered clips of the source, transitions between them.

A clip is {"in": s, "out": s, "transition": {"type": "fade", "dur": 0.5}, "zoom": 1.1, "focus": [x, y],
"rotate": deg, "grade": {...}}; only in/out are required. The transition belongs to the clip it leads INTO
(ignored on the first clip). Clips may be in any order and may repeat source.

Everything is snapped to whole output frames, so the separately built audio stays sample-exact. A transition
overlaps the two clips: the incoming clip starts `dur` before the outgoing one ends, so the edit gets shorter.
studio/index.html mirrors this arithmetic for the live preview; keep the two in step (tests/test_edl.py).
"""
import math

TRANSITIONS = {           # name in the preset -> ffmpeg xfade transition
    'fade': 'fade', 'dip_black': 'fadeblack', 'dip_white': 'fadewhite', 'wipe_left': 'wipeleft',
    'wipe_right': 'wiperight', 'slide_left': 'slideleft', 'slide_up': 'slideup', 'zoom': 'zoomin',
    'circle': 'circleopen',
}


class Edl:
    def __init__(self, clips, fps):
        self.fps = f = fps
        self.clips = []
        for c in clips:
            ka, kb = round(float(c['in']) * f), round(float(c['out']) * f)
            if kb - ka >= 2: self.clips.append({**c, 'ka': ka, 'kb': kb})
        start, prev_len = 0, 0
        for i, c in enumerate(self.clips):
            n = c['kb'] - c['ka']
            t = c.get('transition') or {}
            ov = 0
            if i and t.get('type') in TRANSITIONS:
                ov = max(0, min(round(float(t.get('dur', 0.5)) * f), n // 2, prev_len // 2))
            c['ov'] = ov
            start -= ov
            c['sf'] = start                       # first frame on the edited timeline
            start += n
            prev_len = n
        self.n_frames = start
        self.dur = start / f

    # -- views
    @property
    def segs(self): return [(c['ka'] / self.fps, c['kb'] / self.fps) for c in self.clips]

    @property
    def starts(self): return [c['sf'] / self.fps for c in self.clips]

    def ranges(self):
        """(start, end) of each clip on the edited timeline (they overlap during transitions)."""
        return [(c['sf'] / self.fps, (c['sf'] + c['kb'] - c['ka']) / self.fps) for c in self.clips]

    def monotonic(self):
        """Clips in source order with no transitions: the cheap single-pass render applies."""
        return all(c['ov'] == 0 for c in self.clips) and all(
            a['kb'] <= b['ka'] for a, b in zip(self.clips, self.clips[1:]))

    # -- mapping
    def clip_at(self, t):
        """Index of the clip showing at edited time t (during a transition: the incoming clip)."""
        k = int(math.floor(t * self.fps + 1e-6))
        idx = 0
        for i, c in enumerate(self.clips):
            if c['sf'] <= k: idx = i
            else: break
        return idx

    def src_time(self, t):
        i = self.clip_at(t)
        c = self.clips[i]
        local = min(max(0.0, t - c['sf'] / self.fps), (c['kb'] - c['ka'] - 1) / self.fps)
        return c['ka'] / self.fps + local

    def to_edit(self, src):
        """Source time -> edited time (first clip containing it), None if no clip contains it."""
        for c in self.clips:
            a, b = c['ka'] / self.fps, c['kb'] / self.fps
            if a <= src <= b: return c['sf'] / self.fps + src - a
        return None

    def to_edit_forward(self, src):
        """Like to_edit, but a moment that was cut maps to the next kept moment in source order."""
        t = self.to_edit(src)
        if t is not None: return t
        later = [c for c in self.clips if c['ka'] / self.fps > src]
        if later: return min(later, key=lambda c: c['ka'])['sf'] / self.fps
        return self.dur

    def remap_words(self, words):
        out = []
        for w in words:
            m = (w['start'] + w['end']) / 2
            a = self.to_edit(m)
            if a is None: continue
            d = w['end'] - w['start']
            st = self.to_edit(w['start'])
            st = a - d / 2 if st is None else st
            out.append({**w, 'start': st, 'end': st + d})
        return sorted(out, key=lambda w: w['start'])

    def runs(self):
        """Groups of consecutive clip indices joined by plain cuts; transitions sit between groups."""
        groups = []
        for i, c in enumerate(self.clips):
            if not groups or c['ov']: groups.append([i])
            else: groups[-1].append(i)
        return groups


def cut_range(clips, a, b):
    """Remove source range [a, b] from every clip, keeping order (a clip may split in two)."""
    out = []
    for c in clips:
        x, y = float(c['in']), float(c['out'])
        if b <= x or a >= y: out.append(c); continue
        if a > x: out.append({**c, 'out': a})
        if b < y: out.append({**{k: v for k, v in c.items() if k != 'transition'}, 'in': b})
    return out


def split_at(clips, t, fps):
    """Split the clip under edited time t (seconds) into two at that frame."""
    e = Edl(clips, fps)
    i = e.clip_at(t)
    c = e.clips[i]
    src = round(e.src_time(t) * fps) / fps
    if src - c['ka'] / fps < 2 / fps or c['kb'] / fps - src < 2 / fps: return clips
    raw = [{k: v for k, v in x.items() if k not in ('ka', 'kb', 'ov', 'sf')} for x in e.clips]
    left = {**raw[i], 'out': src}
    right = {k: v for k, v in raw[i].items() if k != 'transition'}
    right['in'] = src
    return raw[:i] + [left, right] + raw[i + 1:]
