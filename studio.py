"""TBJ Reels Studio: a local web UI for every tool in this folder (polaroid recaps, reel presets, interview and
presentation edits). Python standard library only; the page is studio/index.html.

  python studio.py              # opens http://127.0.0.1:8765 in the browser
  studio.bat                    # same, using the tool environment that setup.bat creates
  python studio.py --port 9000 --no-browser

The same page is also served at https://reels-editor.thebooleanjulian.dev (web/build.py + wrangler.jsonc). There it
is only the interface: it finds this program on the device it is open on (127.0.0.1, ports 8765-8784), so the files,
ffmpeg and transcription stay on that device. studio.bat opens the website; --local opens the page served from here.

Safety: listens on 127.0.0.1 only, checks the Host header, and every API call must carry a token minted at launch
(the page receives it when it loads), so other websites open in the same browser cannot drive it. The one website
in WEB_ORIGINS may ask for the token (/api/pair) and read responses (CORS); browsers set Origin themselves, so no other
site can. Jobs only run the scripts in this folder with validated arguments; there is no "run any command" endpoint.
"""
import argparse, http.server, json, math, mimetypes, os, queue, re, secrets, shutil, subprocess, sys, threading, time
import webbrowser
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(ROOT, 'studio', 'index.html')
PROJECTS = os.path.join(ROOT, 'projects')
POLAROID = os.path.join(ROOT, 'polaroid-reel')
CACHE = os.environ.get('TBJ_REELS_CACHE') or os.path.join(os.path.expanduser('~'), '.tbj-reels', 'cache')
FONT_DIRS = (os.path.join(ROOT, 'fonts'), os.path.join(ROOT, 'fonts', 'ui'))
PY = sys.executable
TOKEN = secrets.token_urlsafe(24)
API_VERSION = 1                       # bump when the page and this server must change together (the website checks it)
WEB_URL = 'https://reels-editor.thebooleanjulian.dev/'
WEB_ORIGINS = {WEB_URL.rstrip('/')} | {o.strip().rstrip('/') for o in os.environ.get('TBJ_REELS_WEB_ORIGINS', '').split(',')
                                       if o.strip()}   # extra origins only for testing a copy of the website
NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}')
MAX_BODY = 20 * 1024 * 1024
WIN = os.name == 'nt'
ALLOWED = set()                       # extra files/folders the user picked or a job produced (served via /media)
ALLOWED_LOCK = threading.Lock()

FILTERS = {
    'video': [('Video', '*.mp4 *.mov *.m4v *.mkv *.avi *.mts *.mxf *.webm'), ('All files', '*.*')],
    'audio': [('Audio', '*.mp3 *.wav *.m4a *.aac *.flac *.ogg'), ('All files', '*.*')],
    'image': [('Image', '*.jpg *.jpeg *.png *.webp *.heic *.tif *.tiff'), ('All files', '*.*')],
    'media': [('Photo or video', '*.jpg *.jpeg *.png *.webp *.mp4 *.mov *.m4v *.mkv'), ('All files', '*.*')],
    'background': [('Image or video', '*.png *.jpg *.jpeg *.mp4 *.mov'), ('All files', '*.*')],
}

PICKER = r'''
import json, sys
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    print(json.dumps({"error": "no-tk"})); sys.exit(0)
a = json.loads(sys.argv[1])
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True); root.update()
init = a.get("initial") or None
if a["kind"] == "folder":
    p = filedialog.askdirectory(parent=root, title=a["title"], initialdir=init, mustexist=True)
elif a["kind"] == "save":
    p = filedialog.asksaveasfilename(parent=root, title=a["title"], initialdir=init, initialfile=a.get("name") or "",
                                     defaultextension=".mp4", filetypes=[("MP4 video", "*.mp4")])
else:
    p = filedialog.askopenfilename(parent=root, title=a["title"], initialdir=init, filetypes=a["filters"])
root.destroy()
print(json.dumps({"path": p or ""}))
'''


# ---------------------------------------------------------------- helpers
def norm(p): return os.path.normcase(os.path.realpath(p))


