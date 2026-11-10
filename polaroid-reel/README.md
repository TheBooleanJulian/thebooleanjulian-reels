# Polaroid Reel

Two templates: **template 1** (`polaroid_reel.py`, below) floats prints over a title frame;
**template 2** (`polaroid_pile.py`, see [Template 2](#template-2-pile--instant-camera-polaroid_pilepy))
piles them on a desk and ends on an instant camera printing the end card.

Template 1 turns a folder of event photos into a highlight video: photos float in as polaroid prints over the
event background, rest with a gentle bob, then float back out. Photos play in the order they were
taken (EXIF capture time), so the video follows the day.

## Setup (once per laptop)

**Windows: double-click `setup.bat`.** It installs Python 3.12 with winget if no Python 3.10+ is
found, then installs the packages (plus `pillow-heif` for iPhone HEIC photos) into a private
environment at `%USERPROFILE%\.polaroid-reel\venv`. That folder is on the laptop, not in the synced
Drive folder, and not under AppData, because Microsoft Store Python silently redirects AppData writes
into its own sandbox folder. ffmpeg comes bundled with `imageio-ffmpeg`, so nothing else is needed. It's safe to run
again (e.g. after a failed download). `make_reel.bat` / `make_reel_2.bat` use that environment
automatically, and fall back to plain `python` if it doesn't exist.

If the laptop has no winget, setup opens python.org instead. Install Python, tick "Add python.exe to
PATH", then run `setup.bat` again.

Manual / other OS:

```bash
pip install -r requirements.txt
```

ffmpeg is used for encoding. If it isn't on your PATH, `imageio-ffmpeg` (in requirements) provides it.
For iPhone HEIC photos also run `pip install pillow-heif`.

`background.png` next to the script is the default background — swap it for another event's
title frame (any size; it's cover-fitted). A looping video works too: `-b loop.mp4`.

## Use

```bash
# basic: every photo, ~1.2 s apart
python polaroid_reel.py "D:/Event/selects" -o recap.mp4

# fit to a set length with music (photos are picked evenly across the day if too many)
python polaroid_reel.py "D:/Event/selects" -o recap.mp4 -d 90 -m track.mp3

# quick half-res draft to check pacing
python polaroid_reel.py "D:/Event/selects" -o draft.mp4 -d 90 --preview

# live offload: re-renders automatically 20 s after new photos stop arriving
python polaroid_reel.py "D:/Event/selects" -o recap.mp4 -d 90 -m track.mp3 --watch
```

On Windows you can also drag a photo folder onto `make_reel.bat`.

## Options

| Option | Default | What it does |
|---|---|---|
| `-d, --duration` | auto | Target length in seconds; spacing and hold adapt to the photo count |
| `-m, --music` | — | Audio track, looped or trimmed to fit, faded in and out |
| `-b, --background` | `background.png` | Background image or looping video |
| `--limit N` | all | Use at most N photos, picked evenly through the day |
| `--order` | `time` | `time` (EXIF), `name`, or `random` |
| `--interval` | 1.2 | Seconds between card entrances (when no `-d`) |
| `--hold` | 3.0 | Seconds each card rests on screen |
| `--intro` / `--outro` | 2.0 / 1.5 | Background-only time at start / end |
| `--card-scale` | 1.0 | Bigger or smaller polaroids |
| `--grain` | 6 | Animated film grain strength, 0 = off |
| `--no-zoom` | — | Turn off the slow background push-in |
| `--seed` | 7 | Change for a different card layout and tilt |
| `--size` / `--fps` | 1920x1080 / 30 | Output size (e.g. 3840x2160) and frame rate |
| `--preview` | — | 960×540, 24 fps, fast encode |
| `--best` | — | Slower encode, best quality |
| `--watch`, `--settle` | —, 20 | Keep watching the folder; seconds of quiet before re-rendering |

## Notes for live events

- Point it at a **selects** folder rather than the raw card dump, so only keepers make the cut.
- If two cameras' clocks differ, sync them beforehand, or use `--order name`.
- Files still copying are skipped and picked up on the next render; the previous video stays
  playable until the new one finishes (it renders to a temp file and swaps it in).
- Card spots are the `SLOTS` list near the top of the script (x, y as fractions of the frame) —
  edit them to keep cards clear of a different title layout.

## Template 2: pile + instant camera (`polaroid_pile.py`)

Modelled on the GLS26 "Polaroid_Long" bumper. Captioned polaroids slide in from the edges and pile
up on a desk, with motion blur. When a pile is full (9 cards by default) the view pans down to a
fresh patch of desk and the next pile starts. After the last pile the pan lands on an instant
camera, a flash cuts to a close-up, and the camera ejects a print that develops into the end card.

```bash
python polaroid_pile.py "D:/Event/selects" -o recap.mp4                  # natural pace
python polaroid_pile.py "D:/Event/selects" -o recap.mp4 -d 60 -m track.mp3
python polaroid_pile.py "D:/Event/selects" -o draft.mp4 --preview
```

Or drag a photo folder onto `make_reel_2.bat`.

**Captions** (optional): a `captions.txt` in the photo folder, one per line. Photos without a line
get a blank margin.

```
Accurova_850_7896.jpg = Wichita, KS
Accurova_850_7897 = Cambodia
```

| Option | Default | What it does |
|---|---|---|
| `-d, --duration` | auto | Target length; card spacing and pile rest time adapt, extra photos are dropped evenly |
| `-m, --music` | — | Audio track, looped or trimmed to fit, faded in and out |
| `-e, --end-card` | `template2/end_card.png` | Image the final print develops into. Square works best; wide images are letterboxed |
| `--desk` | `template2/desk.jpg` | Desk background. It is mirror-tiled downwards, so avoid text in it |
| `--captions` | `<photos>/captions.txt` | Captions file elsewhere |
| `--per-page` | 9 | Cards per pile |
| `--interval` | 0.45 | Seconds between cards (when no `-d`) |
| `--grade` | 1.0 | Warm, slightly faded photo look; 0 = untouched |
| `--limit`, `--order`, `--card-scale`, `--grain`, `--seed`, `--size`, `--fps`, `--preview`, `--best` | | Same as template 1. `--size 1080x1920` works for vertical |

**Not in the public GitHub repo:** `background.png` and the `template2/` artwork (everything except
`DancingScript.ttf`) carry client branding, so they live only in the Drive folder. In a fresh clone, pass your own
with `-b` (template 1) and `-e` / `--desk` (template 2), and copy `polaroid.png` + `camera.png` into `template2/`.

Assets in `template2/` came from the GLS26 project files (`GLS26_GLS Bumper - Polaroid_Long`):
the desk (base colour, shading and paper texture, without the handwritten quotes), the polaroid
frame, the camera and the GLS end card. The caption font is Dancing Script (SIL Open Font License).

## Output format (both templates)

Always an `.mp4` (any other extension given to `-o` is swapped for `.mp4`), encoded for playback on
anything, including venue / projector media players: H.264 High profile, yuv420p, AAC 48 kHz,
a keyframe every 2 s, faststart. Quality is CRF 18 with a bitrate ceiling of 24 Mbps at 1080p30
(scaled with resolution and frame rate, 60 Mbps max for 4K). Without the ceiling the film grain pushes
1080p past 50 Mbps, which some players stutter on. `--grain 0` gives a lighter file if a venue still
struggles.

## Tests

```bash
pip install pytest
pytest -q
```
