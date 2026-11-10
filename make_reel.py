"""Build a reel from a preset.

  python3 make_reel.py <preset.json> --stills            # preview sheet in seconds (no full render)
  python3 make_reel.py <preset.json> --roughcut          # talking-head scenes only: trimmed footage + audio, no graphics
  python3 make_reel.py <preset.json> --build             # full render -> exports/ (or out/)
  ... --build --only 3                                    # re-render just scene #3 (0-based), re-join the rest
Presets inside projects/<name>/ keep everything in that folder: work/ (scene files, stills) and exports/.
Source video: "video" in the preset, or REEL_VIDEO=/path/to.mp4.
Presets with "format": "interview" | "presentation" are handed to longform.py (same flags, plus --draft).
"""
import argparse, json, os, subprocess, multiprocessing as mp
from PIL import Image
from preset import Reel
from vio import Src, encoder, has_audio, FPS
from reel import W, H

ROOT = os.path.dirname(os.path.abspath(__file__))


def dirs(cfg):
    d = os.path.dirname(cfg)
    name = os.path.splitext(os.path.basename(cfg))[0]
    if os.path.basename(os.path.dirname(d)) == 'projects':      # projects/<reel>/preset.json
        return os.path.join(d, 'work'), os.path.join(d, 'exports'), os.path.join(d, 'work', 'stills'), os.path.basename(d)
    return os.path.join(ROOT, 'scenes_out', name), os.path.join(ROOT, 'out'), os.path.join(ROOT, 'stills', name), name


def concat_line(path):
    """One ffmpeg concat-list line. Forward slashes: the concat parser reads a backslash as an escape (Windows paths)."""
    p = os.path.abspath(path).replace('\\', '/').replace("'", "'\\''")
    return f"file '{p}'\n"


def scene_path(outdir, i, sc, plain=False): return os.path.join(outdir, f'{i:02d}_{sc.name}{"_plain" if plain else ""}.mp4')


def render_scene(job):
    cfg, i, outdir, plain = job
    reel = Reel(cfg, plain=plain); sc = reel.scenes[i]
    path = scene_path(outdir, i, sc, plain)
    tmp = path[:-4] + '.tmp.mp4'            # still ends in .mp4; renamed only if ffmpeg succeeds
    enc = encoder(tmp)
    src = Src(reel.video) if sc.needs_video else None
    st = sc.new_state()
    for k in range(sc.frames):
        t = k / FPS
        stt = sc.src_time(t)
        bg = src.frame(stt) if stt is not None else None
        enc.stdin.write(sc.draw(t, bg, st).tobytes())
    enc.stdin.close()
    if src: src.close()
    if enc.wait() != 0: raise RuntimeError(f'ffmpeg failed on scene {i}')
    os.replace(tmp, path)
    return f'scene {i} ({sc.name}) ok'