def inside(path, folder):
    path, folder = norm(path), norm(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def allow(path):
    if path:
        with ALLOWED_LOCK: ALLOWED.add(norm(path))


def is_allowed(path):
    if inside(path, ROOT) or inside(path, CACHE): return True
    with ALLOWED_LOCK: extra = list(ALLOWED)
    return any(inside(path, a) for a in extra)


def read_json(path):
    with open(path, encoding='utf-8') as fh: return json.load(fh)


def write_atomic(path, text):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh: fh.write(text)
    os.replace(tmp, path)


def project_dir(name):
    if not isinstance(name, str) or not NAME_RE.fullmatch(name): raise ValueError('bad project name')
    d = os.path.join(PROJECTS, name)
    if not os.path.isfile(os.path.join(d, 'preset.json')): raise ValueError(f'no project called {name}')
    return d


def resolve(proj, p):
    """Same rule as the engines: absolute, else relative to the project folder, else to the tool folder."""
    if not p: return ''
    if os.path.isabs(p): return os.path.normpath(p)
    for base in (proj, ROOT):
        c = os.path.join(base, p)
        if os.path.exists(c): return os.path.normpath(c)
    return os.path.normpath(os.path.join(proj, p))


def kind_of(cfg):
    f = cfg.get('format')
    return f if f in ('interview', 'presentation') else 'reel'


def transcript_path(proj, cfg):
    t = cfg.get('transcript') or next((s.get('transcript') for s in cfg.get('scenes', []) if s.get('transcript')), '')
    return resolve(proj, t) if t else ''


def files_in(folder, exts):
    if not os.path.isdir(folder): return []
    out = []
    for f in sorted(os.listdir(folder)):
        p = os.path.join(folder, f)
        if os.path.isfile(p) and f.lower().endswith(exts) and '.tmp.' not in f:
            st = os.stat(p)
            out.append({'path': p, 'name': f, 'size': st.st_size, 'mtime': st.st_mtime})
    return out


def project_summary(name):
    d = os.path.join(PROJECTS, name)
    cfg = read_json(os.path.join(d, 'preset.json'))
    tp = transcript_path(d, cfg)
    return {'name': name, 'kind': kind_of(cfg), 'video': resolve(d, cfg.get('video', '')),
            'has_transcript': bool(tp and os.path.exists(tp)),
            'exports': len(files_in(os.path.join(d, 'exports'), ('.mp4',))),
            'mtime': os.path.getmtime(os.path.join(d, 'preset.json'))}


def longform_json(name, *flags):
    """Run longform.py on a project with flags that print one JSON line; errors come back as {'error': [...]}."""
    d = project_dir(name)
    r = subprocess.run([PY, os.path.join(ROOT, 'longform.py'), os.path.join(d, 'preset.json'), *flags],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=ROOT)
    if r.returncode != 0:
        return {'error': (r.stderr or r.stdout).strip().splitlines()[-1:] or ['longform.py failed']}
    try: return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception: return {'error': ['could not read the result']}


def plan(name):
    """The long-form engine's own view of the edit (clips, durations, fillers, look)."""
    return longform_json(name, '--plan')


def preview_graphics(name, width=540):
    """Graphic PNGs at preview size (lower thirds, cards, chapters, intro, end card) for the live preview."""
    d = project_dir(name)
    cfg = read_json(os.path.join(d, 'preset.json'))
    folder = os.path.join(CACHE, cfg.get('name', name), 'preview_gfx')
    if os.path.isdir(folder): shutil.rmtree(folder, ignore_errors=True)
    res = longform_json(name, '--graphics', folder, '--width', str(int(width)))
    if isinstance(res, list):
        for g in res: g['mtime'] = os.path.getmtime(g['png'])
    return {'graphics': res} if isinstance(res, list) else res


def export_name(cfg, name):
    base = re.sub(r'[^\w.-]+', '-', str((cfg.get('export') or {}).get('file') or cfg.get('name') or name)).strip('-')
    return base or name


def project_detail(name):
    d = project_dir(name)
    cfg = read_json(os.path.join(d, 'preset.json'))
    with open(os.path.join(d, 'preset.json'), encoding='utf-8') as fh: text = fh.read()
    tp = transcript_path(d, cfg)
    words = read_json(tp)['words'] if tp and os.path.exists(tp) else None
    notes_p = os.path.join(d, 'notes.md')
    notes = open(notes_p, encoding='utf-8').read() if os.path.exists(notes_p) else ''
    for key in ('video',):
        if cfg.get(key): allow(resolve(d, cfg[key]))
    if isinstance(cfg.get('screen'), dict) and cfg['screen'].get('video'): allow(resolve(d, cfg['screen']['video']))
    for b in cfg.get('broll', []): allow(resolve(d, b.get('file', '')))
    if (cfg.get('music') or {}).get('file'): allow(resolve(d, cfg['music']['file']))
    work = os.path.join(d, 'work')
    outputs = files_in(os.path.join(d, 'exports'), ('.mp4', '.srt', '.txt'))
    outputs += [f for f in files_in(work, ('.mp4',)) if f['name'] in ('roughcut.mp4',)]
    stills = files_in(os.path.join(work, 'stills'), ('.jpg',))
    if not stills: stills = files_in(os.path.join(ROOT, 'stills', cfg.get('name', name)), ('.jpg',))
    k = kind_of(cfg)
    return {'name': name, 'kind': k, 'preset': cfg, 'preset_text': text, 'notes': notes,
            'video': resolve(d, cfg.get('video', '')), 'transcript_path': tp, 'words': words,
            'outputs': outputs, 'stills': stills, 'plan': plan(name) if k != 'reel' else None}


def env_info():
    def has(mod):
        r = subprocess.run([PY, '-c', f'import {mod}'], capture_output=True)
        return r.returncode == 0
    ff = shutil.which('ffmpeg')
    if not ff:
        try:
            import imageio_ffmpeg; ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception: ff = None
    return {'python': sys.version.split()[0], 'python_path': PY, 'ffmpeg': ff, 'tk': has('tkinter'),
            'whisper': has('faster_whisper'), 'cv2': has('cv2'), 'root': ROOT}


# ---------------------------------------------------------------- jobs
class Job:
    _n = 0

    def __init__(self, title, argv, kind, outputs=(), project=None):
        Job._n += 1
        self.id, self.title, self.argv, self.kind = Job._n, title, argv, kind
        self.project, self.outputs = project, list(outputs)
        self.status, self.lines, self.progress = 'queued', [], None
        self.created, self.started, self.ended, self.proc, self.code = time.time(), None, None, None, None

    def summary(self):
        return {'id': self.id, 'title': self.title, 'kind': self.kind, 'project': self.project, 'status': self.status,
                'progress': self.progress, 'created': self.created, 'started': self.started, 'ended': self.ended,
                'outputs': [o for o in self.outputs if os.path.exists(o)], 'lines': len(self.lines), 'code': self.code,
                'command': ' '.join(f'"{a}"' if ' ' in a else a for a in self.argv)}


JOBS, JOB_Q, JOBS_LOCK = {}, queue.Queue(), threading.Lock()
# "render 42%", "transcribe 7%", "rendering 120/900 frames", "still 3/9". Numbers must follow a space and end the
# word, so digits inside paths ("...bb45/615dc80f...", "Orbitron%5B...") never move the progress bar.
PROGRESS = [(re.compile(r'(?:^|\s)(\d{1,3}(?:\.\d+)?)%(?=\s|$)'), lambda m: float(m.group(1))),
            (re.compile(r'(?:^|\s)(\d+)/(\d+)(?=\s+frames|\s*$)'), lambda m: 100.0 * int(m.group(1)) / max(1, int(m.group(2))))]


def submit(title, argv, kind, outputs=(), project=None):
    j = Job(title, argv, kind, outputs, project)
    with JOBS_LOCK: JOBS[j.id] = j
    for o in j.outputs: allow(o)
    JOB_Q.put(j)
    return j


def add_line(j, text):
    text = text.rstrip()
    if not text.strip(): return
    for rx, fn in PROGRESS:
        m = rx.search(text)
        if m:
            try: j.progress = max(0.0, min(100.0, fn(m)))
            except ZeroDivisionError: pass
            break
    if j.lines and j.lines[-1].startswith('  rendering ') and text.lstrip().startswith('rendering '):
        j.lines[-1] = text                                   # polaroid's \r progress line: update in place
    elif j.lines and re.match(r'(render|still|transcribe) \d', j.lines[-1]) and re.match(r'(render|still|transcribe) \d', text):
        j.lines[-1] = text
    else:
        j.lines.append(text)
    if len(j.lines) > 3000: del j.lines[:500]


def worker():
    while True:
        j = JOB_Q.get()
        if j.status == 'cancelled': continue
        j.status, j.started = 'running', time.time()
        add_line(j, '$ ' + j.summary()['command'])
        try:
            env = {**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8'}
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if WIN else 0
            j.proc = subprocess.Popen(j.argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, env=env, creationflags=flags)
            buf = b''
            while True:
                chunk = j.proc.stdout.read1(4096) if hasattr(j.proc.stdout, 'read1') else j.proc.stdout.read(1)
                if not chunk: break
                buf += chunk
                parts = re.split(rb'[\r\n]', buf)
                buf = parts.pop()
                for p in parts: add_line(j, p.decode('utf-8', 'replace'))
            if buf: add_line(j, buf.decode('utf-8', 'replace'))
            j.code = j.proc.wait()
            if j.status != 'cancelled':
                j.status = 'done' if j.code == 0 else 'failed'
                if j.code == 0: j.progress = 100.0
        except Exception as e:
            j.status = 'failed'; add_line(j, f'Could not start: {e}')
        j.ended = time.time()
        add_line(j, {'done': 'Finished.', 'failed': f'Failed (exit {j.code}).', 'cancelled': 'Stopped.'}.get(j.status, ''))


def cancel(j):
    if j.status == 'queued':
        j.status = 'cancelled'; return
    if j.status == 'running' and j.proc:
        j.status = 'cancelled'
        if WIN:                                              # whole tree: the script and the ffmpeg it started
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(j.proc.pid)], capture_output=True)
        else:
            j.proc.terminate()


