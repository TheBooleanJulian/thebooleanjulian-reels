"""Captions for interviews and presentations: cue grouping, .ass (burned in by ffmpeg/libass), .srt sidecar, chapters.

Two caption modes:
  words  a few words at a time, the spoken word highlighted (interviews, vertical reels)
  lines  subtitle lines of up to max_chars, one or two lines per cue (presentations, long viewing)
Word times are on the EDITED timeline (cuts.Timeline.remap_words). Pure functions: no ffmpeg, no PIL.
"""
import re
from captions import clean, group_words


def _ts_ass(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f'{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}'


def _ts_srt(t):
    ms = int(round(max(0.0, t) * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def _ass_text(s):
    return s.replace('\\', '⧵').replace('{', '(').replace('}', ')')


def ass_color(rgb, alpha=0):
    """(r, g, b) -> &HAABBGGRR (alpha 0 = opaque)."""
    r, g, b = rgb
    return f'&H{alpha:02X}{b:02X}{g:02X}{r:02X}'


def split_lines(text, max_chars):
    """One line if it fits, else two lines balanced around the middle space."""
    if len(text) <= max_chars or ' ' not in text: return [text]
    spaces = [i for i, c in enumerate(text) if c == ' ']
    best = min(spaces, key=lambda i: max(i, len(text) - i - 1))
    return [text[:best], text[best + 1:]]


def line_cues(words, max_chars=42, max_lines=2, max_dur=6.0, gap=0.8, min_dur=1.0):
    """Words -> subtitle cues [{start, end, lines}]. Breaks at long pauses, sentence ends, length and duration."""
    cues, cur = [], []
    limit = max_chars * max_lines

    def flush():
        if cur: cues.append({'start': cur[0]['start'], 'end': cur[-1]['end'], 'text': ' '.join(w['w'] for w in cur)})
    for w in words:
        if cur:
            text_len = len(' '.join(x['w'] for x in cur)) + 1 + len(w['w'])
            sentence_done = re.search(r'[.!?]$', cur[-1]['w']) and len(' '.join(x['w'] for x in cur)) > max_chars * 0.5
            if (text_len > limit or w['start'] - cur[-1]['end'] > gap or w['end'] - cur[0]['start'] > max_dur
                    or sentence_done):
                flush(); cur = []
        cur.append(w)
    flush()
    for i, c in enumerate(cues):                      # readable minimum, never overlapping the next cue
        nxt = cues[i + 1]['start'] - 0.04 if i + 1 < len(cues) else c['end'] + 2
        c['end'] = min(max(c['end'] + 0.2, c['start'] + min_dur), nxt)
        c['lines'] = split_lines(c.pop('text'), max_chars)
    return cues


def word_events(words, max_words=3, max_chars=22, hold_after=0.25):
    """Words -> [(start, end, [(word, active)], first_in_group)]: the group stays up, the highlight walks along it."""
    groups = group_words(words, max_words, max_chars)
    ev = []
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1]['start'] if gi + 1 < len(groups) else 1e9
        ws = g['words']
        for i, w in enumerate(ws):
            a = g['start'] - 0.04 if i == 0 else w['start']
            b = ws[i + 1]['start'] if i + 1 < len(ws) else min(g['end'] + hold_after, nxt)
            if b > a: ev.append((a, b, [(x['w'], k == i) for k, x in enumerate(ws)], i == 0))
    for k in range(len(ev) - 1):          # never overlap: libass would stack the two groups on top of each other
        a, b, parts, first = ev[k]
        ev[k] = (a, min(b, ev[k + 1][0]), parts, first)
    return [e for e in ev if e[1] - e[0] > 0.005]


def prep_words(words, fixes=None, uppercase=False):
    fixes = fixes or {}
    return [{**w, 'w': clean(w['w'], fixes, uppercase)} for w in words]


def build_ass(words, mode, W, H, st, colors):
    """The .ass script. st = the captions_words / captions_lines style block (already resolved for the orientation)."""
    size = round(st['size'] * H)
    white, hi = colors[st.get('color', 'white')], colors[st.get('highlight', 'cyan')]
    margin_v = round((1 - st['y']) * H)
    margin_lr = round(W * 0.06)
    if mode == 'lines':
        box_a = round(255 * (1 - st.get('box', 0.6)))
        style = (f"Style: Cap,{st['family']},{size},{ass_color(white)},{ass_color(white)},{ass_color((0, 0, 0), box_a)},"
                 f"{ass_color((0, 0, 0), box_a)},{-1 if st.get('bold') else 0},0,0,0,100,100,0,0,3,{max(2, round(size * 0.22))},0,2,"
                 f"{margin_lr},{margin_lr},{margin_v},1")
    else:
        outline = max(2, round(st.get('outline', 0.004) * H))
        style = (f"Style: Cap,{st['family']},{size},{ass_color(white)},{ass_color(white)},{ass_color((0, 0, 0))},"
                 f"{ass_color((0, 0, 0), 128)},{-1 if st.get('bold') else 0},0,0,0,100,100,0,0,1,{outline},0,2,"
                 f"{margin_lr},{margin_lr},{margin_v},1")
    out = ['[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {W}', f'PlayResY: {H}', 'WrapStyle: 2',
           'ScaledBorderAndShadow: yes', '', '[V4+ Styles]',
           'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, '
           'Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, '
           'MarginR, MarginV, Encoding', style, '', '[Events]',
           'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    if mode == 'lines':
        cues = line_cues(words, st['max_chars'], st.get('max_lines', 2), st.get('max_dur', 6.0))
        for c in cues:
            out.append(f"Dialogue: 0,{_ts_ass(c['start'])},{_ts_ass(c['end'])},Cap,,0,0,0,,"
                       + '\\N'.join(_ass_text(l) for l in c['lines']))
    else:
        on_tag = '{\\c&H' + ass_color(hi)[4:] + '&}'          # inline colour is &HBBGGRR& (no alpha byte)
        pos = '{\\an2\\pos(%d,%d)}' % (W // 2, H - margin_v)      # pinned: a group never drifts up or down
        fade = '{\\fad(90,0)}'
        for a, b, parts, first in word_events(words, st.get('max_words', 3), st.get('max_chars', 22), st.get('hold_after', 0.25)):
            txt = ' '.join((on_tag + _ass_text(w) + '{\\r}') if on else _ass_text(w) for w, on in parts)
            out.append(f"Dialogue: 0,{_ts_ass(a)},{_ts_ass(b)},Cap,,0,0,0,,{pos}{fade if first else ''}{txt}")
    return '\n'.join(out) + '\n'


def build_srt(words, max_chars=42):
    cues = line_cues(words, max_chars)
    return '\n'.join(f"{i}\n{_ts_srt(c['start'])} --> {_ts_srt(c['end'])}\n" + '\n'.join(c['lines']) + '\n'
                     for i, c in enumerate(cues, 1))


def chapter_lines(chapters):
    """[(edit_t, title)] -> YouTube chapter lines. YouTube needs the first one at 0:00, so it is pinned there."""
    if not chapters: return ''
    ch = sorted(chapters)
    ch[0] = (0.0, ch[0][1])
    long = ch[-1][0] >= 3600

    def fmt(t):
        t = int(t)
        return f'{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}' if long else f'{t // 60:02d}:{t % 60:02d}'
    return '\n'.join(f'{fmt(t)} {title}' for t, title in ch) + '\n'
