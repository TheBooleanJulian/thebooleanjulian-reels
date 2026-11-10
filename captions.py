"""Word-timed captions: group words, pop them in, highlight the word being spoken."""
import re
from reel import *


def clean(word, fixes, upper):
    core = re.sub(r'^\W+|\W+$', '', word)
    fixed = fixes.get(core.lower())
    if fixed: word = word.replace(core, fixed)
    return word.upper() if upper else word


def group_words(words, max_words=3, max_chars=22, max_gap=0.6):
    groups, cur = [], []
    for w in words:
        chars = sum(len(x['w']) + 1 for x in cur) + len(w['w'])
        brk = cur and (len(cur) >= max_words or chars > max_chars or w['start'] - cur[-1]['end'] > max_gap
                       or re.search(r'[.!?]$', cur[-1]['w']))
        if brk: groups.append(cur); cur = []
        cur.append(w)
    if cur: groups.append(cur)
    return [{'words': g, 'start': g[0]['start'], 'end': g[-1]['end']} for g in groups]


class Captions:
    def __init__(self, words, overrides=None):
        c = {**STYLE['caption'], **(overrides or {})}
        self.c = c
        fixes = {**STYLE.get('fixes', {}), **c.get('fixes', {})}
        ws = [{**w, 'w': clean(w['w'], fixes, c.get('uppercase'))} for w in words]
        self.groups = group_words(ws, c['max_words'], c['max_chars'])

    def active(self, t):
        for i, g in enumerate(self.groups):
            nxt = self.groups[i + 1]['start'] if i + 1 < len(self.groups) else 1e9
            end = min(g['end'] + self.c['hold_after'], nxt)
            if g['start'] - 0.04 <= t < end: return g
        return None

    def draw(self, o, t):
        g = self.active(t)
        if not g: return
        c = self.c
        a_in = ease(prog(t, g['start'] - 0.04, c['pop']))           # whole line pops in
        size = c['size']
        base = font(c['font'], size, c['weight'])
        words = [w['w'] for w in g['words']]
        space = base.getlength(' ') / SS
        widths = [base.getlength(w) / SS for w in words]
        total = sum(widths) + space * (len(words) - 1)
        if total > c['max_width']:                                   # shrink to fit, never overflow
            size = int(size * c['max_width'] / total)
            base = font(c['font'], size, c['weight'])
            space = base.getlength(' ') / SS
            widths = [base.getlength(w) / SS for w in words]
            total = sum(widths) + space * (len(words) - 1)
        x = (W - total) / 2
        y = c['y'] + (1 - a_in) * 24
        for w, wd, word in zip(g['words'], widths, words):
            on = w['start'] <= t < w['end'] + 0.02
            col = COL(c['highlight']) if on else COL(c['color'])
            f = base
            if on:                                                   # spoken word swells slightly
                k = 1 + 0.08 * ease(prog(t, w['start'], 0.1))
                f = font(c['font'], int(size * k), c['weight'])
            o.d.text(((x + wd / 2) * SS, y * SS), word, font=f, fill=col + (int(255 * a_in),), anchor='mm',
                     stroke_width=c['stroke'] * SS, stroke_fill=(0, 0, 0, int(255 * a_in)))
            x += wd + space


def COL(name): return {'white': WHITE, 'cyan': CYAN, 'magenta': MAG}[name]
