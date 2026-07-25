# -*- coding: utf-8 -*-
"""
Finds the REAL Windows microphone by testing every host API (MME / WASAPI /
DirectSound / WDM-KS). Talk loudly during each test.

>>> Before running: TURN OFF / DISCONNECT the Bluetooth headset, and make sure
    the microphone is NOT muted in Windows (Settings > System > Sound > Input).

Run:  python test_windows_mic.py
"""
import numpy as np
import sounddevice as sd

DURATION = 2.0
RATE = 16000

hostapis = sd.query_hostapis()
devices = sd.query_devices()

print("=" * 68)
print("HOST APIs on this PC:")
for h_i, h in enumerate(hostapis):
    din = h["default_input_device"]
    name = devices[din]["name"] if din is not None and din >= 0 else "(none)"
    print(f"  [{h_i}] {h['name']:<22} default input -> {name}")
print("=" * 68)
print("KEEP TALKING LOUDLY into your laptop microphone during each test.\n")

results = []
for idx, dev in enumerate(devices):
    if dev["max_input_channels"] < 1:
        continue
    name = dev["name"].lower()
    # Only test the built-in Realtek / generic microphones (skip headset & speakers).
    if "speaker" in name or "ws-1801" in name or "headset" in name:
        continue
    if not ("realtek" in name or "microphone" in name or "mic" in name
            or "sound mapper" in name or "primary" in name):
        continue

    hostapi_name = hostapis[dev["hostapi"]]["name"]
    label = f"[{idx}] {dev['name'].strip()} via {hostapi_name}"
    try:
        rec = sd.rec(int(DURATION * RATE), samplerate=RATE, channels=1,
                     dtype="int16", device=idx)
        sd.wait()
        peak = int(np.abs(rec).max())
        bar = "#" * min(50, peak // 300)
        status = "  <== WORKS!" if peak > 1200 else "  (silent)"
        print(f"{label}\n     level = {peak:5d} {bar}{status}\n")
        if peak > 1200:
            results.append((peak, idx, dev["name"].strip(), hostapi_name,
                            dev["hostapi"]))
    except Exception as e:
        print(f"{label}\n     (could not open: {e})\n")

print("=" * 68)
if results:
    results.sort(reverse=True)
    p, idx, nm, api, apiidx = results[0]
    print(f"BEST WINDOWS MIC: device [{idx}]  {nm}  via {api}  (level {p})")
    print(f">>> device index = {idx} , host API = {api}")
else:
    print("No built-in mic returned sound. The mic may be MUTED or its input")
    print("volume is 0 in Windows Sound settings. Fix that and re-run.")
print("=" * 68)
