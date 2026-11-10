#!/usr/bin/env python3
"""
polaroid_reel.py — turn a folder of event photos into a highlight video where
photos float in and out as polaroid prints over a background.

    python polaroid_reel.py PHOTOS_DIR -o recap.mp4
    python polaroid_reel.py PHOTOS_DIR -o recap.mp4 --duration 90 --music song.mp3
    python polaroid_reel.py PHOTOS_DIR -o recap.mp4 --watch      # live offload mode

Requires: numpy, opencv-python, pillow  (+ ffmpeg on PATH, or `pip install imageio-ffmpeg`)
"""
from __future__ import annotations

import argparse
import math
import os
import random
import shutil
import subprocess
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

try:  # optional iPhone HEIC support
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    HEIC = True
except Exception:  # pragma: no cover
    HEIC = False

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"} | (
    {".heic", ".heif"} if HEIC else set()
)
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
SCRIPT_DIR = Path(__file__).resolve().parent
PAPER = (246, 245, 240)

# Card resting spots as (x, y) fractions of the frame — arranged around the
# centred title so it stays readable most of the time.
SLOTS = [
    (0.17, 0.24),  # top-left
    (0.85, 0.23),  # top-right
    (0.45, 0.80),  # bottom-centre
    (0.83, 0.76),  # bottom-right
    (0.15, 0.76),  # bottom-left
    (0.57, 0.17),  # top-centre
]


# ─────────────────────────────── settings ────────────────────────────────
@dataclass
class Settings:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    interval: float = 1.2      # seconds between card entrances
    hold: float = 3.0          # seconds a card rests on screen
    enter: float = 1.1         # seconds to float in
    exit: float = 1.0          # seconds to float out
    intro: float = 2.0         # background alone before the first card
    outro: float = 1.5         # background alone after the last card
    fade: float = 0.8          # fade from / to black
    card_scale: float = 1.0
    grain: float = 6.0
    ken_burns: float = 0.04    # slow background push-in (fraction)
    seed: int = 7
    crf: int = 18
    preset: str = "veryfast"

    @property
    def life(self) -> float:
        return self.enter + self.hold + self.exit


