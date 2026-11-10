# TBJ-reels

Preset-driven pipeline for Instagram reels (1080x1920, 30 fps): your own photos and footage plus
code-drawn text, rings, leader lines, zooms and speed ramps. No AI-generated imagery.
Also: polaroid event recaps (`polaroid-reel/`) and full edits of **short interviews** and **medium-length
presentations** (`longform.py`), all driven from one local UI, **TBJ Reels Studio**.
Brand look: black / cyan / magenta; Orbitron + Exo 2 + Share Tech Mono for the reel HUD, Chakra Petch +
IBM Plex Sans for interviews/presentations, JetBrains Mono in the studio UI (all SIL OFL, bundled in `fonts/`).

## TBJ Reels Studio (the UI)
**Double-click `studio.bat`** (first time on a laptop: `polaroid-reel\setup.bat`). It opens
`http://127.0.0.1:8765` in the browser. Same thing by hand: `python studio.py`.

| Section | What it does |
|---|---|
| Polaroid recap | Pick a photo folder (no more drag-and-drop), music, background / end card, length, size; Draft or Render; the result plays in the page |
| Interview | New project from a recording, transcribe, edit **by text**, graphics, render (rough cut, stills, draft, build) |
| Presentation | Same flow, tuned for talks: chapters, subtitles, slides feed with speaker inset |
| Reel presets | The original 9:16 HUD presets: stills, rough cut, build, "only scene N", preset JSON editor |
| Projects / Jobs / Setup | Everything under `projects/`; job queue with live logs and Stop; environment check and `doctor.py` |

