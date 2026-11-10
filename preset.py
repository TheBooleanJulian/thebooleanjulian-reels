"""Config-driven reel presets.

A preset is a JSON file: {"stamp", "handle", "video", "scenes": [ {"kind": ..., ...}, ... ]}.
Scene kinds:  hook | plan | bts_clip | reveal | end
Each scene object answers three questions:
    dur                 how long it runs (seconds)
    src_time(t)         which moment of the BTS video it shows at scene time t (None = no video)
    draw(t, bg, state)  the finished 1080x1920 frame at time t (bg = BTS frame, if any)
"""
import json, math, os
from PIL import Image, ImageDraw, ImageEnhance
from reel import *
from kit import *
from detect import find_moon_jupiter, bright_dot
from vio import grab_frame, probe_duration, FPS
from cuts import detect_silences, segments_from_words, segments_from_silences, apply_manual_cuts, Timeline
from captions import Captions

BASE = os.path.dirname(os.path.abspath(__file__))
COLORS = {'white': WHITE, 'cyan': CYAN, 'magenta': MAG}



class Scene:
    def __init__(self, reel, cfg):
        self.reel, self.cfg = reel, cfg
        self.name = cfg.get('name', cfg['kind'])
        self.dur = float(cfg.get('dur', 3.0))
        self.has_audio = False

    @property
    def frames(self): return round(self.dur * FPS)

    @property
    def adur(self): return self.frames / FPS

    def src_time(self, t): return None
    def new_state(self): return {}

    @property
    def needs_video(self): return self.src_time(0.0) is not None

    def still_t(self): return self.cfg.get('still', {}).get('t', self.dur * 0.7)

    def still_bg(self):
        s = self.cfg.get('still', {})
        if 'bg' in s: return self.reel.img(s['bg'])
        st = self.src_time(self.still_t())
        return grab_frame(self.reel.video, st) if st is not None else None


# ---------------------------------------------------------------- hook
class Hook(Scene):
    def __init__(self, reel, cfg):
        super().__init__(reel, cfg)
        self.dur = float(cfg['clip']['dur'])

    def src_time(self, t): return self.cfg['clip']['start'] + t

    def draw(self, t, bg, st):
        c = self.cfg
        y0 = c.get('text_y', 880)
        img = vgrad(bg, top_h=420, bot_h=0, strength=0.6, band=(y0 + 80, 260))
        o = Overlay()
        o.brackets(ease(prog(t, 0.0, 0.5)))
        hud_header(o, t, self.reel.stamp, a=ease(prog(t, 0.1, 0.4)))
        if c.get('track', True):  # ring on the brightest spot, lightly smoothed
            d = bright_dot(bg); p = st.get('prev')
            st['prev'] = d if p is None else ((p[0] + d[0]) / 2, (p[1] + d[1]) / 2)
            dot = st['prev']
            reticle(o, dot, 40, t, MAG, 0.95, prog(t, 0.3, 0.7), ticks=False)
            if c.get('marker'):
                line_in(o, t, 0.8, (dot[0] + 62, dot[1] - 4), c['marker'], font('exo', 46, 800), MAG, anchor='lm', glow=True)
        exo = font('exo', 70, 700)
        n = len(c['lines'])
        for i, s in enumerate(c['lines']):
            line_in(o, t, 0.25 + 0.3 * i, (W / 2, y0 + 88 * i), s, exo, WHITE, anchor='mm')
        line_in(o, t, 0.25 + 0.3 * (n - 1) + 0.6, (W / 2, y0 + 88 * (n - 1) + 122), c['emphasis'],
                font('orb', 92, 800), CYAN, anchor='mm', glow=True, spacing=4)
        return o.compose(img)


