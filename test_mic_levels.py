# -*- coding: utf-8 -*-
"""
Mic finder: records ~2 seconds from each real input device and prints the
sound LEVEL. Talk LOUDLY the whole time. The device with the highest level
is your working microphone.
Run:  python test_mic_levels.py
"""
import sys
import numpy as np
import sounddevice as sd

DURATION = 2.0   # seconds per device
RATE = 16000

devices = sd.query_devices()
# Candidate input devices (skip duplicates by name to keep the list short).
seen = set()
candidates = []
for i, d in enumerate(devices):
    if d["max_input_channels"] > 0:
        name = d["name"].strip()
        if name in seen:
            continue
        seen.add(name)
        candidates.append((i, name))

print("=" * 60)
print("KEEP TALKING LOUDLY until it finishes (about %d seconds total)." %
      int(len(candidates) * (DURATION + 0.5)))
print("=" * 60)

results = []
for idx, name in candidates:
    try:
        print(f"\n[{idx}] {name} ... recording, TALK NOW", flush=True)
        rec = sd.rec(int(DURATION * RATE), samplerate=RATE, channels=1,
                     dtype="int16", device=idx)
        sd.wait()
        peak = int(np.abs(rec).max())
        # 0..32767. >1500 means it clearly heard you.
        bar = "#" * min(50, peak // 300)
        status = "  <== HEARS YOU!" if peak > 1500 else ""
        print(f"      level = {peak:5d}  {bar}{status}")
        results.append((peak, idx, name))
    except Exception as e:
        print(f"      (could not open: {e})")

print("\n" + "=" * 60)
if results:
    results.sort(reverse=True)
    best = results[0]
    print(f"BEST MICROPHONE: device [{best[1]}]  {best[2]}  (level {best[0]})")
    print(f"\n>>> Use device number: {best[1]}")
else:
    print("No device could be opened.")
print("=" * 60)