Editing by text (Interview / Presentation, Edit tab): click a word to play from it; drag or shift-click to select, then
**Cut** (or Delete), **Restore**, **Cut to card** (removes the interviewer's spoken question and shows it as a card),
**Card**, **Chapter**, **Lower third**, **B-roll** (pick a photo or clip), **Slides / Speaker** (layout switch),
**Fix word** (double-click). **Preview edit** plays the source and skips everything the edit removes, so pacing can be
judged before rendering. Every change saves to `preset.json` at once (Ctrl+Z undoes).
Long recordings that the browser can't play (HEVC, ProRes): **Make proxy** (540p copy in `~/.tbj-reels/cache/`).

The studio is Python standard library + one HTML file (`studio/index.html`, Julian Unified UI, dark/light). It listens on
127.0.0.1 only and every request carries a per-launch token. Jobs run one at a time in a queue.

## Interviews and presentations (`longform.py`)
Templates `presets/interview.json` and `presets/presentation.json` (every key has a `_doc` note).

| | Interview (default 9:16) | Presentation (default 16:9) |
|---|---|---|
| Cut | pauses > 0.6 s removed, um/uh removed, hand cuts | pauses > 1.4 s shortened to ~0.8 s, hand cuts |
| Framing | wide / tight (1.12x) alternating at each jump cut, focus point, zoom | static, optional zoom toward the speaker |
| Captions | a few words at a time, spoken word cyan | subtitle lines on a dark box |
| Graphics | intro title, lower thirds, question cards, b-roll (photo or clip), end card | intro, lower thirds, chapter tags, slides feed with speaker inset, end card |
| Sound | 12 ms fades at every cut, optional music bed ducked under speech, loudness -14 LUFS | same, -16 LUFS, audio from camera or screen recording |
| Also writes | `exports/<name>.srt` | `.srt` + `.chapters.txt` (YouTube, first chapter pinned to 0:00) |

```bash
python new_reel.py jane-tan --template interview --video D:/Shoots/jane.mp4          # copies to raw/
python new_reel.py keynote --template presentation --video E:/keynote.mov --in-place  # big file: read in place
python transcribe.py raw/jane.mp4 --out projects/jane-tan/src/jane.words.json --fillers   # --fillers keeps um/uh
python make_reel.py projects/jane-tan/preset.json --roughcut   # cut only, half size, with sound
python make_reel.py projects/jane-tan/preset.json --stills     # frame at every graphic -> work/stills/sheet.jpg
python make_reel.py projects/jane-tan/preset.json --build --draft   # half size, fast
python make_reel.py projects/jane-tan/preset.json --build      # -> exports/jane-tan.mp4 (+ .srt)
python longform.py projects/jane-tan/preset.json --plan        # the edit as JSON (segments, durations)
```
Times on graphics: `at_src` / `end_src` are source seconds and follow the edit when cuts change (the studio sets these);
`at` / `dur` are seconds on the finished timeline. A graphic placed on a moment that gets cut moves to the next kept moment.

Why a second engine: the reel engine redraws every frame in Python, which is right for 30 s of HUD motion and far too
slow for 20 minutes. `longform.py` only decides the edit and draws a few PNG cards; ffmpeg cuts, scales, overlays,
burns the captions (libass) and encodes in one pass (a 20 s test edit at 1080x1920 builds in ~15 s). Cuts snap to
whole frames, so the separately sliced audio stays sample-exact. Big temporary files (audio, proxies) go to
`~/.tbj-reels/cache/`, not the Drive folder. Design notes and what is deliberately left out: `docs/longform.md`.

## What's in v3
- **Signature Style** (`style/signature.json`): colours, fonts, safe zones, caption look, vocabulary and spelling fixes in one file.
- **Talking-head preset**: transcription (`transcribe.py`), auto rough cut (drops pauses, 12 ms audio fades), word-timed captions,
  punch-ins, lower third, audio carried through. Preview the cut alone with `--roughcut`.
- **Project folders**: `raw/` (untouched originals), `projects/<name>/` (preset, src, work, exports, notes), scaffolded by `new_reel.py`.
- **`doctor.py`** checks the machine; **`WORKFLOW.md`** + **`CLAUDE.md`** make any Claude session follow the approval-gated process.

## Quick start
```bash
pip install -r requirements.txt            # needs ffmpeg + ffprobe on PATH
python3 doctor.py                          # checks tools, fonts, optional speech packages
python3 new_reel.py my-shoot --template talking_head --video ~/clip.mp4     # new project folder
python3 transcribe.py raw/clip.mp4 --out projects/my-shoot/src/clip.words.json
python3 make_reel.py projects/my-shoot/preset.json --roughcut   # approve the cut (with sound) first
python3 make_reel.py projects/my-shoot/preset.json --stills     # then the graphics preview
python3 make_reel.py projects/my-shoot/preset.json --build      # -> projects/my-shoot/exports/
# (older preset style still works:)
python3 make_reel.py presets/hook_plan_bts_reveal.json --stills        # preview sheet in seconds -> stills/<preset>/sheet.jpg
REEL_VIDEO=/path/to/bts.mp4 python3 make_reel.py presets/hook_plan_bts_reveal.json --build   # -> out/<preset>.mp4
```
`--build --only 3` re-renders just scene 3 (0-based) and re-joins; the other scene files are reused.

## Preset: hook -> plan -> BTS -> reveal  (`presets/hook_plan_bts_reveal.json`)
| # | kind | what it does | main fields |
|---|---|---|---|
| 0 | `hook` | BTS clip + hook copy, ring on the brightest spot | `clip{start,dur}`, `lines`, `emphasis`, `marker` |
| 1 | `plan` | "we planned this" still (sky map, floor plan, moodboard) with two marked subjects, dashed link and a chip | `image`, `mask_boxes`, `a{center,radius}`, `b`, `chip_text`, `lines` |
| 2 | `bts_clip` (`bottom`) | speed-ramped BTS footage, tracked callout, two payoff lines | `ramp`, `callout{at,label,sub,track}`, `lines_after` |
| 3 | `bts_clip` (`top`) | plain BTS clip with a caption | `ramp`, `lines`, `sub`, `fade_in` |
| 4 | `reveal` | hero photo: shutter flash, slow push-in, tags locked to subjects, headline | `image`, `anchor`, `z_end`, `tags[]`, `headline` |
| 5 | `end` | roomy end card: title, date/gear lines, handle, fade out | `frame`, `title[]`, `subtitle`, `info[]` |

Every key is explained in the `_doc` notes inside the JSON. Scenes can be reordered, repeated or dropped:
add a second `bts_clip`, delete `plan`, etc.

## Making a new reel from the preset
1. `cp presets/hook_plan_bts_reveal.json presets/my_reel.json`
2. Drop your media in `src/` (hero photo, plan image, key BTS frames for previews; the full video goes via `REEL_VIDEO` or `"video"`).
3. Edit copy, times and coordinates. Get coordinates by opening the image and reading pixel positions;
   `python3 -c "from detect import *"` helpers (`find_moon_jupiter`, `bright_dot`) show how subjects are located.
4. `--stills` until it looks right (cheap), then `--build` once (~4 min for 24 s on 6 cores).
Each scene's `still: {t, bg}` picks the preview moment and (optionally) a saved frame so previews work without the video.

## Files
| file | role |
|---|---|
| `studio.py`, `studio/index.html`, `studio.bat` | TBJ Reels Studio: local UI server (stdlib) and its single-file page |
| `longform.py` | interview / presentation engine: edit plan, rough cut, stills, build, proxy |
| `cards.py` | interview / presentation graphics (lower third, cards, chapter tag, intro, end card) as PNGs |
| `subs.py` | caption cues, `.ass` for burning in, `.srt`, chapter lines |
| `make_reel.py` | CLI: `--stills`, `--roughcut`, `--build`, `--only N` (forwards interview/presentation presets to `longform.py`) |
| `preset.py` | scene kinds (`hook`, `plan`, `bts_clip`, `reveal`, `end`) and preset loader |
| `reel.py` | engine: loads the style, fonts, easing, `Overlay` (2x supersampled drawing + glow), HUD helpers |
| `transcribe.py` | speech -> word timings JSON (faster-whisper, WhisperX, or SRT import); `--fillers` keeps um/uh |
| `cuts.py` | rough cut: keep-segments from words or silence, manual cuts, edited-timeline remapping |
| `captions.py` | word-timed caption grouping + drawing (spoken word highlighted) |
| `new_reel.py` | scaffolds `projects/<name>/` from a template in `presets/` |
| `doctor.py` | environment check, writes `setup/last_check.txt` |
| `kit.py` | zoom-with-coordinate-mapping, reticle, leader/tag, chip |
| `vio.py` | ffmpeg encoder, sequential source reader (auto-rotate + fit to 9:16), frame grab, probes |
| `detect.py` | threshold + connected-components subject finding |
| `ephemeris.py` | astropy Moon/planet separation, phase, distances for caption numbers |

## Rules of thumb
- Preview stills first; only then spend a full render. Keep text between y~250 and y~1560 (Instagram UI zones).
- One mp4 per scene, identical size/fps/encoder, joined with `-c copy`; changing one scene re-renders only that scene.
- Overlays go through the zoom mapping, so labels stay glued to the subject while the image pushes in.
- Orbitron / Share Tech Mono lack some glyphs (U+25C6, middle dot): use `|` and `//`.
- Phone video carries a rotation flag: ffmpeg auto-rotates, and `vio.py` crops any source to 9:16.
- Audio is silent on purpose: add trending audio in Instagram (reel presets without a talking head).

## Tests
```bash
pip install pytest
pytest -q tests              # edit decisions, captions, studio argument checks (no ffmpeg needed)
cd polaroid-reel && pytest -q
```

`examples/moon-jupiter-storyboard.jpg`: the 3 Nov 2026 Moon x Jupiter reel (@accurova).