# ---------------------------------------------------------------- job builders (validated arguments only)
def num(v, lo=None, hi=None, kind=float):
    if v in (None, ''): return None
    x = kind(v)
    if (lo is not None and x < lo) or (hi is not None and x > hi): raise ValueError(f'{v} is out of range')
    return x


def need_file(p, what, folder=False):
    if not p or not (os.path.isdir(p) if folder else os.path.isfile(p)):
        raise ValueError(f'{what} not found: {p or "(empty)"}. Check the path and try again.')
    allow(p)
    return p


def build_polaroid(a):
    tpl = int(a.get('template', 1))
    photos = need_file(a.get('photos'), 'Photo folder', folder=True)
    script = 'polaroid_reel.py' if tpl == 1 else 'polaroid_pile.py'
    out = a.get('output') or (photos.rstrip('\\/') + ('_reel.mp4' if tpl == 1 else '_pile.mp4'))
    if not out.lower().endswith('.mp4'): out = os.path.splitext(out)[0] + '.mp4'
    if not os.path.isdir(os.path.dirname(os.path.abspath(out))): raise ValueError(f'Output folder does not exist: {out}')
    argv = [PY, os.path.join(POLAROID, script), photos, '-o', out]
    d = num(a.get('duration'), 1, 3600)
    if d: argv += ['-d', f'{d:g}']
    if a.get('music'): argv += ['-m', need_file(a['music'], 'Music')]
    if tpl == 1 and a.get('background'): argv += ['-b', need_file(a['background'], 'Background')]
    if tpl == 2 and a.get('end_card'): argv += ['-e', need_file(a['end_card'], 'End card')]
    if tpl == 2 and a.get('desk'): argv += ['--desk', need_file(a['desk'], 'Desk image')]
    size = a.get('size')
    if size:
        if not re.fullmatch(r'\d{3,4}x\d{3,4}', size): raise ValueError('Size must look like 1920x1080')
        argv += ['--size', size]
    if a.get('order') in ('time', 'name', 'random'): argv += ['--order', a['order']]
    for key, flag, lo, hi, kind in (('limit', '--limit', 1, 5000, int), ('seed', '--seed', 0, 10 ** 6, int),
                                    ('grain', '--grain', 0, 50, float), ('card_scale', '--card-scale', 0.3, 3, float),
                                    ('fps', '--fps', 12, 60, int)):
        v = num(a.get(key), lo, hi, kind)
        if v is not None: argv += [flag, f'{v:g}']
    if tpl == 1:
        for key, flag in (('interval', '--interval'), ('hold', '--hold')):
            v = num(a.get(key), 0.05, 60)
            if v is not None: argv += [flag, f'{v:g}']
        if a.get('watch'): argv.append('--watch')
    else:
        v = num(a.get('per_page'), 1, 30, int)
        if v is not None: argv += ['--per-page', str(v)]
        v = num(a.get('grade'), 0, 2)
        if v is not None: argv += ['--grade', f'{v:g}']
    if a.get('draft'): argv.append('--preview')
    elif a.get('best'): argv.append('--best')
    title = ('Polaroid float' if tpl == 1 else 'Polaroid pile') + (' draft' if a.get('draft') else '') + \
        f' – {os.path.basename(photos.rstrip(chr(92) + "/"))}'
    return submit(title, argv, 'polaroid', [out])


