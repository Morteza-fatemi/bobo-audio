# -*- coding: utf-8 -*-
"""
DEBUG tool: prints your microphone devices, then shows LIVE what Vosk hears.
Run:  python debug_listen.py
Speak "bobo", "play music", "stop" and watch what appears.
Press Ctrl+C to quit.
"""
import os, sys, json, queue
import sounddevice as sd
from vosk import Model, KaldiRecognizer, SetLogLevel

SetLogLevel(-1)
SAMPLE_RATE = 16000
BASE = os.path.dirname(os.path.abspath(__file__))

# 1) List all input devices so we can spot the right microphone.
print("=" * 60)
print("AUDIO INPUT DEVICES:")
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        mark = "  <-- DEFAULT" if i == sd.default.device[0] else ""
        print(f"  [{i}] {d['name']}  ({d['max_input_channels']}ch){mark}")
print("=" * 60)
print("Loading model...")

model = Model(os.path.join(BASE, "model"))
# FULL vocabulary (no grammar) so we see EXACTLY what it hears, unfiltered.
rec = KaldiRecognizer(model, SAMPLE_RATE)

q = queue.Queue()
def cb(indata, frames, t, status):
    if status:
        print("audio status:", status, file=sys.stderr)
    q.put(bytes(indata))

print("\n>>> LISTENING. Say: bobo ... play music ... stop")
print(">>> (what the engine hears is printed below)\n")

# Use the default input device. To force a device, put its index in device=NN.
with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=8000,
                       dtype="int16", channels=1, callback=cb):
    last_partial = ""
    while True:
        data = q.get()
        if rec.AcceptWaveform(data):
            text = json.loads(rec.Result()).get("text", "")
            if text:
                print(f"  FINAL   -> '{text}'")
            last_partial = ""
        else:
            p = json.loads(rec.PartialResult()).get("partial", "")
            if p and p != last_partial:
                print(f"  partial -> {p}")
                last_partial = p