# ---------------------------------------------------------------- plan
class Plan(Scene):
    """A still (sky map, floor plan, mood board...) with two marked subjects, a connector and a chip."""
    def __init__(self, reel, cfg):
        super().__init__(reel, cfg)
        im = reel.img(cfg['image']).copy()
        d = ImageDraw.Draw(im)
        for b in cfg.get('mask_boxes', []):  # paint out app chrome / unwanted glyphs
            d.rectangle(b, fill=tuple(cfg['bg_color']))
        self.im = im

    def draw(self, t, bg, st):
        c = self.cfg
        s = c['start_scale'] + (c['end_scale'] - c['start_scale']) * ease_io(t / self.dur)
        A, R, B = c['a']['center'], c['a']['radius'], c['b']
        mid = ((A[0] + B[0]) / 2, (A[1] + B[1]) / 2)
        img, mp = zoom_view(self.im, s, mid, (540, 840), bg=tuple(c['bg_color']))
        img = vgrad(img, top_h=300, bot_h=680, strength=0.85)
        o = Overlay()
        o.brackets(ease(prog(t, 0, 0.4)))
        ac, bc = mp(A), mp(B)
        o.dashed_circle(ac, R * s + 6, CYAN, n=72, duty=0.5, width=2, alpha=0.75, rot=-90, progress=ease(prog(t, 0.3, 0.9)))
        o.dashed_line(ac, bc, CYAN, dash=12, gap=9, width=2, alpha=0.9, progress=ease(prog(t, 0.9, 0.6)))
        if t > 0.9:
            a = ease(prog(t, 0.9, 0.3))
            o.d.ellipse([(ac[0] - 6) * SS, (ac[1] - 6) * SS, (ac[0] + 6) * SS, (ac[1] + 6) * SS], fill=CYAN + (int(255 * a),))
        off = c.get('chip_offset', [-150, 0])
        chip(o, ((ac[0] + bc[0]) / 2 + off[0], (ac[1] + bc[1]) / 2 + off[1]), c['chip_text'], font('exo', 40, 800), MAG,
             ease(prog(t, 1.4, 0.4)))
        l1, l2 = c['lines']
        line_in(o, t, 0.5, (W / 2, 1360), l1, font('exo', 66, 700), WHITE, anchor='mm')
        line_in(o, t, 0.8, (W / 2, 1446), l2, font('exo', 66, 800), CYAN, anchor='mm', glow=True)
        if c.get('footnote'):
            line_in(o, t, 1.2, (W / 2, 1522), c['footnote'], font('mono', 30), MAG, anchor='mm', spacing=1)
        return fade_from_black(o.compose(img), t, c.get('fade_in', 0.2))


# ---------------------------------------------------------------- bts clip
class BtsClip(Scene):
    """BTS footage with a speed-ramp list and either a 'bottom' (callout) or 'top' (caption) layout."""
    def __init__(self, reel, cfg):
        super().__init__(reel, cfg)
        self.ramp = cfg['ramp']
        self.dur = sum(seg['dur'] for seg in self.ramp)

    def src_time(self, t):
        acc = 0.0
        for k, seg in enumerate(self.ramp):
            if t < acc + seg['dur'] or k == len(self.ramp) - 1:
                u = t - acc
                if 'end' in seg: return seg['start'] + (seg['end'] - seg['start']) * u / seg['dur']
                return seg['start'] + u * seg.get('speed', 1.0)
            acc += seg['dur']

    def _track(self, co, bg, st):
        mode = co.get('track', 'blob_companion')
        if mode == 'fixed': return tuple(co['point'])
        if mode == 'brightest': return bright_dot(bg)
        _, j = find_moon_jupiter(bg)  # bright blob + small companion dot beside it
        last = st.get('last')
        if j is not None and (last is None or math.dist(j, last) < 120):
            st['last'] = (float(j[0]), float(j[1]))
        return st.get('last')

    def draw(self, t, bg, st):
        c = self.cfg
        o = Overlay()
        if c.get('layout', 'bottom') == 'top':
            img = vgrad(bg, top_h=640, bot_h=0, strength=0.8)
            o.brackets(); hud_header(o, t, self.reel.stamp)
            l1, l2 = c['lines']
            line_in(o, t, 0.15, (W / 2, 420), l1, font('exo', 66, 700), WHITE, anchor='mm')
            line_in(o, t, 0.45, (W / 2, 506), l2, font('exo', 72, 800), CYAN, anchor='mm', glow=True)
            if c.get('sub'):
                line_in(o, t, 0.8, (W / 2, 584), c['sub'], font('mono', 30), MAG, anchor='mm', spacing=1)
            return fade_from_black(o.compose(img), t, c['fade_in']) if c.get('fade_in') else o.compose(img)
        img = vgrad(bg, top_h=420, bot_h=520, strength=0.7)
        o.brackets(); hud_header(o, t, self.reel.stamp)
        if c.get('tag_right'):
            o.text((W - 72, 286), c['tag_right'], font('mono', 30), MAG, 1, anchor='rm', spacing=1)
        co = c.get('callout')
        at = co['at'] if co else None
        if co is None or t < at:
            if c.get('lines_before'):
                line_in(o, t, 0.1, (W / 2, 1330), c['lines_before'][0], font('exo', 62, 700), WHITE, anchor='mm')
            if co is None or t < at - 0.05: return o.compose(img)
        pt = self._track(co, bg, st) if t >= at - 0.05 else None
        if t >= at:
            rt = t - at
            if pt is not None:
                reticle(o, pt, 26, t, CYAN, 1, prog(rt, 0.0, 0.55))
                tag(o, rt, 0.35, (pt[0] + 32, pt[1] + 24), 'right', co['label'], co.get('sub', ''), scol=WHITE)
            a1, a2 = c['lines_after']
            line_in(o, rt, 0.9, (W / 2, 1330), a1, font('exo', 54, 600), WHITE, anchor='mm')
            line_in(o, rt, 1.3, (W / 2, 1420), a2, font('exo', 76, 800), CYAN, anchor='mm', glow=True)
        return o.compose(img)