def build_new(a):
    name = a.get('name', '')
    if not NAME_RE.fullmatch(name or ''):
        raise ValueError('Project name: letters, digits, dot, dash or underscore (e.g. jane-tan-interview).')
    tpl = a.get('template')
    if not tpl or not os.path.isfile(os.path.join(ROOT, 'presets', f'{tpl}.json')) or not NAME_RE.fullmatch(tpl):
        raise ValueError('Unknown template.')
    argv = [PY, os.path.join(ROOT, 'new_reel.py'), name, '--template', tpl]
    if a.get('video'): argv += ['--video', need_file(a['video'], 'Video')]
    if a.get('in_place'): argv.append('--in-place')
    return submit(f'New project – {name}', argv, 'new', project=name)


def build_transcribe(a):
    d = project_dir(a.get('name'))
    cfg = read_json(os.path.join(d, 'preset.json'))
    video = need_file(resolve(d, cfg.get('video', '')), 'Project video')
    out = transcript_path(d, cfg) or os.path.join(d, 'src', os.path.splitext(os.path.basename(video))[0] + '.words.json')
    model = a.get('model') or 'small.en'
    if model not in ('tiny.en', 'base.en', 'small.en', 'medium.en', 'small', 'medium', 'large-v3'):
        raise ValueError('Unknown model.')
    argv = [PY, os.path.join(ROOT, 'transcribe.py'), video, '--out', out, '--model', model]
    if a.get('fillers'): argv.append('--fillers')
    return submit(f'Transcribe – {a["name"]}', argv, 'transcribe', [out], project=a['name'])


