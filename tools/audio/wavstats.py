# Per-file stats for 16-bit stereo WAV renders: duration, RMS dBFS, peak, clipped samples,
# fraction of 100 ms windows that are silent (< -60 dBFS), longest silent run.
import sys, wave, numpy as np
print(f"{'file':28} {'dur':>6} {'rms dBFS':>9} {'peak':>7} {'clip':>6} {'silent%':>8} {'longest silence':>16} {'first sound':>11}")
for p in sys.argv[1:]:
    w = wave.open(p); n = w.getnframes(); r = w.getframerate()
    x = np.frombuffer(w.readframes(n), dtype='<i2').astype(np.float64).reshape(-1, 2) / 32768.0
    m = x.mean(axis=1)
    rms = np.sqrt((x**2).mean()); peak = np.abs(x).max(); clip = int((np.abs(x) >= 0.9999).sum())
    win = r // 10; k = len(m) // win
    wr = np.sqrt((m[:k*win].reshape(k, win)**2).mean(axis=1))
    silent = wr < 10**(-60/20)
    run = best = 0
    for s in silent:
        run = run + 1 if s else 0; best = max(best, run)
    first = np.argmax(~silent) / 10 if (~silent).any() else -1
    db = 20*np.log10(rms) if rms > 0 else -999
    print(f"{p.split('/')[-1]:28} {n/r:6.1f} {db:9.1f} {peak:7.3f} {clip:6d} {100*silent.mean():7.1f}% {best/10:15.1f}s {first:10.1f}s")
