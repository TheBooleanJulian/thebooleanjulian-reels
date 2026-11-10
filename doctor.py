"""Check that this machine can run TBJ-reels. Writes setup/last_check.txt."""
import importlib, os, shutil, subprocess, sys, json
ROOT = os.path.dirname(os.path.abspath(__file__))
rows = []


def row(name, ok, detail='', optional=False):
    rows.append(('OK  ' if ok else ('warn' if optional else 'FAIL'), name, detail))


def ver(cmd):
    try: return subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode().splitlines()[0]
    except Exception as e: return str(e)


for tool in ('ffmpeg', 'ffprobe'):
    p = shutil.which(tool)
    row(tool, bool(p), ver([tool, '-version'])[:60] if p else 'not on PATH: install from https://ffmpeg.org/download.html')
row('python', sys.version_info >= (3, 9), sys.version.split()[0])
for mod, opt, why in (('PIL', False, 'drawing'), ('numpy', False, 'frames'), ('scipy', False, 'subject finding'),
                      ('astropy', True, 'ephemeris.py (astro captions)'),
                      ('faster_whisper', True, 'transcribe.py default backend: pip install faster-whisper'),
                      ('whisperx', True, 'transcribe.py --backend whisperx (optional)')):
    try: importlib.import_module(mod); row(mod, True, why)
    except Exception: row(mod, False, why, optional=opt)
st = json.load(open(os.path.join(ROOT, 'style', 'signature.json'), encoding='utf-8'))
for k, f in st['fonts'].items():
    row(f'font {k}', os.path.exists(os.path.join(ROOT, 'fonts', f['file'])), f['file'])
row('cpu cores', True, f'{os.cpu_count()} (renders run one scene per core)')
free = shutil.disk_usage(ROOT).free / 1e9
row('free disk', free > 3, f'{free:.0f} GB (a 30 s reel needs ~1 GB of scene files)')
out = '\n'.join(f'{s}  {n:<16} {d}' for s, n, d in rows)
print(out)
os.makedirs(os.path.join(ROOT, 'setup'), exist_ok=True)
open(os.path.join(ROOT, 'setup', 'last_check.txt'), 'w', encoding='utf-8').write(out + '\n')
sys.exit(1 if any(r[0] == 'FAIL' for r in rows) else 0)