def build_make(a):
    d = project_dir(a.get('name'))
    cfg = read_json(os.path.join(d, 'preset.json'))
    action = a.get('action')
    preset = os.path.join(d, 'preset.json')
    longform = kind_of(cfg) != 'reel'
    flags = {'stills': ['--stills'], 'roughcut': ['--roughcut'], 'build': ['--build'], 'draft': ['--build', '--draft'],
             'proxy': ['--proxy']}.get(action)
    if not flags or (action in ('draft', 'proxy') and not longform): raise ValueError('Unknown action.')
    only = num(a.get('only'), 0, 99, int)
    if only is not None and action == 'build' and not longform: flags = flags + ['--only', str(only)]
    script = 'longform.py' if action == 'proxy' else 'make_reel.py'
    name = cfg.get('name', a['name'])
    outs = {'stills': [os.path.join(d, 'work', 'stills', 'sheet.jpg')],
            'roughcut': [os.path.join(d, 'work', 'roughcut.mp4')],
            'build': [os.path.join(d, 'exports', f'{export_name(cfg, a["name"]) if longform else a["name"]}.mp4')],
            'draft': [os.path.join(d, 'exports', f'{export_name(cfg, a["name"])}_draft.mp4')],
            'proxy': [os.path.join(CACHE, name, 'proxy.mp4')]}[action]
    label = {'stills': 'Stills', 'roughcut': 'Rough cut', 'build': 'Build', 'draft': 'Draft build', 'proxy': 'Proxy'}[action]
    return submit(f'{label} – {a["name"]}', [PY, os.path.join(ROOT, script), preset, *flags], action, outs, project=a['name'])


