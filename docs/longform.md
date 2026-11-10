# Interviews and presentations: plan and design notes

Status (2026-11-10): built and tested on synthetic footage (TTS speech over test patterns) and an
interview photo for b-roll. Not yet run on a real client interview or a real 20-minute talk; do one of each
through the full approval workflow before relying on it for paid work.

## What "full edit" means here

### Short interview (1-5 min, usually 9:16 for Instagram or 16:9 for LinkedIn/YouTube)
| Beat | How |
|---|---|
| Tighten | transcript-driven: pauses over 0.6 s removed (0.12 s air kept before speech, 0.22 s after), um/uh removed (transcribe with `--fillers`), hand cuts for false starts and repeats |
| Hide the jump cuts | framing alternates wide / tight (1.12x toward the face) at each cut; segments under 1.2 s keep the framing so it doesn't flicker |
| Who is talking | lower third (name + role) |
| Structure | question cards: "Cut to card" removes the interviewer's spoken question and shows it as a card instead |
| Cover | b-roll over the speech (event photos or clips; sound stays on the speaker) |
| Read without sound | word captions, the spoken word in cyan |
| Open / close | optional intro title over the first seconds, end card with handle |
| Sound | 12 ms fades at each cut, optional music bed ducked under speech, loudness normalised (-14 LUFS) |

### Medium presentation (10-40 min, 16:9)
| Beat | How |
|---|---|
| Trim | `clip` start/end removes setup and applause; pauses over 1.4 s shortened to ~0.8 s (a talk needs its rhythm); hand cuts for tech breaks |
| Navigation | chapters: on-screen tag + `.chapters.txt` for the YouTube description |
| Slides | optional screen recording, synced by `offset`; per-section layout: speaker full, or slides full with the speaker inset top-right |
| Who is talking | lower third |
| Accessibility | subtitle lines (max 42 chars, 2 lines) burned in, plus `.srt` for the platform |
| Wide stage camera | `output.zoom` + `focus` crop toward the speaker |
| Sound | same as interviews, -16 LUFS; audio from the camera or the screen recording (desk feed) |

## Decisions
- **Separate engine, same workflow.** The reel engine draws every frame in Python; fine for 30 s, hours for 20 min.
  `longform.py` plans in Python and renders in one ffmpeg pass (`select` on frame numbers, PNG overlays, libass).
  `make_reel.py --roughcut/--stills/--build` forward to it, so WORKFLOW.md and the approval gates are unchanged.
- **Frame-exact cuts.** Keep-segments are snapped to whole output frames before anything else; audio is sliced in
  Python at the same sample boundaries (48 kHz / 30 fps = 1600 samples per frame), so A/V can't drift across 100+ cuts.
  Output fps must divide 48000 (24, 25, 30, 50, 60).
- **Source-time anchors.** Graphics placed from the transcript store `at_src` (source seconds), so re-cutting never
  orphans them; a graphic on a removed moment moves to the next kept moment.
- **Captions via libass**, not PIL: one `.ass` file, fonts copied next to it, ffmpeg run in that folder so Windows
  paths never need filter escaping. Word mode positions every line with `\pos` and never overlaps events (libass
  stacks overlapping events, which showed as a ghost line during testing).
- **TV range output** (`yuv420p`, limited range): PNG inputs otherwise push the file to full range, which some venue
  players and TVs show wrong. Same compatibility goal as polaroid-reel.
- **Look**: `style/signature.json -> longform`, calmer than the reel HUD (no glow, one magenta rule per graphic),
  Chakra Petch for names/cards/word captions, IBM Plex Sans for subtitles and roles.
- **Big temporary files** (full-length audio, proxies) go to `~/.tbj-reels/cache/<project>/`, never into the Drive folder.
- **No new dependencies.** numpy and Pillow were already required; faster-whisper was already the transcription backend.

## The studio (UI)
`studio.py` (standard library HTTP server, 127.0.0.1 only, per-launch token) + `studio/index.html` (single file,
Julian Unified UI tokens and components, bundled fonts). Chosen over Tkinter because the browser plays the source
and the renders inline, which a text-driven video edit needs, and it matches the fleet's single-file HTML tools.
Native file dialogs still come from Tk (run in a short subprocess), so folders are picked, not dragged.

## The editor and the clip engine (added 2026-11-10)
The first version cut in one ffmpeg pass with `select`, which only works for clips in source order. Reordering,
transitions and per-clip looks needed an edit decision list:

- **`edl.py`**: clips `{in, out, transition, zoom, focus, rotate, grade}`, any order. A transition belongs to the clip
  it leads into and overlaps the two clips (the edit gets shorter by its length, clamped to half the shorter clip).
  Without `clips` in the preset, they come from the transcript as before; the first timeline edit in the studio writes
  them out ("manual timeline"), and **Cut pauses** goes back to the automatic cut.
- **Render**: each clip on its own (rotate, grade, cover-fit at zoom/focus, slides layout), parallel, cached by a hash
  of everything that affects its pixels; plain cuts joined by the concat demuxer, transitions by `xfade`; one overlay
  pass. Audio: frame-aligned slices, 12 ms fades at cuts, equal-power crossfades at transitions.
- **Colour**: a 3x3 RGB matrix (exposure as gain, temperature/tint as channel gains, saturation with Rec.709 weights)
  then contrast around mid-grey. ffmpeg: `colorchannelmixer` + `lutrgb`; studio preview: SVG `feColorMatrix` +
  `feComponentTransfer` in sRGB. Measured within 3/255 of each other on a test colour.
- **Horizon**: rotate about the centre, then zoom by `cos a + max(w/h, h/w) sin a` so no corner shows.
- **Text overlays** are libass events: one style per item (font, size, colour, optional box), an in phase, a hold and an
  out phase. Fade, slide, pop (`\t` scale) and typewriter (`\ko` karaoke with an invisible secondary colour; shadow off
  while typing, or the untyped letters cast one).
- **Beats**: `beats.py` (numpy): log-band spectral-flux onsets, autocorrelation tempo with a mild 120 BPM prior,
  dynamic-programming beat tracking. Within one frame on a synthetic drum pattern; not checked by ear on real music.
- **Preview parity**: `tests/test_preview_parity.py` runs the JavaScript copies of the edit and colour maths in Node and
  compares them with the Python, so the preview can't drift from the render unnoticed.
- **Fixed on the way**: `output.zoom` had no effect in the first version (the zoomed image was cropped back to the
  zoomed size, then scaled down). It now crops in as documented; projects that set a zoom will look tighter.

## Known limits / next steps (not built)
- Loudness is single-pass `loudnorm` (measured -15.4 LUFS against -14 on a 19 s test). A two-pass measure-then-apply
  would hit the target exactly; worth it if clips go to broadcast.
- The live preview plays dissolves and dips as they render; wipes, slides, zoom and circle preview as a dissolve.
  The slides layout and music ducking are not in the live preview (Stills and the export show them).
- No multicam (two camera angles switched by speaker). The `screen` mechanism (second synced source, layout switches)
  is the starting point.
- Speaker diarisation (who is talking) is not attempted; lower thirds are placed by hand from the transcript.
- B-roll photos are static (fade in/out); no Ken Burns move.
- Clips render as 8-bit Rec.709; HDR / 10-bit / log footage is not colour managed (grade it before, or use the
  grade sliders by eye).
- The browser plays H.264 sources directly; HEVC/ProRes need "Make preview proxy" first.
