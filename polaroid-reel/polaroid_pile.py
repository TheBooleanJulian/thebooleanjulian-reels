#!/usr/bin/env python3
"""
polaroid_pile.py — template 2: "Polaroid Long" bumper.

Captioned polaroids slide onto a desk and pile up. When a pile is full the
camera pans down to an empty patch of desk and the next pile starts. After the
last pile the pan lands on an instant camera, a flash cuts to a close-up, and
the camera ejects a print that develops into the end card.

    python polaroid_pile.py PHOTOS_DIR -o recap.mp4
    python polaroid_pile.py PHOTOS_DIR -o recap.mp4 -d 60 -m song.mp3
    python polaroid_pile.py PHOTOS_DIR -o draft.mp4 --preview

Captions: put a captions.txt in the photo folder, one "filename = caption" per line.
Shares photo loading, easing and encoding with polaroid_reel.py (template 1).
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

import polaroid_reel as pr

SCRIPT_DIR = Path(__file__).resolve().parent
ASSETS = SCRIPT_DIR / "template2"

# polaroid.png geometry: frame 525x637, photo window (left, top, right, bottom)
FRAME_W, FRAME_H = 525, 637
WINDOW = (33, 35, 492, 506)
# camera.png geometry: camera body box (left, top, right, bottom) inside the image
CAM_BODY = (5, 220, 712, 1045)
INK = (38, 36, 40)


# ─────────────────────────────── settings ────────────────────────────────
@dataclass
class Settings:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    per_page: int = 9          # cards in each pile
    interval: float = 0.45     # seconds between card arrivals
    slide: float = 0.6         # seconds for a card to slide in and settle
    linger: float = 1.2        # pile rests this long before the pan
    pan: float = 0.9           # pan between piles
    intro: float = 0.3         # empty desk before the first card
    cam_pan: float = 1.3       # pan from the last pile to the camera
    cam_hold: float = 0.7      # camera on screen (slow push-in) before the flash
    flash: float = 0.6         # flash length (cut happens 40% in)
    eject: float = 1.6         # print slides out of the camera
    develop: float = 2.4       # print develops from dark to the end card
    end_hold: float = 2.5      # finished print on screen
    fade_in: float = 0.3
    fade_out: float = 0.8
    card_scale: float = 1.0
    grade: float = 1.0         # warm, slightly faded photo look; 0 = untouched
    grain: float = 5.0
    seed: int = 7
    crf: int = 18
    preset: str = "veryfast"

    @property
    def unit(self) -> float:   # 1.0 at 1080p, scales every pixel size
        return min(self.width, self.height) / 1080

    @property
    def ending(self) -> float:
        """Seconds from the last pile leaving to the end of the video."""
        return self.cam_pan + self.cam_hold + 0.4 * self.flash + 0.3 + 0.6 * self.eject + \
            self.develop + self.end_hold


def pages_for(n: int, per_page: int) -> list[int]:
    """Split n photos into piles as evenly as possible (e.g. 20 by 9 -> 7, 7, 6)."""
    if n <= 0:
        return []
    k = math.ceil(n / per_page)
    base, extra = divmod(n, k)
    return [base + (1 if i < extra else 0) for i in range(k)]


def total_duration(n: int, s: Settings) -> float:
    pages = pages_for(n, s.per_page)
    if not pages:
        return s.intro + s.ending
    return (s.intro + (n - len(pages)) * s.interval + len(pages) * (s.slide + s.linger)
            + (len(pages) - 1) * s.pan + s.ending)


def fit_timing(n: int, s: Settings, duration: float | None,
               min_interval: float = 0.3, max_interval: float = 1.1) -> tuple[Settings, int]:
    """Adjust interval/linger so n photos fill `duration`. Returns (settings, photos_to_use)."""
    if not duration or n == 0:
        return s, n
    while n > 1:
        pages = len(pages_for(n, s.per_page))
        free = duration - s.intro - s.ending - (pages - 1) * s.pan - pages * s.slide
        if n == pages:                       # one card per pile: all time goes to lingering
            s.linger = max(0.5, free / pages)
            return s, n
        s.linger = 1.2
        interval = (free - pages * s.linger) / (n - pages)
        if interval >= min_interval:
            s.interval = min(interval, max_interval)
            # anything left over (few photos, long video) lets each pile linger longer
            s.linger = (free - (n - pages) * s.interval) / pages
            return s, n
        n -= 1
    s.linger = max(0.5, duration - s.intro - s.ending - s.slide)
    return s, n


# ─────────────────────────────── captions ────────────────────────────────
def load_captions(path: Path | None) -> dict[str, str]:
    """captions.txt: one 'filename = caption' (or tab-separated) per line, # for comments."""
    if not path or not path.is_file():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = "\t" if "\t" in line else "="
        if sep not in line:
            continue
        name, text = line.split(sep, 1)
        out[name.strip().lower()] = text.strip()
    return out