def build_doctor(_):
    return submit('Setup check', [PY, os.path.join(ROOT, 'doctor.py')], 'doctor')


BUILDERS = {'polaroid': build_polaroid, 'new': build_new, 'transcribe': build_transcribe, 'make': build_make,
            'doctor': build_doctor}


# ---------------------------------------------------------------- HTTP
class Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # A browser dropping a connection (seeking a video, reloading, a refused permission) is not an error worth
        # a traceback in the studio window.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)): return
        super().handle_error(request, client_address)


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'TBJReelsStudio/1'

    def log_message(self, fmt, *args): pass

    def _host_ok(self):
        host = (self.headers.get('Host') or '').split(':')[0]
        return host in ('127.0.0.1', 'localhost')

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD': self.wfile.write(data)

    def _err(self, code, msg): self._send(code, {'error': msg})

    def _token_ok(self, q):
        return secrets.compare_digest(self.headers.get('X-Token') or (q.get('t') or [''])[0], TOKEN)

    def _web(self):                       # the request comes from the website (browsers set Origin, pages can't)
        return (self.headers.get('Origin') or '') in WEB_ORIGINS

    def end_headers(self):                # every response may be read by the website, and only by it
        if self._web():
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Vary', 'Origin')
        super().end_headers()

    def do_OPTIONS(self):                 # CORS + local network access preflight from the website
        if not self._host_ok() or not self._web(): return self._err(403, 'Forbidden')
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST')
        self.send_header('Access-Control-Allow-Headers', 'X-Token, Content-Type')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Access-Control-Max-Age', '600')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_HEAD(self): self.do_GET()

    def do_GET(self):
        if not self._host_ok(): return self._err(403, 'Forbidden host')
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path in ('/', '/index.html'):
            with open(UI, encoding='utf-8') as fh: page = fh.read().replace('__TOKEN__', TOKEN)
            return self._send(200, page.encode('utf-8'), 'text/html; charset=utf-8',
                              {'Content-Security-Policy': "default-src 'self'; img-src 'self' data: blob:; "
                               "media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                               "font-src 'self'; connect-src 'self'; frame-ancestors 'none'"})
        if u.path.startswith('/fonts/'):
            name = os.path.basename(u.path)
            for d in FONT_DIRS:
                p = os.path.join(d, name)
                if name.endswith('.ttf') and os.path.isfile(p):
                    with open(p, 'rb') as fh: return self._send(200, fh.read(), 'font/ttf', {'Cache-Control': 'max-age=86400'})
            return self._err(404, 'no such font')
        if u.path == '/api/pair':         # the website, open on this device, asks for this launch's token
            if not self._web(): return self._err(403, 'Only the TBJ Reels website can pair with the studio')
            return self._send(200, {'app': 'tbj-reels', 'token': TOKEN, 'version': API_VERSION})
        if not self._token_ok(q): return self._err(403, 'Missing or wrong token. Reload the page.')
        try:
            if u.path == '/media': return self._media(q.get('p', [''])[0])
            if u.path == '/api/state':
                names = sorted(n for n in os.listdir(PROJECTS) if os.path.isfile(os.path.join(PROJECTS, n, 'preset.json'))) \
                    if os.path.isdir(PROJECTS) else []
                projects = []
                for n in names:
                    try: projects.append(project_summary(n))
                    except Exception as e: projects.append({'name': n, 'kind': 'broken', 'error': str(e)})
                templates = sorted(f[:-5] for f in os.listdir(os.path.join(ROOT, 'presets')) if f.endswith('.json'))
                with JOBS_LOCK: jobs = [j.summary() for j in JOBS.values()]
                return self._send(200, {'projects': projects, 'templates': templates, 'jobs': jobs[::-1],
                                        'polaroid_defaults': {'background': os.path.join(POLAROID, 'background.png'),
                                                              'end_card': os.path.join(POLAROID, 'template2', 'end_card.png'),
                                                              'desk': os.path.join(POLAROID, 'template2', 'desk.jpg')}})
            if u.path == '/api/env': return self._send(200, env_info())
            if u.path == '/api/project': return self._send(200, project_detail(q.get('name', [''])[0]))
            if u.path == '/api/plan': return self._send(200, plan(q.get('name', [''])[0]))
            if u.path == '/api/graphics':
                return self._send(200, preview_graphics(q.get('name', [''])[0], int(q.get('width', ['540'])[0])))
            if u.path == '/api/beats': return self._send(200, longform_json(q.get('name', [''])[0], '--beats'))
            if u.path == '/api/audition':
                t = float(q.get('t', ['0'])[0])
                if not math.isfinite(t): raise ValueError('Bad time.')
                return self._send(200, longform_json(q.get('name', [''])[0], '--audition', f'{max(0.0, t):.3f}'))
            if u.path == '/api/job':
                j = JOBS.get(int(q.get('id', ['0'])[0]))
                if not j: return self._err(404, 'No such job')
                start = int(q.get('from', ['0'])[0])
                return self._send(200, {**j.summary(), 'from': start, 'log': j.lines[start:]})
        except ValueError as e:
            return self._err(400, str(e))
        except Exception as e:
            return self._err(500, f'{type(e).__name__}: {e}')
        return self._err(404, 'Not found')

    def do_POST(self):
        if not self._host_ok(): return self._err(403, 'Forbidden host')
        u = urlparse(self.path)
        if not self._token_ok({}): return self._err(403, 'Missing or wrong token. Reload the page.')
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY: return self._err(413, 'Request too large')
        try:
            a = json.loads(self.rfile.read(n) or b'{}')
            if u.path == '/api/run':
                b = BUILDERS.get(a.get('tool'))
                if not b: raise ValueError('Unknown tool')
                return self._send(200, b(a).summary())
            if u.path == '/api/cancel':
                j = JOBS.get(int(a.get('id', 0)))
                if j: cancel(j)
                return self._send(200, {'ok': True})
            if u.path == '/api/pick': return self._send(200, self._pick(a))
            if u.path == '/api/open': return self._send(200, self._open(a.get('path', '')))
            if u.path == '/api/save': return self._send(200, self._save(a))
            if u.path == '/api/transcript': return self._send(200, self._save_words(a))
            if u.path == '/api/notes':
                d = project_dir(a.get('name'))
                write_atomic(os.path.join(d, 'notes.md'), str(a.get('notes', '')))
                return self._send(200, {'ok': True})
        except ValueError as e:
            return self._err(400, str(e))
        except Exception as e:
            return self._err(500, f'{type(e).__name__}: {e}')
        return self._err(404, 'Not found')

    # -- endpoints
    def _media(self, p):
        if not p or not os.path.isfile(p) or not is_allowed(p): return self._err(404, 'Not available')
        size = os.path.getsize(p)
        ctype = mimetypes.guess_type(p)[0] or 'application/octet-stream'
        if p.lower().endswith(('.srt', '.txt', '.ass', '.md', '.json')): ctype = 'text/plain; charset=utf-8'
        rng = self.headers.get('Range')
        start, end = 0, size - 1
        m = re.match(r'bytes=(\d*)-(\d*)', rng or '')
        if m and size:
            if m.group(1): start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
            elif m.group(2): start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end: return self._send(416, b'', 'text/plain', {'Content-Range': f'bytes */{size}'})
        length = end - start + 1
        self.send_response(206 if m else 200)
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        if m: self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command == 'HEAD': return
        try:
            with open(p, 'rb') as fh:
                fh.seek(start)
                left = length
                while left > 0:
                    chunk = fh.read(min(1 << 20, left))
                    if not chunk: break
                    self.wfile.write(chunk); left -= len(chunk)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass                                               # the player seeked and dropped this request

    def _pick(self, a):
        kind = a.get('kind') if a.get('kind') in ('folder', 'file', 'save') else 'file'
        initial = a.get('initial') or ''
        if initial and not os.path.isdir(initial): initial = os.path.dirname(initial)
        args = {'kind': kind, 'title': str(a.get('title') or 'Choose'), 'initial': initial if os.path.isdir(initial) else '',
                'name': os.path.basename(a.get('initial') or '') if kind == 'save' else '',
                'filters': FILTERS.get(a.get('filter'), [('All files', '*.*')])}
        r = subprocess.run([PY, '-c', PICKER, json.dumps(args)], capture_output=True, text=True, timeout=3600)
        try: res = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception: res = {'error': 'picker failed'}
        if res.get('error') == 'no-tk':
            return {'path': '', 'error': 'This Python has no Tk, so there is no file dialog. Paste the path instead.'}
        path = os.path.normpath(res['path']) if res.get('path') else ''
        if path: allow(path)
        return {'path': path, **({'error': res['error']} if res.get('error') else {})}

    def _open(self, p):
        if not p or not os.path.exists(p) or not is_allowed(p): raise ValueError('Not available')
        if WIN:
            if os.path.isfile(p): subprocess.Popen(['explorer', '/select,', os.path.normpath(p)])
            else: os.startfile(p)
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', p if os.path.isdir(p) else os.path.dirname(p)])
        return {'ok': True}

    def _save(self, a):
        d = project_dir(a.get('name'))
        if 'preset_text' in a:
            text = a['preset_text']
            try: cfg = json.loads(text)
            except json.JSONDecodeError as e: raise ValueError(f'Preset is not valid JSON (line {e.lineno}): {e.msg}')
        else:
            cfg = a.get('preset')
            if not isinstance(cfg, dict): raise ValueError('Preset missing')
            text = json.dumps(cfg, indent=2, ensure_ascii=False) + '\n'
        if not isinstance(cfg, dict) or ('scenes' not in cfg and kind_of(cfg) == 'reel'):
            raise ValueError('Preset needs "scenes" (reel) or "format": interview | presentation')
        write_atomic(os.path.join(d, 'preset.json'), text)
        return {'ok': True, 'plan': plan(a['name']) if kind_of(cfg) != 'reel' else None}

    def _save_words(self, a):
        d = project_dir(a.get('name'))
        cfg = read_json(os.path.join(d, 'preset.json'))
        tp = transcript_path(d, cfg)
        if not tp or not os.path.exists(tp): raise ValueError('No transcript to update yet')
        words = a.get('words')
        if not isinstance(words, list) or not all(isinstance(w, dict) and {'w', 'start', 'end'} <= set(w) for w in words):
            raise ValueError('Words must be a list of {w, start, end}')
        data = read_json(tp)
        data['words'] = [{'w': str(w['w']), 'start': float(w['start']), 'end': float(w['end'])} for w in words]
        data['text'] = ' '.join(w['w'] for w in data['words'])
        write_atomic(tp, json.dumps(data, indent=1, ensure_ascii=False))
        return {'ok': True}


def main():
    ap = argparse.ArgumentParser(description='TBJ Reels Studio')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--local', action='store_true', help='open the page served by this program instead of the website')
    a = ap.parse_args()
    os.makedirs(PROJECTS, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    srv = None
    for port in range(a.port, a.port + 20):                 # next free port if 8765 is taken
        try:
            srv = Server(('127.0.0.1', port), Handler); break
        except OSError:
            continue
    if not srv: sys.exit('No free port near %d' % a.port)
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    print(f'TBJ Reels Studio is running on this device. Keep this window open (Ctrl+C to stop).\n'
          f'  Website:   {WEB_URL}\n  Offline:   {url}', flush=True)
    if not a.no_browser: threading.Timer(0.6, lambda: webbrowser.open(url if a.local else WEB_URL)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print('Stopped.')


if __name__ == '__main__':
    main()
