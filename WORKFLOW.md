# Reel workflow (approval-gated)

Nothing gets rendered at full size, and nothing moves to the next step, until Julian says so.
Every project has a log in `projects/<name>/notes.md`; tick each step when it is approved.

## 0. Setup (once per machine)
`python3 doctor.py` -> fix any FAIL. `pip install faster-whisper` for talking-head transcription.

Every step below can also be done in **TBJ Reels Studio** (`studio.bat`): same commands underneath, same files,
same approvals. The Notes tab of a project edits `notes.md`.

## 1. New project
`python3 new_reel.py <name> --template talking_head|hook_plan_bts_reveal|interview|presentation [--video path] [--in-place]`
Original recordings are copied into `raw/` and never edited (`--in-place`: large files are read where they are,
still never edited). Everything generated lives in `projects/<name>/`.

## 2. Rough cut  (talking head)
`python3 transcribe.py raw/<file>.mp4 --out projects/<name>/src/<file>.words.json`, fix mishearings in the JSON
(names, brands; recurring ones go in `style/signature.json` -> `fixes`), then
`python3 make_reel.py projects/<name>/preset.json --roughcut`. Show it WITH sound. Wait for approval.
Remove repeated takes with `cuts: [[start,end]]`; tune `trim.max_gap` for tighter or looser pacing.

### Interviews and presentations
Transcribe interviews with `--fillers` so um/uh are written out and can be cut. Do the text edit (studio Edit tab:
cuts, "Cut to card" for the interviewer's questions, b-roll), then `--roughcut` WITH sound for approval. Check the
transcript for hallucinated words over silence at the start/end (Whisper sometimes writes "Thank you." there) and cut them.
Presentations: agree the source range (`clip`) first, so setup and applause are trimmed before anything else.
Names and roles in lower thirds, chapter titles and question wording are facts: confirm the spelling with Julian.

## 3. Creative direction
Ask for: hook line, mood, any extra clips/photos/music, what the viewer should do at the end. Offer ideas, but the direction is Julian's.

## 4. Graphics plan
A beat-by-beat plan: captions, callouts, zooms/punch-ins, pacing, end card, plus the exact on-screen text.
Facts on screen (numbers, specs) must come from a source (EXIF, ephemeris, his own notes): say which are calculated.
Offer the `--stills` sheet as the visual storyboard. Wait for approval.

## 5. Opening
Render the first 10-15 s with sound (`--build --only 0` or a short preset) and show it. Take feedback before building the rest.
Interviews/presentations: `--build --draft` (half size, fast) instead, reviewed for timing; then the full build.
The studio's live preview is for editing, not for approval: approval is always on a rendered file (rough cut, draft, export).

## 6. Full video
`python3 make_reel.py projects/<name>/preset.json --build`, then CHECK it: pull frames at the busiest moments,
check cuts, caption timing and audio. Say plainly what was and was not checked (e.g. "I can't listen to audio").

## 7. Final approval and cleanup
Only an explicit approval of a specific file counts ("keep going" and "looks good so far" do not).
Then: record the approved version and lessons in `notes.md`; move reusable lessons into `style/`;
delete `work/` scene files; keep `raw/`, `src/`, `preset.json`, `exports/` and the approved video.

## Rules
- Original footage is never overwritten. Exports keep the source frame rate and quality unless asked.
- Preview stills first, full renders once.
- Don't claim to have reviewed something that couldn't be reviewed.
- Own assets only: no AI-generated images or video, no stock without being asked.