# ---------------------------------------------------------------- reveal
class Reveal(Scene):
    """The hero still: shutter flash, slow push-in, tags that stay attached to the subjects."""
    def __init__(self, reel, cfg):
        super().__init__(reel, cfg)
        self.hero = reel.img(cfg['image'])
        self.fit = W / self.hero.width
        a = cfg['anchor']
        self.out = (a[0] * self.fit, a[1] * self.fit + (H - self.hero.height * self.fit) / 2)
        self.zend = cfg.get('z_end', 1.15)

    def draw(self, t, bg, st):
        c = self.cfg
        z = 1.0 + (self.zend - 1.0) * ease_io(t / self.dur)
        s = self.fit * z
        img, mp = zoom_view(self.hero, s, c['anchor'], self.out)
        flash = clamp(1 - t / 0.35)
        if flash > 0:
            img = Image.blend(img, Image.new('RGB', (W, H), (225, 255, 255)), flash * 0.9)
        img = vgrad(img, top_h=560, bot_h=380, strength=0.8)
        o = Overlay()
        o.brackets(ease(prog(t, 0.3, 0.4)))
        if c.get('label'):
            o.text((72, 286), c['label'], font('mono', 30), MAG, ease(prog(t, 0.35, 0.4)), anchor='lm', spacing=1)
        for tg in c['tags']:
            side, at = tg['side'], tg['at']
            if tg['kind'] == 'limb':  # leader starts on the edge of a large round-ish subject
                cx, cy = mp(tg['center']); r = tg['radius'] * s; a = math.radians(tg['angle'])
                p0 = (cx + math.cos(a) * (r + 10), cy + math.sin(a) * (r + 10))
                tag(o, t, at, p0, side, tg['title'], tg['sub'], tcol=COLORS[tg.get('color', 'cyan')],
                    title_glow=tg.get('glow', True), drop=tg.get('drop', 90), run=tg.get('run', 60))
            else:  # 'reticle': ring on a small point subject
                pc = mp(tg['point'])
                reticle(o, pc, 24, t, CYAN, 1, prog(t, at - 0.3, 0.5))
                tag(o, t, at, (pc[0] + 28, pc[1] + 22), side, tg['title'], tg['sub'], tcol=COLORS[tg.get('color', 'cyan')],
                    title_glow=tg.get('glow', True), drop=tg.get('drop', 90), run=tg.get('run', 60))
        hl = c['headline']; y = hl.get('y', 372); at = hl.get('at', 3.1)
        line_in(o, t, at, (W / 2, y), hl['lines'][0], font('exo', 60, 700), WHITE, anchor='mm')
        for k, s_ in enumerate(hl['lines'][1:]):
            line_in(o, t, at + 0.3 + 0.2 * k, (W / 2, y + 80 + 76 * k), s_, font('exo', 66, 800), CYAN, anchor='mm', glow=True)
        return o.compose(img)


