"""Drawing helpers shared by every preset: zoom with coordinate mapping, reticle, leader/tag, chip."""
import math
from PIL import Image
from reel import *


def zoom_view(src, s, anchor_src, anchor_out, bg=(0, 0, 0)):
    """Render src scaled by s so that anchor_src lands at anchor_out. Returns image + mapping fn."""
    ox = anchor_out[0] - anchor_src[0] * s
    oy = anchor_out[1] - anchor_src[1] * s
    x0, y0 = -ox / s, -oy / s
    x1, y1 = x0 + W / s, y0 + H / s
    canvas = Image.new('RGB', (W, H), bg)
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(src.width, x1), min(src.height, y1)
    piece = src.resize((round((cx1 - cx0) * s), round((cy1 - cy0) * s)), Image.LANCZOS,
                       box=(cx0, cy0, cx1, cy1))
    canvas.paste(piece, (round(cx0 * s + ox), round(cy0 * s + oy)))
    mp = lambda p: (p[0] * s + ox, p[1] * s + oy)
    return canvas, mp


def reticle(o, c, r, t, color=CYAN, a=1.0, prog_=1.0, ticks=True):
    o.arc(c, r, -90, -90 + 360 * ease(prog_), color, 3, a)
    o.dashed_circle(c, r + 14, color, n=24, duty=0.5, width=1, alpha=0.6 * a * ease(prog_), rot=t * 40)
    if ticks and prog_ > 0.5:
        ta = a * ease((prog_ - 0.5) * 2)
        for ang in (0, 90, 180, 270):
            ux, uy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            o.line([(c[0] + ux * (r + 22), c[1] + uy * (r + 22)), (c[0] + ux * (r + 38), c[1] + uy * (r + 38))], MAG, 3, ta, glow=False)


def leader(o, p0, p1, p2, a=1.0, prog_=1.0, color=CYAN):
    """Elbow leader line p0 -> p1 -> p2, drawn progressively."""
    l1, l2 = math.dist(p0, p1), math.dist(p1, p2)
    L = (l1 + l2) * ease(prog_)
    if L <= 0: return
    if L <= l1:
        f = L / l1; o.line([p0, (p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f)], color, 2, a)
    else:
        f = (L - l1) / l2; o.line([p0, p1, (p1[0] + (p2[0] - p1[0]) * f, p1[1] + (p2[1] - p1[1]) * f)], color, 2, a)
    o.d.ellipse([(p0[0] - 5) * SS, (p0[1] - 5) * SS, (p0[0] + 5) * SS, (p0[1] + 5) * SS], fill=color + (int(255 * a),))


def tag(o, t, t0, p0, side, title, sub, tcol=CYAN, scol=CYAN, drop=90, run=60, title_glow=True):
    """Leader from p0 diagonally down to an elbow, then horizontal to the frame margin;
    the label sits BELOW the horizontal line so the leader never crosses the text."""
    sx = 1 if side == 'right' else -1
    p1 = (p0[0] + sx * run, p0[1] + drop)
    edge = W - 72 if side == 'right' else 72
    leader(o, p0, p1, (edge, p1[1]), 1, prog(t, t0, 0.5))
    anc = 'r' if side == 'right' else 'l'
    line_in(o, t, t0 + 0.3, (edge, p1[1] + 16), title, font('orb', 42, 800), tcol, anchor=anc + 't', spacing=3, glow=title_glow, rise=14)
    line_in(o, t, t0 + 0.45, (edge, p1[1] + 72), sub, font('mono', 32), scol, anchor=anc + 't', spacing=1, rise=14)


def chip(o, center, text, fnt, color, a=1.0, pad=(18, 10)):
    if a <= 0: return
    w = fnt.getlength(text) / SS
    h = fnt.size / SS
    x0, y0 = center[0] - w / 2 - pad[0], center[1] - h / 2 - pad[1]
    x1, y1 = center[0] + w / 2 + pad[0], center[1] + h / 2 + pad[1]
    o.d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=8 * SS, fill=(0, 0, 0, int(190 * a)),
                          outline=color + (int(255 * a),), width=2 * SS)
    o.text(center, text, fnt, color, a, anchor='mm', shadow=False)


def fade_from_black(img, t, d=0.2):
    a = clamp(t / d)
    return img if a >= 1 else Image.blend(Image.new('RGB', (W, H)), img, a)
