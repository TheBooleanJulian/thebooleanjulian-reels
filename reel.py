"""Reel engine: style loading, fonts, easing, Overlay drawing. Look comes from style/signature.json."""
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

W, H = 1080, 1920
SS = 2  # supersample factor for overlay drawing
import os, json
BASE = os.path.dirname(os.path.abspath(__file__))
F = os.path.join(BASE, 'fonts') + '/'


def load_style(path=None):
    path = path or os.environ.get('REEL_STYLE') or os.path.join(BASE, 'style', 'signature.json')
    with open(path, encoding='utf-8') as fh: return json.load(fh)


STYLE = load_style()
CYAN = tuple(STYLE['colors']['cyan'])
MAG = tuple(STYLE['colors']['magenta'])
WHITE = tuple(STYLE['colors']['white'])
SAFE_TOP, SAFE_BOTTOM = STYLE['safe']['top'], STYLE['safe']['bottom']

_fc = {}
def font(name, size, wght=None):
    key = (name, size, wght)
    if key not in _fc:
        spec = STYLE['fonts'][name]
        f = ImageFont.truetype(F + spec['file'], size * SS)
        if wght and spec.get('variable'):
            f.set_variation_by_axes([wght])
        _fc[key] = f
    return _fc[key]

# ---------- easing ----------
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease(x):  # easeOutCubic
    x = clamp(x); return 1 - (1 - x) ** 3
def ease_io(x):
    x = clamp(x); return x * x * (3 - 2 * x)
def prog(t, start, dur): return clamp((t - start) / dur)

