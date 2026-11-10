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

## Known limits / next steps (not built)
- Loudness is single-pass `loudnorm` (measured -15.4 LUFS against -14 on a 19 s test). A two-pass measure-then-apply
  would hit the target exactly; worth it if clips go to broadcast.
- No multicam (two camera angles switched by speaker). The `screen` mechanism (second synced source, layout
  switches) is the starting point: a second camera would be another layout.
- Interview captions highlight per word; there is no per-word emphasis styling or emoji (by design for client work).
- Speaker diarisation (who is talking) is not attempted; lower thirds are placed by hand from the transcript.
- B-roll photos are static (fade in/out). A slow push-in would need per-frame scaling; possible with ffmpeg `zoompan`
  but it shimmers, so it was left out.
- The browser plays H.264 sources directly; HEVC/ProRes need "Make proxy" first.