# ---------------------------------------------------------------- end card
class End(Scene):
    """Glides the hero still out to a roomy end-card framing and lays the title block around it."""
    def __init__(self, reel, cfg):
        super().__init__(reel, cfg)
        self.rv = next((s for s in reel.scenes_raw if s['kind'] == 'reveal'), None)
        if self.rv is None:  # no hero photo: plain branded end card
            self.hero = None
            return
        self.hero = reel.img(self.rv['image'])
        self.fit = W / self.hero.width
        a = self.rv['anchor']
        self.out0 = (a[0] * self.fit, a[1] * self.fit + (H - self.hero.height * self.fit) / 2)
        self.s0 = self.fit * self.rv.get('z_end', 1.15)
        f = cfg['frame']
        self.s1 = self.fit * f['scale']
        self.out1 = (540 + (a[0] - f['subject_cx']) * self.s1, f['top_y'] + (a[1] - f['subject_top']) * self.s1)
        self.anchor = a

    def draw(self, t, bg, st):
        c = self.cfg
        o = Overlay()
        if self.hero is None:
            img = Image.new('RGB', (W, H), (0, 0, 0))
            for k, line in enumerate(c.get('cta', [])):
                line_in(o, t, 0.9 + 0.25 * k, (W / 2, 820 + 96 * k), line, font('exo', 70, 800),
                        WHITE if k == 0 else CYAN, anchor='mm', glow=k > 0)
        else:
            m = ease_io(prog(t, 0, 0.8))
            s = self.s0 + (self.s1 - self.s0) * m
            out = (self.out0[0] + (self.out1[0] - self.out0[0]) * m, self.out0[1] + (self.out1[1] - self.out0[1]) * m)
            img, _ = zoom_view(self.hero, s, self.anchor, out)
            img = ImageEnhance.Brightness(img).enhance(1 - 0.25 * m)
            img = vgrad(img, top_h=560, bot_h=380, strength=0.8)
        o.brackets()
        title = font('orb', 76, 900)
        parts = c['title']
        full = ''.join(p['text'] for p in parts)
        x0 = (W - title.getlength(full) / SS) / 2
        pa = ease(prog(t, 0.3, 0.5))
        for k, p in enumerate(parts):
            x = x0 + title.getlength(''.join(q['text'] for q in parts[:k])) / SS
            o.text((x, 380), p['text'], title, COLORS[p['color']], pa, anchor='lm', glow=p['color'] != 'white')
        line_in(o, t, 0.55, (W / 2, 458), c['subtitle'], font('mono', 32), WHITE, anchor='mm')
        mono = font('mono', 30)
        line_in(o, t, 0.8, (W / 2, 1290), c['info'][0], mono, WHITE, anchor='mm', spacing=1)
        line_in(o, t, 0.9, (W / 2, 1336), c['info'][1], mono, CYAN, anchor='mm')
        dp = ease(prog(t, 1.0, 0.5))
        o.line([(W / 2 - 200 * dp, 1386), (W / 2 + 200 * dp, 1386)], MAG, 2, 1)
        line_in(o, t, 1.15, (W / 2, 1462), self.reel.handle, font('orb', 72, 800), CYAN, anchor='mm', glow=True, spacing=2)
        im = o.compose(img)
        fo = clamp((self.dur - t) / c.get('fade_out', 0.4))
        return im if fo >= 1 else Image.blend(Image.new('RGB', (W, H)), im, fo)


