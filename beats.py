"""Beat detection for music beds, numpy only: tempo and beat times, used to snap cuts, text and b-roll to the music.

  python beats.py track.mp3            # {"bpm": 118.2, "beats": [0.51, 1.02, ...], "duration": 212.4}

Method: spectral-flux onset strength (log-magnitude STFT), tempo from the autocorrelation of that envelope with a
mild preference for ~120 BPM, then dynamic-programming beat tracking (D. Ellis, 2007): each beat is placed on a
strong onset while keeping the spacing close to the tempo period. Good on music with a steady pulse; on rubato or
beatless ambient tracks the result is a regular grid at the estimated tempo.
"""
import json, subprocess, sys
import numpy as np

SR, HOP, NFFT = 22050, 512, 2048
FPS = SR / HOP                                   # envelope frames per second (~43)


def load_mono(path):
    from vio import ffmpeg_exe
    raw = subprocess.run([ffmpeg_exe(), '-v', 'error', '-i', path, '-vn', '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, '<i2').astype(np.float32) / 32768.0


OFFSET = NFFT / 2 / SR                           # a frame is stamped at its window centre


def _band_matrix(n_bins, n_bands=40, lo=30.0, hi=11000.0):
    """Average FFT bins into log-spaced bands, so a broadband click doesn't outvote the kick drum."""
    freqs = np.arange(n_bins) * SR / NFFT
    edges = np.geomspace(lo, hi, n_bands + 1)
    m = np.zeros((n_bins, n_bands), np.float32)
    for b in range(n_bands):
        sel = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if not sel.any(): sel[np.argmin(np.abs(freqs - (edges[b] + edges[b + 1]) / 2))] = True
        m[sel, b] = 1.0 / sel.sum()
    return m


def onset_envelope(y):
    if len(y) < NFFT: return np.zeros(1, np.float32)
    n = 1 + (len(y) - NFFT) // HOP
    win = np.hanning(NFFT).astype(np.float32)
    bands = _band_matrix(NFFT // 2 + 1)
    env = np.zeros(n, np.float32)
    prev = None
    for s in range(0, n, 512):                   # blocks keep memory small on long tracks
        idx = np.arange(NFFT)[None, :] + HOP * np.arange(s, min(n, s + 512))[:, None]
        mag = np.log1p(100.0 * (np.abs(np.fft.rfft(y[idx] * win, axis=1)) @ bands)).astype(np.float32)
        full = mag if prev is None else np.vstack([prev[None, :], mag])
        flux = np.maximum(0.0, np.diff(full, axis=0)).sum(axis=1)
        env[s + (1 if prev is None else 0): s + len(mag)] = flux
        prev = mag[-1]
    k = 16                                       # remove the slow trend, keep the attacks
    trend = np.convolve(env, np.ones(k) / k, mode='same')
    env = np.maximum(0.0, env - trend)
    return env / (env.std() + 1e-9)


def tempo(env, lo_bpm=60, hi_bpm=200):
    x = env - env.mean()
    nfft = 1 << int(np.ceil(np.log2(2 * len(x) + 1)))
    ac = np.fft.irfft(np.abs(np.fft.rfft(x, nfft)) ** 2)[:len(x)]
    best, best_lag = -np.inf, None
    for lag in range(int(60 * FPS / hi_bpm), min(len(ac) - 1, int(60 * FPS / lo_bpm)) + 1):
        bpm = 60 * FPS / lag
        w = np.exp(-0.5 * (np.log2(bpm / 120.0) / 1.0) ** 2)
        if ac[lag] * w > best: best, best_lag = ac[lag] * w, lag
    if best_lag is None: return 120.0, 60 * FPS / 120.0
    a, b, c = ac[best_lag - 1], ac[best_lag], ac[best_lag + 1]       # parabolic refinement
    shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
    period = best_lag + float(np.clip(shift, -0.5, 0.5))
    return 60 * FPS / period, period


def track(env, period, tightness=100.0):
    n = len(env)
    offsets = np.arange(-int(round(2 * period)), -int(round(period / 2)) + 1)
    cost = -tightness * np.log(-offsets / period) ** 2
    score, back = env.astype(np.float64).copy(), np.full(n, -1)
    for i in range(n):
        prev = i + offsets
        ok = prev >= 0
        if ok.any():
            cand = score[prev[ok]] + cost[ok]
            j = int(np.argmax(cand))
            if cand[j] > 0: score[i] = env[i] + cand[j]; back[i] = prev[ok][j]
    tail = max(1, int(round(period)))
    i = n - tail + int(np.argmax(score[-tail:]))
    beats = []
    while i >= 0: beats.append(i); i = back[i]
    return np.array(beats[::-1])


def detect(path):
    y = load_mono(path)
    env = onset_envelope(y)
    bpm, period = tempo(env)
    frames = track(env, period)
    return {'bpm': round(float(bpm), 1), 'beats': [round(float(f) / FPS + OFFSET, 3) for f in frames],
            'duration': round(len(y) / SR, 3)}


if __name__ == '__main__':
    if len(sys.argv) != 2: sys.exit('usage: python beats.py <music file>')
    print(json.dumps(detect(sys.argv[1])))
