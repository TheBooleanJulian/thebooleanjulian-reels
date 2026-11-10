"""Speech -> word timings JSON.

  python3 transcribe.py raw/clip.mp4                       # faster-whisper (default), writes raw/clip.words.json
  python3 transcribe.py raw/clip.mp4 --backend whisperx    # WhisperX (better word alignment, heavier install)
  python3 transcribe.py raw/clip.mp4 --backend srt --srt clip.srt   # import captions you already have (APPROXIMATE word times)
  python3 transcribe.py raw/interview.mp4 --fillers         # also write out um/uh so interview edits can cut them

Output: {"source", "language", "backend", "text", "words": [{"w": "Hello", "start": 0.52, "end": 0.81}, ...]}
Edit the JSON by hand to fix mishearings; captions and the rough cut both read it.
The vocab list in style/signature.json is passed to Whisper as a hint (brand names, places).
"""
import argparse, json, os, re, subprocess, sys
from reel import STYLE


def extract_audio(src, wav):
    from vio import ffmpeg_exe
    subprocess.check_call([ffmpeg_exe(), '-v', 'error', '-y', '-i', src, '-vn', '-ac', '1', '-ar', '16000', wav])


FILLER_HINT = 'Umm, uh, so, like, erm, you know, I mean.'


def vocab_prompt(fillers=False):
    """Whisper hint: brand names, plus (fillers=True) a disfluent sample so it writes 'um'/'uh' out instead of
    silently dropping them; the interview edit can then cut them."""
    v = STYLE.get('vocab', [])
    parts = ([FILLER_HINT] if fillers else []) + (['Vocabulary: ' + ', '.join(v) + '.'] if v else [])
    return ' '.join(parts) or None


def fill_missing(words):
    """Some aligners return words without times (numbers, symbols): interpolate from neighbours."""
    n = len(words)
    for i, w in enumerate(words):
        if w.get('start') is None:
            j = i - 1
            while j >= 0 and words[j].get('end') is None: j -= 1
            k = i + 1
            while k < n and words[k].get('start') is None: k += 1
            a = words[j]['end'] if j >= 0 else (words[k]['start'] if k < n else 0.0)
            b = words[k]['start'] if k < n else a + 0.3
            span = max(0.05, (b - a) / (k - j))
            w['start'] = a + span * (i - j - 1)
            w['end'] = w['start'] + span
    return words


def run_faster_whisper(wav, model, lang, fillers=False):
    from faster_whisper import WhisperModel
    import wave, numpy as np
    with wave.open(wav, 'rb') as r:           # our ffmpeg already made 16 kHz mono: skip faster-whisper's PyAV decoder
        audio = np.frombuffer(r.readframes(r.getnframes()), '<i2').astype(np.float32) / 32768.0
    m = WhisperModel(model, device='cpu', compute_type='int8')
    segs, info = m.transcribe(audio, word_timestamps=True, vad_filter=True, language=lang,
                              initial_prompt=vocab_prompt(fillers))
    words = [{'w': w.word.strip(), 'start': float(w.start), 'end': float(w.end)}
             for s in segs for w in (s.words or []) if w.word.strip()]
    return words, info.language


def run_whisperx(wav, model, lang, fillers=False):
    import whisperx  # not exercised in the build workspace (no model access there)
    dev = 'cpu'
    m = whisperx.load_model(model, dev, compute_type='int8', language=lang,
                            asr_options={'initial_prompt': vocab_prompt(fillers)})
    audio = whisperx.load_audio(wav)
    res = m.transcribe(audio, batch_size=8)
    lang = res.get('language', lang or 'en')
    am, meta = whisperx.load_align_model(language_code=lang, device=dev)
    res = whisperx.align(res['segments'], am, meta, audio, dev, return_char_alignments=False)
    words = [{'w': w['word'].strip(), 'start': w.get('start'), 'end': w.get('end')}
             for w in res['word_segments'] if w['word'].strip()]
    return fill_missing(words), lang


def _t(s):
    h, m, rest = s.replace(',', '.').split(':')
    return int(h) * 3600 + int(m) * 60 + float(rest)


def run_srt(path):
    """SRT/VTT cues -> words, spread evenly across each cue (approximate!)."""
    txt = open(path, encoding='utf-8').read().replace('\r', '')
    words = []
    for m in re.finditer(r'(\d+:\d\d:\d\d[.,]\d+)\s*-->\s*(\d+:\d\d:\d\d[.,]\d+)[^\n]*\n(.*?)(?:\n\n|\Z)', txt, re.S):
        a, b, body = _t(m.group(1)), _t(m.group(2)), re.sub(r'<[^>]+>', '', m.group(3)).replace('\n', ' ')
        toks = body.split()
        if not toks: continue
        step = (b - a) / len(toks)
        for i, w in enumerate(toks): words.append({'w': w, 'start': a + i * step, 'end': a + (i + 1) * step})
    return words, 'unknown'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('media')
    ap.add_argument('--backend', default='faster-whisper', choices=['faster-whisper', 'whisperx', 'srt'])
    ap.add_argument('--model', default='small.en', help='e.g. tiny.en, small.en, medium, large-v3')
    ap.add_argument('--lang', default='en')
    ap.add_argument('--srt')
    ap.add_argument('--out')
    ap.add_argument('--fillers', action='store_true', help='keep um/uh in the transcript so the edit can cut them')
    a = ap.parse_args()
    out = a.out or os.path.splitext(a.media)[0] + '.words.json'
    if a.backend == 'srt':
        words, lang = run_srt(a.srt)
    else:
        wav = os.path.splitext(out)[0] + '.16k.wav'
        extract_audio(a.media, wav)
        try:
            words, lang = (run_faster_whisper if a.backend == 'faster-whisper' else run_whisperx)(wav, a.model, a.lang, a.fillers)
        except ImportError:
            sys.exit(f'{a.backend} is not installed: pip install {"faster-whisper" if a.backend == "faster-whisper" else "whisperx"}')
        finally:
            if os.path.exists(wav): os.remove(wav)
    json.dump({'source': os.path.basename(a.media), 'language': lang, 'backend': a.backend,
               'text': ' '.join(w['w'] for w in words), 'words': words}, open(out, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    print(f'{len(words)} words -> {out}')


if __name__ == '__main__':
    main()