# ---------------------------------------------------------------- talking head
class TalkingHead(Scene):
    """Talk-to-camera footage: auto rough cut (drop pauses), word-timed captions, punch-ins, lower third.
    Audio is carried through (the cut audio is assembled by make_reel.py from the same keep-segments)."""
    def __init__(self, reel, cfg):
        super().__init__(reel, cfg)
        self.has_audio = True
        path = reel.resolve(cfg['transcript'])
        if not os.path.exists(path):
            raise FileNotFoundError(f'no transcript at {path}: run  python3 transcribe.py <recording>  first')
        words = json.load(open(path, encoding='utf-8'))['words']
        clip = cfg.get('clip', {})
        total = probe_duration(reel.video)
        lo, hi = clip.get('start', 0.0), clip.get('end') or total
        words = [w for w in words if w['start'] >= lo - 0.01 and w['end'] <= hi + 0.01]
        tr = cfg.get('trim', {})
        mode = tr.get('mode', 'words')
        if mode == 'words':
            segs = segments_from_words(words, tr.get('max_gap', 0.5), tr.get('pad', 0.12))
        elif mode == 'silence':
            sil, tot = detect_silences(reel.video, tr.get('noise_db', -35), tr.get('min_dur', 0.45))
            segs = segments_from_silences(sil, tot, tr.get('pad', 0.08))
        else:
            segs = [(lo, hi)]
        segs = [(max(lo, a), min(hi, b)) for a, b in segs if min(hi, b) > max(lo, a)]
        self.tl = Timeline(apply_manual_cuts(segs, cfg.get('cuts', [])))
        self.dur = self.tl.dur
        self.caps = Captions(self.tl.remap_words(words), cfg.get('captions')) if cfg.get('captions', True) is not False else None

    @property
    def audio_segments(self): return self.tl.segs

    def src_time(self, t): return self.tl.src_time(min(t, self.tl.dur - 1e-3))

    def _punch(self, t):
        amt = 0.0
        for p in self.cfg.get('punch_ins', []):
            up = ease_io(prog(t, p['at'], 0.18)); down = ease_io(prog(t, p['at'] + p['dur'] - 0.18, 0.18))
            amt = max(amt, (p['scale'] - 1.0) * up * (1 - down))
        return 1.0 + amt

    def draw(self, t, bg, st):
        if self.reel.plain: return bg  # rough-cut preview: footage only
        c = self.cfg
        k = self._punch(t)
        if k > 1.001:  # punch-in = crop toward the face (focus is a fraction of the frame)
            fx, fy = c.get('focus', [0.5, 0.38])
            cw, ch = W / k, H / k
            x0 = clamp(fx * W - cw / 2, 0, W - cw); y0 = clamp(fy * H - ch / 2, 0, H - ch)
            bg = bg.resize((W, H), Image.LANCZOS, box=(x0, y0, x0 + cw, y0 + ch))
        img = vgrad(bg, top_h=0, bot_h=720, strength=0.55)
        o = Overlay()
        if c.get('hud', True):
            o.brackets(ease(prog(t, 0, 0.5)))
            if self.reel.stamp: hud_header(o, t, self.reel.stamp, a=ease(prog(t, 0.1, 0.4)))
        lt = c.get('lower_third')
        if lt:
            a = ease(prog(t, lt['at'], 0.4)) * (1 - ease(prog(t, lt['at'] + lt['dur'], 0.4)))
            if a > 0:
                o.line([(72, 1086), (72 + 280 * ease(prog(t, lt['at'], 0.5)), 1086)], MAG, 3, a)
                o.text((72, 1104), lt['name'], font('orb', 42, 800), WHITE, a, anchor='la', spacing=2)
                o.text((72, 1160), lt['title'], font('mono', 30), CYAN, a, anchor='la', spacing=1)
        if self.caps: self.caps.draw(o, t)
        return o.compose(img)


KINDS = {'hook': Hook, 'plan': Plan, 'bts_clip': BtsClip, 'reveal': Reveal, 'end': End, 'talking_head': TalkingHead}


class Reel:
    def __init__(self, cfg_path, plain=False):
        self.cfg = json.load(open(cfg_path, encoding='utf-8'))
        self.dir = os.path.dirname(os.path.abspath(cfg_path))
        self.plain = plain  # rough-cut preview mode: footage only, no graphics
        self.name = self.cfg.get('name', os.path.splitext(os.path.basename(cfg_path))[0])
        self.stamp = self.cfg.get('stamp', '')
        self.handle = self.cfg.get('handle', STYLE.get('handle', ''))
        self._img = {}
        self.video = os.environ.get('REEL_VIDEO') or self.resolve(self.cfg.get('video', 'src/bts.mp4'))
        self.scenes_raw = self.cfg['scenes']
        self.scenes = [KINDS[s['kind']](self, s) for s in self.scenes_raw]

    def resolve(self, p):
        """Media paths: absolute, or relative to the preset's folder, or to the project root."""
        if os.path.isabs(p): return p
        for base in (self.dir, BASE):
            c = os.path.join(base, p)
            if os.path.exists(c): return os.path.normpath(c)
        return os.path.normpath(os.path.join(self.dir, p))

    def img(self, path):
        if path not in self._img:
            full = self.resolve(path)
            if not os.path.exists(full):
                raise FileNotFoundError(f'media not found: {full}  (put it in the project src/ folder or fix the preset)')
            self._img[path] = Image.open(full).convert('RGB')
        return self._img[path]
