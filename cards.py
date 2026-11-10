"""Graphics for interviews and presentations: each one is a transparent full-frame PNG, drawn once at 2x and
overlaid by ffmpeg (longform.py), so a 20-minute edit doesn't redraw every frame in Python.
Look: style/signature.json -> "longform". Sizes there are fractions of the frame height."""
from PIL import Image, ImageDraw
from reel import STYLE, font, CYAN, MAG, WHITE, SS

LF = STYLE['longform']
BG = tuple(LF.get('bg', [5, 6, 13]))


def orient(W, H): return 'vertical' if H > W else 'landscape'


def pick(v, W, H):
    """Style values can be one number or {"vertical": a, "landscape": b}."""
    return v[orient(W, H)] if isinstance(v, dict) else v


class Canvas:
    def __init__(self, W, H, fill=(0, 0, 0, 0)):
        self.W, self.H = W, H
        self.im = Image.new('RGBA', (W * SS, H * SS), fill)
        self.d = ImageDraw.Draw(self.im)

    @staticmethod
    def fnt(key, px): return font(key, max(8, round(px)))

    @staticmethod
    def width(s, f): return f.getlength(s) / SS

    def text(self, xy, s, f, fill, anchor='la'):
        self.d.text((xy[0] * SS, xy[1] * SS), s, font=f, fill=tuple(fill) + (255,), anchor=anchor)

    def rect(self, box, fill, radius=0):
        self.d.rounded_rectangle([v * SS for v in box], radius=radius * SS, fill=fill)

    def done(self): return self.im.resize((self.W, self.H), Image.LANCZOS)


def wrap(s, f, max_w):
    lines, cur = [], ''
    for word in s.split():
        trial = (cur + ' ' + word).strip()
        if cur and Canvas.width(trial, f) > max_w:
            lines.append(cur); cur = word
        else:
            cur = trial
    if cur: lines.append(cur)
    return lines or ['']


def lower_third(W, H, name, title=''):
    """Name + role, bottom-left, on a soft dark plate with a magenta rule."""
    st = LF['lower_third']
    c = Canvas(W, H)
    nh, th = st['name_size'] * H, st['title_size'] * H
    nf, tf = c.fnt(st['name_font'], nh), c.fnt(st['title_font'], th)
    x, bottom = st['x'] * W, pick(st['y'], W, H) * H
    w = max(c.width(name, nf), c.width(title, tf) if title else 0)
    rule_h = max(2, round(H * 0.003))
    y_rule = bottom - (th * 1.35 if title else 0) - nh * 1.25 - nh * 0.35 - rule_h
    pad = nh * 0.45
    c.rect((x - pad, y_rule - pad, x + w + pad, bottom + pad * 0.6), BG + (round(255 * st.get('plate', 0.6)),), radius=H / 180)
    c.rect((x, y_rule, x + min(w, W * 0.22), y_rule + rule_h), MAG + (255,))
    c.text((x, y_rule + rule_h + nh * 0.35), name, nf, WHITE)
    if title: c.text((x, y_rule + rule_h + nh * 0.35 + nh * 1.25), title, tf, CYAN)
    return c.done()


def title_card(W, H, text, label=''):
    """A question or statement over dimmed footage. label = small line above (e.g. 'Question 2')."""
    st = LF['card']
    c = Canvas(W, H, (0, 0, 0, round(255 * st.get('dim', 0.6))))
    sz = pick(st['size'], W, H) * H
    f = c.fnt(st['font'], sz)
    lines = wrap(text, f, st.get('max_width', 0.8) * W)
    lh = sz * 1.22
    y = H * 0.5 - lh * len(lines) / 2
    if label:
        c.text((W / 2, y - sz * 0.9), label, c.fnt(st.get('label_font', 'mono'), sz * 0.48), CYAN, anchor='ms')
    for i, line in enumerate(lines):
        c.text((W / 2, y + lh * i + lh / 2), line, f, WHITE, anchor='mm')
    rule_w, rule_h = min(W, H) * 0.08, max(2, round(H * 0.003))
    yr = y + lh * len(lines) + sz * 0.45
    c.rect((W / 2 - rule_w / 2, yr, W / 2 + rule_w / 2, yr + rule_h), MAG + (255,))
    return c.done()


def intro_card(W, H, title, subtitle=''):
    st = LF['card']
    c = Canvas(W, H, (0, 0, 0, round(255 * st.get('dim', 0.6))))
    sz = pick(st['size'], W, H) * H * 1.2
    f = c.fnt(st['font'], sz)
    lines = wrap(title, f, st.get('max_width', 0.8) * W)
    lh = sz * 1.18
    y = H * 0.47 - lh * len(lines) / 2
    for i, line in enumerate(lines):
        c.text((W / 2, y + lh * i + lh / 2), line, f, WHITE, anchor='mm')
    yb = y + lh * len(lines)
    rule_w, rule_h = min(W, H) * 0.1, max(2, round(H * 0.003))
    c.rect((W / 2 - rule_w / 2, yb + sz * 0.3, W / 2 + rule_w / 2, yb + sz * 0.3 + rule_h), MAG + (255,))
    if subtitle:
        c.text((W / 2, yb + sz * 0.95), subtitle, c.fnt('plex', sz * 0.42), CYAN, anchor='mm')
    return c.done()


def chapter_card(W, H, index, title):
    """Small top-left section tag: '02  Q&A'."""
    st = LF['chapter']
    c = Canvas(W, H)
    sz = st['size'] * H
    nf, tf = c.fnt('mono', sz * 0.8), c.fnt(st['font'], sz)
    x = W * 0.06
    y = H * (0.15 if orient(W, H) == 'vertical' else 0.08)
    num = f'{index:02d}'
    nw = c.width(num, nf)
    tw = c.width(title, tf)
    pad = sz * 0.45
    c.rect((x - pad, y - pad, x + nw + sz * 0.7 + tw + pad, y + sz * 1.15 + pad), BG + (170,), radius=H / 180)
    c.text((x, y + sz * 0.6), num, nf, CYAN, anchor='lm')
    c.text((x + nw + sz * 0.7, y + sz * 0.6), title, tf, WHITE, anchor='lm')
    c.rect((x, y + sz * 1.15, x + nw, y + sz * 1.15 + max(2, round(H * 0.0025))), MAG + (255,))
    return c.done()


def end_card(W, H, lines, handle=''):
    """Opaque closing card: first line large, the rest smaller, a rule, then the handle."""
    st = LF['end']
    c = Canvas(W, H, BG + (255,))
    base = H * (0.034 if orient(W, H) == 'vertical' else 0.05)
    tf, lf = c.fnt(st['title_font'], base * 1.3), c.fnt(st['line_font'], base * 0.62)
    y = H * 0.42
    if lines:
        for row in wrap(lines[0], tf, W * 0.82):
            c.text((W / 2, y), row, tf, WHITE, anchor='mm'); y += base * 1.5
        for line in lines[1:]:
            c.text((W / 2, y), line, lf, WHITE, anchor='mm'); y += base * 0.95
    rule_w, rule_h = min(W, H) * 0.25, max(2, round(H * 0.003))
    y += base * 0.3
    c.rect((W / 2 - rule_w / 2, y, W / 2 + rule_w / 2, y + rule_h), MAG + (255,))
    if handle:
        c.text((W / 2, y + base * 1.3), handle, c.fnt(st['title_font'], base * 0.95), CYAN, anchor='mm')
    return c.done()
