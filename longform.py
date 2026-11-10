"""Interviews and presentations: transcript-driven full edits, rendered by ffmpeg in one pass.

  python longform.py projects/<name>/preset.json --plan        # JSON summary of the edit (the studio reads it)
  python longform.py projects/<name>/preset.json --roughcut    # cut only, half size, with sound -> work/roughcut.mp4
  python longform.py projects/<name>/preset.json --stills      # graphics preview sheet -> work/stills/sheet.jpg
  python longform.py projects/<name>/preset.json --build       # -> exports/<name>.mp4 (+ .srt, .chapters.txt)
  ... --build --draft                                           # half size, fast encode, for checking timing
  python longform.py projects/<name>/preset.json --proxy       # small H.264 copy of the source for the studio player
make_reel.py forwards --roughcut / --stills / --build here when the preset says "format": "interview" | "presentation".

Why not the reel engine: that one redraws every frame in Python (fine for 30 s, hours for 20 min). Here Python only
decides the edit and draws a handful of PNG cards; ffmpeg cuts, scales, overlays, burns captions (libass) and encodes.
Cuts are snapped to whole frames, so the audio (sliced here with 12 ms fades) is sample-exact against the video.
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, wave
import numpy as np
from vio import ffmpeg_exe
from cuts import segments_from_words, apply_manual_cuts, Timeline
from reel import STYLE, CYAN, MAG, WHITE
import cards, subs

ROOT = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(ROOT, 'fonts')
CACHE = os.environ.get('TBJ_REELS_CACHE') or os.path.join(os.path.expanduser('~'), '.tbj-reels', 'cache')
FORMATS = ('interview', 'presentation')
SIZES = {'9:16': (1080, 1920), '16:9': (1920, 1080), '1:1': (1080, 1080), '4:5': (1080, 1350)}
SR = 48000
LF = STYLE['longform']
COLORS = {'white': WHITE, 'cyan': CYAN, 'magenta': MAG}
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.bmp')
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


def expr_ranges(var, ranges, f):
    """Frame-exact enable/select expression: ranges are (start, end) seconds on a frame grid."""
    h = 0.5 / f
    return '+'.join(f'between({var},{a - h:.4f},{b - h:.4f})' for a, b in ranges) or '0'


def resolve_style(block, W, H, overrides=None):
    st = {k: cards.pick(v, W, H) for k, v in block.items() if not k.startswith('_')}
    st.update({k: v for k, v in (overrides or {}).items() if k != 'mode'})
    return st


class Edit:
    """Every decision of the edit, no rendering: keep-segments, edited word times, graphics, layouts."""

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
        self.W, self.H = tuple(out['size']) if out.get('size') else SIZES[out.get('aspect', self.d['aspect'])]
        self.fps = int(out.get('fps', 30))
        if SR % self.fps: raise SystemExit('output fps must divide 48000 (24, 25, 30, 50 or 60)')
        self.focus = out.get('focus', [0.5, 0.4])
        self.zoom = max(1.0, float(out.get('zoom', 1.0)))
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
        lo, hi = float(clip.get('start', 0.0)), float(clip.get('end') or total)
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
        segs = apply_manual_cuts(segs, removed)
        f, frames = self.fps, []
        for a, b in sorted(segs):                    # whole frames; merge anything that now touches
            ka, kb = round(a * f), round(b * f)
            if kb - ka < 2: continue
            if frames and ka <= frames[-1][1]: frames[-1] = (frames[-1][0], max(frames[-1][1], kb))
            else: frames.append((ka, kb))
        if not frames: raise SystemExit('nothing left after the cuts: check clip / cuts / trim')
        self.frames = frames
        self.segs = [(ka / f, kb / f) for ka, kb in frames]
        self.tl = Timeline(self.segs)
        self.dur = sum(kb - ka for ka, kb in frames) / f
        self.words = self.tl.remap_words(kept)
        self.source_range = (lo, hi)

    def edit_t(self, src):
        """Source time -> edited time; a moment that was cut maps to the next kept moment."""
        t = self.tl.to_edit(src)
        if t is not None: return t
        for (a, _), s in zip(self.tl.segs, self.tl.starts):
            if a > src: return s
        return self.dur

    def span(self, item, default_dur):
        t = self.edit_t(float(item['at_src'])) if 'at_src' in item else float(item.get('at', 0.0))
        d = self.edit_t(float(item['end_src'])) - t if 'end_src' in item else float(item.get('dur', default_dur))
        t = min(max(0.0, t), self.dur)
        return t, max(0.0, min(d, self.dur - t))

    def seg_ranges(self):
        """Keep-segments on the edited timeline."""
        return [(s, s + (b - a)) for (a, b), s in zip(self.tl.segs, self.tl.starts)]

    def tight_ranges(self):
        """Alternate wide/tight framing at each jump cut (hides the jump). Short segments don't switch."""
        p = {**self.d['punch'], **(self.cfg.get('punch') or {})}
        if p.get('mode', 'off') != 'alternate': return []
        out, tight = [], False
        for i, (a, b) in enumerate(self.seg_ranges()):
            if i and b - a >= p.get('min_seg', 1.2): tight = not tight
            if tight: out.append((a, b))
        return out

    def punch_scale(self):
        return float({**self.d['punch'], **(self.cfg.get('punch') or {})}.get('scale', 1.12))

    def screen_ranges(self):
        if not self.screen: return []
        sw = sorted(((self.edit_t(float(x['at_src'])) if 'at_src' in x else float(x.get('at', 0))), x['layout'])
                    for x in self.screen.get('layouts', []))
        cur, t0, out = self.screen.get('default', 'speaker'), 0.0, []
        for t, lay in sw + [(self.dur, None)]:
            if cur == 'screen' and t > t0: out.append((t0, t))
            if lay: cur, t0 = lay, t
        return out

    def chapters(self):
        ch = [((self.edit_t(float(x['at_src'])) if 'at_src' in x else float(x.get('at', 0))), x['title'])
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
        """[(kind, draw(W, H) -> RGBA, t, dur)] on the edited timeline."""
        c, out = self.cfg, []
        intro = c.get('intro') or {}
        intro_end = 0.0
        if intro.get('title'):
            d = min(float(intro.get('dur', 3.0)), self.dur)
            out.append(('intro', lambda W, H, i=intro: cards.intro_card(W, H, i['title'], i.get('subtitle', '')), 0.0, d))
            intro_end = d
        for lt in c.get('lower_thirds', []):
            t, d = self.span(lt, LF['lower_third']['dur'])
            out.append(('lower_third', lambda W, H, x=lt: cards.lower_third(W, H, x['name'], x.get('title', '')), t, d))
        for tc in c.get('titles', []):
            t, d = self.span(tc, LF['card']['dur'])
            out.append(('title', lambda W, H, x=tc: cards.title_card(W, H, x['text'], x.get('label', '')), t, d))
        if c.get('chapter_cards', self.format == 'presentation'):
            for k, (t, title) in enumerate(self.chapters(), 1):
                if t < intro_end: continue
                out.append(('chapter', lambda W, H, k=k, x=title: cards.chapter_card(W, H, k, x), t,
                            min(LF['chapter']['dur'], self.dur - t)))
        return [g for g in out if g[3] > 0.1]

    def end(self):
        e = self.cfg.get('end')
        if not e: return None
        return {'lines': e.get('lines', []), 'handle': e.get('handle', STYLE.get('handle', '')),
                'dur': float(e.get('dur', LF['end']['dur']))}

    def broll(self):
        out = []
        for b in self.cfg.get('broll', []):
            path = self.resolve(b['file'])
            if not os.path.exists(path):
                self.warnings.append(f'b-roll not found, skipped: {b["file"]}'); continue
            t, d = self.span(b, 3.0)
            if d > 0.2: out.append({'path': path, 't': t, 'd': d, 'from': float(b.get('from', 0.0)),
                                    'image': path.lower().endswith(IMAGE_EXT)})
        return out

    def plan(self):
        return {'name': self.name, 'format': self.format, 'video': self.video, 'size': [self.W, self.H], 'fps': self.fps,
                'proxy': self.proxy_path() if os.path.exists(self.proxy_path()) else None,
                'source_duration': self.info['duration'], 'source_range': self.source_range,
                'edit_duration': round(self.dur, 3), 'segments': [[round(a, 3), round(b, 3)] for a, b in self.segs],
                'cuts_count': len(self.segs) - 1, 'words_total': len(self.all_words), 'words_kept': len(self.words),
                'filler_idx': self.filler_idx, 'captions': self.captions_mode(),
                'chapters': [{'t': round(t, 2), 'title': x} for t, x in self.chapters()],
                'graphics': [{'kind': k, 't': round(t, 2), 'dur': round(d, 2)} for k, _, t, d in self.graphics()],
                'warnings': self.warnings}

    def proxy_path(self): return os.path.join(self.cache, 'proxy.mp4')

    # ---------------------------------------------------------------- audio
    def edited_audio(self, tmp):
        """Slice the source audio at the (frame-aligned) keep-segments, 12 ms fade at every cut."""
        src, off = self.video, 0.0
        if (self.cfg.get('audio') or {}).get('source') == 'screen' and self.screen:
            src, off = self.screen_path, float(self.screen.get('offset', 0.0))
        out = os.path.join(tmp, 'edit.wav')
        spf, fade = SR // self.fps, int(0.012 * SR)
        total = sum(kb - ka for ka, kb in self.frames) * spf
        with wave.open(out, 'wb') as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
            if not probe(src)['audio']:
                w.writeframes(b'\0' * 4 * total); return out
            full = os.path.join(tmp, 'full.wav')
            subprocess.check_call([ffmpeg_exe(), '-v', 'error', '-y', '-i', src, '-vn', '-ac', '2', '-ar', str(SR),
                                   '-c:a', 'pcm_s16le', full])
            with wave.open(full, 'rb') as r:
                n_src, shift = r.getnframes(), int(round(off * SR))
                for ka, kb in self.frames:
                    a, n = ka * spf + shift, (kb - ka) * spf
                    buf = np.zeros((n, 2), np.float32)
                    lo, hi = max(0, a), min(n_src, a + n)
                    if hi > lo:
                        r.setpos(lo)
                        got = np.frombuffer(r.readframes(hi - lo), '<i2').reshape(-1, 2).astype(np.float32)
                        buf[lo - a:lo - a + len(got)] = got
                    k = min(fade, n // 2)
                    if k:
                        ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)[:, None]
                        buf[:k] *= ramp; buf[n - k:] *= ramp[::-1]
                    w.writeframes(np.clip(buf, -32768, 32767).astype('<i2').tobytes())
            os.remove(full)
        return out

    # ---------------------------------------------------------------- video graph
    def _crop(self, w, h):
        fx, fy = self.focus
        return (f"crop=w={w}:h={h}:x='max(0,min(iw-ow,{fx}*iw-ow/2))':y='max(0,min(ih-oh,{fy}*ih-oh/2))'")

    def _cover(self, w, h):
        return f'scale={w}:{h}:force_original_aspect_ratio=increase,{self._crop(w, h)},setsar=1'

    def _framing(self, W, H, tight_ranges, lines, src_label, out_label):
        """Cover-fit (with zoom + focus) and the wide/tight punch alternation."""
        Wz, Hz = even(W * self.zoom), even(H * self.zoom)
        if not tight_ranges:
            lines.append(f'[{src_label}]{self._cover(Wz, Hz)},scale={W}:{H},setsar=1[{out_label}]')
            return
        s = self.punch_scale()
        Ws, Hs = even(W * self.zoom * s), even(H * self.zoom * s)
        lines.append(f'[{src_label}]{self._cover(Ws, Hs)},split[pw][pt]')
        lines.append(f'[pw]scale={W}:{H},setsar=1[wide]')
        lines.append(f'[pt]{self._crop(W, H)},setsar=1[tight]')
        lines.append(f"[wide][tight]overlay=0:0:enable='{expr_ranges('t', tight_ranges, self.fps)}'[{out_label}]")

    def _select(self, shift=0, pad=0):
        return '+'.join(f'between(n,{ka + shift + pad},{kb + shift + pad - 1})' for ka, kb in self.frames)

    def render(self, out, rough=False, draft=False, progress=True):
        os.makedirs(self.work, exist_ok=True); os.makedirs(os.path.dirname(out), exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        half = rough or draft
        W, H, f = (even(self.W / 2), even(self.H / 2), self.fps) if half else (self.W, self.H, self.fps)
        tmp = tempfile.mkdtemp(prefix='edit-', dir=self.cache)
        try:
            wav = self.edited_audio(tmp)
            inputs = [['-i', self.video], ['-i', wav]]
            L = [f'[0:v]setpts=PTS-STARTPTS,fps={f},select=\'{self._select()}\',setpts=N/FRAME_RATE/TB[src]']
            self._framing(W, H, [] if rough else self.tight_ranges(), L, 'src', 'v0')
            cur = 'v0'
            if not rough and self.screen and self.screen_ranges():
                cur = self._screen_layers(W, H, f, inputs, L, cur)
            if not rough:
                cur = self._broll_layers(W, H, f, inputs, L, cur)
                cur = self._card_layers(W, H, f, inputs, L, cur, tmp)
                cur = self._captions(W, H, L, cur, tmp)
            # TV range throughout: PNG/photo inputs would otherwise pull the output to full range (yuvj420p),
            # which some venue players and TVs show crushed or washed out
            L.append(f'[{cur}]scale=out_range=tv,format=yuv420p,fade=t=in:st=0:d=0.2[vm]')
            vlab, alab = 'vm', '1:a'
            end = None if rough else self.end()
            if end:
                png = os.path.join(tmp, 'end.png')
                cards.end_card(W, H, end['lines'], end['handle']).convert('RGB').save(png)
                k = len(inputs); inputs.append(['-loop', '1', '-framerate', str(f), '-t', f"{end['dur']:.3f}", '-i', png])
                L.append(f"[{k}:v]scale=out_range=tv,format=yuv420p,setsar=1,fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, end['dur'] - 0.6):.3f}:d=0.6[ev]")
                L.append(f"anullsrc=r={SR}:cl=stereo,atrim=0:{end['dur']:.3f}[es]")
                L.append(f'[vm][1:a][ev][es]concat=n=2:v=1:a=1[vc][ac]')
                vlab, alab = 'vc', 'ac'
            total = self.dur + (end['dur'] if end else 0)
            alab = self._music(inputs, L, alab, total, rough)
            L.append(f"[{alab}]loudnorm=I={LF['loudness'][self.format]}:TP=-1.5:LRA=11,aresample={SR}[aout]")
            graph = os.path.join(tmp, 'graph.txt')
            with open(graph, 'w', encoding='utf-8') as fh: fh.write(';\n'.join(L))
            gflag = ['-/filter_complex', graph] if ffmpeg_major() >= 7 else ['-filter_complex_script', graph]
            part = out[:-4] + '.tmp.mp4'
            cmd = [ffmpeg_exe(), '-hide_banner', '-v', 'error', '-y', *[x for i in inputs for x in i], *gflag,
                   '-map', f'[{vlab}]', '-map', '[aout]', '-c:v', 'libx264', '-preset', 'veryfast' if half else 'medium',
                   '-crf', '23' if half else '18', '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-g', str(2 * f),
                   '-c:a', 'aac', '-b:a', '192k', '-ar', str(SR), '-movflags', '+faststart',
                   '-progress', 'pipe:1', '-nostats', part]
            self._run(cmd, total, progress, cwd=tmp)
            os.replace(part, out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if not rough: self._sidecars(out)
        print('edit ->', out, flush=True)
        return out

    def _screen_layers(self, W, H, f, inputs, L, cur):
        k = len(inputs); inputs.append(['-i', self.screen_path])
        off = round(float(self.screen.get('offset', 0.0)) * f)
        pad = max(0, -off)
        tpad = f'tpad=start={pad}:start_mode=clone,' if pad else ''
        L.append(f"[{k}:v]setpts=PTS-STARTPTS,fps={f},{tpad}select='{self._select(off, pad)}',setpts=N/FRAME_RATE/TB,"
                 f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x05060d,setsar=1[scr]")
        st = LF['inset']
        iw, m, b = even(W * st['width']), round(W * st['margin']), max(2, round(H * 0.002))
        en = expr_ranges('t', self.screen_ranges(), f)
        L.append(f'[{cur}]split[sp0][sp1]')
        L.append(f'[sp1]scale={iw}:-2,pad=iw+{2 * b}:ih+{2 * b}:{b}:{b}:color=0x6370b6[ins]')
        L.append(f"[sp0][scr]overlay=0:0:enable='{en}'[sv]")
        L.append(f"[sv][ins]overlay=x=W-w-{m}:y={m}:enable='{en}'[v1]")
        return 'v1'

    def _broll_layers(self, W, H, f, inputs, L, cur):
        for i, b in enumerate(self.broll()):
            k = len(inputs)
            if b['image']:
                inputs.append(['-loop', '1', '-framerate', str(f), '-t', f"{b['d']:.3f}", '-i', b['path']])
            else:
                inputs.append(['-ss', f"{b['from']:.3f}", '-t', f"{b['d']:.3f}", '-i', b['path']])
            fo = max(0.0, b['d'] - 0.25)
            L.append(f"[{k}:v]setpts=PTS-STARTPTS,fps={f},{self._cover(W, H)},format=yuva420p,"
                     f"fade=t=in:st=0:d=0.25:alpha=1,fade=t=out:st={fo:.3f}:d=0.25:alpha=1,setpts=PTS+{b['t']:.3f}/TB[b{i}]")
            L.append(f'[{cur}][b{i}]overlay=0:0:eof_action=pass[bo{i}]')
            cur = f'bo{i}'
        return cur

    def _card_layers(self, W, H, f, inputs, L, cur, tmp):
        for i, (kind, draw, t, d) in enumerate(self.graphics()):
            png = os.path.join(tmp, f'card{i:02d}_{kind}.png')
            draw(W, H).save(png)
            k = len(inputs); inputs.append(['-loop', '1', '-framerate', str(f), '-t', f'{d:.3f}', '-i', png])
            fi = 0.0 if kind == 'intro' and t == 0 else 0.3
            chain = 'format=rgba' + (f',fade=t=in:st=0:d={fi}:alpha=1' if fi else '')
            chain += f',fade=t=out:st={max(0.0, d - 0.35):.3f}:d=0.35:alpha=1,setpts=PTS+{t:.3f}/TB'
            x = f"'-{round(W * 0.02)}*max(0,1-(t-{t:.3f})/0.45)'" if kind == 'lower_third' else '0'
            L.append(f'[{k}:v]{chain}[c{i}]')
            L.append(f'[{cur}][c{i}]overlay=x={x}:y=0:eof_action=pass[co{i}]')
            cur = f'co{i}'
        return cur

    def write_captions(self, W, H, folder):
        """captions.ass + its font into folder (ffmpeg runs there, so libass gets plain relative names:
        no Windows drive-colon escaping). Returns False when there is nothing to caption."""
        mode = self.captions_mode()
        if mode == 'off' or not self.words: return False
        st = self.caption_style(W, H, mode)
        words = subs.prep_words(self.words, {**STYLE.get('fixes', {}), **st.get('fixes', {})}, st.get('uppercase'))
        with open(os.path.join(folder, 'captions.ass'), 'w', encoding='utf-8') as fh:
            fh.write(subs.build_ass(words, mode, W, H, st, COLORS))
        os.makedirs(os.path.join(folder, 'fonts'), exist_ok=True)
        shutil.copy2(os.path.join(FONTS, st['file']), os.path.join(folder, 'fonts'))
        return True

    def _captions(self, W, H, L, cur, tmp):
        if not self.write_captions(W, H, tmp): return cur
        shutil.copy2(os.path.join(tmp, 'captions.ass'), self.work)      # kept for inspection
        L.append(f'[{cur}]subtitles=filename=captions.ass:fontsdir=fonts[cap]')
        return 'cap'

    def _music(self, inputs, L, alab, total, rough):
        m = self.cfg.get('music') or {}
        if rough or not m.get('file'): return alab
        path = self.resolve(m['file'])
        if not os.path.exists(path):
            self.warnings.append(f'music not found, skipped: {m["file"]}'); return alab
        k = len(inputs); inputs.append(['-stream_loop', '-1', '-i', path])
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
        """Frames at every graphic plus a few evenly spaced ones, rendered through the same graph pieces."""
        from PIL import Image, ImageDraw
        out_dir = os.path.join(self.work, 'stills'); os.makedirs(out_dir, exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        W, H, f = self.W, self.H, self.fps
        moments = [(t + min(d * 0.6, 1.2), kind) for kind, _, t, d in self.graphics()]
        moments += [(self.dur * (i + 0.5) / n_even, 'frame') for i in range(n_even)]
        moments = sorted(moments)[:14]
        tight = self.tight_ranges()
        scr = self.screen_ranges()
        tmp = tempfile.mkdtemp(prefix='stills-', dir=self.cache)
        shots = []
        try:
            graphics = self.graphics()
            pngs = []
            for i, (kind, draw, t, d) in enumerate(graphics):
                p = os.path.join(tmp, f'g{i}.png'); draw(W, H).save(p); pngs.append((p, t, d))
            has_caps = self.write_captions(W, H, tmp)
            for j, (t, kind) in enumerate(moments):
                src = self.tl.src_time(min(t, self.dur - 1e-3))
                is_tight = any(a <= t < b for a, b in tight)
                inputs = ['-ss', f'{src:.3f}', '-i', self.video]
                Wz, Hz = even(W * self.zoom), even(H * self.zoom)
                if is_tight:
                    s = self.punch_scale(); Ws, Hs = even(Wz * s), even(Hz * s)
                    chain = f'{self._cover(Ws, Hs)},{self._crop(W, H)}'
                else:
                    chain = f'{self._cover(Wz, Hz)},scale={W}:{H}'
                L = [f'[0:v]{chain},setsar=1,setpts={t:.3f}/TB[v]']
                cur, n_in = 'v', 1
                if self.screen and any(a <= t < b for a, b in scr):
                    so = src + float(self.screen.get('offset', 0.0))
                    inputs += ['-ss', f'{max(0, so):.3f}', '-i', self.screen_path]
                    st = LF['inset']; iw, m = even(W * st['width']), round(W * st['margin'])
                    L.append(f'[{n_in}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x05060d,setsar=1[scr]')
                    L.append(f'[{cur}]scale={iw}:-2,pad=iw+4:ih+4:2:2:color=0x6370b6[ins]')
                    L.append(f'[scr][ins]overlay=x=W-w-{m}:y={m}[sv]')
                    cur, n_in = 'sv', n_in + 1
                for b in self.broll():
                    if b['t'] <= t < b['t'] + b['d']:
                        bi = ['-i', b['path']] if b['image'] else ['-ss', f"{b['from'] + t - b['t']:.3f}", '-i', b['path']]
                        inputs += bi
                        L.append(f'[{n_in}:v]{self._cover(W, H)}[bb{n_in}]')
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
                subprocess.check_call([ffmpeg_exe(), '-hide_banner', '-v', 'error', '-y', *inputs,
                                       '-filter_complex', ';'.join(L), '-map', f'[{cur}]', '-frames:v', '1',
                                       '-q:v', '3', shot], cwd=tmp)
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
        label_font = cards.Canvas.fnt('mono', 11)                    # 22 px at the 2x supersample size
        for i, (shot, t, kind) in enumerate(shots):
            x, y = 16 + (i % cols) * (tw + 16), 16 + (i // cols) * (th + 52)
            sheet.paste(Image.open(shot).convert('RGB').resize((tw, th), Image.LANCZOS), (x, y))
            d.text((x, y + th + 10), f'{int(t // 60):02d}:{t % 60:04.1f}  {kind}', font=label_font, fill=(156, 165, 212))
        path = os.path.join(out_dir, 'sheet.jpg')
        sheet.save(path, quality=90)
        print('stills ->', path, flush=True)
        return path

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
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    e = Edit(a.preset)
    if a.plan:
        print(json.dumps(e.plan(), ensure_ascii=False)); return 0
    for w in e.warnings: print('warn:', w, flush=True)
    if a.proxy: e.proxy()
    if a.roughcut: e.render(os.path.join(e.work, 'roughcut.mp4'), rough=True)
    if a.stills: e.stills()
    if a.build:
        e.render(a.out or os.path.join(e.exports, f'{e.name}{"_draft" if a.draft else ""}.mp4'), draft=a.draft)
    if not (a.proxy or a.roughcut or a.stills or a.build): ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
