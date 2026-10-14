"""Compare a Windows loopback recording of EE music with dmusic renders (docs/reference/audio.md §11).

Measures, for each input WAV over given time windows:
  - tempo (onset-envelope autocorrelation, 60-200 BPM) and bar-period strength;
  - RMS level, spectral centroid, energy above 11 kHz;
  - 1/3-octave long-term spectrum (63 Hz - 10 kHz), normalised, for shape comparison
    (log-spectral distance between files, per window).

usage: compare_loopback.py --ref ref.wav[@offset] --cmp a.wav[@offset] [--cmp b.wav ...]
                           --windows "name:start:end,..."   (seconds, relative to each file's offset)
Only numpy is required. Writes nothing.
"""
import argparse
import wave

import numpy as np


def load(spec):
    path, _, off = spec.partition("@")
    w = wave.open(path)
    r, ch = w.getframerate(), w.getnchannels()
    x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float64).reshape(-1, ch) / 32768.0
    return path.split("/")[-1], x.mean(axis=1), r, float(off or 0)


def onset_env(x, r, hop=512, n=2048):
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop] * np.hanning(n)
    mag = np.log1p(100 * np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")
    return np.maximum(flux, 0), r / hop


def tempo(x, r):
    env, fps = onset_env(x, r)
    env = env - env.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    ac /= ac[0] + 1e-12
    lags = np.arange(len(ac)) / fps
    sel = (lags >= 60 / 200) & (lags <= 60 / 60)
    i = np.argmax(np.where(sel, ac, -1))
    beat = lags[i]
    # refine with parabolic interpolation
    if 0 < i < len(ac) - 1:
        a, b, c = ac[i - 1], ac[i], ac[i + 1]
        beat = (i + 0.5 * (a - c) / (a - 2 * b + c + 1e-12)) / fps
    return 60 / beat, ac[i]


THIRD = [63 * 2 ** (k / 3) for k in range(0, 23)]  # 63 Hz .. ~10 kHz


def spectrum(x, r, n=8192):
    f = np.fft.rfftfreq(n, 1 / r)
    P = np.zeros(len(f))
    for i in range(0, len(x) - n, n // 2):
        P += np.abs(np.fft.rfft(x[i:i + n] * np.hanning(n))) ** 2
    bands = []
    for c in THIRD:
        sel = (f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))
        bands.append(P[sel].sum() + 1e-20)
    b = 10 * np.log10(np.array(bands))
    cent = (f * P).sum() / (P.sum() + 1e-20)
    hi = 10 * np.log10(P[f >= 11025].sum() / (P.sum() + 1e-20) + 1e-20)
    return b - b.max(), cent, hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--cmp", action="append", default=[])
    ap.add_argument("--windows", required=True)
    a = ap.parse_args()
    files = [load(a.ref)] + [load(c) for c in a.cmp]
    wins = [(n, float(s), float(e)) for n, s, e in (w.split(":") for w in a.windows.split(","))]
    print(f"{'window':10} {'file':34} {'BPM':>6} {'ac':>5} {'RMS dB':>7} {'centroid':>8} {'>11k dB':>8} {'LSD vs ref':>10}")
    for name, s, e in wins:
        ref_b = None
        for i, (fn, x, r, off) in enumerate(files):
            seg = x[int((off + s) * r):int((off + e) * r)]
            if len(seg) < r:
                continue
            bpm, acv = tempo(seg, r)
            b, cent, hi = spectrum(seg, r)
            rms = 20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-12)
            if i == 0:
                ref_b = b
            lsd = np.sqrt(((b - ref_b) ** 2).mean()) if ref_b is not None else float("nan")
            print(f"{name:10} {fn[:34]:34} {bpm:6.1f} {acv:5.2f} {rms:7.1f} {cent:8.0f} {hi:8.1f} {lsd:10.2f}")


if __name__ == "__main__":
    main()