def scene_audio(reel, i, sc, outdir, plain):
    """One 48 kHz stereo wav per scene, exactly as long as its video frames."""
    wav = os.path.join(outdir, f'{i:02d}_{sc.name}{"_plain" if plain else ""}.wav')
    base = ['ffmpeg', '-v', 'error', '-y']
    if sc.has_audio and has_audio(reel.video):
        parts, labels, fd = [], [], 0.012                     # 12 ms fades at every cut: no clicks
        for k, (a, b) in enumerate(sc.audio_segments):
            parts.append(f'[0:a]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS,'
                         f'afade=t=in:st=0:d={fd},afade=t=out:st={max(0, b - a - fd):.4f}:d={fd}[a{k}]')
            labels.append(f'[a{k}]')
        flt = ';'.join(parts) + f';{"".join(labels)}concat=n={len(labels)}:v=0:a=1,aresample=48000,apad,atrim=0:{sc.adur:.5f}[o]'
        subprocess.check_call(base + ['-i', reel.video, '-filter_complex', flt, '-map', '[o]', '-ac', '2', '-ar', '48000', wav])
    else:
        subprocess.check_call(base + ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', f'{sc.adur:.5f}', wav])
    return wav


def stills(cfg, outdir):
    reel = Reel(cfg); os.makedirs(outdir, exist_ok=True)
    frames = []
    for i, sc in enumerate(reel.scenes):
        im = sc.draw(sc.still_t(), sc.still_bg(), sc.new_state())
        im.save(os.path.join(outdir, f'{i}_{sc.name}.jpg'), quality=92)
        frames.append(im)
    sheet = Image.new('RGB', (len(frames) * 560 + 20, 1000), (18, 18, 22))
    for i, f in enumerate(frames): sheet.paste(f.resize((540, 960), Image.LANCZOS), (20 + i * 560, 20))
    sheet.save(os.path.join(outdir, 'sheet.jpg'), quality=90)
    print('stills ->', outdir)


def build(cfg, out, workdir, only=None, plain=False):
    reel = Reel(cfg, plain=plain)
    os.makedirs(workdir, exist_ok=True); os.makedirs(os.path.dirname(out), exist_ok=True)
    join = [i for i, sc in enumerate(reel.scenes) if (sc.has_audio or not plain)]   # rough cut: talking scenes only
    todo = [only] if only is not None else join
    ctx = mp.get_context('fork' if 'fork' in mp.get_all_start_methods() else 'spawn')   # Windows has no fork
    with ctx.Pool(min(len(todo), os.cpu_count() or 2)) as pool:
        for msg in pool.imap_unordered(render_scene, [(cfg, i, workdir, plain) for i in todo]): print(msg, flush=True)
    lst = os.path.join(workdir, 'list.txt')
    with open(lst, 'w', encoding='utf-8') as f:
        for i in join: f.write(concat_line(scene_path(workdir, i, reel.scenes[i], plain)))
    joined = os.path.join(workdir, 'joined.mp4')
    subprocess.check_call(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', joined])
    if any(reel.scenes[i].has_audio for i in join):          # real audio: per-scene wavs, joined, encoded once
        wavs = [scene_audio(reel, i, reel.scenes[i], workdir, plain) for i in join]
        alst = os.path.join(workdir, 'audio_list.txt')
        with open(alst, 'w', encoding='utf-8') as f:
            for w in wavs: f.write(concat_line(w))
        audio = os.path.join(workdir, 'audio.wav')
        subprocess.check_call(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', alst, '-c', 'copy', audio])
        subprocess.check_call(['ffmpeg', '-v', 'error', '-y', '-i', joined, '-i', audio, '-map', '0:v', '-map', '1:a',
                               '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', out])
    else:                                                    # silent track (add trending audio in Instagram)
        subprocess.check_call(['ffmpeg', '-v', 'error', '-y', '-i', joined, '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                               '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k', '-shortest',
                               '-movflags', '+faststart', out])
    print('reel ->', out)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('preset')
    ap.add_argument('--stills', action='store_true')
    ap.add_argument('--roughcut', action='store_true')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--only', type=int)
    ap.add_argument('--out')
    ap.add_argument('--draft', action='store_true', help='interview/presentation: half-size fast build')
    a = ap.parse_args()
    cfg = os.path.abspath(a.preset)
    with open(cfg, encoding='utf-8') as fh: fmt = json.load(fh).get('format')
    if fmt in ('interview', 'presentation'):                 # long-form edits have their own ffmpeg engine
        import longform
        flags = [f for f, on in (('--stills', a.stills), ('--roughcut', a.roughcut), ('--build', a.build),
                                 ('--draft', a.draft)) if on]
        raise SystemExit(longform.main([cfg, *flags, *(['--out', a.out] if a.out else [])]))
    work, exports, stills_dir, name = dirs(cfg)
    if a.stills: stills(cfg, stills_dir)
    if a.roughcut: build(cfg, os.path.join(work, 'roughcut.mp4'), work, plain=True)
    if a.build: build(cfg, a.out or os.path.join(exports, f'{name}.mp4'), work, a.only)
    if not (a.stills or a.build or a.roughcut): ap.print_help()
