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

## Sound clean-up, video denoise, transcription fix (added 2026-11-11)
Found on the first real talk (13.6 min, phone at the back of a hall, voice through a PA):

- **The transcript stopped at 3:06.** faster-whisper's built-in speech detector (Silero VAD) classed 67 s of the
  13.6 min as speech, and only those parts reach Whisper. A softer threshold still lost most of it (452 words). With the
  detector off Whisper got 2,084 words to the end. `transcribe.py` now transcribes everything by default (`--vad` brings
  the detector back for long, quiet recordings), with `condition_on_previous_text=False` (one misheard stretch can't
  derail what follows) and `hallucination_silence_threshold=2` (no "Thank you." invented over applause). Costs time:
  about 3 min of CPU for the 13.6 min talk here, against 17 s with the detector.
- **What the room sounded like** (octave bands, speech vs the stretches between words): 30-60 Hz rumble as loud as the
  voice itself (air-con / handling), the rest an echo tail ~11 dB under the speech with the same shape as speech, and a
  boomy voice (125-250 Hz ~18 dB above 2-4 kHz). So the useful moves are a steep high-pass, a calibrated FFT denoise,
  and EQ; spectral denoise can't do much about echo.
- **`audio.denoise`**: high-pass (two stages at 90/100 Hz for medium/strong: rumble down 14 dB against 7 for one stage,
  0.7 dB off the voice's low notes) + `afftdn` at the measured room noise (10th percentile of 50 ms levels, cached per
  file). afftdn's default noise floor (-50 dB) sat far below this hall's (-32 dB), and at that setting every strength
  measured the same.
- **`audio.voice`**: -2/-4/-6 dB at 220 Hz, +2/+4/+6 dB at 3.2 kHz, a small 9 kHz shelf, de-essing, 3 dB headroom.
  On the talk: +4.4 dB of 2-5 kHz relative to the whole. No compressor: measured, it raised the room between words by
  2-4 dB, and the final loudness pass evens the level anyway.
- The clean-up runs on the whole recording before it is cut (no pumping at cuts) and is cached in
  `~/.tbj-reels/cache/<project>/voice_<hash>.wav` (one per project; 13.6 min took a few seconds).
- **Listen** (studio) renders 10 s at the playhead twice, original and cleaned, both at -20 dBFS RMS so loudness
  doesn't decide which sounds better. Nothing in the clean-up was judged by ear during development; Listen is how to judge.
- **Video denoise** (`grade.denoise`, 0-1, per clip or project): `hqdn3d` at source size before rotate/scale; 1 = 1.5x
  ffmpeg's default. On the hall footage at 0.6 the pixel-to-pixel variation went 2.41 -> 1.91 with the face intact.
  Not in the live preview (Stills show it).

## The website (added 2026-11-12)
Julian wanted the editor at `reels-editor.thebooleanjulian.dev` with **the browser as the front end only**: files stay
local (PC on the PC, laptop on the laptop, a client's device on that device), each browser its own session, and later
a Google login to see the same projects on every device.

- **Chosen: a local helper.** The existing engine (`studio.py`) runs on each device; the website serves the same
  `studio/index.html` as static files (Worker `tbj-reels-editor`, assets only). The page sees it isn't on 127.0.0.1,
  probes `http://127.0.0.1:8765-8784/api/pair` (last port first) and uses that origin for every API and media URL.
  Rejected for now: the whole engine in the browser (ffmpeg.wasm, Whisper on WebGPU): a second engine to keep in step,
  several times slower, trouble with files over ~2 GB. Revisit only if clients really need zero install.
- **Security**: unchanged for the local page. The studio adds CORS for exactly one origin
  (`WEB_ORIGINS`; `TBJ_REELS_WEB_ORIGINS` adds more, for testing only). `/api/pair` returns the launch token only when
  the request's `Origin` is the website: browsers set `Origin` and pages can't, so no other site can pair; local
  programs could, but they could read the files anyway. The Host check still blocks DNS rebinding. Preflights answer
  `Access-Control-Allow-Private-Network` (older Chrome); current Chrome/Edge show a one-time "apps on this device"
  (Local Network Access) prompt instead. The website's CSP only allows `connect/img/media` to `http://127.0.0.1:*`.
- **Restarts**: a closed or restarted studio makes calls fail or return 403; the page re-pairs once and retries, and
  on the first load it shows "Connect to the studio on this device" and keeps looking every 3 s.
- **Versions**: the website updates on deploy, each device's studio when its folder updates. `API_VERSION` (page and
  studio) only changes when they must move together; on a mismatch the page says which side to update.
- **Your account (Google sign-in + sync, added 2026-11-12, dormant until set up)**: `web/worker/index.js` runs first
  for `/auth/*` and `/cloud/*`; everything else is the static page. Sign-in is Google's code flow with identity scopes
  only (`openid email profile`), the same sealed-cookie scheme as rothko-cal; no Google token is kept, and the database
  stores Google's account id (`sub`), not the email. Each interview/presentation project you upload is kept as one
  bundle (preset text, transcript, notes, and each media file's name + size) in schema `thebooleanjulian_reels`
  (thebooleanjulian-db migration `20261112120000`), stored as text so the Worker never parses it (Free plan CPU).
  - **Sync rule**: each device records, per project and account, the account copy's `updated_at` and a hash of its own
    files at the last sync (`projects/<name>/.sync.json`). Changed here only: uploaded 4 s after a save. Changed in the
    account only: downloaded when the project opens. Changed in both: nothing is overwritten; Projects offers "Keep
    this device's" / "Use the account's". The server enforces it too (`base` must match, else 409).
  - **Footage on the other device**: media paths are looked up by file name + size in `~/.tbj-reels/media_index.json`
    (every file a project on this device has used); same name, different size is not trusted (cameras reuse C0001).
    Still missing: the editor shows "Find files"; pointing at one also fixes the others in that folder.
  - Downloading over an existing project keeps the previous preset in `work/backup/`. Reel presets don't sync.
  - Tested: Worker unit tests (sign-in, separate accounts, 409, cross-site writes, limits), helper tests (two simulated
    devices), and the whole flow in headless Edge against the real Worker code with a fake database and fake Google:
    upload on A, download on B with footage elsewhere, Find files, auto-sync after an edit, pull on A with A's own
    footage paths, and a two-device conflict. Not tested: real Google sign-in and the real database (not set up yet).

## Known limits / next steps (not built)
- Room echo is not removed: that needs a dereverberation model (a new dependency). For a talk, a feed from the venue's
  mixing desk (`audio.source: screen` with the slides recording, or a separate recorder) beats any clean-up.
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