# ---------- overlay ----------
class Overlay:
    def __init__(self):
        self.img = Image.new('RGBA', (W * SS, H * SS), (0, 0, 0, 0))
        self.glow = Image.new('RGBA', (W * SS, H * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.g = ImageDraw.Draw(self.glow)

    @staticmethod
    def S(v): return v * SS

    def text(self, xy, s, fnt, fill, alpha=1.0, anchor='la', spacing=0, glow=False, shadow=True):
        if alpha <= 0 or not s: return
        x, y = xy[0] * SS, xy[1] * SS
        a = int(255 * alpha)
        col = fill + (a,)
        if spacing:
            # manual letter spacing; compute total width for anchors
            widths = [fnt.getlength(c) for c in s]
            total = sum(widths) + spacing * SS * (len(s) - 1)
            if anchor[0] == 'm': x -= total / 2
            elif anchor[0] == 'r': x -= total
            anc = 'l' + {'t': 'a', 'b': 's'}.get(anchor[1], anchor[1])
            cx = x
            for c, w in zip(s, widths):
                if shadow: self.g.text((cx, y), c, font=fnt, fill=(0, 0, 0, int(a * 0.9)), anchor=anc)
                if glow: self.g.text((cx, y), c, font=fnt, fill=fill + (int(a * 0.8),), anchor=anc)
                self.d.text((cx, y), c, font=fnt, fill=col, anchor=anc)
                cx += w + spacing * SS
        else:
            if shadow: self.g.text((x, y), s, font=fnt, fill=(0, 0, 0, int(a * 0.9)), anchor=anchor)
            if glow: self.g.text((x, y), s, font=fnt, fill=fill + (int(a * 0.8),), anchor=anchor)
            self.d.text((x, y), s, font=fnt, fill=col, anchor=anchor)

    def line(self, pts, color, width=2, alpha=1.0, glow=True):
        if alpha <= 0: return
        p = [(x * SS, y * SS) for x, y in pts]
        self.d.line(p, fill=color + (int(255 * alpha),), width=width * SS, joint='curve')
        if glow: self.g.line(p, fill=color + (int(200 * alpha),), width=width * SS * 3)

    def arc(self, c, r, a0, a1, color, width=3, alpha=1.0, glow=True):
        if alpha <= 0 or a1 <= a0: return
        box = [(c[0] - r) * SS, (c[1] - r) * SS, (c[0] + r) * SS, (c[1] + r) * SS]
        self.d.arc(box, a0, a1, fill=color + (int(255 * alpha),), width=width * SS)
        if glow: self.g.arc(box, a0, a1, fill=color + (int(200 * alpha),), width=width * SS * 3)

    def dashed_circle(self, c, r, color, n=48, duty=0.55, width=2, alpha=1.0, rot=0, progress=1.0):
        seg = 360 / n
        for i in range(int(n * progress)):
            a0 = rot + i * seg
            self.arc(c, r, a0, a0 + seg * duty, color, width, alpha, glow=False)

    def dashed_line(self, p0, p1, color, dash=14, gap=10, width=2, alpha=1.0, progress=1.0):
        L = math.dist(p0, p1) * progress
        if L <= 0: return
        ux, uy = (p1[0] - p0[0]) / math.dist(p0, p1), (p1[1] - p0[1]) / math.dist(p0, p1)
        s = 0
        while s < L:
            e = min(s + dash, L)
            self.line([(p0[0] + ux * s, p0[1] + uy * s), (p0[0] + ux * e, p0[1] + uy * e)], color, width, alpha, glow=False)
            s += dash + gap

    def rect(self, box, fill=None, outline=None, width=2, alpha=1.0):
        b = [v * SS for v in box]
        self.d.rectangle(b, fill=(fill + (int(255 * alpha),)) if fill else None,
                         outline=(outline + (int(255 * alpha),)) if outline else None, width=width * SS)

    def brackets(self, alpha=1.0, inset=48, top=SAFE_TOP, bottom=SAFE_BOTTOM, L=46, color=CYAN):
        """HUD corner brackets framing the safe area."""
        x0, x1 = inset, W - inset
        for (x, y, dx, dy) in [(x0, top, 1, 1), (x1, top, -1, 1), (x0, bottom, 1, -1), (x1, bottom, -1, -1)]:
            self.line([(x, y + dy * L), (x, y), (x + dx * L, y)], color, 2, alpha * 0.85, glow=False)

    def compose(self, base):
        glow = self.glow.filter(ImageFilter.GaussianBlur(7 * SS))
        layer = Image.alpha_composite(glow, self.img).resize((W, H), Image.LANCZOS)
        out = base.convert('RGBA')
        out.alpha_composite(layer)
        return out.convert('RGB')


def vgrad(img, top_h=0, bot_h=0, strength=0.75, band=None):
    """Darken top/bottom (or a band) for legibility."""
    g = Image.new('L', (1, H), 0)
    px = g.load()
    for y in range(H):
        v = 0
        if top_h and y < top_h: v = max(v, (1 - y / top_h))
        if bot_h and y > H - bot_h: v = max(v, (y - (H - bot_h)) / bot_h)
        if band:
            c, hw = band
            v = max(v, clamp(1 - abs(y - c) / hw) ** 0.8)
        px[0, y] = int(255 * strength * clamp(v))
    mask = g.resize((W, H))
    black = Image.new('RGB', (W, H), (0, 0, 0))
    return Image.composite(black, img, mask)


def hud_header(o, t, label='05:04 SGT | 03.11.2026', a=1.0):
    """Small REC-style header tag at top-left of safe zone."""
    blink = 1.0 if int(t * 2) % 2 == 0 else 0.35
    o.d.ellipse([(72 - 7) * SS, (286 - 7) * SS, (72 + 7) * SS, (286 + 7) * SS], fill=MAG + (int(255 * a * blink),))
    o.g.ellipse([(72 - 12) * SS, (286 - 12) * SS, (72 + 12) * SS, (286 + 12) * SS], fill=MAG + (int(160 * a * blink),))
    o.text((92, 286), label, font('mono', 30), CYAN, a, anchor='lm', spacing=1)


def line_in(o, t, start, xy, s, fnt, fill, anchor='la', spacing=0, dur=0.45, rise=26, glow=False):
    p = ease(prog(t, start, dur))
    if p <= 0: return
    o.text((xy[0], xy[1] + (1 - p) * rise), s, fnt, fill, p, anchor=anchor, spacing=spacing, glow=glow)


def typewriter(o, t, start, cps, xy, s, fnt, fill, anchor='la', cursor=True):
    n = int(clamp((t - start) * cps, 0, len(s)))
    if t < start: return
    shown = s[:n]
    o.text(xy, shown, fnt, fill, 1.0, anchor=anchor)
    if cursor and (n < len(s) or int(t * 3) % 2 == 0):
        x = xy[0] + fnt.getlength(shown) / SS + 6
        o.rect((x, xy[1] + 6, x + 16, xy[1] + fnt.size / SS * 0.95), fill=CYAN)
