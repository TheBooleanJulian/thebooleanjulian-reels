# TBJ-reels (project instructions)

Python + Pillow + ffmpeg pipeline for Julian's Instagram reels (@accurova). Own footage and photos only; text and animated
graphics are drawn in code. No AI-generated images or video.

- Follow `WORKFLOW.md` (approval-gated). Log each approval in `projects/<name>/notes.md`.
- The look comes from `style/signature.json`; the quality bar is `style/motion_brief.md`. Change the look there, not in code.
- Scenes: `preset.py` (hook, plan, bts_clip, reveal, end, talking_head). Engine: `reel.py`, `kit.py`. Presets in `presets/`, projects in `projects/`.
- Interviews / presentations (`"format"` in the preset): `longform.py` (ffmpeg one-pass engine), `cards.py`, `subs.py`; look in `style/signature.json` -> `longform`. `make_reel.py` forwards to it. Design notes: `docs/longform.md`.
- UI: `studio.py` + `studio/index.html` (stdlib server, Julian Unified UI). Keep it stdlib and single-file; jobs only run this folder's scripts with validated arguments.
- Windows is the main machine: open text files with `encoding='utf-8'`, write ffmpeg concat paths with forward slashes, no `fork`. Tool venv: `%USERPROFILE%\.polaroid-reel\venv` (made by `polaroid-reel\setup.bat`).
- Tests: `pytest -q tests` (and `polaroid-reel/tests`).
- Always preview with `--stills` (or `--roughcut` for talking heads) before a full `--build`.
- Never edit or delete anything in `raw/`. Originals stay untouched.
- Check outputs by pulling frames from the finished file. Say what could not be checked.
- Orbitron/Share Tech Mono lack some glyphs: use `|` and `//` instead of U+25C6 / middle dot in those fonts.
