"""Interviews and presentations: transcript-driven full edits with a clip timeline, rendered by ffmpeg.

  python longform.py projects/<name>/preset.json --plan        # JSON summary of the edit (the studio reads it)
  python longform.py projects/<name>/preset.json --roughcut    # clips + transitions + sound, half size -> work/roughcut.mp4
  python longform.py projects/<name>/preset.json --stills      # graphics preview sheet -> work/stills/sheet.jpg
  python longform.py projects/<name>/preset.json --build       # export -> exports/<file>.mp4 (+ .srt, .chapters.txt)
  ... --build --draft                                           # half size, fast encode, for checking timing
  python longform.py projects/<name>/preset.json --proxy       # small H.264 copy of the source for the studio player
  python longform.py projects/<name>/preset.json --beats       # music beat times on the edited timeline (JSON)
  python longform.py projects/<name>/preset.json --graphics DIR --width 540   # graphic PNGs for the studio preview
make_reel.py forwards --roughcut / --stills / --build here when the preset says "format": "interview" | "presentation".

The edit is a list of clips (edl.py). Without "clips" in the preset they come from the transcript: speech kept,
pauses trimmed, fillers and hand cuts removed. Once the timeline is edited (studio), "clips" holds them explicitly:
any order, trims, transitions, per-clip framing and colour.

Render: (1) every clip on its own, framed / rotated / graded / with the slides layout, in parallel and cached by
content, so re-exporting after a text change reuses every clip; (2) clips joined: plain cuts by the concat demuxer,
transitions by xfade; (3) one pass for b-roll, graphics, captions and text (libass), end card, music, loudness and
the export encode. Cuts are whole frames, so the audio (sliced here, crossfaded at transitions) stays sample-exact.
"""
import argparse, hashlib, json, math, os, re, shutil, subprocess, sys, tempfile, wave
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from vio import ffmpeg_exe
from cuts import segments_from_words, apply_manual_cuts
from edl import Edl, TRANSITIONS
from reel import STYLE, CYAN, MAG, WHITE
import cards, subs

ROOT = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(ROOT, 'fonts')
CACHE = os.environ.get('TBJ_REELS_CACHE') or os.path.join(os.path.expanduser('~'), '.tbj-reels', 'cache')
FORMATS = ('interview', 'presentation')
SIZES = {'9:16': (1080, 1920), '16:9': (1920, 1080), '1:1': (1080, 1080), '4:5': (1080, 1350)}
SR = 48000
LF = STYLE['longform']
COLORS = {'white': WHITE, 'cyan': CYAN, 'magenta': MAG, 'black': (0, 0, 0)}
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.bmp')
QUALITY = {'high': (18, 20), 'standard': (21, 23), 'small': (25, 27)}      # crf for (h264, h265)
DEFAULTS = {
    'interview': {'aspect': '9:16', 'captions': 'words', 'punch': {'mode': 'alternate', 'scale': 1.12, 'min_seg': 1.2},
                  'trim': {'mode': 'words', 'max_gap': 0.6, 'pad': 0.1, 'lead': 0.12, 'tail': 0.22, 'fillers': True}},
    'presentation': {'aspect': '16:9', 'captions': 'lines', 'punch': {'mode': 'off'},
                     'trim': {'mode': 'words', 'max_gap': 1.4, 'pad': 0.15, 'lead': 0.3, 'tail': 0.5, 'fillers': False}},
}


def even(v): return max(2, int(round(v / 2)) * 2)


def probe(path):
    """Duration / size / fps / audio from `ffmpeg -i` (no ffprobe needed)."""
    r = subprocess.run([ffmpeg_exe(), '-hide_banner', '-i', path], capture_output=True, text=True, errors='replace')
    err = r.stderr
    info = {'duration': None, 'width': None, 'height': None, 'fps': None, 'audio': ' Audio:' in err}
    m = re.search(r'Duration: (\d+):(\d+):([\d.]+)', err)
    if m: info['duration'] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r'Video:.*?, (\d{2,5})x(\d{2,5})', err)
    if m: info['width'], info['height'] = int(m.group(1)), int(m.group(2))
    m = re.search(r'([\d.]+) fps', err)
    if m: info['fps'] = float(m.group(1))
    if re.search(r'rotation of -?90', err) and info['width']:
        info['width'], info['height'] = info['height'], info['width']
    return info


def ffmpeg_major():
    out = subprocess.run([ffmpeg_exe(), '-hide_banner', '-version'], capture_output=True, text=True).stdout
    m = re.match(r'ffmpeg version n?(\d+)\.', out)
    return int(m.group(1)) if m else 99           # git builds ("N-12345") are new


def graph_flag(path):
    return ['-/filter_complex', path] if ffmpeg_major() >= 7 else ['-filter_complex_script', path]


def expr_ranges(var, ranges, f):
    """Frame-exact enable expression: ranges are (start, end) seconds on a frame grid."""
    h = 0.5 / f
    return '+'.join(f'between({var},{a - h:.4f},{b - h:.4f})' for a, b in ranges) or '0'


def resolve_style(block, W, H, overrides=None):
    st = {k: cards.pick(v, W, H) for k, v in block.items() if not k.startswith('_')}
    st.update({k: v for k, v in (overrides or {}).items() if k != 'mode'})
    return st


# ---------------------------------------------------------------- look: rotation and colour (mirrored in the studio)
def rotate_zoom(deg, w, h):
    """Zoom that hides the corners after rotating a w x h frame by deg about its centre."""
    a = abs(math.radians(deg))
    return math.cos(a) + max(w / h, h / w) * math.sin(a)


def grade_matrix(g):
    """3x3 RGB matrix for exposure (EV), temperature, tint and saturation; contrast is applied after it.
    studio/index.html builds the same matrix for its SVG preview filter, so the preview matches the render."""
    g = g or {}
    e = 2.0 ** float(g.get('exposure', 0) or 0)
    t, ti, s = float(g.get('temperature', 0) or 0), float(g.get('tint', 0) or 0), float(g.get('saturation', 1) if g.get('saturation') is not None else 1)
    gains = (e * (1 + 0.25 * t), e * (1 - 0.15 * ti), e * (1 - 0.25 * t))
    lr, lg, lb = 0.2126, 0.7152, 0.0722
    S = [[lr + (1 - lr) * s, lg - lg * s, lb - lb * s],
         [lr - lr * s, lg + (1 - lg) * s, lb - lb * s],
         [lr - lr * s, lg - lg * s, lb + (1 - lb) * s]]
    return [[S[r][c] * gains[c] for c in range(3)] for r in range(3)]


