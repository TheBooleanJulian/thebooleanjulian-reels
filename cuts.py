"""Rough cut: decide which parts of the recording to keep, and remap word times onto the edited timeline."""
import re, subprocess


def detect_silences(video, noise_db=-35, min_dur=0.45):
    """ffmpeg silencedetect -> [(start, end)] of silent stretches."""
    r = subprocess.run(['ffmpeg', '-hide_banner', '-i', video, '-af', f'silencedetect=noise={noise_db}dB:d={min_dur}',
                        '-vn', '-f', 'null', '-'], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r'silence_start: ([\d.]+)', r.stderr)]
    ends = [float(x) for x in re.findall(r'silence_end: ([\d.]+)', r.stderr)]
    dur = re.search(r'Duration: (\d+):(\d+):([\d.]+)', r.stderr)
    total = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)) if dur else None
    if len(starts) > len(ends): ends.append(total)
    return list(zip(starts, ends)), total


def segments_from_words(words, max_gap=0.5, pad=0.12, lead=0.15, tail=0.25, breaks=()):
    """Keep speech, drop pauses longer than max_gap. pad stays on each side of a cut.
    breaks: [(start, end)] source ranges that were removed (hand cuts, filler words). A gap containing one is
    always cut, so a short removed word can't survive inside a continuous segment."""
    if not words: return []
    segs, s, e = [], words[0]['start'], words[0]['end']
    for w in words[1:]:
        removed = any(e - 0.01 <= b0 and b1 <= w['start'] + 0.01 for b0, b1 in breaks)
        if w['start'] - e > max_gap or removed:
            segs.append((max(0, s - lead), e + tail)); s = w['start']
        e = max(e, w['end'])
    segs.append((max(0, s - lead), e + tail))
    out = _merge(segs, pad)
    return apply_manual_cuts(out, breaks) if breaks else out     # lead/tail air must not re-join a removed range


def segments_from_silences(silences, total, pad=0.08, min_keep=0.3):
    keep, cur = [], 0.0
    for a, b in silences:
        if a - cur >= min_keep: keep.append((cur, a + pad))
        cur = max(0.0, b - pad)
    if total - cur >= min_keep: keep.append((cur, total))
    return _merge(keep, 0.0)


def _merge(segs, pad):
    out = []
    for a, b in sorted(segs):
        if out and a - out[-1][1] <= pad * 2: out[-1] = (out[-1][0], max(out[-1][1], b))
        else: out.append((a, b))
    return out


def apply_manual_cuts(segs, cuts):
    """Remove [start, end] ranges (source time) from keep-segments, e.g. a repeated take."""
    for c0, c1 in cuts:
        nxt = []
        for a, b in segs:
            if c1 <= a or c0 >= b: nxt.append((a, b)); continue
            if c0 > a: nxt.append((a, c0))
            if c1 < b: nxt.append((c1, b))
        segs = nxt
    return segs


class Timeline:
    """Keep-segments (source time) laid end to end = the edited timeline."""
    def __init__(self, segs):
        self.segs = [(a, b) for a, b in segs if b - a > 0.02]
        self.starts, acc = [], 0.0
        for a, b in self.segs: self.starts.append(acc); acc += b - a
        self.dur = acc

    def src_time(self, t):
        for (a, b), s in zip(self.segs, self.starts):
            if t < s + (b - a): return a + max(0.0, t - s)
        return self.segs[-1][1]

    def to_edit(self, ts):
        """source time -> edited time (None if that moment was cut)."""
        for (a, b), s in zip(self.segs, self.starts):
            if a <= ts <= b: return s + ts - a
        return None

    def remap_words(self, words):
        out = []
        for w in words:
            m = (w['start'] + w['end']) / 2
            a = self.to_edit(m)
            if a is None: continue
            d = w['end'] - w['start']
            st = self.to_edit(w['start']); st = a - d / 2 if st is None else st
            out.append({**w, 'start': st, 'end': st + d})
        return out