# ─────────────────────────────── photos ──────────────────────────────────
def capture_time(path: Path) -> float:
    """EXIF DateTimeOriginal if present, else file modified time."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)
        if raw:
            return datetime.strptime(str(raw).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        pass
    return path.stat().st_mtime


def list_photos(folder: Path, order: str = "time", seed: int = 7) -> list[Path]:
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
             and not p.name.startswith(".")]
    if order == "name":
        files.sort(key=lambda p: p.name.lower())
    elif order == "random":
        files.sort(key=lambda p: p.name.lower())
        random.Random(seed).shuffle(files)
    else:
        files.sort(key=lambda p: (capture_time(p), p.name.lower()))
    return files


def evenly_pick(items: list, k: int) -> list:
    if k >= len(items):
        return list(items)
    if k <= 1:
        return items[:1]
    return [items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)]


def load_photo(path: Path, max_side: int) -> np.ndarray:
    im = Image.open(path)
    if im.format == "JPEG":
        im.draft("RGB", (max_side * 2, max_side * 2))  # fast decode of big camera JPEGs
    im = ImageOps.exif_transpose(im).convert("RGB")
    return np.asarray(im)


def crop_to_aspect(img: np.ndarray, aspect: float, top_bias: float = 0.4) -> np.ndarray:
    """Crop to width/height == aspect. Vertical crops favour the upper part
    (where heads usually are) instead of dead centre."""
    h, w = img.shape[:2]
    if w / h > aspect:
        nw = int(round(h * aspect))
        x0 = (w - nw) // 2
        return img[:, x0:x0 + nw]
    nh = int(round(w / aspect))
    y0 = int((h - nh) * top_bias)
    return img[y0:y0 + nh, :]


# ─────────────────────────────── polaroid card ───────────────────────────
@dataclass
class CardSprite:
    rgba: np.ndarray        # (h, w, 4) uint8 — paper colour outside the card, alpha 0
    shadow: np.ndarray      # (h, w) uint8 blurred alpha
    w: int
    h: int
    card_w: int
    card_h: int


def make_card(path: Path, s: Settings) -> CardSprite:
    k = s.height / 1080 * s.card_scale
    img = load_photo(path, int(700 * k))
    portrait = img.shape[0] >= img.shape[1]
    if portrait:
        pw, aspect = int(310 * k), 4 / 5
    else:
        pw, aspect = int(405 * k), 5 / 4
    ph = int(round(pw / aspect))
    photo = cv2.resize(crop_to_aspect(img, aspect), (pw, ph), interpolation=cv2.INTER_AREA)

    side = max(2, int(pw * 0.06))
    bottom = max(4, int(pw * (0.22 if portrait else 0.18)))
    cw, ch = pw + 2 * side, ph + side + bottom

    blur = max(1.0, 9 * k)
    pad = int(blur * 3 + 40 * k)
    W, H = cw + 2 * pad, ch + 2 * pad

    rgba = np.zeros((H, W, 4), np.uint8)
    rgba[..., :3] = PAPER
    # paper: faint texture + gentle top-to-bottom shading
    rng = np.random.default_rng(zlib.crc32(path.name.encode()))
    paper = np.full((ch, cw, 3), PAPER, np.float32)
    paper += rng.normal(0, 1.6, (ch, cw, 1)).astype(np.float32)
    paper *= np.linspace(1.0, 0.965, ch, dtype=np.float32)[:, None, None]
    rgba[pad:pad + ch, pad:pad + cw, :3] = np.clip(paper, 0, 255).astype(np.uint8)
    rgba[pad:pad + ch, pad:pad + cw, 3] = 255
    # photo with a hairline inset edge, like a real print
    y0, x0 = pad + side, pad + side
    rgba[y0 - 1:y0 + ph + 1, x0 - 1:x0 + pw + 1, :3] = (214, 212, 206)
    rgba[y0:y0 + ph, x0:x0 + pw, :3] = photo

    shadow = cv2.GaussianBlur(rgba[..., 3], (0, 0), blur)
    return CardSprite(rgba, shadow, W, H, cw, ch)


# ─────────────────────────────── easing ──────────────────────────────────
def ease_out_back(x: float, s: float = 0.8) -> float:
    c3 = s + 1
    return 1 + c3 * (x - 1) ** 3 + s * (x - 1) ** 2


def ease_out_cubic(x: float) -> float:
    return 1 - (1 - x) ** 3


def ease_in_cubic(x: float) -> float:
    return x ** 3


def smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


# ─────────────────────────────── schedule ────────────────────────────────
@dataclass
class Placement:
    index: int
    path: Path
    start: float
    slot: int
    x: float
    y: float
    rot: float
    rot_in: float
    rot_out: float
    drift: tuple[float, float]
    bob_phase: float
    direction: tuple[float, float] = field(default=(1.0, 0.0))


def fit_timing(n: int, s: Settings, duration: float | None) -> tuple[Settings, int]:
    """Adjust interval/hold so n photos fit in `duration` seconds.
    Returns (settings, number_of_photos_to_use)."""
    if not duration or n == 0:
        return s, n
    lanes = len(SLOTS) - 1          # max cards on screen at once
    min_hold = 1.2
    span = duration - s.intro - s.outro   # time from first entrance to last exit
    if n == 1:
        s.hold = max(min_hold, span - s.enter - s.exit)
        return s, n
    # 1) keep the requested hold, spread entrances to fill the time
    s.interval = (span - s.life) / (n - 1)
    if s.life < s.interval * 3:
        # sparse: let cards linger so ~3 share the screen (hold capped at 8s)
        s.interval = span / (n - 1 + 3)
        s.hold = min(8.0, max(s.hold, s.interval * 3 - s.enter - s.exit))
        s.interval = (span - s.life) / (n - 1)
    if s.life <= s.interval * lanes:
        return s, n
    # 2) too crowded: shorten the hold so cards never pile into the same slot
    s.interval = span / (n - 1 + lanes)
    s.hold = s.interval * lanes - s.enter - s.exit
    if s.hold >= min_hold:
        return s, n
    # 3) still too crowded: shortest hold, and use fewer photos
    s.hold = min_hold
    min_interval = s.life / lanes
    n = max(1, min(n, int((span - s.life) / min_interval) + 1))
    if n > 1:
        s.interval = (span - s.life) / (n - 1)
    return s, n


def total_duration(n: int, s: Settings) -> float:
    if n == 0:
        return s.intro + s.outro
    return s.intro + (n - 1) * s.interval + s.life + s.outro


def build_schedule(photos: list[Path], s: Settings) -> list[Placement]:
    rng = random.Random(s.seed)
    free_at = [-1e9] * len(SLOTS)
    last_used = [-1e9] * len(SLOTS)
    out: list[Placement] = []
    prev_slot = -1
    cx, cy = s.width / 2, s.height / 2
    for i, p in enumerate(photos):
        t = s.intro + i * s.interval
        free = [k for k in range(len(SLOTS)) if free_at[k] <= t and k != prev_slot]
        if not free:
            free = [min(range(len(SLOTS)), key=lambda k: free_at[k])]
        # least recently used, random tie-break
        free.sort(key=lambda k: (last_used[k], rng.random()))
        slot = free[0] if len(free) == 1 else rng.choice(free[:2])
        free_at[slot] = t + s.life
        last_used[slot] = t
        prev_slot = slot

        fx, fy = SLOTS[slot]
        x = (fx + rng.uniform(-0.025, 0.025)) * s.width
        y = (fy + rng.uniform(-0.03, 0.03)) * s.height
        sign = -1 if fx < 0.5 else 1
        rot = sign * rng.uniform(2.5, 8.0) * (1 if rng.random() > 0.25 else -1)
        dx, dy = x - cx, y - cy
        norm = math.hypot(dx, dy) or 1.0
        direction = (dx / norm, dy / norm)
        k = s.height / 1080
        drift = (rng.uniform(-14, 14) * k, rng.uniform(-10, 10) * k)
        out.append(Placement(i, p, t, slot, x, y, rot,
                             rot_in=rng.choice([-1, 1]) * rng.uniform(8, 16),
                             rot_out=rng.choice([-1, 1]) * rng.uniform(6, 12),
                             drift=drift, bob_phase=rng.uniform(0, 2 * math.pi),
                             direction=direction))
    return out


def offscreen_distance(x: float, y: float, d: tuple[float, float], s: Settings, radius: float) -> float:
    """Distance along d from (x, y) until a circle of `radius` is fully outside the frame."""
    dx, dy = d
    ts = []
    if dx > 1e-6:
        ts.append((s.width + radius - x) / dx)
    if dx < -1e-6:
        ts.append((-radius - x) / dx)
    if dy > 1e-6:
        ts.append((s.height + radius - y) / dy)
    if dy < -1e-6:
        ts.append((-radius - y) / dy)
    return min(ts) if ts else radius * 2


def card_state(pl: Placement, sprite: CardSprite, t: float, s: Settings):
    """Return (x, y, angle_deg, lift 0..1) at global time t, or None if not visible."""
    lt = t - pl.start
    if lt < 0 or lt > s.life:
        return None
    radius = math.hypot(sprite.w, sprite.h) / 2
    travel = offscreen_distance(pl.x, pl.y, pl.direction, s, radius)
    dx, dy = pl.direction
    k = s.height / 1080

    def rest(tt: float):
        hp = min(max(tt / max(s.hold, 1e-6), 0.0), 1.0)
        bob = math.sin(2 * math.pi * tt / 3.2 + pl.bob_phase)
        return (pl.x + pl.drift[0] * hp, pl.y + pl.drift[1] * hp + 3.5 * k * bob,
                pl.rot + 0.5 * math.sin(2 * math.pi * tt / 4.1 + pl.bob_phase))

    if lt < s.enter:
        p = lt / s.enter
        e = ease_out_back(p)
        rx, ry, rr = rest(0.0)
        x = rx + dx * travel * (1 - e)
        y = ry + dy * travel * (1 - e)
        ang = rr + pl.rot_in * (1 - ease_out_cubic(p))
        lift = 1 - ease_out_cubic(p)
    elif lt < s.enter + s.hold:
        x, y, ang = rest(lt - s.enter)
        lift = 0.0
    else:
        p = (lt - s.enter - s.hold) / s.exit
        e = ease_in_cubic(p)
        rx, ry, rr = rest(s.hold)
        x = rx + dx * travel * e
        y = ry + dy * travel * e
        ang = rr + pl.rot_out * e
        lift = smoothstep(p * 2.5)
    return x, y, ang, lift


# ─────────────────────────────── compositing ─────────────────────────────
def _warp_region(src: np.ndarray, M: np.ndarray, w: int, h: int, frame_w: int, frame_h: int,
                 border):
    corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float64)
    pts = corners @ M.T
    x0 = max(0, int(math.floor(pts[:, 0].min())))
    y0 = max(0, int(math.floor(pts[:, 1].min())))
    x1 = min(frame_w, int(math.ceil(pts[:, 0].max())))
    y1 = min(frame_h, int(math.ceil(pts[:, 1].max())))
    if x1 <= x0 or y1 <= y0:
        return None
    M2 = M.copy()
    M2[0, 2] -= x0
    M2[1, 2] -= y0
    out = cv2.warpAffine(src, M2, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=border)
    return out, x0, y0, x1, y1


def draw_card(frame: np.ndarray, sprite: CardSprite, x: float, y: float, ang: float,
              lift: float, s: Settings) -> None:
    H, W = frame.shape[:2]
    k = s.height / 1080
    scale = 1.0 + 0.035 * lift
    c = (sprite.w / 2, sprite.h / 2)
    M = cv2.getRotationMatrix2D(c, -ang, scale)
    M[0, 2] += x - c[0]
    M[1, 2] += y - c[1]

    # shadow — deeper and softer-looking when the card is "lifted" in motion
    Ms = M.copy()
    Ms[0, 2] += (5 + 16 * lift) * k
    Ms[1, 2] += (9 + 26 * lift) * k
    r = _warp_region(sprite.shadow, Ms, sprite.w, sprite.h, W, H, 0)
    if r is not None:
        a, x0, y0, x1, y1 = r
        op = 0.42 + 0.12 * lift
        reg = frame[y0:y1, x0:x1].astype(np.float32)
        reg *= (1.0 - a.astype(np.float32)[..., None] * (op / 255.0))
        frame[y0:y1, x0:x1] = reg.astype(np.uint8)

    r = _warp_region(sprite.rgba, M, sprite.w, sprite.h, W, H, (*PAPER, 0))
    if r is not None:
        rgba, x0, y0, x1, y1 = r
        a = rgba[..., 3:4].astype(np.float32) / 255.0
        reg = frame[y0:y1, x0:x1].astype(np.float32)
        reg += (rgba[..., :3].astype(np.float32) - reg) * a
        frame[y0:y1, x0:x1] = (reg + 0.5).astype(np.uint8)


class Background:
    def __init__(self, path: Path, s: Settings, duration: float):
        self.s, self.duration = s, duration
        self.video = path.suffix.lower() in VIDEO_EXTS
        if self.video:
            self.cap = cv2.VideoCapture(str(path))
            if not self.cap.isOpened():
                raise SystemExit(f"Could not open background video: {path}")
        else:
            img = np.asarray(ImageOps.exif_transpose(Image.open(path)).convert("RGB"))
            self.base = cover_resize(img, s.width, s.height)

    def frame(self, t: float) -> np.ndarray:
        s = self.s
        if self.video:
            ok, f = self.cap.read()
            if not ok:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, f = self.cap.read()
            return cover_resize(cv2.cvtColor(f, cv2.COLOR_BGR2RGB), s.width, s.height)
        if s.ken_burns <= 0:
            return self.base.copy()
        p = smoothstep(t / max(self.duration, 1e-6))
        z = 1.0 + s.ken_burns * p
        M = cv2.getRotationMatrix2D((s.width / 2, s.height * 0.48), 0, z)
        return cv2.warpAffine(self.base, M, (s.width, s.height), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REFLECT)


def cover_resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = img.shape[:2]
    sc = max(w / iw, h / ih)
    nw, nh = max(w, round(iw * sc)), max(h, round(ih * sc))
    r = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_CUBIC)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return np.ascontiguousarray(r[y0:y0 + h, x0:x0 + w])


# ─────────────────────────────── encoding ────────────────────────────────
def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise SystemExit("ffmpeg not found. Install it (https://ffmpeg.org) or run: pip install imageio-ffmpeg")


def mp4_path(p: Path) -> Path:
    """Output is always an .mp4 (whatever extension was typed)."""
    return p if p.suffix.lower() == ".mp4" else p.with_suffix(".mp4")


def peak_kbps(s: Settings) -> int:
    """Video bitrate ceiling: 24 Mbps at 1080p30, scaled by pixel rate, 60 Mbps max (4K).
    Film grain would otherwise push CRF past 50 Mbps, which projector/USB players choke on."""
    return int(min(60000, max(4000, 24000 * s.width * s.height * s.fps / (1920 * 1080 * 30))))


def open_encoder(out: Path, s: Settings, duration: float, music: Path | None) -> subprocess.Popen:
    """Plain H.264 High / yuv420p / AAC 48 kHz in an MP4 — plays on anything, incl. venue players."""
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{s.width}x{s.height}",
           "-r", str(s.fps), "-i", "-"]
    if music:
        fade_st = max(0.0, duration - 2.5)
        cmd += ["-stream_loop", "-1", "-i", str(music),
                "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-af", f"afade=t=in:d=0.5,afade=t=out:st={fade_st:.2f}:d=2.5"]
    peak = peak_kbps(s)
    cmd += ["-c:v", "libx264", "-preset", s.preset, "-crf", str(s.crf), "-profile:v", "high",
            "-maxrate", f"{peak}k", "-bufsize", f"{peak}k",       # capped CRF: quality, but never too heavy
            "-g", str(s.fps * 2), "-keyint_min", str(s.fps),        # keyframe every 2 s: easy seeking
            "-pix_fmt", "yuv420p", "-tag:v", "avc1", "-movflags", "+faststart",
            "-t", f"{duration:.3f}", "-f", "mp4", str(out)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


# ─────────────────────────────── render ──────────────────────────────────
def render(photos: list[Path], out: Path, background: Path, s: Settings,
           music: Path | None = None, quiet: bool = False) -> float:
    schedule = build_schedule(photos, s)
    duration = total_duration(len(photos), s)
    nframes = int(round(duration * s.fps))
    bg = Background(background, s, duration)

    rng = np.random.default_rng(s.seed)
    grains = [rng.normal(0, s.grain, (s.height, s.width, 1)).astype(np.int16) for _ in range(8)] \
        if s.grain > 0 else []

    tmp = out.with_name(out.stem + ".rendering" + out.suffix)
    enc = open_encoder(tmp, s, duration, music)
    pool = ThreadPoolExecutor(max_workers=max(1, min(4, (os.cpu_count() or 2) - 1)))
    futures: dict[int, object] = {}
    sprites: dict[int, CardSprite | None] = {}
    lookahead = 4.0
    started = time.time()

    def build(pl: Placement):
        try:
            return make_card(pl.path, s)
        except Exception as exc:  # unreadable / half-copied file — skip it
            print(f"\n  ! skipping {pl.path.name}: {exc}", file=sys.stderr)
            return None

    try:
        for fi in range(nframes):
            t = fi / s.fps
            # prefetch cards appearing soon, release ones that are done
            for pl in schedule:
                if pl.start - lookahead <= t and pl.index not in futures and pl.start + s.life >= t:
                    futures[pl.index] = pool.submit(build, pl)
                if pl.start + s.life < t and pl.index in sprites:
                    sprites[pl.index] = None

            frame = bg.frame(t)
            for pl in schedule:  # later cards drawn on top
                if not (pl.start <= t <= pl.start + s.life):
                    continue
                if pl.index not in sprites:
                    sprites[pl.index] = futures[pl.index].result()
                sp = sprites[pl.index]
                if sp is None:
                    continue
                st = card_state(pl, sp, t, s)
                if st:
                    draw_card(frame, sp, *st, s)

            if grains:
                g = grains[fi % len(grains)]
                frame = np.clip(frame.astype(np.int16) + g, 0, 255).astype(np.uint8)
            fade = min(1.0, t / s.fade if s.fade else 1.0, (duration - t) / s.fade if s.fade else 1.0)
            if fade < 1.0:
                frame = (frame.astype(np.float32) * smoothstep(fade)).astype(np.uint8)

            enc.stdin.write(frame.tobytes())
            if not quiet and (fi % s.fps == 0 or fi == nframes - 1):
                el = time.time() - started
                rate = (fi + 1) / max(el, 1e-6)
                eta = (nframes - fi - 1) / max(rate, 1e-6)
                print(f"\r  rendering {fi + 1}/{nframes} frames  {rate:5.1f} fps  ETA {eta:5.0f}s ",
                      end="", flush=True)
        enc.stdin.close()
        if enc.wait() != 0:
            raise SystemExit("ffmpeg failed — see message above")
        os.replace(tmp, out)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        if enc.poll() is None:
            enc.kill()
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    if not quiet:
        print(f"\n  done → {out}  ({duration:.1f}s, {len(photos)} photos, {time.time() - started:.0f}s)")
    return duration


# ─────────────────────────────── CLI ─────────────────────────────────────
def parse_size(v: str) -> tuple[int, int]:
    w, h = v.lower().split("x")
    return int(w) // 2 * 2, int(h) // 2 * 2


def run_once(args, s: Settings) -> None:
    photos = list_photos(args.photos, args.order, s.seed)
    if not photos:
        print(f"No photos found in {args.photos}")
        return
    if args.limit:
        photos = evenly_pick(photos, args.limit)
    s, n = fit_timing(len(photos), s, args.duration)
    if n < len(photos):
        print(f"  {len(photos)} photos won't fit in {args.duration:.0f}s — using {n}, picked evenly across the day")
        photos = evenly_pick(photos, n)
    print(f"  {len(photos)} photos · new card every {s.interval:.2f}s · each holds {s.hold:.1f}s · "
          f"{total_duration(len(photos), s):.1f}s video")
    render(photos, args.output, args.background, s, args.music)


def watch(args, s0: Settings) -> None:
    print(f"Watching {args.photos} — re-renders {args.settle:.0f}s after new photos stop arriving. Ctrl+C to stop.")
    last_sig = None
    while True:
        files = [p for p in args.photos.iterdir() if p.suffix.lower() in IMAGE_EXTS]
        sig = (len(files), max((p.stat().st_mtime for p in files), default=0))
        settled = files and time.time() - sig[1] >= args.settle
        if sig != last_sig and settled:
            print(f"\n[{datetime.now():%H:%M:%S}] {len(files)} photos — rendering…")
            run_once(args, Settings(**vars(s0)))
            last_sig = sig
        time.sleep(3)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Folder of photos → floating polaroid highlight video.")
    ap.add_argument("photos", type=Path, help="folder of photos")
    ap.add_argument("-o", "--output", type=Path, default=Path("polaroid_reel.mp4"))
    ap.add_argument("-b", "--background", type=Path, default=SCRIPT_DIR / "background.png",
                    help="background image or looping video (default: background.png next to this script)")
    ap.add_argument("-d", "--duration", type=float, help="target length in seconds (fits photos to it)")
    ap.add_argument("-m", "--music", type=Path, help="audio track (looped/trimmed, faded out)")
    ap.add_argument("--limit", type=int, help="use at most N photos, picked evenly across the day")
    ap.add_argument("--order", choices=["time", "name", "random"], default="time",
                    help="time = EXIF capture time (default)")
    ap.add_argument("--interval", type=float, default=1.2, help="seconds between card entrances")
    ap.add_argument("--hold", type=float, default=3.0, help="seconds each card rests on screen")
    ap.add_argument("--intro", type=float, default=2.0, help="seconds of background before first card")
    ap.add_argument("--outro", type=float, default=1.5)
    ap.add_argument("--card-scale", type=float, default=1.0)
    ap.add_argument("--grain", type=float, default=6.0, help="film grain strength, 0 = off")
    ap.add_argument("--no-zoom", action="store_true", help="disable slow background push-in")
    ap.add_argument("--size", type=parse_size, default=(1920, 1080), help="e.g. 1920x1080, 3840x2160")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7, help="change for a different card layout")
    ap.add_argument("--preview", action="store_true", help="fast half-res draft")
    ap.add_argument("--best", action="store_true", help="slower encode, best quality")
    ap.add_argument("--watch", action="store_true", help="keep watching the folder and re-render")
    ap.add_argument("--settle", type=float, default=20, help="watch mode: quiet seconds before rendering")
    args = ap.parse_args(argv)

    if not args.photos.is_dir():
        ap.error(f"not a folder: {args.photos}")
    if not args.background.exists():
        ap.error(f"background not found: {args.background}")
    if args.music and not args.music.exists():
        ap.error(f"music not found: {args.music}")

    w, h = args.size
    s = Settings(width=w, height=h, fps=args.fps, interval=args.interval, hold=args.hold,
                 intro=args.intro, outro=args.outro, card_scale=args.card_scale, grain=args.grain,
                 ken_burns=0 if args.no_zoom else 0.04, seed=args.seed)
    if args.preview:
        s.width, s.height, s.fps, s.preset, s.crf = 960, 540, 24, "ultrafast", 23
        s.grain *= 0.6
    if args.best:
        s.preset, s.crf = "slow", 16
    args.output = mp4_path(args.output)

    try:
        watch(args, s) if args.watch else run_once(args, s)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
