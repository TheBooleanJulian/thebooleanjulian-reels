"""Sound clean-up filters, noise floor, video denoise, and the transcriber's settings."""
import os, subprocess, sys, types, wave
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import longform  # noqa: E402
from vio import ffmpeg_exe  # noqa: E402


def write_wav(path, x, sr=48000):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(np.clip(x * 32767, -32768, 32767).astype('<i2').tobytes())


def read_wav(path):
    with wave.open(path, 'rb') as r:
        return np.frombuffer(r.readframes(r.getnframes()), '<i2').astype(np.float32) / 32768, r.getframerate()


def db(x): return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)


# ---------------------------------------------------------------- filter strings
def test_cleanup_off_is_empty():
    assert longform.cleanup_filter({}, -40) == ''
    assert longform.cleanup_filter({'denoise': 'off', 'voice': 'off'}, -40) == ''


def test_denoise_uses_measured_floor_and_clamps_it():
    f = longform.cleanup_filter({'denoise': 'medium'}, -36.4)
    assert f == 'highpass=f=90,highpass=f=90,afftdn=nr=12:nf=-36.4'
    assert longform.cleanup_filter({'denoise': 'light'}, -40).startswith('highpass=f=80,afftdn')
    assert 'nf=-20.0' in longform.cleanup_filter({'denoise': 'light'}, -5)      # afftdn's range is -80..-20
    assert 'nf=-80.0' in longform.cleanup_filter({'denoise': 'light'}, -120)


def test_voice_alone_still_cuts_rumble_and_leaves_headroom():
    f = longform.cleanup_filter({'voice': 'strong'}, -40)
    assert f.startswith('highpass=f=80,') and 'afftdn' not in f
    assert 'g=-6' in f and 'g=6' in f and 'deesser=i=0.5' in f and f.endswith('volume=-3dB')
    assert 'deesser' not in longform.cleanup_filter({'voice': 'light'}, -40)   # light: EQ only


def test_video_denoise_filter():
    assert longform.denoise_filter({}) == ''
    assert longform.denoise_filter({'denoise': 0}) == ''
    assert longform.denoise_filter({'denoise': 1}) == 'hqdn3d=6.00:4.50:9.00:6.75'
    assert longform.denoise_filter({'denoise': 5}) == longform.denoise_filter({'denoise': 1})


# ---------------------------------------------------------------- with ffmpeg
def test_noise_floor_finds_the_room_not_the_speech(tmp_path):
    sr, rng = 48000, np.random.default_rng(1)
    x = rng.normal(0, 10 ** (-40 / 20), sr * 6)                     # -40 dBFS hiss
    t = np.arange(sr) / sr
    for k in (1, 3):                                                  # two loud "speech" seconds
        x[k * sr:(k + 1) * sr] += 0.3 * np.sin(2 * np.pi * 300 * t)
    p = str(tmp_path / 'room.wav'); write_wav(p, x, sr)
    assert abs(longform.noise_floor(p) - -40) < 2


@pytest.mark.parametrize('dn,vo', [('light', 'off'), ('medium', 'medium'), ('strong', 'strong'), ('off', 'light')])
def test_every_level_runs_in_ffmpeg_and_keeps_length(tmp_path, dn, vo):
    sr, rng = 48000, np.random.default_rng(2)
    t = np.arange(sr * 2) / sr
    x = 0.2 * np.sin(2 * np.pi * 220 * t) * (t > 0.5) + rng.normal(0, 0.003, len(t)) + 0.05 * np.sin(2 * np.pi * 40 * t)
    src, out = str(tmp_path / 'in.wav'), str(tmp_path / 'out.wav')
    write_wav(src, x, sr)
    af = longform.cleanup_filter({'denoise': dn, 'voice': vo}, longform.noise_floor(src))
    subprocess.check_call([ffmpeg_exe(), '-v', 'error', '-y', '-i', src, '-af', af, '-ac', '1', '-c:a', 'pcm_s16le', out])
    y, _ = read_wav(out)
    assert abs(len(y) - len(x)) <= 1
    if dn != 'off':                                                   # the hiss before the tone comes down
        assert db(y[int(0.1 * sr):int(0.45 * sr)]) < db(x[int(0.1 * sr):int(0.45 * sr)]) - 3


def test_audition_writes_level_matched_pair(tmp_path, monkeypatch):
    sr, rng = 48000, np.random.default_rng(3)
    t = np.arange(sr * 14) / sr
    x = 0.25 * np.sin(2 * np.pi * 180 * t) * (np.sin(2 * np.pi * 0.5 * t) > 0) + rng.normal(0, 0.01, len(t))
    wavp = str(tmp_path / 'talk.wav'); write_wav(wavp, x, sr)
    e = longform.Edit.__new__(longform.Edit)                         # only what audition() needs
    e.cfg = {'audio': {'denoise': 'medium', 'voice': 'medium'}}
    e.cache, e.screen, e.video = str(tmp_path / 'cache'), None, wavp
    e.info = {'duration': 14.0}
    monkeypatch.setattr(longform, 'probe', lambda p: {'audio': True})
    r = e.audition(12.0)                                              # clamped so 10 s still fit
    assert r['t'] == 4.0 and 'afftdn' in r['filter']
    a, _ = read_wav(r['orig']); b, _ = read_wav(r['clean'])
    assert abs(len(a) - 2 * 10 * sr) < 200 and abs(len(a) - len(b)) < 200      # stereo samples
    assert abs(db(a) - -20) < 0.5 and abs(db(b) - -20) < 0.5
    e.cfg = {'audio': {}}
    assert e.audition(0)['note'].startswith('clean-up is off')


# ---------------------------------------------------------------- transcriber settings (regression)
def test_transcriber_keeps_speech_detector_off_by_default(tmp_path, monkeypatch):
    """faster-whisper's VAD heard 67 s of speech in a 13.6 min talk through a PA; the transcript stopped at 3:06."""
    seen = {}

    class Seg:
        end, words = 1.0, []

    class Model:
        def __init__(self, *a, **k): pass
        def transcribe(self, audio, **kw):
            seen.update(kw); return iter([Seg()]), types.SimpleNamespace(language='en')

    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=Model))
    import transcribe
    p = str(tmp_path / 'a.wav')
    with wave.open(p, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0' * 16000)
    transcribe.run_faster_whisper(p, 'tiny.en', 'en')
    assert seen['vad_filter'] is False and seen['condition_on_previous_text'] is False
    assert seen['hallucination_silence_threshold'] == 2.0
    transcribe.run_faster_whisper(p, 'tiny.en', 'en', vad=True)
    assert seen['vad_filter'] is True