def caption_for(path: Path, captions: dict[str, str]) -> str:
    return captions.get(path.name.lower()) or captions.get(path.stem.lower(), "")


# ─────────────────────────────── assets ──────────────────────────────────
@dataclass
class Sprite:
    rgba: np.ndarray        # (h, w, 4) uint8
    shadow: np.ndarray      # (h, w) uint8, blurred alpha (zeros = no shadow)
    w: int
    h: int


class Assets:
    def __init__(self, s: Settings, desk: Path, end_card: Path):
        self.s = s
        self.frame_src = np.asarray(Image.open(ASSETS / "polaroid.png").convert("RGBA"))
        self.camera_src = np.asarray(Image.open(ASSETS / "camera.png").convert("RGBA"))
        self.desk_src = np.asarray(ImageOps.exif_transpose(Image.open(desk)).convert("RGB"))
        self.end_card_src = np.asarray(ImageOps.exif_transpose(Image.open(end_card)).convert("RGB"))
        font = ASSETS / "DancingScript.ttf"
        self.font_path = str(font) if font.exists() else None

    def font(self, size: int):
        if self.font_path:
            return ImageFont.truetype(self.font_path, size)
        return ImageFont.load_default()


def card_size(s: Settings) -> tuple[int, int]:
    ch = int(round(0.56 * 1080 * s.unit * s.card_scale))
    return int(round(ch * FRAME_W / FRAME_H)), ch


def window_rect(cw: int, ch: int) -> tuple[int, int, int, int]:
    sx, sy = cw / FRAME_W, ch / FRAME_H
    l, t, r, b = WINDOW
    return int(round(l * sx)), int(round(t * sy)), int(round(r * sx)), int(round(b * sy))


def grade_photo(img: np.ndarray, amount: float) -> np.ndarray:
    """Warm, slightly faded print look — matches the template's photo treatment."""
    if amount <= 0:
        return img
    f = img.astype(np.float32)
    grey = f @ np.array([0.299, 0.587, 0.114], np.float32)
    f = f + (grey[..., None] - f) * (0.28 * amount)                  # desaturate
    f = f * (1 - 0.07 * amount) + 255 * 0.07 * amount * np.array([1.0, 0.95, 0.86], np.float32)
    f *= np.array([1 + 0.03 * amount, 1.0, 1 - 0.05 * amount], np.float32)
    return np.clip(f, 0, 255).astype(np.uint8)


SHADOW_BLUR = 9.0


def padded(rgba: np.ndarray, s: Settings) -> np.ndarray:
    """Transparent border so the blurred shadow has room."""
    pad = int(SHADOW_BLUR * s.unit * 3 + 30 * s.unit)
    h, w = rgba.shape[:2]
    out = np.zeros((h + 2 * pad, w + 2 * pad, 4), np.uint8)
    out[..., :3] = 245
    out[pad:pad + h, pad:pad + w] = rgba
    return out


def with_shadow(rgba: np.ndarray, s: Settings) -> Sprite:
    out = padded(rgba, s)
    shadow = cv2.GaussianBlur(out[..., 3], (0, 0), max(1.0, SHADOW_BLUR * s.unit))
    return Sprite(out, shadow, out.shape[1], out.shape[0])


def frame_rgba(assets: Assets, cw: int, ch: int) -> np.ndarray:
    return cv2.resize(assets.frame_src, (cw, ch), interpolation=cv2.INTER_AREA)