def grade_filter(g):
    g = g or {}
    m = grade_matrix(g)
    c = float(g.get('contrast', 1) if g.get('contrast') is not None else 1)
    ident = all(abs(m[r][k] - (1.0 if r == k else 0.0)) < 1e-4 for r in range(3) for k in range(3))
    parts = []
    if not ident:
        names = ['rr', 'rg', 'rb', 'gr', 'gg', 'gb', 'br', 'bg', 'bb']
        parts.append('colorchannelmixer=' + ':'.join(f'{n}={m[i // 3][i % 3]:.5f}' for i, n in enumerate(names)))
    if abs(c - 1) > 1e-4:
        lut = f"clip((val-128)*{c:.4f}+128,0,255)"
        parts.append(f"lutrgb=r='{lut}':g='{lut}':b='{lut}'")
    return ('format=rgb24,' + ','.join(parts)) if parts else ''


class Edit:
    """Every decision of the edit, no rendering: clips, edited word times, graphics, layouts, export settings."""

    def __init__(self, cfg_path):
        self.path = os.path.abspath(cfg_path)
        self.dir = os.path.dirname(self.path)
        with open(self.path, encoding='utf-8') as fh: self.cfg = c = json.load(fh)
        self.format = c.get('format', 'interview')
        if self.format not in FORMATS: raise SystemExit(f'format must be one of {FORMATS}')
        self.d = DEFAULTS[self.format]
        self.name = c.get('name') or os.path.splitext(os.path.basename(self.path))[0]
        in_project = os.path.basename(os.path.dirname(self.dir)) == 'projects'
        self.work = os.path.join(self.dir, 'work') if in_project else os.path.join(ROOT, 'scenes_out', self.name)
        self.exports = os.path.join(self.dir, 'exports') if in_project else os.path.join(ROOT, 'out')
        self.cache = os.path.join(CACHE, self.name)
        self.warnings = []
        self.video = self.resolve(c['video'])
        if not os.path.exists(self.video): raise SystemExit(f'video not found: {self.video}')
        self.info = probe(self.video)
        out = c.get('output', {})
        self.ex = c.get('export') or {}
        W0, H0 = tuple(out['size']) if out.get('size') else SIZES[out.get('aspect', self.d['aspect'])]
        k = float(self.ex.get('short_side') or min(W0, H0)) / min(W0, H0)
        self.W, self.H = even(W0 * k), even(H0 * k)
        self.fps = int(self.ex.get('fps') or out.get('fps', 30))
        if SR % self.fps: raise SystemExit('frame rate must divide 48000 (24, 25, 30, 50 or 60)')
        self.focus = out.get('focus', [0.5, 0.4])
        self.zoom = max(1.0, float(out.get('zoom', 1.0) or 1.0))
        self.rotate = float(out.get('rotate', 0) or 0)
        self.grade = c.get('grade') or {}
        self.screen = c.get('screen') or None
        if self.screen:
            self.screen_path = self.resolve(self.screen['video'])
            if not os.path.exists(self.screen_path): raise SystemExit(f'screen video not found: {self.screen_path}')
        self.all_words = self._load_words()
        self._plan()

    # ---------------------------------------------------------------- inputs
    def resolve(self, p):
        if os.path.isabs(p): return os.path.normpath(p)
        for base in (self.dir, ROOT):
            cand = os.path.join(base, p)
            if os.path.exists(cand): return os.path.normpath(cand)
        return os.path.normpath(os.path.join(self.dir, p))

    def _load_words(self):
        t = self.cfg.get('transcript')
        if not t:
            self.warnings.append('no transcript: the whole clip is kept and there are no captions'); return []
        path = self.resolve(t)
        if not os.path.exists(path):
            self.warnings.append(f'transcript not found ({t}): transcribe first'); return []
        with open(path, encoding='utf-8') as fh: return json.load(fh)['words']

    # ---------------------------------------------------------------- the cut
    def _plan(self):
        c = self.cfg
        total = self.info['duration'] or 0.0
        clip = c.get('clip', {})
        lo, hi = float(clip.get('start', 0.0) or 0.0), float(clip.get('end') or total)
        tr = {**self.d['trim'], **c.get('trim', {})}
        fillers = {f.lower() for f in LF.get('fillers', [])} if tr.get('fillers') else set()
        cuts = [(float(a), float(b)) for a, b in c.get('cuts', [])]
        idx = [i for i, w in enumerate(self.all_words) if w['start'] >= lo - 0.01 and w['end'] <= hi + 0.01]

        def in_cut(w):
            m = (w['start'] + w['end']) / 2
            return any(a <= m <= b for a, b in cuts)
        self.filler_idx = [i for i in idx if re.sub(r'\W+', '', self.all_words[i]['w'].lower()) in fillers
                           and not in_cut(self.all_words[i])]
        fset = set(self.filler_idx)
        kept = [self.all_words[i] for i in idx if i not in fset and not in_cut(self.all_words[i])]
        removed = cuts + [(self.all_words[i]['start'], self.all_words[i]['end']) for i in self.filler_idx]
        mode = tr.get('mode', 'words')
        if mode == 'words' and kept:
            segs = segments_from_words(kept, tr['max_gap'], tr['pad'], tr['lead'], tr['tail'], breaks=removed)
        elif mode == 'head_tail' and kept:
            segs = [(kept[0]['start'] - tr['lead'], kept[-1]['end'] + tr['tail'])]
        else:
            segs = [(lo, hi)]
        segs = [(max(lo, a), min(hi, b)) for a, b in segs if min(hi, b) > max(lo, a)]
        self.auto_segs = [(round(a, 4), round(b, 4)) for a, b in apply_manual_cuts(segs, removed)]
        self.manual = isinstance(c.get('clips'), list) and len(c['clips']) > 0
        clips = c['clips'] if self.manual else [{'in': a, 'out': b} for a, b in self.auto_segs]
        if total:
            clips = [{**x, 'in': max(0.0, float(x['in'])), 'out': min(total, float(x['out']))} for x in clips]
        self.edl = Edl(clips, self.fps)
        if not self.edl.clips: raise SystemExit('nothing left after the cuts: check clip / cuts / trim')
        self.dur = self.edl.dur
        self.words = self.edl.remap_words(self.all_words if self.manual else kept)
        self.source_range = (lo, hi)

    def edit_t(self, src):
        return self.edl.to_edit_forward(src)

    def span(self, item, default_dur):
        t = self.edit_t(float(item['at_src'])) if 'at_src' in item else float(item.get('at', 0.0) or 0.0)
        d = self.edit_t(float(item['end_src'])) - t if 'end_src' in item else float(item.get('dur', default_dur) or default_dur)
        t = min(max(0.0, t), self.dur)
        return t, max(0.0, min(d, self.dur - t))

    # ---------------------------------------------------------------- per-clip look
    def punch(self):
        return {**self.d['punch'], **(self.cfg.get('punch') or {})}

    def clip_looks(self):
        """Resolved framing / rotation / grade per clip: project look, then the clip's own overrides.
        Clips without their own zoom alternate wide / tight at each cut when punch mode is 'alternate'."""
        p = self.punch()
        out, tight = [], False
        for i, c in enumerate(self.edl.clips):
            dur = (c['kb'] - c['ka']) / self.fps
            if i and dur >= p.get('min_seg', 1.2): tight = not tight
            z = float(c['zoom']) if c.get('zoom') else (float(p.get('scale', 1.12)) if p.get('mode') == 'alternate' and tight else 1.0)
            out.append({'zoom': self.zoom * z, 'focus': c.get('focus') or self.focus,
                        'rotate': float(c['rotate']) if c.get('rotate') is not None else self.rotate,
                        'grade': {**self.grade, **(c.get('grade') or {})} if c.get('grade') else self.grade})
        return out

    def layout_ranges_src(self):
        """Source-time ranges shown in the 'screen' layout (slides full, speaker inset)."""
        if not self.screen: return []
        sw = sorted((float(x.get('at_src', x.get('at', 0)) or 0), x['layout']) for x in self.screen.get('layouts', []))
        cur, t0, out = self.screen.get('default', 'speaker'), 0.0, []
        for t, lay in sw + [(1e12, None)]:
            if cur == 'screen' and t > t0: out.append((t0, t))
            if lay: cur, t0 = lay, t
        return out

    def chapters(self):
        ch = [((self.edit_t(float(x['at_src'])) if 'at_src' in x else float(x.get('at', 0) or 0)), x['title'])
              for x in self.cfg.get('chapters', [])]
        return sorted(ch)

    def captions_mode(self):
        cap = self.cfg.get('captions', {})
        if cap is False: return 'off'
        return cap.get('mode', self.d['captions']) if isinstance(cap, dict) else self.d['captions']

    def caption_style(self, W, H, mode):
        block = LF['captions_lines' if mode == 'lines' else 'captions_words']
        cap = self.cfg.get('captions') if isinstance(self.cfg.get('captions'), dict) else {}
        return resolve_style(block, W, H, cap)

    def graphics(self):
        """[(kind, draw(W, H) -> RGBA, t, dur, anim, key, idx)] on the edited timeline."""
        c, out = self.cfg, []
        intro = c.get('intro') or {}
        intro_end = 0.0
        if intro.get('title'):
            d = min(float(intro.get('dur', 3.0) or 3.0), self.dur)
            out.append(('intro', lambda W, H, i=intro: cards.intro_card(W, H, i['title'], i.get('subtitle', '')), 0.0, d,
                        intro.get('anim', 'fade'), 'intro', 0))
            intro_end = d
        for k, lt in enumerate(c.get('lower_thirds', [])):
            t, d = self.span(lt, LF['lower_third']['dur'])
            out.append(('lower_third', lambda W, H, x=lt: cards.lower_third(W, H, x['name'], x.get('title', '')), t, d,
                        lt.get('anim', 'slide_right'), 'lower_thirds', k))
        for k, tc in enumerate(c.get('titles', [])):
            t, d = self.span(tc, LF['card']['dur'])
            out.append(('title', lambda W, H, x=tc: cards.title_card(W, H, x['text'], x.get('label', '')), t, d,
                        tc.get('anim', 'fade'), 'titles', k))
        if c.get('chapter_cards', self.format == 'presentation'):
            src = c.get('chapters', [])
            order = sorted(range(len(src)), key=lambda k: self.edit_t(float(src[k].get('at_src', src[k].get('at', 0)) or 0)))
            for n, k in enumerate(order, 1):
                t = self.edit_t(float(src[k].get('at_src', src[k].get('at', 0)) or 0))
                if t < intro_end: continue
                out.append(('chapter', lambda W, H, n=n, x=src[k]['title']: cards.chapter_card(W, H, n, x), t,
                            min(LF['chapter']['dur'], self.dur - t), src[k].get('anim', 'fade'), 'chapters', k))
        return [g for g in out if g[3] > 0.1]

    def texts(self):
        """Free text overlays: (item, t, dur)."""
        out = []
        for item in self.cfg.get('texts', []):
            if not str(item.get('text', '')).strip(): continue
            t, d = self.span(item, 3.0)
            if d > 0.1: out.append((item, t, d))
        return out

    def end(self):
        e = self.cfg.get('end')
        if not e: return None
        return {'lines': e.get('lines', []), 'handle': e.get('handle', STYLE.get('handle', '')),
                'dur': float(e.get('dur', LF['end']['dur']) or LF['end']['dur'])}

    def broll(self):
        out = []
        for b in self.cfg.get('broll', []):
            path = self.resolve(b['file']) if b.get('file') else ''
            if not path or not os.path.exists(path):
                self.warnings.append(f'b-roll not found, skipped: {b.get("file")}'); continue
            t, d = self.span(b, 3.0)
            if d > 0.2: out.append({'path': path, 't': t, 'd': d, 'from': float(b.get('from', 0.0) or 0),
                                    'image': path.lower().endswith(IMAGE_EXT)})
        return out

    def music_path(self):
        m = self.cfg.get('music') or {}
        if not m.get('file'): return None
        p = self.resolve(m['file'])
        return p if os.path.exists(p) else None

    def plan(self):
        clips = []
        for c, (a, b), look in zip(self.edl.clips, self.edl.ranges(), self.clip_looks()):
            clips.append({**{k: v for k, v in c.items() if k not in ('ka', 'kb', 'ov', 'sf')},
                          'in': c['ka'] / self.fps, 'out': c['kb'] / self.fps, 'start': round(a, 4), 'end': round(b, 4),
                          'overlap': c['ov'] / self.fps, 'look': look})
        return {'name': self.name, 'format': self.format, 'video': self.video, 'size': [self.W, self.H], 'fps': self.fps,
                'source': {k: self.info[k] for k in ('width', 'height', 'fps', 'audio')},
                'proxy': self.proxy_path() if os.path.exists(self.proxy_path()) else None,
                'source_duration': self.info['duration'], 'source_range': self.source_range,
                'edit_duration': round(self.dur, 3), 'manual': self.manual, 'clips': clips,
                'auto_segments': self.auto_segs, 'segments': [[round(a, 3), round(b, 3)] for a, b in self.edl.segs],
                'cuts_count': len(self.edl.clips) - 1, 'words_total': len(self.all_words), 'words_kept': len(self.words),
                'filler_idx': self.filler_idx, 'captions': self.captions_mode(),
                'caption_style': self.caption_style(self.W, self.H, self.captions_mode())
                if self.captions_mode() != 'off' else None,
                'look': {'zoom': self.zoom, 'focus': self.focus, 'rotate': self.rotate, 'grade': self.grade},
                'punch': self.punch(), 'export': self.ex,
                'output_file': os.path.basename(self.output_path()),
                'chapters': [{'t': round(t, 2), 'title': x} for t, x in self.chapters()],
                'graphics': [{'kind': k, 't': round(t, 2), 'dur': round(d, 2), 'anim': an, 'key': key, 'idx': i}
                             for k, _, t, d, an, key, i in self.graphics()],
                'end': self.end(), 'music': self.music_path(), 'warnings': self.warnings}

    def proxy_path(self): return os.path.join(self.cache, 'proxy.mp4')

    # ---------------------------------------------------------------- audio
    def edited_audio(self, tmp):
        """The edited soundtrack: source audio sliced at frame-aligned clip bounds, 12 ms fades at plain cuts,
        equal-power crossfades at transitions."""
        src, off = self.video, 0.0
        if (self.cfg.get('audio') or {}).get('source') == 'screen' and self.screen:
            src, off = self.screen_path, float(self.screen.get('offset', 0.0))
        out = os.path.join(tmp, 'edit.wav')
        spf, fade = SR // self.fps, int(0.012 * SR)
        clips = self.edl.clips
        with wave.open(out, 'wb') as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
            if not probe(src)['audio']:
                w.writeframes(b'\0' * 4 * self.edl.n_frames * spf); return out
            full = os.path.join(tmp, 'full.wav')
            subprocess.check_call([ffmpeg_exe(), '-v', 'error', '-y', '-i', src, '-vn', '-ac', '2', '-ar', str(SR),
                                   '-c:a', 'pcm_s16le', full])
            with wave.open(full, 'rb') as r:
                n_src, shift = r.getnframes(), int(round(off * SR))
                pending = np.zeros((0, 2), np.float32)
                for i, c in enumerate(clips):
                    a, n = c['ka'] * spf + shift, (c['kb'] - c['ka']) * spf
                    buf = np.zeros((n, 2), np.float32)
                    lo, hi = max(0, a), min(n_src, a + n)
                    if hi > lo:
                        r.setpos(lo)
                        got = np.frombuffer(r.readframes(hi - lo), '<i2').reshape(-1, 2).astype(np.float32)
                        buf[lo - a:lo - a + len(got)] = got
                    ov_in = c['ov'] * spf
                    ov_out = clips[i + 1]['ov'] * spf if i + 1 < len(clips) else 0
                    k = min(fade, n // 2)
                    ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)[:, None]
                    if k and not ov_in: buf[:k] *= ramp
                    if k and not ov_out: buf[n - k:] *= ramp[::-1]
                    if ov_in:                                  # equal-power crossfade with the previous tail
                        x = np.linspace(0.0, 1.0, ov_in, dtype=np.float32)[:, None]
                        mix = pending * np.cos(x * np.pi / 2) + buf[:ov_in] * np.sin(x * np.pi / 2)
                        w.writeframes(np.clip(mix, -32768, 32767).astype('<i2').tobytes())
                        buf = buf[ov_in:]
                    keep = len(buf) - ov_out
                    w.writeframes(np.clip(buf[:keep], -32768, 32767).astype('<i2').tobytes())
                    pending = buf[keep:]
            os.remove(full)
        return out

    # ---------------------------------------------------------------- video: one clip
    def _frame_chain(self, look, W, H, src_label, out_label, L, screen=None):
        """Rotate -> grade -> cover-fit at zoom / focus (-> slides layout). Appends filter lines to L."""
        fx, fy = look['focus']
        chain = []
        if abs(look['rotate']) > 1e-3:
            s = rotate_zoom(look['rotate'], self.info['width'] or W, self.info['height'] or H)
            chain.append(f"rotate=a={math.radians(look['rotate']):.6f}:ow=iw:oh=ih:c=black,"
                         f"scale=trunc(iw*{s:.5f}/2)*2:trunc(ih*{s:.5f}/2)*2,crop=trunc(iw/{s:.5f}/2)*2:trunc(ih/{s:.5f}/2)*2")
        gf = grade_filter(look['grade'])
        if gf: chain.append(gf)
        Wz, Hz = even(W * look['zoom']), even(H * look['zoom'])
        chain.append(f"scale={Wz}:{Hz}:force_original_aspect_ratio=increase,"
                     f"crop=w={W}:h={H}:x='max(0,min(iw-ow,{fx}*iw-ow/2))':y='max(0,min(ih-oh,{fy}*ih-oh/2))',setsar=1")
        if not screen:
            L.append(f"[{src_label}]{','.join(chain)},format=yuv420p[{out_label}]")
            return
        scr_label, ranges = screen
        st = LF['inset']
        iw, m, b = even(W * st['width']), round(W * st['margin']), max(2, round(H * 0.002))
        en = expr_ranges('t', ranges, self.fps)
        L.append(f"[{src_label}]{','.join(chain)},split[sp0][sp1]")
        L.append(f"[{scr_label}]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                 f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x05060d,setsar=1[scr]")
        L.append(f'[sp1]scale={iw}:-2,pad=iw+{2 * b}:ih+{2 * b}:{b}:{b}:color=0x6370b6[ins]')
        L.append(f"[sp0][scr]overlay=0:0:enable='{en}'[sv]")
        L.append(f"[sv][ins]overlay=x=W-w-{m}:y={m}:enable='{en}',format=yuv420p[{out_label}]")

    def _clip_screen_ranges(self, c):
        a, b = c['ka'] / self.fps, c['kb'] / self.fps
        return [(max(a, x) - a, min(b, y) - a) for x, y in self.layout_ranges_src() if min(b, y) > max(a, x)]

    def _clip_job(self, i, c, look, W, H, crf, preset, folder):
        f, n = self.fps, c['kb'] - c['ka']
        a = c['ka'] / f
        local = self._clip_screen_ranges(c)
        key = json.dumps([os.path.abspath(self.video), os.path.getmtime(self.video), c['ka'], c['kb'], look, W, H, f,
                          crf, preset, local, self.screen and [self.screen_path, self.screen.get('offset', 0)]],
                         sort_keys=True, default=str)
        path = os.path.join(folder, hashlib.sha1(key.encode()).hexdigest()[:20] + '.mp4')
        if os.path.exists(path): return path, True
        inputs, L = ['-ss', f'{a:.4f}', '-i', self.video], []
        L.append(f'[0:v]setpts=PTS-STARTPTS,fps={f},tpad=stop_mode=clone:stop_duration=2[src]')
        screen = None
        if local:
            so = a + float(self.screen.get('offset', 0.0))
            pad = max(0.0, -so)
            inputs += ['-ss', f'{max(0.0, so):.4f}', '-i', self.screen_path]
            L.append(f"[1:v]setpts=PTS-STARTPTS,fps={f},{f'tpad=start_duration={pad:.4f}:start_mode=clone,' if pad else ''}"
                     f"tpad=stop_mode=clone:stop_duration=2[scrsrc]")
            screen = ('scrsrc', local)
        self._frame_chain(look, W, H, 'src', 'v', L, screen)
        part = path[:-4] + '.part.mp4'
        cmd = [ffmpeg_exe(), '-hide_banner', '-v', 'error', '-y', *inputs, '-filter_complex', ';'.join(L), '-map', '[v]',
               '-frames:v', str(n), '-an', '-c:v', 'libx264', '-preset', preset, '-crf', str(crf), '-pix_fmt', 'yuv420p',
               '-g', str(2 * f), '-video_track_timescale', str(f * 1000), part]
        r = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
        if r.returncode != 0: raise SystemExit(f'clip {i + 1} failed: {r.stderr.strip()[-400:]}')
        os.replace(part, path)
        return path, False

    def render_clips(self, W, H, draft):
        folder = os.path.join(self.cache, 'clips'); os.makedirs(folder, exist_ok=True)
        crf, preset = (20, 'ultrafast') if draft else (12, 'veryfast')
        looks = self.clip_looks()
        jobs = list(enumerate(zip(self.edl.clips, looks)))
        paths, done, reused = [None] * len(jobs), 0, 0
        workers = max(1, min(4, (os.cpu_count() or 2) // 2))
        with ThreadPoolExecutor(workers) as pool:
            futs = {pool.submit(self._clip_job, i, c, lk, W, H, crf, preset, folder): i for i, (c, lk) in jobs}
            for fut in futs:
                i = futs[fut]
                paths[i], was_cached = fut.result()
                done += 1; reused += was_cached
                print(f'clips {done}/{len(jobs)}', flush=True)
        if reused: print(f'reused {reused} unchanged clip(s)', flush=True)
        keep = set(os.path.basename(p) for p in paths)                  # prune clips no edit uses any more
        if not draft:
            for fn in os.listdir(folder):
                if fn not in keep and fn.endswith('.mp4') and not fn.endswith('.part.mp4'):
                    try: os.remove(os.path.join(folder, fn))
                    except OSError: pass
        return paths

    def join_runs(self, paths, tmp):
        runs = []
        for k, group in enumerate(self.edl.runs()):
            if len(group) == 1: runs.append(paths[group[0]]); continue
            lst = os.path.join(tmp, f'run{k}.txt')
            with open(lst, 'w', encoding='utf-8') as fh:
                for i in group:
                    p = paths[i].replace('\\', '/').replace("'", "'\\''")
                    fh.write(f"file '{p}'\n")
            out = os.path.join(tmp, f'run{k}.mp4')
            subprocess.check_call([ffmpeg_exe(), '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', out])
            runs.append(out)
        return runs

    # ---------------------------------------------------------------- the export
    def out_size(self, half):
        return (even(self.W / 2), even(self.H / 2)) if half else (self.W, self.H)

    def encode_args(self, half):
        if half:
            return ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-profile:v', 'high']
        q = QUALITY.get(self.ex.get('quality', 'high'), QUALITY['high'])
        if self.ex.get('codec') == 'h265':
            args = ['-c:v', 'libx265', '-preset', 'medium', '-crf', str(q[1]), '-pix_fmt', 'yuv420p', '-tag:v', 'hvc1',
                    '-x265-params', 'log-level=error']                    # x265 logs on its own otherwise
        else:
            args = ['-c:v', 'libx264', '-preset', 'medium', '-crf', str(q[0]), '-pix_fmt', 'yuv420p', '-profile:v', 'high']
        mx = self.ex.get('max_mbps')
        if mx: args += ['-maxrate', f'{float(mx):g}M', '-bufsize', f'{2 * float(mx):g}M']
        return args

    def loudness(self):
        if 'loudness' in self.ex: return self.ex['loudness']
        return LF['loudness'][self.format]

    def output_path(self, draft=False):
        base = re.sub(r'[^\w.-]+', '-', str(self.ex.get('file') or self.name)).strip('-') or self.name
        return os.path.join(self.exports, f'{base}{"_draft" if draft else ""}.mp4')

    def render(self, out, rough=False, draft=False, progress=True):
        os.makedirs(self.work, exist_ok=True); os.makedirs(os.path.dirname(out), exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        half = rough or draft
        W, H = self.out_size(half)
        f = self.fps
        tmp = tempfile.mkdtemp(prefix='edit-', dir=self.cache)
        try:
            wav = self.edited_audio(tmp)
            runs = self.join_runs(self.render_clips(W, H, half), tmp)
            inputs = [['-i', p] for p in runs]
            L = []
            cur = '0:v'
            for r in range(1, len(runs)):                    # transitions between runs
                c = self.edl.clips[self.edl.runs()[r][0]]
                kind = TRANSITIONS[c['transition']['type']]
                L.append(f'[{cur}][{r}:v]xfade=transition={kind}:duration={c["ov"] / f:.4f}:offset={c["sf"] / f:.4f}[x{r}]')
                cur = f'x{r}'
            ai = len(inputs); inputs.append(['-i', wav])
            if not rough:
                cur = self._broll_layers(W, H, f, inputs, L, cur)
                cur = self._card_layers(W, H, f, inputs, L, cur, tmp)
                cur = self._captions(W, H, L, cur, tmp)
            L.append(f'[{cur}]scale=out_range=tv,format=yuv420p,fade=t=in:st=0:d=0.2[vm]')
            vlab, alab = 'vm', f'{ai}:a'
            end = None if rough else self.end()
            if end:
                png = os.path.join(tmp, 'end.png')
                cards.end_card(W, H, end['lines'], end['handle']).convert('RGB').save(png)
                k = len(inputs); inputs.append(['-loop', '1', '-framerate', str(f), '-t', f"{end['dur']:.3f}", '-i', png])
                L.append(f"[{k}:v]scale=out_range=tv,format=yuv420p,setsar=1,fade=t=in:st=0:d=0.3,"
                         f"fade=t=out:st={max(0, end['dur'] - 0.6):.3f}:d=0.6[ev]")
                L.append(f"anullsrc=r={SR}:cl=stereo,atrim=0:{end['dur']:.3f}[es]")
                L.append(f'[vm][{ai}:a][ev][es]concat=n=2:v=1:a=1[vc][ac]')
                vlab, alab = 'vc', 'ac'
            total = self.dur + (end['dur'] if end else 0)
            alab = self._music(inputs, L, alab, total, rough)
            lufs = self.loudness()
            if lufs is not None:
                L.append(f"[{alab}]loudnorm=I={float(lufs)}:TP=-1.5:LRA=11,aresample={SR}[aout]")
            else:
                L.append(f"[{alab}]aresample={SR}[aout]")
            graph = os.path.join(tmp, 'graph.txt')
            with open(graph, 'w', encoding='utf-8') as fh: fh.write(';\n'.join(L))
            part = out[:-4] + '.tmp.mp4'
            kbps = int(self.ex.get('audio_kbps') or 192)
            cmd = [ffmpeg_exe(), '-hide_banner', '-v', 'error', '-y', *[x for i in inputs for x in i], *graph_flag(graph),
                   '-map', f'[{vlab}]', '-map', '[aout]', *self.encode_args(half), '-r', str(f), '-g', str(2 * f),
                   '-c:a', 'aac', '-b:a', f'{kbps}k', '-ar', str(SR), '-movflags', '+faststart',
                   '-progress', 'pipe:1', '-nostats', part]
            self._run(cmd, total, progress, cwd=tmp)
            os.replace(part, out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if not rough: self._sidecars(out)
        print('edit ->', out, flush=True)
        return out

    def _broll_layers(self, W, H, f, inputs, L, cur):
        for i, b in enumerate(self.broll()):
            k = len(inputs)
            if b['image']:
                inputs.append(['-loop', '1', '-framerate', str(f), '-t', f"{b['d']:.3f}", '-i', b['path']])
            else:
                inputs.append(['-ss', f"{b['from']:.3f}", '-t', f"{b['d']:.3f}", '-i', b['path']])
            fo = max(0.0, b['d'] - 0.25)
            L.append(f"[{k}:v]setpts=PTS-STARTPTS,fps={f},scale={W}:{H}:force_original_aspect_ratio=increase,"
                     f"crop={W}:{H},setsar=1,format=yuva420p,"
                     f"fade=t=in:st=0:d=0.25:alpha=1,fade=t=out:st={fo:.3f}:d=0.25:alpha=1,setpts=PTS+{b['t']:.3f}/TB[b{i}]")
            L.append(f'[{cur}][b{i}]overlay=0:0:eof_action=pass[bo{i}]')
            cur = f'bo{i}'
        return cur

    @staticmethod
    def anim_xy(anim, t, d, W, H):
        """overlay x/y expressions for graphic in/out animations (0.45 s in, 0.35 s out)."""
        if anim in ('slide_right', 'slide_left'):
            sgn = -1 if anim == 'slide_right' else 1
            dx = round(W * 0.03) * sgn
            return (f"'{dx}*max(0,1-(t-{t:.3f})/0.45)+{dx}*max(0,(t-{t + d - 0.35:.3f})/0.35)'", '0')
        if anim == 'slide_up':
            dy = round(H * 0.025)
            return ('0', f"'{dy}*max(0,1-(t-{t:.3f})/0.45)+{dy}*max(0,(t-{t + d - 0.35:.3f})/0.35)'")
        return ('0', '0')

    def _card_layers(self, W, H, f, inputs, L, cur, tmp):
        for i, (kind, draw, t, d, anim, _key, _idx) in enumerate(self.graphics()):
            png = os.path.join(tmp, f'card{i:02d}_{kind}.png')
            draw(W, H).save(png)
            k = len(inputs); inputs.append(['-loop', '1', '-framerate', str(f), '-t', f'{d:.3f}', '-i', png])
            fi = 0.0 if (kind == 'intro' and t == 0) or anim == 'none' else 0.3
            fo = 0.0 if anim == 'none' else 0.35
            chain = 'format=rgba' + (f',fade=t=in:st=0:d={fi}:alpha=1' if fi else '')
            chain += (f',fade=t=out:st={max(0.0, d - fo):.3f}:d={fo}:alpha=1' if fo else '') + f',setpts=PTS+{t:.3f}/TB'
            x, y = self.anim_xy(anim, t, d, W, H)
            L.append(f'[{k}:v]{chain}[c{i}]')
            L.append(f'[{cur}][c{i}]overlay=x={x}:y={y}:eof_action=pass[co{i}]')
            cur = f'co{i}'
        return cur

    def write_captions(self, W, H, folder):
        """captions.ass (captions + text overlays) and their fonts into folder; ffmpeg runs there, so libass gets
        plain relative names (no Windows drive-colon escaping). False when there is nothing to draw."""
        mode = self.captions_mode()
        words, st = [], None
        if mode != 'off' and self.words:
            st = self.caption_style(W, H, mode)
            words = subs.prep_words(self.words, {**STYLE.get('fixes', {}), **st.get('fixes', {})}, st.get('uppercase'))
        texts = self.texts()
        if not words and not texts: return False
        with open(os.path.join(folder, 'captions.ass'), 'w', encoding='utf-8') as fh:
            fh.write(subs.build_ass(words, mode if words else 'off', W, H, st, COLORS, texts=texts))
        os.makedirs(os.path.join(folder, 'fonts'), exist_ok=True)
        files = {subs.TEXT_FONTS[k][1] for k in subs.TEXT_FONTS} | ({st['file']} if st else set())
        for fn in files: shutil.copy2(os.path.join(FONTS, fn), os.path.join(folder, 'fonts'))
        return True

    def _captions(self, W, H, L, cur, tmp):
        if not self.write_captions(W, H, tmp): return cur
        shutil.copy2(os.path.join(tmp, 'captions.ass'), self.work)      # kept for inspection
        L.append(f'[{cur}]subtitles=filename=captions.ass:fontsdir=fonts[cap]')
        return 'cap'

    def _music(self, inputs, L, alab, total, rough):
        m = self.cfg.get('music') or {}
        if rough or not m.get('file'): return alab
        path = self.music_path()
        if not path:
            self.warnings.append(f'music not found, skipped: {m["file"]}'); return alab
        k = len(inputs); inputs.append(['-stream_loop', '-1', '-ss', f"{float(m.get('start', 0) or 0):.3f}", '-i', path])
        db = float(m.get('db', LF.get('music_db', -24)))
        fo = max(0.0, total - 2.5)
        L.append(f'[{k}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,aresample={SR},aformat=channel_layouts=stereo,'
                 f'volume={db}dB,afade=t=in:st=0:d=1.5,afade=t=out:st={fo:.3f}:d=2.5[mus]')
        if m.get('duck', True):
            L.append(f'[{alab}]asplit[sa][sb]')
            L.append('[mus][sb]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[duck]')
            L.append('[sa][duck]amix=inputs=2:duration=first:normalize=0[amix]')
        else:
            L.append(f'[{alab}][mus]amix=inputs=2:duration=first:normalize=0[amix]')
        return 'amix'

    def _run(self, cmd, total, progress, cwd):
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, text=True, errors='replace')
        last = -5
        for line in p.stdout:
            if line.startswith('out_time_us=') and progress and total:
                try: pct = int(line.split('=')[1]) / 1e6 / total * 100
                except ValueError: continue
                if pct - last >= 2:
                    last = pct; print(f'render {min(99, pct):.0f}%', flush=True)
        if p.wait() != 0: raise SystemExit('ffmpeg failed (see the messages above)')

    def _sidecars(self, out):
        stem = out[:-4]
        if self.words:
            st = self.caption_style(self.W, self.H, 'lines')
            words = subs.prep_words(self.words, STYLE.get('fixes', {}))
            with open(stem + '.srt', 'w', encoding='utf-8') as fh: fh.write(subs.build_srt(words, st['max_chars']))
        ch = self.chapters()
        if ch:
            with open(stem + '.chapters.txt', 'w', encoding='utf-8') as fh: fh.write(subs.chapter_lines(ch))

    # ---------------------------------------------------------------- previews
    def stills(self, n_even=6):
        """Frames at every graphic and text plus a few evenly spaced ones, built from the same filter pieces."""
        from PIL import Image, ImageDraw
        out_dir = os.path.join(self.work, 'stills'); os.makedirs(out_dir, exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        W, H, f = self.W, self.H, self.fps
        graphics = self.graphics()
        moments = [(t + min(d * 0.6, 1.2), kind) for kind, _, t, d, *_ in graphics]
        moments += [(t + min(d * 0.6, 1.2), 'text') for _, t, d in self.texts()]
        moments += [(self.dur * (i + 0.5) / n_even, 'frame') for i in range(n_even)]
        moments = sorted(moments)[:16]
        looks = self.clip_looks()
        tmp = tempfile.mkdtemp(prefix='stills-', dir=self.cache)
        shots = []
        try:
            pngs = []
            for i, (kind, draw, t, d, *_r) in enumerate(graphics):
                p = os.path.join(tmp, f'g{i}.png'); draw(W, H).save(p); pngs.append((p, t, d))
            has_caps = self.write_captions(W, H, tmp)
            for j, (t, kind) in enumerate(moments):
                ci = self.edl.clip_at(min(t, self.dur - 1e-3))
                c = self.edl.clips[ci]
                src = self.edl.src_time(min(t, self.dur - 1e-3))
                inputs, L = ['-ss', f'{src:.3f}', '-i', self.video], []
                screen, n_in = None, 1
                local = self._clip_screen_ranges(c)
                lt = src - c['ka'] / f
                if local:
                    so = src + float(self.screen.get('offset', 0.0))
                    inputs += ['-ss', f'{max(0, so):.3f}', '-i', self.screen_path]
                    L.append(f'[1:v]setpts={lt:.4f}/TB[scrsrc]')
                    screen, n_in = ('scrsrc', local), 2
                L.append(f'[0:v]setpts={lt:.4f}/TB[src]')
                self._frame_chain(looks[ci], W, H, 'src', 'v0', L, screen)
                L.append(f'[v0]setpts={t:.3f}/TB[v]')
                cur = 'v'
                for b in self.broll():
                    if b['t'] <= t < b['t'] + b['d']:
                        inputs += ['-i', b['path']] if b['image'] else ['-ss', f"{b['from'] + t - b['t']:.3f}", '-i', b['path']]
                        L.append(f'[{n_in}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[bb{n_in}]')
                        L.append(f'[{cur}][bb{n_in}]overlay=0:0[bo{n_in}]')
                        cur, n_in = f'bo{n_in}', n_in + 1
                for p, gt, gd in pngs:
                    if gt <= t < gt + gd:
                        inputs += ['-i', p]
                        L.append(f'[{cur}][{n_in}:v]overlay=0:0[o{n_in}]')
                        cur, n_in = f'o{n_in}', n_in + 1
                if has_caps:
                    L.append(f'[{cur}]subtitles=filename=captions.ass:fontsdir=fonts[cap]'); cur = 'cap'
                shot = os.path.join(out_dir, f'{j:02d}_{kind}.jpg')
                r = subprocess.run([ffmpeg_exe(), '-hide_banner', '-v', 'error', '-y', *inputs, '-filter_complex', ';'.join(L),
                                    '-map', f'[{cur}]', '-frames:v', '1', '-q:v', '3', shot], cwd=tmp,
                                   capture_output=True, text=True, errors='replace')
                if r.returncode != 0: raise SystemExit(f'still {j + 1} failed: {r.stderr.strip()[-400:]}')
                shots.append((shot, t, kind))
                print(f'still {j + 1}/{len(moments)}', flush=True)
            end = self.end()
            if end:
                shot = os.path.join(out_dir, f'{len(shots):02d}_end.jpg')
                cards.end_card(W, H, end['lines'], end['handle']).convert('RGB').save(shot, quality=90)
                shots.append((shot, self.dur, 'end'))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        cols = 4 if H > W else 3
        tw = 420 if H > W else 560
        th = round(tw * H / W)
        rows = (len(shots) + cols - 1) // cols
        sheet = Image.new('RGB', (cols * (tw + 16) + 16, rows * (th + 52) + 16), (11, 14, 26))
        d = ImageDraw.Draw(sheet)
        label_font = cards.Canvas.fnt('mono', 11)
        for i, (shot, t, kind) in enumerate(shots):
            x, y = 16 + (i % cols) * (tw + 16), 16 + (i // cols) * (th + 52)
            sheet.paste(Image.open(shot).convert('RGB').resize((tw, th), Image.LANCZOS), (x, y))
            d.text((x, y + th + 10), f'{int(t // 60):02d}:{t % 60:04.1f}  {kind}', font=label_font, fill=(156, 165, 212))
        path = os.path.join(out_dir, 'sheet.jpg')
        sheet.save(path, quality=90)
        print('stills ->', path, flush=True)
        return path

    def graphics_pngs(self, folder, width):
        """Each graphic (and the end card) drawn at preview size, for the studio's live preview."""
        os.makedirs(folder, exist_ok=True)
        W = even(width); H = even(width * self.H / self.W)
        out = []
        for i, (kind, draw, t, d, anim, key, idx) in enumerate(self.graphics()):
            p = os.path.join(folder, f'{key}_{idx}.png')
            draw(W, H).save(p)
            out.append({'kind': kind, 'key': key, 'idx': idx, 'png': p, 't': t, 'dur': d, 'anim': anim})
        e = self.end()
        if e:
            p = os.path.join(folder, 'end.png')
            cards.end_card(W, H, e['lines'], e['handle']).convert('RGB').save(p)
            out.append({'kind': 'end', 'key': 'end', 'idx': 0, 'png': p, 't': self.dur, 'dur': e['dur'], 'anim': 'fade'})
        return out

    def beats(self):
        """Beat times of the music bed on the edited timeline (music starts at 0, skipping music.start)."""
        path = self.music_path()
        if not path: return {'bpm': None, 'beats': [], 'error': 'no music file set'}
        os.makedirs(self.cache, exist_ok=True)
        key = hashlib.sha1(f'{path}|{os.path.getmtime(path)}'.encode()).hexdigest()[:16]
        cp = os.path.join(self.cache, f'beats_{key}.json')
        if os.path.exists(cp):
            with open(cp, encoding='utf-8') as fh: d = json.load(fh)
        else:
            import beats as bt
            d = bt.detect(path)
            with open(cp, 'w', encoding='utf-8') as fh: json.dump(d, fh)
        start = float((self.cfg.get('music') or {}).get('start', 0) or 0)
        return {'bpm': d['bpm'], 'beats': [round(b - start, 3) for b in d['beats'] if b >= start]}

    def proxy(self):
        os.makedirs(self.cache, exist_ok=True)
        out = self.proxy_path()
        part = out[:-4] + '.tmp.mp4'
        cmd = [ffmpeg_exe(), '-hide_banner', '-v', 'error', '-y', '-i', self.video, '-vf', 'scale=-2:540',
               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28', '-g', '15', '-c:a', 'aac', '-b:a', '96k',
               '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', part]
        self._run(cmd, self.info['duration'], True, cwd=self.cache)
        os.replace(part, out)
        print('proxy ->', out, flush=True)
        return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='Interview / presentation full edit')
    ap.add_argument('preset')
    ap.add_argument('--plan', action='store_true', help='print the edit as JSON')
    ap.add_argument('--roughcut', action='store_true')
    ap.add_argument('--stills', action='store_true')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--draft', action='store_true', help='with --build: half size, fast encode')
    ap.add_argument('--proxy', action='store_true')
    ap.add_argument('--beats', action='store_true', help='print music beat times as JSON')
    ap.add_argument('--graphics', metavar='DIR', help='write preview PNGs of the graphics to DIR, print JSON')
    ap.add_argument('--width', type=int, default=540)
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    e = Edit(a.preset)
    if a.plan:
        print(json.dumps(e.plan(), ensure_ascii=False)); return 0
    if a.beats:
        print(json.dumps(e.beats())); return 0
    if a.graphics:
        print(json.dumps(e.graphics_pngs(a.graphics, a.width), ensure_ascii=False)); return 0
    for w in e.warnings: print('warn:', w, flush=True)
    if a.proxy: e.proxy()
    if a.roughcut: e.render(os.path.join(e.work, 'roughcut.mp4'), rough=True)
    if a.stills: e.stills()
    if a.build: e.render(a.out or e.output_path(a.draft), draft=a.draft)
    if not (a.proxy or a.roughcut or a.stills or a.build): ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
