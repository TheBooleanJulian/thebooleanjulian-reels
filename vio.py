"""Video I/O: ffmpeg encoder for raw frames, sequential source reader, single-frame grab."""
import shutil, subprocess
from PIL import Image
from reel import W, H

FPS = 30
FIT = f'scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}'  # any source -> 9:16 fill


def ffmpeg_exe():
    """ffmpeg on PATH, else the copy bundled with imageio-ffmpeg (installed by setup.bat)."""
    p = shutil.which('ffmpeg')
    if p: return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise SystemExit('ffmpeg not found: install it (https://ffmpeg.org) or run setup.bat (bundles one)')


def encoder(path):
    return subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}',
                             '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16',
                             '-pix_fmt', 'yuv420p', '-r', str(FPS), path], stdin=subprocess.PIPE)


def probe_fps(video):
    out = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                                   'stream=r_frame_rate', '-of', 'default=nw=1:nk=1', video]).decode().strip()
    n, d = out.split('/')
    return float(n) / float(d)


class Src:
    """Sequential reader (auto-rotated, fitted to 1080x1920). Ask for increasing times only."""
    def __init__(self, video):
        self.fps = probe_fps(video)
        self.p = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', video, '-vf', FIT, '-f', 'rawvideo',
                                   '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.idx, self.cur = -1, None

    def close(self):
        self.p.kill(); self.p.wait()

    def frame(self, t):
        want = int(round(t * self.fps))
        while self.idx < want:
            buf = self.p.stdout.read(W * H * 3)
            if len(buf) < W * H * 3: break
            self.cur = buf; self.idx += 1
        return Image.frombuffer('RGB', (W, H), self.cur)


def grab_frame(video, t):
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', video, '-frames:v', '1',
                                   '-vf', FIT, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    return Image.frombuffer('RGB', (W, H), raw)


def probe_duration(video):
    return float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                          '-of', 'default=nw=1:nk=1', video]).decode().strip())


def has_audio(video):
    out = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries',
                                   'stream=index', '-of', 'csv=p=0', video]).decode().strip()
    return bool(out)