def fit_photo(img: np.ndarray, ww: int, wh: int) -> np.ndarray:
    """Crop/resize a photo to fill a ww x wh window."""
    return cv2.resize(pr.crop_to_aspect(img, ww / wh), (ww, wh), interpolation=cv2.INTER_AREA)


def make_card(path: Path, caption: str, assets: Assets) -> Sprite:
    s = assets.s
    cw, ch = card_size(s)
    l, t, r, b = window_rect(cw, ch)
    ww, wh = r - l, b - t
    img = pr.load_photo(path, int(max(ww, wh) * 1.3))
    photo = grade_photo(fit_photo(img, ww, wh), s.grade)

    card = frame_rgba(assets, cw, ch).copy()
    fa = card[..., 3:4].astype(np.float32) / 255
    win = card[t:b, l:r].astype(np.float32)
    # photo sits behind the frame (frame alpha is 0 inside the window)
    wa = fa[t:b, l:r]
    win[..., :3] = win[..., :3] * wa + photo * (1 - wa)
    win[..., 3] = 255
    card[t:b, l:r] = (win + 0.5).astype(np.uint8)
    # faint paper grain so frames don't look flat
    rng = np.random.default_rng(zlib.crc32(path.name.encode()))
    noise = rng.normal(0, 1.4, (ch, cw, 1))
    card[..., :3] = np.clip(card[..., :3] + noise, 0, 255).astype(np.uint8)

    if caption:
        draw_caption(card, caption, (l, b, r, int(ch * 0.985)), assets, rng)
    return with_shadow(card, s)


def draw_caption(card: np.ndarray, text: str, box: tuple[int, int, int, int], assets: Assets,
                 rng: np.random.Generator) -> None:
    """Handwrite `text` into the bottom margin box (left, top, right, bottom), slightly tilted."""
    l, t, r, b = box
    bw, bh = r - l, b - t
    size = max(8, int(bh * 0.36))
    font = assets.font(size)
    while size > 8:
        x0, y0, x1, y1 = font.getbbox(text)
        if x1 - x0 <= bw * 0.82:
            break
        size -= 2
        font = assets.font(size)
    x0, y0, x1, y1 = font.getbbox(text)
    tw, th = x1 - x0, y1 - y0
    layer = Image.new("L", (tw + 20, th + 20), 0)
    ImageDraw.Draw(layer).text((10 - x0, 10 - y0), text, font=font, fill=230)
    layer = layer.rotate(float(rng.uniform(-3.5, 2.0)), resample=Image.BICUBIC, expand=True)
    a = np.asarray(layer).astype(np.float32) / 255
    lh, lw = a.shape
    # sit a little right of centre, like a hand-written label
    cx = l + bw * 0.5 + rng.uniform(0, 0.12) * bw
    cy = t + bh * 0.48
    x, y = int(cx - lw / 2), int(cy - lh / 2)
    xa, ya = max(0, x), max(0, y)
    xb, yb = min(card.shape[1], x + lw), min(card.shape[0], y + lh)
    if xb <= xa or yb <= ya:
        return
    a = a[ya - y:yb - y, xa - x:xb - x, None]
    reg = card[ya:yb, xa:xb, :3].astype(np.float32)
    card[ya:yb, xa:xb, :3] = (reg * (1 - a) + np.array(INK, np.float32) * a + 0.5).astype(np.uint8)


