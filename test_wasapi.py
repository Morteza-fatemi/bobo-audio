# -*- coding: utf-8 -*-
"""
Tests the WASAPI microphone at its NATIVE sample rate (WASAPI refuses 16 kHz).
Talk loudly the whole time.  Run:  python test_wasapi.py
"""
import numpy as np
import sounddevice as sd

hostapis = sd.query_hostapis()
devices = sd.query_devices()

# Find the WASAPI host API and its default input device.
wasapi_idx = next((i for i, h in enumerate(hostapis)
                   if "WASAPI" in h["name"]), None)
if wasapi_idx is None:
    print("No WASAPI host API found."); raise SystemExit

dev_index = hostapis[wasapi_idx]["default_input_device"]
dev = devices[dev_index]
native_rate = int(dev["default_samplerate"])
print(f"WASAPI default input: [{dev_index}] {dev['name'].strip()}")
print(f"Native sample rate  : {native_rate} Hz")
print("\nKEEP TALKING LOUDLY for a few seconds...\n")

for rate in [native_rate, 48000, 44100]:
    try:
        rec = sd.rec(int(2.0 * rate), samplerate=rate, channels=1,
                     dtype="int16", device=dev_index)
        sd.wait()
        peak = int(np.abs(rec).max())
        bar = "#" * min(50, peak // 300)
        status = "  <== WORKS!" if peak > 1200 else "  (silent)"
        print(f"  {rate} Hz -> level {peak:5d} {bar}{status}")
    except Exception as e:
        print(f"  {rate} Hz -> could not open: {e}")

print("\nIf any line says WORKS, the Windows mic is fine — we'll use WASAPI.")
print("If ALL are silent, the mic is muted/disabled in Windows itself.")
