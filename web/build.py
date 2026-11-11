"""Builds web/dist: the files served at reels-editor.thebooleanjulian.dev (see wrangler.jsonc).

Only the studio page, the fonts it uses and their licences go out. The page there is the interface only; it pairs
with studio.py running on the visitor's own device, so no footage, project or render ever goes to the website.

  python web/build.py           # then: npx wrangler deploy (wrangler runs this build itself first)
"""
import os, re, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, 'web', 'dist')
PAGE = os.path.join(ROOT, 'studio', 'index.html')
FONT_DIRS = (os.path.join(ROOT, 'fonts'), os.path.join(ROOT, 'fonts', 'ui'))

# The page talks to studio.py on 127.0.0.1 (any port: it takes the first free one from 8765); nothing else.
LOCAL = 'http://127.0.0.1:*'
CSP = (f"default-src 'self'; img-src 'self' data: blob: {LOCAL}; media-src 'self' blob: {LOCAL}; "
       f"style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; font-src 'self'; "
       f"connect-src 'self' {LOCAL}; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
HEADERS = f"""/*
  Content-Security-Policy: {CSP}
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer
  Cache-Control: no-cache
/fonts/*
  Cache-Control: public, max-age=604800
"""


def build():
    with open(PAGE, encoding='utf-8') as fh: page = fh.read()
    fonts = sorted(set(re.findall(r'url\(/fonts/([^)]+\.ttf)\)', page)))
    if not fonts: sys.exit('No /fonts/ references found in studio/index.html')
    if os.path.isdir(DIST): shutil.rmtree(DIST)
    os.makedirs(os.path.join(DIST, 'fonts'))
    shutil.copyfile(PAGE, os.path.join(DIST, 'index.html'))
    for name in fonts:
        src = next((os.path.join(d, name) for d in FONT_DIRS if os.path.isfile(os.path.join(d, name))), None)
        if not src: sys.exit(f'Font {name} (used by the page) not found in fonts/ or fonts/ui/')
        shutil.copyfile(src, os.path.join(DIST, 'fonts', name))
    for d in FONT_DIRS:                      # SIL OFL texts travel with the fonts
        for f in os.listdir(d):
            if f.startswith('OFL') and f.endswith('.txt'): shutil.copyfile(os.path.join(d, f), os.path.join(DIST, 'fonts', f))
    with open(os.path.join(DIST, '_headers'), 'w', encoding='utf-8', newline='\n') as fh: fh.write(HEADERS)
    return fonts


if __name__ == '__main__':
    used = build()
    print(f'web/dist: index.html, {len(used)} fonts, _headers')
