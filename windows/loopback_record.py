"""Record the default render device via WASAPI loopback to a 16-bit stereo WAV (legion reference audio).

Run in the interactive user's session (session 1), not over plain SSH (session 0 has no user audio
session). Needs `pip install soundcard numpy` in C:\\EEPort\\tools\\venv (soundcard: BSD-3-Clause).

    python loopback_record.py <seconds> <out.wav> [--rate 48000]

Writes <out.wav> and <out.wav>.json with the wall-clock start (epoch ms) so the recording can be
aligned with music_hooks.js events (snapshot t0Epoch + event ms).
Caveat: legion has Nahimic APOs injected into audio processes; disable its effects for a clean capture.
"""
import argparse
import json
import time
import wave

import numpy as np
import soundcard as sc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seconds", type=float)
    ap.add_argument("out")
    ap.add_argument("--rate", type=int, default=48000)
    a = ap.parse_args()
    speaker = sc.default_speaker()
    mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    block = a.rate // 10
    chunks = []
    with mic.recorder(samplerate=a.rate, channels=2) as rec:
        start = time.time()
        while time.time() - start < a.seconds:
            chunks.append(rec.record(numframes=block))
    data = np.concatenate(chunks) if chunks else np.zeros((0, 2))
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(a.out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(a.rate)
        w.writeframes(pcm.tobytes())
    meta = {"startEpochMs": int(start * 1000), "seconds": len(pcm) / a.rate, "rate": a.rate,
            "device": speaker.name, "clipped": int((np.abs(data) >= 1.0).sum())}
    with open(a.out + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