def make_camera(assets: Assets, scale: float) -> Sprite:
    src = assets.camera_src
    h, w = src.shape[:2]
    rgba = cv2.resize(src, (max(1, int(w * scale)), max(1, int(h * scale))),
                      interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    return Sprite(rgba, np.zeros(rgba.shape[:2], np.uint8), rgba.shape[1], rgba.shape[0])


def fit_window(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Cover-crop close-to-square images; letterbox wide ones over a blurred copy."""
    ih, iw = img.shape[:2]
    if 0.75 <= (iw / ih) / (w / h) <= 1.33:
        return pr.cover_resize(img, w, h)
    back = cv2.GaussianBlur(pr.cover_resize(img, w, h), (0, 0), max(w, h) / 30)
    back = (back.astype(np.float32) * 0.6).astype(np.uint8)
    sc = min(w / iw, h / ih)
    nw, nh = int(iw * sc), int(ih * sc)
    fg = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    x, y = (w - nw) // 2, (h - nh) // 2
    back[y:y + nh, x:x + nw] = fg
    return back


# ─────────────────────────────── compositing ─────────────────────────────
Pose = tuple  # (x, y, angle_deg, scale, lift)


def composite(dst: np.ndarray, sp: Sprite, poses: list[Pose], k: float, shadow_op: float = 0.45) -> None:
    """Draw sprite onto dst. Several poses = motion-blur samples (averaged)."""
    H, W = dst.shape[:2]
    mats = []
    xs, ys = [], []
    corners = np.array([[0, 0, 1], [sp.w, 0, 1], [0, sp.h, 1], [sp.w, sp.h, 1]], np.float64)
    for x, y, ang, scale, lift in poses:
        M = cv2.getRotationMatrix2D((sp.w / 2, sp.h / 2), -ang, scale)
        M[0, 2] += x - sp.w / 2
        M[1, 2] += y - sp.h / 2
        off = ((5 + 18 * lift) * k, (9 + 28 * lift) * k)
        mats.append((M, off, lift))
        pts = corners @ M.T
        xs += [pts[:, 0].min(), pts[:, 0].max() + off[0]]
        ys += [pts[:, 1].min(), pts[:, 1].max() + off[1]]
    x0, y0 = max(0, int(math.floor(min(xs)))), max(0, int(math.floor(min(ys))))
    x1, y1 = min(W, int(math.ceil(max(xs)))), min(H, int(math.ceil(max(ys))))
    if x1 <= x0 or y1 <= y0:
        return
    rw, rh = x1 - x0, y1 - y0
    n = len(mats)
    sh_acc = np.zeros((rh, rw), np.float32)
    a_acc = np.zeros((rh, rw, 1), np.float32)
    c_acc = np.zeros((rh, rw, 3), np.float32)
    has_shadow = sp.shadow.any()
    for M, off, lift in mats:
        M2 = M.copy()
        M2[0, 2] -= x0
        M2[1, 2] -= y0
        if has_shadow:
            Ms = M2.copy()
            Ms[0, 2] += off[0]
            Ms[1, 2] += off[1]
            sh = cv2.warpAffine(sp.shadow, Ms, (rw, rh), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            sh_acc += sh.astype(np.float32) * ((shadow_op + 0.12 * lift) / 255)
        rgba = cv2.warpAffine(sp.rgba, M2, (rw, rh), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(245, 245, 245, 0))
        a = rgba[..., 3:4].astype(np.float32) / 255
        a_acc += a
        c_acc += rgba[..., :3].astype(np.float32) * a
    reg = dst[y0:y1, x0:x1].astype(np.float32)
    if has_shadow:
        reg *= 1 - (sh_acc / n)[..., None]
    reg = reg * (1 - a_acc / n) + c_acc / n
    dst[y0:y1, x0:x1] = (reg + 0.5).astype(np.uint8)


def ease_in_out(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out_quart(x: float) -> float:
    return 1 - (1 - min(max(x, 0.0), 1.0)) ** 4


# ─────────────────────────────── schedule ────────────────────────────────
@dataclass
class Card:
    index: int
    path: Path
    caption: str
    page: int
    start: float
    x: float              # resting centre, world coordinates (y grows down the desk)
    y: float
    rot: float
    rot_in: float
    direction: tuple[float, float]


@dataclass
class Timeline:
    cards: list[Card]
    pans: list[tuple[float, float, float, float]]   # (t0, t1, from_y, to_y)
    cam_arrive: float
    cut: float
    eject_start: float
    develop_start: float
    end: float
    pages: int


def page_slots(count: int, s: Settings, rng: random.Random) -> list[tuple[float, float]]:
    """Resting spots (fractions of the frame) spread over the whole frame for any count.
    Best-candidate sampling: each new spot is the farthest of several random tries from
    the spots already taken, so consecutive cards also land far apart."""
    aspect = s.width / s.height
    out: list[tuple[float, float]] = []
    for _ in range(count):
        best, best_d = None, -1.0
        for _ in range(24):
            c = (rng.uniform(0.1, 0.9), rng.uniform(0.14, 0.86))
            d = min((math.hypot((c[0] - o[0]) * aspect, c[1] - o[1]) for o in out), default=0.0)
            if d > best_d:
                best, best_d = c, d
        out.append(best)
    return out


def build_timeline(photos: list[Path], captions: dict[str, str], s: Settings) -> Timeline:
    rng = random.Random(s.seed)
    W, H = s.width, s.height
    cards: list[Card] = []
    pans = []
    t = s.intro
    i = 0
    pages = pages_for(len(photos), s.per_page)
    for p, count in enumerate(pages):
        slots = page_slots(count, s, rng)
        for j in range(count):
            fx, fy = slots[j]
            x, y = fx * W, fy * H
            dx, dy = x - W / 2, y - H / 2
            if math.hypot(dx / W, dy / H) < 0.12:          # centre spot: pick any edge
                a = rng.uniform(0, 2 * math.pi)
            else:
                a = math.atan2(dy, dx) + rng.uniform(-0.35, 0.35)
            rot = rng.uniform(-11, 11)          # every frame upright, just a casual tilt
            path = photos[i]
            cards.append(Card(i, path, caption_for(path, captions), p, t + j * s.interval,
                              x, y + p * H, rot, rng.choice([-1, 1]) * rng.uniform(10, 22),
                              (math.cos(a), math.sin(a))))
            i += 1
        t = cards[-1].start + s.slide + s.linger
        if p < len(pages) - 1:
            pans.append((t, t + s.pan, p * H, (p + 1) * H))
            t += s.pan
    n_pages = max(1, len(pages))
    pans.append((t, t + s.cam_pan, (n_pages - 1) * H, n_pages * H))
    cam_arrive = t + s.cam_pan
    cut = cam_arrive + s.cam_hold + 0.4 * s.flash
    eject_start = cut + 0.3
    develop_start = eject_start + 0.6 * s.eject
    end = develop_start + s.develop + s.end_hold
    return Timeline(cards, pans, cam_arrive, cut, eject_start, develop_start, end, n_pages)


def view_y(tl: Timeline, t: float) -> tuple[float, float]:
    """World y of the top of the frame at time t, and pan speed (px/s)."""
    y = 0.0
    for t0, t1, a, b in tl.pans:
        if t >= t1:
            y = b
        elif t >= t0:
            p = (t - t0) / (t1 - t0)
            dt = 1e-3
            v = (ease_in_out(p + dt) - ease_in_out(p)) / dt * (b - a) / (t1 - t0)
            return a + (b - a) * ease_in_out(p), v
    return y, 0.0


def card_pose(c: Card, sp: Sprite, t: float, s: Settings) -> Pose | None:
    """Pose in world coordinates while sliding in; None before it starts."""
    lt = t - c.start
    if lt < 0:
        return None
    if lt >= s.slide:
        return (c.x, c.y, c.rot, 1.0, 0.0)
    top = c.page * s.height
    radius = math.hypot(sp.w, sp.h) / 2
    travel = pr.offscreen_distance(c.x, c.y - top, c.direction, s, radius)
    e = ease_out_quart(lt / s.slide)
    dx, dy = c.direction
    return (c.x + dx * travel * (1 - e), c.y + dy * travel * (1 - e),
            c.rot + c.rot_in * (1 - e), 1.0 + 0.05 * (1 - e), 1 - e)


# ─────────────────────────────── desk ────────────────────────────────────
def desk_strip(src: np.ndarray, width: int, height: int) -> np.ndarray:
    """Desk image scaled to width and mirror-tiled downwards to `height` rows."""
    sc = width / src.shape[1]
    tile = cv2.resize(src, (width, max(2, int(round(src.shape[0] * sc)))),
                      interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_CUBIC)
    reps = math.ceil(height / tile.shape[0])
    parts = [tile if r % 2 == 0 else tile[::-1] for r in range(reps)]
    return np.ascontiguousarray(np.concatenate(parts, axis=0)[:height])


def zoom(frame: np.ndarray, z: float, cx: float, cy: float) -> np.ndarray:
    if abs(z - 1) < 1e-4:
        return frame
    M = cv2.getRotationMatrix2D((cx, cy), 0, z)
    return cv2.warpAffine(frame, M, (frame.shape[1], frame.shape[0]), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REFLECT)


# ─────────────────────────────── render ──────────────────────────────────
def render(photos: list[Path], out: Path, s: Settings, desk: Path, end_card: Path,
           captions: dict[str, str] | None = None, music: Path | None = None,
           quiet: bool = False) -> float:
    captions = captions or {}
    assets = Assets(s, desk, end_card)
    tl = build_timeline(photos, captions, s)
    W, H, k = s.width, s.height, s.unit
    duration = tl.end
    nframes = int(round(duration * s.fps))

    # world: one screen per pile plus one for the camera
    strip = desk_strip(assets.desk_src, W, (tl.pages + 1) * H)
    cam_small = make_camera(assets, 0.58 * 1080 * k / (CAM_BODY[2] - CAM_BODY[0]))
    cam_cx = (CAM_BODY[0] + CAM_BODY[2]) / 2 * (cam_small.w / assets.camera_src.shape[1])
    cam_cy = (CAM_BODY[1] + CAM_BODY[3]) / 2 * (cam_small.h / assets.camera_src.shape[0])
    cam_world = (W / 2 + cam_small.w / 2 - cam_cx, tl.pages * H + H * 0.5 + cam_small.h / 2 - cam_cy)
    composite(strip, cam_small, [(*cam_world, 0.0, 1.0, 0.0)], k)

    # close-up after the flash: big camera at the top, print slides out under it
    pw = int(round(0.78 * 1080 * k))
    ph = int(round(pw * FRAME_H / FRAME_W))
    print_top = max(0.02 * H, (H - ph) / 2)
    cam_big_scale = 1.5 * 1080 * k / (CAM_BODY[2] - CAM_BODY[0])
    cam_big = make_camera(assets, cam_big_scale)
    body_bottom = print_top + 0.05 * ph
    cam_big_pos = (W / 2 + cam_big.w / 2 - (CAM_BODY[0] + CAM_BODY[2]) / 2 * cam_big_scale,
                   body_bottom + cam_big.h / 2 - CAM_BODY[3] * cam_big_scale)
    close_bg = pr.cover_resize(assets.desk_src[:, ::-1], W, H)
    pframe = frame_rgba(assets, pw, ph)
    pl, pt, pr_, pb = window_rect(pw, ph)
    end_img = fit_window(assets.end_card_src, pr_ - pl, pb - pt).astype(np.float32)
    end_grey = end_img @ np.array([0.299, 0.587, 0.114], np.float32)
    dark = np.array([30, 31, 33], np.float32)
    print_shadow = with_shadow(pframe, s).shadow

    def print_sprite(dev: float) -> Sprite:
        c = pr.smoothstep(dev)
        sat = pr.smoothstep((dev - 0.35) / 0.65)
        img = end_grey[..., None] + (end_img - end_grey[..., None]) * sat
        img = img * np.array([0.95 + 0.05 * sat, 1.0, 0.97 + 0.03 * sat], np.float32)  # green-ish while developing
        win = dark * (1 - c) + img * c
        rgba = pframe.copy()
        fa = rgba[pt:pb, pl:pr_, 3:4].astype(np.float32) / 255
        rgba[pt:pb, pl:pr_, :3] = (rgba[pt:pb, pl:pr_, :3] * fa + win * (1 - fa) + 0.5).astype(np.uint8)
        rgba[pt:pb, pl:pr_, 3] = 255
        out = padded(rgba, s)
        return Sprite(out, print_shadow, out.shape[1], out.shape[0])

    rng = np.random.default_rng(s.seed)
    grains = [rng.normal(0, s.grain, (H, W, 1)).astype(np.int16) for _ in range(8)] if s.grain > 0 else []
    streak_y = np.exp(-((np.arange(H) - 0.6 * H) / (5 * k)) ** 2)[:, None, None].astype(np.float32)
    glow_y = np.exp(-((np.arange(H) - 0.6 * H) / (60 * k)) ** 2)[:, None, None].astype(np.float32)
    warm = np.array([255, 196, 130], np.float32)

    tmp = out.with_name(out.stem + ".rendering" + out.suffix)
    enc = pr.open_encoder(tmp, s, duration, music)
    pool = ThreadPoolExecutor(max_workers=max(1, min(4, (os.cpu_count() or 2) - 1)))
    futures: dict[int, object] = {}
    baked: set[int] = set()
    shutter = 0.5 / s.fps
    started = time.time()

    def build(c: Card):
        try:
            return make_card(c.path, c.caption, assets)
        except Exception as exc:  # unreadable / half-copied file — skip it
            print(f"\n  ! skipping {c.path.name}: {exc}", file=sys.stderr)
            return None

    def sprite(c: Card):
        if c.index not in futures:
            futures[c.index] = pool.submit(build, c)
        return futures[c.index].result()

    try:
        for fi in range(nframes):
            t = fi / s.fps
            for c in tl.cards:   # prefetch upcoming cards
                if c.index not in futures and c.index not in baked and c.start - 3.0 <= t:
                    futures[c.index] = pool.submit(build, c)

            if t < tl.cut:
                # bake cards that have landed into the desk, in arrival order
                for c in tl.cards:
                    if c.index not in baked and t >= c.start + s.slide:
                        sp = sprite(c)
                        if sp is not None:
                            composite(strip, sp, [card_pose(c, sp, c.start + s.slide, s)], k)
                        baked.add(c.index)
                        futures.pop(c.index, None)
                vy, speed = view_y(tl, t)
                y0 = int(round(vy))
                frame = strip[y0:y0 + H].copy()
                for c in tl.cards:   # cards still sliding in, drawn with motion blur
                    if c.index in baked or t < c.start:
                        continue
                    sp = sprite(c)
                    if sp is None:
                        continue
                    a = card_pose(c, sp, t, s)
                    b = card_pose(c, sp, max(c.start, t - shutter), s)
                    # enough samples that blurred copies overlap into a smear
                    n = min(12, 1 + int(math.hypot(a[0] - b[0], a[1] - b[1]) / (6 * k)))
                    poses = []
                    for j in range(n):
                        pose = card_pose(c, sp, max(c.start, t - shutter * j / max(1, n - 1)), s)
                        poses.append((pose[0], pose[1] - y0, *pose[2:]))
                    composite(frame, sp, poses, k)
                blur = int(abs(speed) * shutter)
                if blur >= 3:
                    frame = cv2.blur(frame, (1, blur))
                if t > tl.cam_arrive:   # slow push-in on the camera
                    z = 1 + 0.12 * pr.smoothstep((t - tl.cam_arrive) / (tl.cut - tl.cam_arrive))
                    frame = zoom(frame, z, W / 2, H / 2)
                flash = pr.ease_in_cubic(min(1.0, max(0.0, (t - (tl.cut - 0.4 * s.flash)) / (0.4 * s.flash))))
            else:
                lt = t - tl.cut
                frame = zoom(close_bg, 1 + 0.03 * pr.smoothstep(lt / max(duration - tl.cut, 1e-6)),
                             W / 2, H * 0.45)
                dev = (t - tl.develop_start) / s.develop
                sp = print_sprite(min(max(dev, 0.0), 1.0))
                e = pr.ease_out_cubic(min(max((t - tl.eject_start) / s.eject, 0.0), 1.0))
                y_hidden = body_bottom - ph * 0.98
                y = y_hidden + (print_top - y_hidden) * e
                composite(frame, sp, [(W / 2, y + ph / 2, 0.0, 1.0, 0.0)], k, shadow_op=0.35)
                composite(frame, cam_big, [(*cam_big_pos, 0.0, 1.0, 0.0)], k)
                # warm anamorphic streak drifting across, like the template's light leak
                sx = (0.15 + 0.7 * pr.smoothstep(lt / max(duration - tl.cut, 1e-6))) * W
                streak_x = np.exp(-((np.arange(W) - sx) / (0.35 * W)) ** 2)[None, :, None].astype(np.float32)
                light = (0.22 * streak_y + 0.07 * glow_y) * streak_x * (0.6 + 0.4 * math.sin(lt * 1.7))
                f = frame.astype(np.float32)
                f += (warm - f * 0.5) * light
                frame = np.clip(f, 0, 255).astype(np.uint8)
                flash = 1 - pr.ease_out_cubic(min(1.0, lt / (0.6 * s.flash + 0.4)))

            if flash > 0:
                f = frame.astype(np.float32)
                frame = (f + (np.array([255, 250, 242], np.float32) - f) * flash).astype(np.uint8)
            if grains:
                frame = np.clip(frame.astype(np.int16) + grains[fi % len(grains)], 0, 255).astype(np.uint8)
            fade = min(1.0, t / s.fade_in if s.fade_in else 1.0,
                       (duration - t) / s.fade_out if s.fade_out else 1.0)
            if fade < 1.0:
                frame = (frame.astype(np.float32) * pr.smoothstep(fade)).astype(np.uint8)

            enc.stdin.write(np.ascontiguousarray(frame).tobytes())
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
        print(f"\n  done -> {out}  ({duration:.1f}s, {len(photos)} photos, {time.time() - started:.0f}s)")
    return duration


# ─────────────────────────────── CLI ─────────────────────────────────────
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Template 2: polaroids pile up on a desk, then an instant "
                                             "camera prints the end card.")
    ap.add_argument("photos", type=Path, help="folder of photos")
    ap.add_argument("-o", "--output", type=Path, default=Path("polaroid_pile.mp4"))
    ap.add_argument("-d", "--duration", type=float, help="target length in seconds (fits photos to it)")
    ap.add_argument("-m", "--music", type=Path, help="audio track (looped/trimmed, faded out)")
    ap.add_argument("-e", "--end-card", type=Path, default=ASSETS / "end_card.png",
                    help="image the final print develops into (default: template2/end_card.png)")
    ap.add_argument("--desk", type=Path, default=ASSETS / "desk.jpg", help="desk background image")
    ap.add_argument("--captions", type=Path,
                    help="captions file (default: captions.txt in the photo folder)")
    ap.add_argument("--per-page", type=int, default=9, help="cards in each pile")
    ap.add_argument("--limit", type=int, help="use at most N photos, picked evenly across the day")
    ap.add_argument("--order", choices=["time", "name", "random"], default="time")
    ap.add_argument("--interval", type=float, default=0.45, help="seconds between cards (when no -d)")
    ap.add_argument("--card-scale", type=float, default=1.0)
    ap.add_argument("--grade", type=float, default=1.0, help="warm faded photo look, 0 = off")
    ap.add_argument("--grain", type=float, default=5.0, help="film grain strength, 0 = off")
    ap.add_argument("--size", type=pr.parse_size, default=(1920, 1080), help="e.g. 1920x1080, 1080x1920")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7, help="change for a different pile layout")
    ap.add_argument("--preview", action="store_true", help="fast half-res draft")
    ap.add_argument("--best", action="store_true", help="slower encode, best quality")
    args = ap.parse_args(argv)

    if not args.photos.is_dir():
        ap.error(f"not a folder: {args.photos}")
    for label, p in (("end card", args.end_card), ("desk", args.desk), ("music", args.music)):
        if p and not p.exists():
            ap.error(f"{label} not found: {p}")
    if args.per_page < 1:
        ap.error("--per-page must be at least 1")

    w, h = args.size
    s = Settings(width=w, height=h, fps=args.fps, per_page=args.per_page, interval=args.interval,
                 card_scale=args.card_scale, grade=args.grade, grain=args.grain, seed=args.seed)
    if args.preview:
        s.width, s.height, s.fps, s.preset, s.crf = s.width // 4 * 2, s.height // 4 * 2, 24, "ultrafast", 23
        s.grain *= 0.6
    if args.best:
        s.preset, s.crf = "slow", 16
    args.output = pr.mp4_path(args.output)

    photos = pr.list_photos(args.photos, args.order, s.seed)
    if not photos:
        print(f"No photos found in {args.photos}")
        return
    if args.limit:
        photos = pr.evenly_pick(photos, args.limit)
    s, n = fit_timing(len(photos), s, args.duration)
    if n < len(photos):
        print(f"  {len(photos)} photos won't fit in {args.duration:.0f}s — using {n}, picked evenly across the day")
        photos = pr.evenly_pick(photos, n)
    captions = load_captions(args.captions or args.photos / "captions.txt")
    piles = pages_for(len(photos), s.per_page)
    print(f"  {len(photos)} photos in {len(piles)} piles · new card every {s.interval:.2f}s · "
          f"{len(captions)} captions · {total_duration(len(photos), s):.1f}s video")
    try:
        render(photos, args.output, s, args.desk, args.end_card, captions, args.music)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
