"""Scaffold a reel project.

  python3 new_reel.py nikon-z8-shoot --template talking_head --video ~/Videos/clip.mp4
  python3 new_reel.py moon-jupiter --template hook_plan_bts_reveal
  python3 new_reel.py jane-tan --template interview --video D:/Shoots/jane.mp4
  python3 new_reel.py summit-keynote --template presentation --video E:/keynote.mov --in-place   # big file: not copied

Creates projects/<name>/ {preset.json, notes.md, src/, work/, exports/}.
The original recording is COPIED to raw/ (never edited). Templates live in presets/.
"""
import argparse, json, os, re, shutil, sys

ROOT = os.path.dirname(os.path.abspath(__file__))

NOTES = """# {name}

Template: {template}    Created: {date}

## Approval log (the reel only moves on when you say so)
- [ ] Rough cut approved        (python3 make_reel.py projects/{name}/preset.json --roughcut)
- [ ] Creative direction agreed (hook, tone, music idea, clips/images to add)
- [ ] Graphics plan approved    (storyboard / --stills sheet)
- [ ] Opening approved          (first 10-15 s rendered with sound)
- [ ] Full video approved       (exports/{name}.mp4)
- [ ] FINAL version approved by you: ____ (date + file)  -> only then clean up work/

## Direction
## Decisions / changes
## Lessons (what went wrong, how it was fixed, which belong in style/ for every reel)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('name')
    ap.add_argument('--template', default='talking_head')
    ap.add_argument('--video')
    ap.add_argument('--in-place', action='store_true',
                    help="don't copy the video into raw/ (large files); the preset points at it where it is")
    a = ap.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', a.name):
        sys.exit('project name: letters, digits, dot, dash, underscore only (e.g. jane-tan-interview)')
    tpl = os.path.join(ROOT, 'presets', a.template + '.json')
    if not os.path.exists(tpl): sys.exit('unknown template; available: ' + ', '.join(
        f[:-5] for f in sorted(os.listdir(os.path.join(ROOT, 'presets'))) if f.endswith('.json')))
    proj = os.path.join(ROOT, 'projects', a.name)
    if os.path.exists(proj): sys.exit(f'{proj} already exists')
    if a.video and not os.path.isfile(a.video): sys.exit(f'video not found: {a.video}')
    for d in ('src', 'work', 'exports'): os.makedirs(os.path.join(proj, d))
    with open(tpl, encoding='utf-8') as fh: cfg = json.load(fh)
    cfg['name'] = a.name
    if a.video:
        if a.in_place:
            cfg['video'] = os.path.abspath(a.video)                # never edited, just read from where it is
        else:
            dst = os.path.join(ROOT, 'raw', os.path.basename(a.video))
            if not os.path.exists(dst): shutil.copy2(a.video, dst)     # originals stay untouched in raw/
            cfg['video'] = 'raw/' + os.path.basename(a.video)
        stem = os.path.splitext(os.path.basename(a.video))[0]
        if cfg.get('format') in ('interview', 'presentation'):
            cfg['transcript'] = f'src/{stem}.words.json'
        for sc in cfg.get('scenes', []):
            if sc.get('kind') == 'talking_head': sc['transcript'] = f'src/{stem}.words.json'
    with open(os.path.join(proj, 'preset.json'), 'w', encoding='utf-8') as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
    import datetime
    with open(os.path.join(proj, 'notes.md'), 'w', encoding='utf-8') as fh:
        fh.write(NOTES.format(name=a.name, template=a.template, date=datetime.date.today()))
    print(f'created {proj}')
    if cfg.get('format') in ('interview', 'presentation') or a.template == 'talking_head':
        v = cfg['video']
        print(f'next: python3 transcribe.py {v} --out projects/{a.name}/src/{os.path.splitext(os.path.basename(v))[0]}.words.json')
        print(f'then: python3 make_reel.py projects/{a.name}/preset.json --roughcut   (approve the cut before graphics)')
    else:
        print(f'next: put media in projects/{a.name}/src/, edit preset.json, then --stills')


if __name__ == '__main__':
    main()
