# -*- coding: utf-8 -*-
"""
BOBO — Offline Voice Assistant + Wi-Fi Web Remote for Windows
=============================================================

Voice
-----
* Wake word: say "Hey Bobo" (engine = Vosk, confidence-gated so noise doesn't
  trigger it).  You can switch to openWakeWord's noise-robust ready-made words
  ("alexa", "hey_jarvis", ...) with one config line — see WAKE_ENGINE.
* On wake: a Siri-like glowing 3-D orb appears at the top of the screen, the
  system volume ducks so your command is heard clearly, then restores.
* Commands: "sleep pc"/"screen off", "play"/"play music", "stop"/"pause",
  "next", "back"/"previous".

Wi-Fi Web Remote
----------------
* A small built-in web server serves a modern control page on your local
  network, so you can control media from your phone's browser.
* URL is printed at startup, e.g.  http://192.168.1.5:49731

Threads: tkinter GUI on the main thread, audio on a worker thread, web server
on its own thread. They communicate through a thread-safe queue.
"""

import io
import os
import sys
import json
import time
import math
import queue
import ctypes
import ctypes.wintypes as wintypes
import hmac
import socket
import hashlib
import secrets
import colorsys
import threading
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import sounddevice as sd
from vosk import Model, KaldiRecognizer, SetLogLevel

SetLogLevel(-1)   # silence Vosk log spam


# ===========================================================================
#  CONFIGURATION
# ===========================================================================

APP_NAME = "BOBO_Voice_Assistant"
COMMAND_LISTEN_SECONDS = 6      # how long we listen for a command after wake
SAMPLE_RATE = 16000             # both engines use 16 kHz mono

# --- Which wake-word engine? -----------------------------------------------
#   "vosk" -> custom phrase "hey bobo"  (what you asked for)
#   "oww"  -> openWakeWord ready-made words: better over loud music, but the
#             phrase must be one of: alexa / hey_jarvis / hey_mycroft /
#             hey_rhasspy (it has no "bobo" model without custom training).
WAKE_ENGINE = "vosk"

# -- settings for WAKE_ENGINE = "vosk"
WAKE_WORD = "bobo"              # the word inside "hey bobo" we key on
WAKE_CONFIDENCE = 0.80          # 0..1 — raise if it false-triggers, lower if
                                # it sometimes ignores you

# -- settings for WAKE_ENGINE = "oww"
WAKE_MODEL = "alexa"
WAKE_THRESHOLD = 0.4

# After handling a command, ignore new wake detections for this long.
WAKE_COOLDOWN = 1.5

# --- CPU saving: skip speech decoding while the room is quiet --------------
# Audio frames quieter than this (RMS, 0..32767) are treated as silence and are
# NOT fed to the recognizer, which is where nearly all the CPU goes. Raise it if
# BOBO still uses CPU in a quiet room; lower it if it stops hearing you.
VAD_RMS = 300
# Keep decoding for this many frames (80 ms each) after the last sound, so the
# tail of a word is never cut off.
VAD_HANGOVER = 10
# Frames of audio kept before speech starts, so the beginning of "Hey Bobo"
# is not clipped when the gate opens.
VAD_PREROLL = 3

# --- Ducking ---------------------------------------------------------------
DUCK_LEVEL = 15                 # volume (0..100 %) while listening for a command

# --- Wi-Fi web remote ------------------------------------------------------
WEB_PORT = 49731                # deliberately odd/high port
WEB_BIND = "0.0.0.0"            # "0.0.0.0" = reachable over Wi-Fi.
                                # Use "127.0.0.1" to allow this PC only.
# --- Web remote password ---------------------------------------------------
# Leave WEB_PASSWORD empty and put the password in a file named
# "web_password.txt" next to app.py (recommended — keeps it out of the source).
# If both are empty, the remote is OPEN to anyone on the network.
# After logging in once, the browser is remembered for WEB_SESSION_DAYS.
WEB_PASSWORD = ""
WEB_SESSION_DAYS = 30

# --- Live screen preview ---------------------------------------------------
# ⚠ This streams pictures of your desktop to anyone who can open the remote.
#    Set SCREEN_VIEW = False to disable it, and set a password if you keep it on.
SCREEN_VIEW = True
SCREEN_WIDTH = 760              # preview width in pixels (smaller = faster)
SCREEN_QUALITY = 55             # JPEG quality 1..95
SCREEN_REFRESH_MS = 1000        # how often the phone asks for a new frame

# --- Microphone selection --------------------------------------------------
INPUT_DEVICE_INDEX = None       # set a number to force a specific microphone
MIC_KEYWORDS = ["Microphone", "WS-1801", "Headset", "Realtek"]

# --- Vosk vocabulary (wake phrase + commands; small = fast and accurate) ---
GRAMMAR = json.dumps([
    "hey", "bobo",
    "sleep", "pc", "window", "screen",
    "play", "music", "stop", "pause",
    "next", "back", "previous", "track", "song",
    "[unk]"
])
COMMAND_CONFIDENCE = 0.55       # every word of a command must be this sure

# --- Paths (works both as .py and as a PyInstaller .exe) -------------------
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL_PATH = os.path.join(BASE_DIR, "model")
AUDIO_FRAME = 1280              # 80 ms blocks (required size for openWakeWord)

# --- Hotkey to wake BOBO from anywhere (even from lock screen) -------------
VK_B = 0x42                    # B key
VK_LCONTROL = 0xA2             # Left Ctrl
VK_RCONTROL = 0xA3             # Right Ctrl
VK_LSHIFT = 0xA0               # Left Shift
VK_RSHIFT = 0xA1               # Right Shift
WAKE_HOTKEY_LABEL = "Ctrl+Shift+B"


# ===========================================================================
#  ACTIONS  (shared by the voice commands and the web remote)
# ===========================================================================

def turn_off_screen():
    """Turn the MONITOR off; the PC keeps running. Mouse/key wakes it."""
    HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF = (
        0xFFFF, 0x0112, 0xF170, 2
    )
    ctypes.windll.user32.SendMessageW(
        HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF
    )


# --- Media control ----------------------------------------------------------
# Virtual-key codes for the keyboard media keys (used as a fallback).
VK = {"playpause": 0xB3, "next": 0xB0, "prev": 0xB1,
      "mute": 0xAD, "voldown": 0xAE, "volup": 0xAF}


def press_key(vk_code):
    """Tap a virtual key (KEYEVENTF_KEYUP = 2 releases it)."""
    ctypes.windll.user32.keybd_event(vk_code, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk_code, 0, 2, 0)


class _MediaWorker:
    """
    Talks to the Windows "media session" (the same thing the volume-flyout
    shows). This controls the app that is actually playing — including players
    like Telegram that ignore the global media keys.

    WinRT is async, so it gets its own thread with its own event loop.
    """

    def __init__(self):
        import asyncio
        self._asyncio = asyncio
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        self._asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def call(self, make_coro, timeout=4.0):
        fut = self._asyncio.run_coroutine_threadsafe(make_coro(), self._loop)
        return fut.result(timeout)


_media = None
_media_lock = threading.Lock()


def _media_run(make_coro):
    global _media
    with _media_lock:
        if _media is None:
            _media = _MediaWorker()
    return _media.call(make_coro)


async def _current_session():
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager)
    manager = await Manager.request_async()
    return manager.get_current_session()


def media_info():
    """What's playing right now, or None if nothing is."""
    async def go():
        session = await _current_session()
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        playback = session.get_playback_info()
        # PlaybackStatus: 4 = Playing, 5 = Paused
        return {
            "playing": int(playback.playback_status) == 4,
            "title": (props.title or "").strip(),
            "artist": (props.artist or "").strip(),
        }
    try:
        return _media_run(go)
    except Exception:
        return None


def media_command(action):
    """
    Control playback through the media session; fall back to the media key if
    there is no session (or the session refuses).
    """
    async def go():
        session = await _current_session()
        if session is None:
            return False
        if action == "playpause":
            return await session.try_toggle_play_pause_async()
        if action == "pause":
            # Pause ONLY — never resumes. Used by sleep timers so a timer can't
            # accidentally start music that was already stopped.
            if int(session.get_playback_info().playback_status) != 4:
                return True                      # already paused: nothing to do
            return await session.try_pause_async()
        if action == "next":
            return await session.try_skip_next_async()
        if action == "prev":
            return await session.try_skip_previous_async()
        return False
    try:
        if _media_run(go):
            return
    except Exception:
        pass
    # Fallback to the keyboard media key ("pause" has no key of its own).
    press_key(VK.get(action, VK["playpause"]))


ACTIONS = {
    "playpause": lambda: media_command("playpause"),
    "pause":     lambda: media_command("pause"),
    "next":      lambda: media_command("next"),
    "prev":      lambda: media_command("prev"),
    "volup":     lambda: press_key(VK["volup"]),
    "voldown":   lambda: press_key(VK["voldown"]),
    "mute":      lambda: press_key(VK["mute"]),
    "screenoff": turn_off_screen,
}


# --- Scheduled timers -------------------------------------------------------
# Actions a timer is allowed to run, with the label shown in the UI.
TIMER_ACTIONS = {
    "pause":     "Pause music",
    "screenoff": "Turn screen off",
    "mute":      "Mute",
}


class Scheduler(threading.Thread):
    """
    Runs actions after a delay ("pause the music in 10 minutes").

    Costs nothing while idle: the thread sleeps on a Condition until the next
    timer is actually due, rather than polling a clock.
    """

    def __init__(self):
        super().__init__(daemon=True)
        self._cv = threading.Condition()
        self._timers = {}          # id -> dict
        self._next_id = 1

    def add(self, action, seconds):
        if action not in TIMER_ACTIONS:
            raise ValueError("unknown action")
        seconds = int(seconds)
        if not (1 <= seconds <= 24 * 3600):
            raise ValueError("delay must be between 1 second and 24 hours")
        with self._cv:
            timer = {"id": self._next_id, "action": action,
                     "label": TIMER_ACTIONS[action],
                     "fire_at": time.time() + seconds, "total": seconds}
            self._timers[self._next_id] = timer
            self._next_id += 1
            self._cv.notify_all()          # re-evaluate the sleep deadline
        return timer

    def cancel(self, timer_id):
        with self._cv:
            gone = self._timers.pop(int(timer_id), None)
            self._cv.notify_all()
        return gone is not None

    def clear(self):
        with self._cv:
            count = len(self._timers)
            self._timers.clear()
            self._cv.notify_all()
        return count

    def list(self):
        now = time.time()
        with self._cv:
            items = list(self._timers.values())
        return [{"id": t["id"], "action": t["action"], "label": t["label"],
                 "remaining": max(0, round(t["fire_at"] - now)),
                 "total": t["total"]}
                for t in sorted(items, key=lambda t: t["fire_at"])]

    def run(self):
        while True:
            with self._cv:
                if not self._timers:
                    self._cv.wait()            # sleep until a timer is added
                    continue
                now = time.time()
                due = [t for t in self._timers.values() if t["fire_at"] <= now]
                if not due:
                    nearest = min(t["fire_at"] for t in self._timers.values())
                    self._cv.wait(timeout=max(0.05, nearest - now))
                    continue
                for t in due:
                    self._timers.pop(t["id"], None)
            for t in due:                       # run outside the lock
                try:
                    ACTIONS[t["action"]]()
                except Exception:
                    pass


_scheduler = None
_scheduler_lock = threading.Lock()


def scheduler():
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = Scheduler()
            _scheduler.start()
    return _scheduler


# --- Clipboard --------------------------------------------------------------
_clip_lock = threading.Lock()


def set_clipboard(text):
    """Put UTF-16 text on the Windows clipboard so you can Ctrl+V it."""
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p

    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    with _clip_lock:
        for _ in range(12):                 # another app may hold the clipboard
            if u32.OpenClipboard(0):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("clipboard is busy, try again")
        try:
            u32.EmptyClipboard()
            handle = k32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not handle:
                raise RuntimeError("could not allocate clipboard memory")
            ptr = k32.GlobalLock(handle)
            ctypes.memmove(ptr, buf, size)
            k32.GlobalUnlock(handle)
            if not u32.SetClipboardData(CF_UNICODETEXT, handle):
                raise RuntimeError("could not set clipboard data")
            # On success Windows owns the memory — do not free it.
        finally:
            u32.CloseClipboard()


# --- System state: volume, brightness, battery ------------------------------
# These are called from web-server threads, so COM is initialized per call.

class _ComWorker:
    """
    Runs COM calls on ONE dedicated thread that initializes COM exactly once
    and keeps a single cached volume interface alive.

    Why: the web server uses a new thread per request. Initializing and then
    uninitializing COM per call leaves interface objects to be released after
    COM is gone, which throws "Win32 exception releasing IUnknown" and can
    crash the process on exit. Keeping all COM on one long-lived thread avoids
    that entirely.
    """

    def __init__(self):
        self._jobs = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        try:
            import comtypes
            comtypes.CoInitialize()      # once, never uninitialized
        except Exception:
            pass
        iface = None
        while True:
            fn, box, done = self._jobs.get()
            try:
                if iface is None:
                    from ctypes import cast, POINTER
                    from comtypes import CLSCTX_ALL
                    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
                    speakers = AudioUtilities.GetSpeakers()
                    iface = cast(
                        speakers.Activate(IAudioEndpointVolume._iid_,
                                          CLSCTX_ALL, None),
                        POINTER(IAudioEndpointVolume)
                    )
                box.append(("ok", fn(iface)))
            except Exception as e:
                box.append(("err", e))
            done.set()

    def run(self, fn, timeout=5.0):
        box, done = [], threading.Event()
        self._jobs.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("audio COM worker timed out")
        kind, value = box[0]
        if kind == "err":
            raise value
        return value


_com = None
_com_lock = threading.Lock()


def _com_run(fn):
    global _com
    with _com_lock:
        if _com is None:
            _com = _ComWorker()
    return _com.run(fn)


def get_volume():
    """Master volume as 0..100, or None if unavailable."""
    try:
        return _com_run(lambda v: round(v.GetMasterVolumeLevelScalar() * 100))
    except Exception:
        return None


def set_volume(percent):
    """Set master volume from a 0..100 value."""
    level = max(0, min(100, int(percent))) / 100.0
    _com_run(lambda v: v.SetMasterVolumeLevelScalar(level, None))


def get_muted():
    try:
        return _com_run(lambda v: bool(v.GetMute()))
    except Exception:
        return False


def get_brightness():
    """Screen brightness as 0..100, or None if the display doesn't support it."""
    try:
        import screen_brightness_control as sbc
        values = sbc.get_brightness()
        return int(values[0]) if values else None
    except Exception:
        return None


def set_brightness(percent):
    import screen_brightness_control as sbc
    sbc.set_brightness(max(0, min(100, int(percent))))


def get_battery():
    """Battery info dict, or None on desktops without a battery."""
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is None:
            return None
        secs = b.secsleft
        # psutil uses huge sentinel values for "unknown"/"plugged in"
        mins = None if secs is None or secs < 0 else secs // 60
        return {"percent": round(b.percent), "plugged": bool(b.power_plugged),
                "minutes": mins}
    except Exception:
        return None


class ScreenCapturer:
    """
    Captures the screen on ONE dedicated thread.

    Why: the web server handles each request on a new thread, and the Windows
    screen-grab API (GDI device contexts used by `mss`) is not thread-safe —
    grabbing from many threads crashes the process. So all grabs happen here,
    on a single long-lived thread, and the latest JPEG is cached and shared by
    every viewer.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._stamp = 0.0
        self._error = None
        self._want = threading.Event()
        self._ready = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _grab_image(self, sct):
        from PIL import Image
        if sct is not None:
            raw = sct.grab(sct.monitors[1])            # [1] = primary monitor
            return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        from PIL import ImageGrab
        return ImageGrab.grab()

    def _loop(self):
        sct = None
        try:
            import mss
            MSS = getattr(mss, "MSS", None) or mss.mss   # newer/older mss API
            sct = MSS()
        except Exception:
            sct = None                                   # fall back to ImageGrab

        while True:
            self._want.wait()
            self._want.clear()
            try:
                from PIL import Image
                img = self._grab_image(sct)
                if img.width > SCREEN_WIDTH:
                    h = round(img.height * SCREEN_WIDTH / img.width)
                    img = img.resize((SCREEN_WIDTH, h), Image.BILINEAR)
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=SCREEN_QUALITY)
                with self._lock:
                    self._frame, self._stamp, self._error = (
                        buf.getvalue(), time.time(), None
                    )
            except Exception as e:
                with self._lock:
                    self._error = e
            self._ready.set()

    def get(self, max_age=0.5):
        """Return a JPEG no older than `max_age` seconds."""
        with self._lock:
            if self._frame is not None and time.time() - self._stamp < max_age:
                return self._frame
        self._ready.clear()
        self._want.set()
        self._ready.wait(timeout=6)
        with self._lock:
            if self._frame is None:
                raise self._error or RuntimeError("screen capture failed")
            return self._frame


_capturer = None
_capturer_lock = threading.Lock()

# Shared event: manually wake the listener (triple-* or future triggers)
manual_wake = threading.Event()


def capture_screen_jpeg():
    """Latest screen frame as JPEG bytes (captures on a dedicated thread)."""
    global _capturer
    with _capturer_lock:
        if _capturer is None:
            _capturer = ScreenCapturer()
    return _capturer.get()


_status_cache = {"at": 0.0, "data": None}
_status_lock = threading.Lock()


def get_status():
    """
    Everything the web remote shows.

    Cached for a moment so several open phones (or a fast-polling page) don't
    each trigger a fresh WinRT / COM round trip.
    """
    with _status_lock:
        if _status_cache["data"] and time.time() - _status_cache["at"] < 1.2:
            data = dict(_status_cache["data"])
            data["timers"] = scheduler().list()      # always live
            return data

    data = {
        "volume": get_volume(),
        "muted": get_muted(),
        "brightness": get_brightness(),
        "battery": get_battery(),
        "screen": bool(SCREEN_VIEW),
        "media": media_info(),
    }
    with _status_lock:
        _status_cache["at"], _status_cache["data"] = time.time(), data
    data = dict(data)
    data["timers"] = scheduler().list()
    return data


def execute_command(text):
    """Map recognized speech to an action. Returns True if something ran."""
    text = text.lower().strip()
    if not text:
        return False
    if "sleep" in text or "screen" in text:
        ACTIONS["screenoff"]();  return True
    if "next" in text:
        ACTIONS["next"]();       return True
    if "back" in text or "previous" in text:
        ACTIONS["prev"]();       return True
    if "play" in text:
        ACTIONS["playpause"]();  return True
    if "stop" in text or "pause" in text:
        ACTIONS["playpause"]();  return True
    return False


# ===========================================================================
#  WINDOWS STARTUP REGISTRATION
# ===========================================================================

def add_to_startup():
    """Register this program to auto-start with Windows (current user)."""
    try:
        import winreg
    except ImportError:
        return
    if getattr(sys, "frozen", False):
        run_command = f'"{sys.executable}"'
    else:
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        run_command = f'"{pythonw}" "{os.path.abspath(__file__)}"'
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, run_command)
    except OSError:
        pass


# ===========================================================================
#  WI-FI WEB REMOTE
# ===========================================================================

WEB_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<!-- user-scalable=no stops double-tap zoom when tapping controls repeatedly -->
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#08080d">
<title>BOBO Remote</title>
<style>
  :root{
    --bg:#08080d; --card:rgba(255,255,255,.05); --line:rgba(255,255,255,.09);
    --txt:#f2f2f8; --dim:#9494ae; --accent:#8b5cf6; --accent2:#c084fc;
    --ok:#4ade80; --warn:#ff8b8b;
  }
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  /* Page scrolls normally; margin:auto centres it WITHOUT clipping when tall */
  body{margin:0;min-height:100dvh;display:flex;justify-content:center;
    padding:18px;color:var(--txt);
    font-family:'Segoe UI Variable Display','Segoe UI',system-ui,-apple-system,sans-serif;
    background:
      radial-gradient(900px 600px at 15% -5%,#2a1a5e 0%,transparent 60%),
      radial-gradient(700px 500px at 95% 105%,#4a1d5c 0%,transparent 55%),
      var(--bg);background-attachment:fixed;}
  .wrap{width:min(440px,100%);margin:auto 0;display:flex;flex-direction:column;gap:14px}
  button,label.sw,input{font-family:inherit}
  button,.tap{touch-action:manipulation;-webkit-user-select:none;user-select:none}
  :focus-visible{outline:2.5px solid var(--accent2);outline-offset:3px;border-radius:12px}

  /* ---------- header ---------- */
  .top{display:flex;align-items:center;gap:13px}
  .orb{width:42px;height:42px;border-radius:50%;flex:none;
    background:radial-gradient(circle at 34% 29%,#fff 0%,#d9b8ff 15%,#a06cf5 45%,#5b21b6 75%,#2a0f5e 100%);
    box-shadow:0 0 22px rgba(150,90,255,.6),inset 0 0 12px rgba(255,255,255,.25);
    animation:breathe 4s ease-in-out infinite}
  @keyframes breathe{0%,100%{transform:scale(1)}50%{transform:scale(1.07)}}
  .brand{flex:1;min-width:0}
  .brand h1{font-size:19px;margin:0;font-weight:650;letter-spacing:.2px}
  .brand p{font-size:12.5px;color:var(--dim);margin:2px 0 0}
  .batt{display:flex;align-items:center;gap:7px;padding:8px 13px;border-radius:999px;
    background:var(--card);border:1px solid var(--line);font-size:13px;
    font-variant-numeric:tabular-nums;white-space:nowrap}
  .batt.low{color:var(--warn);border-color:rgba(255,120,120,.32)}
  .batt.chg{color:var(--ok);border-color:rgba(110,230,160,.30)}

  .card{background:var(--card);border:1px solid var(--line);border-radius:24px;
    padding:18px;backdrop-filter:blur(16px);box-shadow:0 20px 50px rgba(0,0,0,.45)}

  /* ---------- now playing ---------- */
  .np{display:flex;align-items:center;gap:13px;margin-bottom:16px;min-width:0}
  .art{width:50px;height:50px;border-radius:14px;flex:none;display:grid;place-items:center;
    background:linear-gradient(140deg,#3b1d78,#7c3aed);color:#e9d5ff}
  .nptxt{flex:1;min-width:0}
  .npttl{font-size:15px;font-weight:600;white-space:nowrap;overflow:hidden;
    text-overflow:ellipsis}
  .npart{font-size:12.5px;color:var(--dim);white-space:nowrap;overflow:hidden;
    text-overflow:ellipsis;margin-top:2px}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--ok);
    margin-right:6px;vertical-align:middle}
  .dot.pause{background:var(--dim);animation:none}
  .dot.play{animation:blink 1.6s ease-in-out infinite}
  @keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}

  .media{display:flex;align-items:center;justify-content:center;gap:22px}
  .mbtn{width:62px;height:62px;border-radius:50%;border:1px solid var(--line);
    background:rgba(255,255,255,.055);color:var(--txt);display:grid;place-items:center;
    cursor:pointer;transition:transform .12s,background .18s}
  .mbtn:active{transform:scale(.9);background:rgba(255,255,255,.13)}
  .mbtn.main{width:80px;height:80px;border:none;
    background:linear-gradient(140deg,var(--accent),var(--accent2));
    box-shadow:0 12px 30px rgba(139,92,246,.45)}
  .mbtn.main:active{box-shadow:0 6px 16px rgba(139,92,246,.35)}
  .hide{display:none}

  /* ---------- sliders ---------- */
  .row{display:flex;align-items:center;gap:9px;min-height:52px}
  .row + .row{margin-top:8px}
  .ico{width:42px;height:42px;border-radius:13px;display:grid;place-items:center;
    background:rgba(255,255,255,.06);color:var(--dim);flex:none;border:none;
    cursor:default;padding:0}
  button.ico{cursor:pointer;color:var(--txt);transition:.15s}
  button.ico:active{transform:scale(.9);background:rgba(255,255,255,.14)}
  button.ico.on{color:var(--warn);background:rgba(255,120,120,.16)}
  .stp{width:38px;height:42px;border-radius:12px;border:1px solid var(--line);
    background:rgba(255,255,255,.05);color:var(--txt);font-size:19px;font-weight:600;
    cursor:pointer;flex:none;display:grid;place-items:center;transition:.14s;padding:0}
  .stp:active{transform:scale(.9);background:rgba(255,255,255,.14)}
  .val{width:46px;text-align:right;font-size:14px;color:var(--dim);
    font-variant-numeric:tabular-nums;flex:none}
  input[type=range]{-webkit-appearance:none;appearance:none;flex:1;min-width:0;height:44px;
    background:transparent;cursor:pointer;touch-action:none;margin:0}
  input[type=range]::-webkit-slider-runnable-track{height:10px;border-radius:99px;
    background:linear-gradient(90deg,var(--accent) var(--p,50%),rgba(255,255,255,.13) var(--p,50%))}
  input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:26px;height:26px;
    border-radius:50%;background:#fff;margin-top:-8px;box-shadow:0 2px 10px rgba(0,0,0,.55)}
  input[type=range]::-moz-range-track{height:10px;border-radius:99px;background:rgba(255,255,255,.13)}
  input[type=range]::-moz-range-progress{height:10px;border-radius:99px;background:var(--accent)}
  input[type=range]::-moz-range-thumb{width:26px;height:26px;border:none;border-radius:50%;
    background:#fff;box-shadow:0 2px 10px rgba(0,0,0,.55)}
  input[type=range]:disabled{opacity:.35;cursor:not-allowed}

  /* ---------- clipboard ---------- */
  .chead{display:flex;align-items:center;gap:12px;margin-bottom:14px}
  .ctitle{flex:1;font-size:15px;font-weight:600;line-height:1.25}
  .ctitle span{display:block;font-size:11.5px;color:var(--dim);font-weight:400;margin-top:2px}
  textarea{width:100%;min-height:96px;resize:vertical;padding:14px;border-radius:16px;
    border:1px solid var(--line);background:rgba(0,0,0,.28);color:var(--txt);
    font-size:15px;line-height:1.5;outline:none;transition:border-color .15s}
  textarea:focus{border-color:var(--accent)}
  textarea::placeholder{color:#6c6c86}
  .crow{display:flex;align-items:center;gap:10px;margin-top:11px}
  .count{font-size:12px;color:var(--dim);flex:1;font-variant-numeric:tabular-nums}
  .btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;
    padding:13px 18px;border-radius:15px;border:1px solid var(--line);
    background:rgba(255,255,255,.05);color:var(--txt);font-size:14.5px;font-weight:600;
    cursor:pointer;transition:.15s}
  .btn:active{transform:scale(.97)}
  .btn.pri{border:none;background:linear-gradient(135deg,var(--accent),var(--accent2));
    box-shadow:0 8px 22px rgba(139,92,246,.4)}
  .btn:disabled{opacity:.45;cursor:not-allowed;transform:none}

  /* ---------- timers ---------- */
  .pills{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}
  .pill{padding:10px 14px;border-radius:13px;border:1px solid var(--line);
    background:rgba(255,255,255,.05);color:var(--dim);font-size:13.5px;
    font-weight:600;cursor:pointer;transition:.14s;min-height:42px}
  .pill:active{transform:scale(.94)}
  .pill.on{color:#fff;border-color:transparent;
    background:linear-gradient(135deg,var(--accent),var(--accent2));
    box-shadow:0 6px 16px rgba(139,92,246,.35)}
  .pill.min{flex:1;min-width:56px;text-align:center}
  .wideBtn{width:100%;margin-top:4px}
  .tlist{list-style:none;margin:14px 0 0;padding:0;display:flex;
    flex-direction:column;gap:9px}
  .tlist:empty{margin:0}
  .titem{display:flex;align-items:center;gap:11px;padding:12px 14px;
    border-radius:15px;background:rgba(0,0,0,.26);border:1px solid var(--line)}
  .tname{flex:1;font-size:14px;font-weight:600;min-width:0}
  .tleft{font-size:15px;color:var(--accent2);font-variant-numeric:tabular-nums;
    font-weight:650}
  .tx{width:34px;height:34px;border-radius:11px;border:1px solid var(--line);
    background:rgba(255,255,255,.05);color:var(--dim);display:grid;
    place-items:center;cursor:pointer;flex:none;font-size:17px;line-height:1}
  .tx:active{transform:scale(.9);color:var(--warn)}

  /* ---------- live screen ---------- */
  .sw{position:relative;width:52px;height:31px;flex:none;cursor:pointer}
  .sw input{position:absolute;opacity:0;width:100%;height:100%;margin:0;cursor:pointer}
  .track{position:absolute;inset:0;border-radius:99px;background:rgba(255,255,255,.14);
    transition:.22s;pointer-events:none}
  .knob{position:absolute;top:3px;left:3px;width:25px;height:25px;border-radius:50%;
    background:#fff;transition:.22s;box-shadow:0 2px 7px rgba(0,0,0,.45)}
  .sw input:checked ~ .track{background:linear-gradient(135deg,var(--accent),var(--accent2))}
  .sw input:checked ~ .track .knob{transform:translateX(21px)}
  .screen{position:relative;aspect-ratio:16/9;border-radius:16px;overflow:hidden;
    background:#0a0a10;border:1px solid var(--line);display:grid;place-items:center}
  .screen img{width:100%;height:100%;object-fit:contain;display:block;opacity:0;
    transition:opacity .25s}
  .screen img.on{opacity:1}
  .ph{position:absolute;inset:0;display:grid;place-items:center;text-align:center;
    color:var(--dim);font-size:13px;padding:16px;gap:10px}
  .exp{position:absolute;right:10px;bottom:10px;width:42px;height:42px;border-radius:13px;
    border:1px solid var(--line);background:rgba(10,10,16,.72);color:var(--txt);
    display:none;place-items:center;cursor:pointer;backdrop-filter:blur(8px)}
  .exp.show{display:grid}
  .exp:active{transform:scale(.92)}
  .screen:fullscreen{aspect-ratio:auto;width:100vw;height:100vh;border:none;
    border-radius:0;background:#000}

  .wide{width:100%;display:flex;align-items:center;justify-content:center;gap:10px;
    padding:17px;border-radius:20px;border:1px solid var(--line);
    background:rgba(255,255,255,.045);color:var(--txt);font-size:15px;font-weight:600;
    cursor:pointer;transition:.15s}
  .wide:active{transform:scale(.98);background:rgba(255,255,255,.11)}

  #toast{position:fixed;left:50%;bottom:26px;transform:translate(-50%,20px);
    background:rgba(18,18,26,.97);border:1px solid var(--line);padding:12px 20px;
    border-radius:14px;font-size:14px;opacity:0;transition:.22s;pointer-events:none;
    max-width:calc(100% - 40px);text-align:center;z-index:9}
  #toast.on{opacity:1;transform:translate(-50%,0)}
  .sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
    white-space:nowrap}

  @media (prefers-reduced-motion:reduce){
    *{animation:none!important;transition:none!important}
  }
</style></head><body>
<div class="wrap">

  <header class="top">
    <div class="orb" aria-hidden="true"></div>
    <div class="brand"><h1>BOBO</h1><p>Wi-Fi Remote</p></div>
    <div class="batt" id="batt" role="status" aria-live="polite" aria-label="Battery">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="2" y="7" width="16" height="10" rx="2.5"/>
        <line x1="21" y1="10.5" x2="21" y2="13.5"/>
        <rect id="bfill" x="4" y="9" width="9" height="6" rx="1" fill="currentColor" stroke="none"/>
      </svg>
      <span id="bt">--</span>
    </div>
  </header>

  <!-- ---------------- now playing ---------------- -->
  <section class="card" aria-label="Playback">
    <div class="np">
      <div class="art" aria-hidden="true">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
      </div>
      <div class="nptxt">
        <div class="npttl" id="npTitle" aria-live="polite">Nothing playing</div>
        <div class="npart" id="npArtist"><span class="dot pause" id="npDot"></span>Paused</div>
      </div>
    </div>
    <div class="media">
      <button class="mbtn" onclick="go('prev')" aria-label="Previous track">
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polygon points="19 20 9 12 19 4" fill="currentColor"/>
          <line x1="5" y1="19" x2="5" y2="5"/></svg>
      </button>
      <button class="mbtn main" id="ppBtn" onclick="go('playpause')" aria-label="Play">
        <svg id="icPlay" width="32" height="32" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true">
          <polygon points="7 4 20 12 7 20" fill="currentColor"/></svg>
        <svg id="icPause" class="hide" width="32" height="32" viewBox="0 0 24 24"
             fill="currentColor" aria-hidden="true">
          <rect x="6" y="4" width="4.5" height="16" rx="1.6"/>
          <rect x="13.5" y="4" width="4.5" height="16" rx="1.6"/></svg>
      </button>
      <button class="mbtn" onclick="go('next')" aria-label="Next track">
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polygon points="5 4 15 12 5 20" fill="currentColor"/>
          <line x1="19" y1="5" x2="19" y2="19"/></svg>
      </button>
    </div>
  </section>

  <!-- ---------------- volume + brightness ---------------- -->
  <section class="card" aria-label="Volume and brightness">
    <div class="row">
      <button class="ico" id="muteBtn" onclick="go('mute',1)" aria-label="Mute or unmute">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19" fill="currentColor"/>
          <path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/></svg>
      </button>
      <button class="stp" onclick="step('vol',-5)" aria-label="Volume down">&minus;</button>
      <input type="range" id="vol" min="0" max="100" step="1" value="50" aria-label="Volume">
      <button class="stp" onclick="step('vol',5)" aria-label="Volume up">+</button>
      <div class="val" id="volv">--</div>
    </div>
    <div class="row">
      <div class="ico" aria-hidden="true">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="4.2" fill="currentColor" stroke="none"/>
          <line x1="12" y1="1.5" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="22.5"/>
          <line x1="1.5" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="22.5" y2="12"/>
          <line x1="4.6" y1="4.6" x2="6.4" y2="6.4"/><line x1="17.6" y1="17.6" x2="19.4" y2="19.4"/>
          <line x1="4.6" y1="19.4" x2="6.4" y2="17.6"/><line x1="17.6" y1="6.4" x2="19.4" y2="4.6"/></svg>
      </div>
      <button class="stp" onclick="step('bri',-5)" aria-label="Brightness down">&minus;</button>
      <input type="range" id="bri" min="5" max="100" step="1" value="50" aria-label="Brightness">
      <button class="stp" onclick="step('bri',5)" aria-label="Brightness up">+</button>
      <div class="val" id="briv">--</div>
    </div>
  </section>

  <!-- ---------------- clipboard ---------------- -->
  <section class="card" aria-label="Send text to PC clipboard">
    <div class="chead">
      <div class="ico" aria-hidden="true">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="8" y="3" width="8" height="4" rx="1.4"/>
          <path d="M16 5h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2"/>
          <line x1="8.5" y1="12" x2="15.5" y2="12"/><line x1="8.5" y1="16" x2="13" y2="16"/></svg>
      </div>
      <div class="ctitle"><label for="clip">Send to PC clipboard</label>
        <span>Type here, then paste on the laptop with Ctrl+V</span></div>
    </div>
    <textarea id="clip" placeholder="Type or paste text here…" spellcheck="false"
              autocomplete="off"></textarea>
    <div class="crow">
      <div class="count" id="clipCount">0 characters</div>
      <button class="btn" id="clipClear" onclick="clipClear()">Clear</button>
      <button class="btn pri" id="clipSend" onclick="clipSend()">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        Send
      </button>
    </div>
  </section>

  <!-- ---------------- timers ---------------- -->
  <section class="card" aria-label="Scheduled timers">
    <div class="chead">
      <div class="ico" aria-hidden="true">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="13" r="8.5"/><polyline points="12 8.5 12 13 15 14.5"/>
          <line x1="9" y1="2" x2="15" y2="2"/></svg>
      </div>
      <div class="ctitle">Timers<span>Do something after a delay</span></div>
    </div>

    <div class="pills" role="group" aria-label="What should happen">
      <button class="pill on" data-act="pause">Pause music</button>
      <button class="pill" data-act="screenoff">Screen off</button>
      <button class="pill" data-act="mute">Mute</button>
    </div>
    <div class="pills" role="group" aria-label="After how long">
      <button class="pill min on" data-min="5">5m</button>
      <button class="pill min" data-min="10">10m</button>
      <button class="pill min" data-min="15">15m</button>
      <button class="pill min" data-min="20">20m</button>
      <button class="pill min" data-min="30">30m</button>
      <button class="pill min" data-min="45">45m</button>
      <button class="pill min" data-min="60">60m</button>
    </div>
    <button class="btn pri wideBtn" id="tAdd">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      <span id="tAddLbl">Add timer</span>
    </button>
    <ul class="tlist" id="tList"></ul>
  </section>

  <!-- ---------------- live screen ---------------- -->
  <section class="card" id="screenCard" aria-label="Live screen">
    <div class="chead">
      <div class="ico" aria-hidden="true">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="2" y="3.5" width="20" height="14" rx="2.5"/>
          <line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17.5" x2="12" y2="21"/></svg>
      </div>
      <div class="ctitle">Live Screen<span id="shint">Preview is off</span></div>
      <label class="sw"><input type="checkbox" id="scrTog" aria-label="Show live screen">
        <span class="track"><span class="knob"></span></span></label>
    </div>
    <div class="screen" id="scrWrap">
      <img id="scr" alt="Live preview of the laptop screen">
      <div class="ph" id="scrPh">
        <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="2" y="3.5" width="20" height="14" rx="2.5"/>
          <line x1="8" y1="21" x2="16" y2="21"/></svg>
        <div>Turn on to see your laptop screen</div>
      </div>
      <button class="exp" id="expBtn" aria-label="Fullscreen">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>
          <line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>
      </button>
    </div>
  </section>

  <button class="wide" onclick="go('screenoff')">
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
    Turn Screen Off
  </button>
</div>
<div id="toast" role="status" aria-live="polite"></div>

<script>
/* Auth is a signed HttpOnly cookie — nothing secret ever goes in a URL. */
const q = s => document.querySelector(s);
const url = p => p;
let toastTimer, dragging = null, dragUntil = 0;

function toast(msg){
  const t = q('#toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('on'), 1400);
}
async function go(cmd, quiet){
  try{
    const r = await fetch(url('/api/' + cmd), {method:'POST'});
    if(r.status === 401) return location.replace('/');
    if(!r.ok) return toast('Failed (' + r.status + ')');
    if(!quiet) toast('Done');
    setTimeout(refresh, 250);           // reflect the new play/pause state
  }catch(e){ toast('No connection'); }
}

/* ---------- sliders ---------- */
function paint(el, v){ el.style.setProperty('--p', v + '%'); }
function throttle(fn, ms){
  let last = 0, pending = null, timer = null;
  return v => {
    pending = v; const now = Date.now();
    if(now - last >= ms){ last = now; fn(pending); }
    else if(!timer){ timer = setTimeout(() => { timer = null; last = Date.now();
                                                fn(pending); }, ms - (now - last)); }
  };
}
const sliders = {};
function wire(key, id, api, valId){
  const el = q(id), lab = q(valId);
  const send = throttle(v => fetch(url(api + '?v=' + v), {method:'POST'}).catch(()=>{}));
  function apply(v, push){
    el.value = v; paint(el, v); lab.textContent = v + '%';
    el.setAttribute('aria-valuetext', v + ' percent');
    if(push){ dragging = key; dragUntil = Date.now() + 1500; send(v); }
  }
  el.addEventListener('input', () => apply(+el.value, true));
  sliders[key] = {el, apply};
  return sliders[key];
}
const vol = wire('vol', '#vol', '/api/volume', '#volv');
const bri = wire('bri', '#bri', '/api/brightness', '#briv');
function step(key, delta){
  const s = sliders[key], el = s.el;
  s.apply(Math.max(+el.min, Math.min(+el.max, (+el.value) + delta)), true);
}

/* ---------- clipboard ---------- */
const clip = q('#clip');
function updCount(){
  const n = clip.value.length;
  q('#clipCount').textContent = n + (n === 1 ? ' character' : ' characters');
  q('#clipSend').disabled = n === 0;
}
clip.addEventListener('input', updCount);
function clipClear(){ clip.value = ''; updCount(); clip.focus(); }
async function clipSend(){
  const text = clip.value;
  if(!text) return;
  const btn = q('#clipSend'); btn.disabled = true;
  try{
    const r = await fetch(url('/api/clipboard'), {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text})
    });
    const j = await r.json().catch(()=>({}));
    toast(r.ok ? ('Copied to PC · ' + (j.chars ?? text.length) + ' chars')
               : ('Failed: ' + (j.error || r.status)));
  }catch(e){ toast('No connection'); }
  btn.disabled = false; updCount();
}
updCount();

/* ---------- timers ---------- */
let tAction = 'pause', tMins = 5, timers = [];
function pickPill(group, el, set){
  document.querySelectorAll(group).forEach(b => b.classList.toggle('on', b === el));
  set();
}
document.querySelectorAll('.pill[data-act]').forEach(b =>
  b.addEventListener('click', () =>
    pickPill('.pill[data-act]', b, () => tAction = b.dataset.act)));
document.querySelectorAll('.pill[data-min]').forEach(b =>
  b.addEventListener('click', () =>
    pickPill('.pill[data-min]', b, () => tMins = +b.dataset.min)));

function mmss(s){
  s = Math.max(0, s);
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60;
  return (h ? h + ':' + String(m).padStart(2,'0') : m) + ':' + String(x).padStart(2,'0');
}
function drawTimers(){
  const ul = q('#tList');
  if(!timers.length){ ul.innerHTML = ''; return; }
  ul.innerHTML = timers.map(t =>
    '<li class="titem"><span class="tname">' + t.label + '</span>' +
    '<span class="tleft">' + mmss(t.remaining) + '</span>' +
    '<button class="tx" data-id="' + t.id + '" aria-label="Cancel ' + t.label +
    ' timer">&times;</button></li>').join('');
  ul.querySelectorAll('.tx').forEach(b =>
    b.addEventListener('click', () => cancelTimer(+b.dataset.id)));
}
q('#tAdd').addEventListener('click', async () => {
  try{
    const r = await fetch(url('/api/timers'), {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({action: tAction, minutes: tMins})});
    if(r.status === 401) return location.replace('/');
    const j = await r.json();
    if(!r.ok) return toast(j.error || 'Could not add timer');
    toast(j.timer.label + ' in ' + tMins + ' min');
    refresh();
  }catch(e){ toast('No connection'); }
});
async function cancelTimer(id){
  try{
    await fetch(url('/api/timers/cancel'), {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
    timers = timers.filter(t => t.id !== id); drawTimers(); toast('Timer cancelled');
  }catch(e){ toast('No connection'); }
}
// tick the countdown locally so it moves every second without polling
setInterval(() => {
  if(!timers.length) return;
  let fired = false;
  timers.forEach(t => { if(t.remaining > 0) t.remaining--; else fired = true; });
  drawTimers();
  if(fired) refresh();
}, 1000);

/* ---------- live screen ---------- */
const SCREEN_MS = __SCREEN_MS__;
const scrImg = q('#scr'), scrTog = q('#scrTog'), scrHint = q('#shint');
let scrOn = false, scrTimer = null;
function scrStop(){
  clearTimeout(scrTimer); scrTimer = null;
  scrImg.classList.remove('on'); q('#scrPh').style.display = 'grid';
  q('#expBtn').classList.remove('show'); scrHint.textContent = 'Preview is off';
}
function scrTick(){
  if(!scrOn) return;
  if(document.hidden){ scrHint.textContent = 'Paused'; return; }  // resumes on focus
  const im = new Image();
  im.onload = () => {                     // swap only once loaded (no flicker)
    scrImg.src = im.src; scrImg.classList.add('on');
    q('#scrPh').style.display = 'none'; q('#expBtn').classList.add('show');
    scrHint.innerHTML = '<span class="dot play"></span>Live from your laptop';
    scrTimer = setTimeout(scrTick, SCREEN_MS);
  };
  im.onerror = () => { scrHint.textContent = 'Preview unavailable';
                       scrTimer = setTimeout(scrTick, 2500); };
  im.src = url('/api/screen.jpg?_=' + Date.now());
}
scrTog.addEventListener('change', () => {
  scrOn = scrTog.checked;
  if(scrOn){ scrHint.textContent = 'Connecting…'; scrTick(); } else scrStop();
});
document.addEventListener('visibilitychange', () => {
  if(document.hidden){ clearTimeout(scrTimer); scrTimer = null; }
  else if(scrOn && !scrTimer) scrTick();
});
q('#expBtn').addEventListener('click', () => {
  const el = q('#scrWrap');
  if(document.fullscreenElement) document.exitFullscreen();
  else (el.requestFullscreen || el.webkitRequestFullscreen || (()=>{})).call(el);
});

/* ---------- status polling ---------- */
async function refresh(){
  try{
    const res = await fetch(url('/api/status'));
    if(res.status === 401) return location.replace('/');
    const s = await res.json();
    const free = Date.now() > dragUntil;   // don't fight the user mid-drag

    if(s.screen === false) q('#screenCard').style.display = 'none';
    if(s.timers){ timers = s.timers; drawTimers(); }

    if(s.volume !== null && (free || dragging !== 'vol')) vol.apply(s.volume, false);
    q('#muteBtn').classList.toggle('on', !!s.muted);
    q('#muteBtn').setAttribute('aria-pressed', !!s.muted);

    if(s.brightness === null){
      bri.el.disabled = true; q('#briv').textContent = 'n/a';
    }else if(free || dragging !== 'bri') bri.apply(s.brightness, false);

    const m = s.media, playing = !!(m && m.playing);
    q('#icPlay').classList.toggle('hide', playing);
    q('#icPause').classList.toggle('hide', !playing);
    q('#ppBtn').setAttribute('aria-label', playing ? 'Pause' : 'Play');
    q('#npTitle').textContent = m && m.title ? m.title : 'Nothing playing';
    q('#npDot').className = 'dot ' + (playing ? 'play' : 'pause');
    q('#npArtist').innerHTML = '<span class="dot ' + (playing ? 'play' : 'pause') +
      '" id="npDot"></span>' +
      (m ? ((m.artist ? m.artist + ' · ' : '') + (playing ? 'Playing' : 'Paused'))
         : 'Nothing playing');

    const b = s.battery, box = q('#batt');
    if(b){
      q('#bt').textContent = b.percent + '%' + (b.plugged ? ' · Charging' : '');
      box.setAttribute('aria-label',
        'Battery ' + b.percent + ' percent' + (b.plugged ? ', charging' : ''));
      q('#bfill').setAttribute('width', Math.max(1, Math.round(b.percent / 100 * 12)));
      box.classList.toggle('chg', b.plugged);
      box.classList.toggle('low', !b.plugged && b.percent <= 20);
    }else box.style.display = 'none';
  }catch(e){}
}
refresh(); setInterval(refresh, 4000);
</script></body></html>
"""


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#08080d">
<title>BOBO Remote — Sign in</title>
<style>
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{margin:0;min-height:100dvh;display:flex;align-items:center;justify-content:center;
    padding:22px;color:#f2f2f8;
    font-family:'Segoe UI Variable Display','Segoe UI',system-ui,-apple-system,sans-serif;
    background:radial-gradient(900px 600px at 15% -5%,#2a1a5e 0%,transparent 60%),
      radial-gradient(700px 500px at 95% 105%,#4a1d5c 0%,transparent 55%),#08080d}
  .card{width:min(380px,100%);background:rgba(255,255,255,.05);
    border:1px solid rgba(255,255,255,.09);border-radius:26px;padding:30px 24px;
    backdrop-filter:blur(16px);box-shadow:0 24px 60px rgba(0,0,0,.5);text-align:center}
  .orb{width:60px;height:60px;border-radius:50%;margin:0 auto 18px;
    background:radial-gradient(circle at 34% 29%,#fff 0%,#d9b8ff 15%,#a06cf5 45%,#5b21b6 75%,#2a0f5e 100%);
    box-shadow:0 0 26px rgba(150,90,255,.6),inset 0 0 12px rgba(255,255,255,.25)}
  h1{font-size:21px;margin:0 0 4px;font-weight:650}
  p{font-size:13px;color:#9494ae;margin:0 0 22px}
  input{width:100%;padding:15px 16px;border-radius:15px;font-size:16px;
    border:1px solid rgba(255,255,255,.11);background:rgba(0,0,0,.3);color:#f2f2f8;
    outline:none;transition:border-color .15s;text-align:center}
  input:focus{border-color:#8b5cf6}
  button{width:100%;margin-top:12px;padding:15px;border-radius:15px;border:none;
    background:linear-gradient(135deg,#8b5cf6,#c084fc);color:#fff;font-size:15.5px;
    font-weight:650;cursor:pointer;font-family:inherit;
    box-shadow:0 10px 26px rgba(139,92,246,.4);transition:.15s}
  button:active{transform:scale(.98)}
  button:disabled{opacity:.5}
  .err{margin-top:14px;font-size:13.5px;color:#ff8b8b;min-height:19px}
  .note{margin-top:18px;font-size:11.5px;color:#71718c;line-height:1.5}
</style></head><body>
<form class="card" id="f" autocomplete="on">
  <div class="orb" aria-hidden="true"></div>
  <h1>BOBO Remote</h1>
  <p>Enter the password to continue</p>
  <input id="pw" type="password" name="password" placeholder="Password"
         autocomplete="current-password" aria-label="Password" required>
  <button type="submit" id="go">Unlock</button>
  <div class="err" id="err" role="alert" aria-live="assertive"></div>
  <div class="note">This device stays signed in for __DAYS__ days.</div>
</form>
<script>
const f=document.getElementById('f'), pw=document.getElementById('pw'),
      err=document.getElementById('err'), go=document.getElementById('go');
f.addEventListener('submit', async e => {
  e.preventDefault(); err.textContent=''; go.disabled=true; go.textContent='Checking…';
  try{
    const r = await fetch('/api/login', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({password: pw.value})});
    if(r.ok){ location.replace('/'); return; }
    err.textContent = r.status===429 ? 'Too many attempts. Wait a moment.'
                                     : 'Wrong password.';
    pw.value=''; pw.focus();
  }catch(e){ err.textContent='Connection failed.'; }
  go.disabled=false; go.textContent='Unlock';
});
pw.focus();
</script></body></html>
"""


def load_web_password():
    """Password from WEB_PASSWORD, else from web_password.txt next to app.py."""
    if WEB_PASSWORD:
        return WEB_PASSWORD
    path = os.path.join(BASE_DIR, "web_password.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _session_secret():
    """
    Long-lived random key kept in .bobo_session_key so that logins survive an
    app restart. Created once, never shown to the user.
    """
    path = os.path.join(BASE_DIR, ".bobo_session_key")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                key = f.read().strip()
                if key:
                    return key
        key = secrets.token_hex(32)
        with open(path, "w", encoding="utf-8") as f:
            f.write(key)
        return key
    except OSError:
        return "bobo-fallback-key"


def session_cookie_value():
    """
    The value a signed-in browser must present. It is derived from the secret
    and the password, so changing the password signs everyone out.
    """
    password = load_web_password()
    return hmac.new(_session_secret().encode(),
                    ("bobo-remote:" + password).encode(),
                    hashlib.sha256).hexdigest()


# Simple brute-force brake: (failures, blocked_until) per client IP.
_login_fails = {}
_login_lock = threading.Lock()


def login_blocked(ip):
    with _login_lock:
        fails, until = _login_fails.get(ip, (0, 0.0))
        return time.time() < until


def note_login_result(ip, success):
    with _login_lock:
        if success:
            _login_fails.pop(ip, None)
            return
        fails, _ = _login_fails.get(ip, (0, 0.0))
        fails += 1
        # after 5 bad tries, lock that IP out for 30 seconds
        until = time.time() + 30 if fails >= 5 else 0.0
        _login_fails[ip] = (0 if until else fails, until)


class RemoteHandler(BaseHTTPRequestHandler):
    """Serves the remote page and executes /api/<action> requests."""

    server_version = "BOBO/1.0"

    def log_message(self, *args):
        pass                      # stay silent (this is a background app)

    def _authorized(self):
        """Signed in? (cookie only — never a password in the URL)"""
        if not load_web_password():
            return True                      # no password configured = open
        from http.cookies import SimpleCookie
        raw = self.headers.get("Cookie")
        if not raw:
            return False
        try:
            got = SimpleCookie(raw).get("bobo")
        except Exception:
            return False
        return bool(got) and hmac.compare_digest(got.value,
                                                 session_cookie_value())

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self._send_bytes(code, body.encode("utf-8"), ctype)

    def _send_bytes(self, code, data, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass        # phone navigated away mid-frame; harmless

    def _query(self, name):
        from urllib.parse import urlparse, parse_qs
        return parse_qs(urlparse(self.path).query).get(name, [None])[0]

    def do_GET(self):
        path = self.path.split("?")[0]
        if not self._authorized():
            # Show the sign-in page for normal navigation; JSON for API calls.
            if path in ("/", "/index.html", "/login"):
                page = LOGIN_PAGE.replace("__DAYS__", str(WEB_SESSION_DAYS))
                return self._send(200, page, "text/html; charset=utf-8")
            return self._send(401, '{"error":"not signed in"}')
        if path in ("/", "/index.html"):
            page = WEB_PAGE.replace("__SCREEN_MS__", str(SCREEN_REFRESH_MS))
            return self._send(200, page, "text/html; charset=utf-8")
        if path == "/api/status":
            return self._send(200, json.dumps(get_status()))
        if path == "/api/screen.jpg":
            if not SCREEN_VIEW:
                return self._send(403, '{"error":"screen view disabled"}')
            try:
                return self._send_bytes(200, capture_screen_jpeg(), "image/jpeg")
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}))
        self._send(404, '{"error":"not found"}')

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            return None
        return json.loads(self.rfile.read(length).decode("utf-8", "replace"))

    def do_POST(self):
        path = self.path.split("?")[0]
        if not path.startswith("/api/"):
            return self._send(404, '{"error":"not found"}')
        action = path[len("/api/"):]

        # ---- sign in (the only endpoint reachable while logged out) --------
        if action == "login":
            ip = self.client_address[0]
            if login_blocked(ip):
                return self._send(429, '{"error":"too many attempts"}')
            expected = load_web_password()
            try:
                given = (self._read_json() or {}).get("password", "")
            except Exception:
                given = ""
            ok = bool(expected) and hmac.compare_digest(str(given), expected)
            note_login_result(ip, ok)
            if not ok:
                time.sleep(0.4)              # slow down guessing
                return self._send(401, '{"error":"wrong password"}')
            data = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header(
                "Set-Cookie",
                f"bobo={session_cookie_value()}; Max-Age={WEB_SESSION_DAYS*86400}; "
                "Path=/; HttpOnly; SameSite=Lax"
            )
            self.end_headers()
            return self.wfile.write(data)

        if action == "logout":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", "11")
            self.send_header("Set-Cookie",
                             "bobo=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
            self.end_headers()
            return self.wfile.write(b'{"ok":true}')

        if not self._authorized():
            return self._send(401, '{"error":"unauthorized"}')

        try:
            # Clipboard text arrives in the BODY (not the URL) so it is not
            # logged or length-limited.
            if action == "clipboard":
                text = (self._read_json() or {}).get("text", "")
                if not text:
                    return self._send(400, '{"error":"no text"}')
                set_clipboard(text)
                return self._send(200, json.dumps({"ok": True,
                                                   "chars": len(text)}))

            # ---- scheduled timers ----------------------------------------
            if action == "timers":
                body = self._read_json() or {}
                minutes = float(body.get("minutes", 0))
                timer = scheduler().add(body.get("action", ""),
                                        round(minutes * 60))
                return self._send(200, json.dumps({"ok": True, "timer": {
                    "id": timer["id"], "label": timer["label"],
                    "remaining": timer["total"]}}))
            if action == "timers/cancel":
                body = self._read_json() or {}
                ok = scheduler().cancel(body.get("id", -1))
                return self._send(200 if ok else 404,
                                  json.dumps({"ok": ok}))
            if action == "timers/clear":
                return self._send(200, json.dumps(
                    {"ok": True, "cancelled": scheduler().clear()}))

            # Sliders send an absolute 0..100 value in ?v=
            if action in ("volume", "brightness"):
                raw = self._query("v")
                if raw is None:
                    return self._send(400, '{"error":"missing v"}')
                value = max(0, min(100, int(float(raw))))
                (set_volume if action == "volume" else set_brightness)(value)
                return self._send(200, json.dumps({"ok": True, "action": action,
                                                   "value": value}))
            fn = ACTIONS.get(action)
            if fn is None:
                return self._send(400, '{"error":"unknown action"}')
            fn()
            self._send(200, json.dumps({"ok": True, "action": action}))
        except ValueError:
            self._send(400, '{"error":"bad value"}')
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "error": str(e)}))


def lan_ip():
    """Best-effort local network IP (no packets are actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def start_web_server():
    """Run the remote-control web server on its own daemon thread."""
    try:
        httpd = ThreadingHTTPServer((WEB_BIND, WEB_PORT), RemoteHandler)
    except OSError as e:
        print(f"Web remote could not start on port {WEB_PORT}: {e}")
        return None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"Web remote ready:  http://{lan_ip()}:{WEB_PORT}/")
    if load_web_password():
        print("  Password protected — you'll sign in once per device.")
    else:
        print("  WARNING: no password set. Anyone on this network can open it.")
        print("  Create 'web_password.txt' next to app.py to protect it.")
    return httpd


# ===========================================================================
#  VOLUME DUCKER
# ===========================================================================

class Ducker:
    """
    Lowers the master volume while BOBO listens, then restores it.
    Uses the shared COM worker, so every COM call in the app happens on the
    same thread.
    """

    def __init__(self):
        self._saved = None

    def duck(self, level_percent):
        try:
            current = get_volume()
            # Only ever lower it: if you're already quieter than the duck
            # level, leave the volume alone.
            if current is None or current <= level_percent:
                return
            self._saved = current
            set_volume(level_percent)
        except Exception:
            self._saved = None

    def restore(self):
        if self._saved is None:
            return
        try:
            set_volume(self._saved)
        except Exception:
            pass
        self._saved = None


# ===========================================================================
#  THE OVERLAY (tkinter GUI — main thread)
# ===========================================================================

class Overlay:
    """
    Frameless, transparent, always-on-top dark "glass" pill holding a smooth,
    glossy 3-D orb that breathes and drifts blue → purple → pink.

    Frames are rendered ONCE with Pillow (smooth gradients, no tkinter
    banding) at startup and then simply cycled, so it looks premium while
    costing almost no CPU.
    """

    TRANSPARENT_KEY = "#010203"
    W, H = 264, 72
    ORB_CX = 40
    ORB_R = 16
    FRAMES = 40                # animation frames held in RAM (~3 MB)

    def __init__(self, root):
        from PIL import ImageTk
        self.root = root
        self._visible = False
        self._idx = 0

        root.overrideredirect(True)
        root.wm_attributes("-topmost", True)
        root.config(bg=self.TRANSPARENT_KEY)
        root.wm_attributes("-transparentcolor", self.TRANSPARENT_KEY)

        self.canvas = tk.Canvas(root, width=self.W, height=self.H,
                                bg=self.TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack()

        self._frames = [ImageTk.PhotoImage(self._render(k / self.FRAMES))
                        for k in range(self.FRAMES)]
        self._img_item = self.canvas.create_image(self.W // 2, self.H // 2,
                                                   image=self._frames[0])
        self._center_top()
        root.withdraw()

    def _center_top(self):
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - self.W) // 2
        self.root.geometry(f"{self.W}x{self.H}+{x}+14")

    @staticmethod
    def _rgb(h, s, v):
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
        return (int(r * 255), int(g * 255), int(b * 255))

    def _orb_sphere(self, radius, hue):
        """Smooth shaded 3-D sphere with a glossy highlight (RGBA image)."""
        from PIL import Image
        size = int(radius * 2 + 8)
        yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
        cx = cy = size / 2.0
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        lx, ly = cx - radius * 0.4, cy - radius * 0.4       # light from up-left
        ld = np.sqrt((xx - lx) ** 2 + (yy - ly) ** 2)
        b = np.clip(1.0 - ld / (radius * 1.7), 0.0, 1.0)
        val = 0.18 + 0.82 * b
        br, bg, bb = colorsys.hsv_to_rgb(hue % 1.0, 0.72, 1.0)
        r, g, bl = br * val, bg * val, bb * val
        spec = np.clip((b - 0.80) / 0.20, 0.0, 1.0) ** 1.5  # glossy dot
        r, g, bl = r + spec * (1 - r), g + spec * (1 - g), bl + spec * (1 - bl)
        alpha = np.clip(radius - dist + 1.2, 0.0, 1.0)      # soft edge
        arr = np.zeros((size, size, 4), np.uint8)
        arr[..., 0] = (r * 255).astype(np.uint8)
        arr[..., 1] = (g * 255).astype(np.uint8)
        arr[..., 2] = (bl * 255).astype(np.uint8)
        arr[..., 3] = (alpha * 255).astype(np.uint8)
        return Image.fromarray(arr, "RGBA")

    def _render(self, phase):
        """Render one animation frame (phase 0..1)."""
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
        hue = 0.60 + 0.16 * math.sin(phase * 2 * math.pi)
        breath = 1.0 + 0.12 * math.sin(phase * 2 * math.pi)
        cy = self.H // 2
        R = int(self.ORB_R * breath)

        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        rad = self.H // 2 - 3
        d.rounded_rectangle([3, 3, self.W - 4, self.H - 4], radius=rad,
                            fill=(22, 22, 30, 255))
        d.rounded_rectangle([3, 3, self.W - 4, self.H - 4], radius=rad,
                            outline=self._rgb(hue, 0.55, 1.0), width=2)

        glow = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse(
            [self.ORB_CX - R * 2, cy - R * 2, self.ORB_CX + R * 2, cy + R * 2],
            fill=self._rgb(hue, 0.85, 0.9) + (140,))
        img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(9)))

        orb = self._orb_sphere(R, hue)
        img.alpha_composite(orb,
                            (self.ORB_CX - orb.width // 2, cy - orb.height // 2))

        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 19)
        except Exception:
            font = ImageFont.load_default()
        d.text((self.ORB_CX + 34, cy), "Listening", font=font,
               fill=(238, 238, 245), anchor="lm")
        return img

    def show(self):
        if not self._visible:
            self._visible = True
            self.root.deiconify()
            self.root.wm_attributes("-topmost", True)
            self._animate()

    def hide(self):
        if self._visible:
            self._visible = False
            self.root.withdraw()

    def _animate(self):
        if not self._visible:
            return
        self._idx = (self._idx + 1) % self.FRAMES
        self.canvas.itemconfig(self._img_item, image=self._frames[self._idx])
        self.root.after(45, self._animate)      # ~22 FPS, very light


# ===========================================================================
#  AUDIO LISTENING (worker thread): wake -> duck -> command
# ===========================================================================

class Listener(threading.Thread):

    def __init__(self, gui_queue):
        super().__init__(daemon=True)
        self.gui_queue = gui_queue
        self._audio_q = queue.Queue()
        self._running = True
        self.device = self._pick_microphone()
        self.wake = None

        # Only load openWakeWord if that engine was selected.
        if WAKE_ENGINE == "oww":
            import openwakeword
            from openwakeword.model import Model as WakeModel
            openwakeword.utils.download_models()
            self.wake = WakeModel(wakeword_models=[WAKE_MODEL],
                                  inference_framework="onnx")

        if not os.path.isdir(MODEL_PATH):
            raise FileNotFoundError(
                f"Vosk model not found at '{MODEL_PATH}' (see README)."
            )
        # The Vosk model costs ~115 MB of RAM. With WAKE_ENGINE = "vosk" it is
        # needed all the time, but in "oww" mode nothing needs it until you
        # actually speak a command — so load it on demand.
        self.model = Model(MODEL_PATH) if WAKE_ENGINE == "vosk" else None

    def _get_model(self):
        if self.model is None:
            self.model = Model(MODEL_PATH)
        return self.model

    def _pick_microphone(self):
        if INPUT_DEVICE_INDEX is not None:
            return INPUT_DEVICE_INDEX
        try:
            devices = sd.query_devices()
        except Exception:
            return None
        for keyword in MIC_KEYWORDS:
            for idx, dev in enumerate(devices):
                if dev["max_input_channels"] < 1:
                    continue
                if "speaker" in dev["name"].lower():
                    continue
                if keyword.lower() in dev["name"].lower():
                    try:
                        sd.check_input_settings(device=idx,
                                                samplerate=SAMPLE_RATE,
                                                channels=1, dtype="int16")
                        return idx
                    except Exception:
                        continue
        return None

    def _audio_callback(self, indata, frames, time_info, status):
        self._audio_q.put(bytes(indata))

    @staticmethod
    def _wake_conf(result):
        """Highest confidence with which WAKE_WORD appears in a Vosk result."""
        return max((w.get("conf", 0.0) for w in result.get("result", [])
                    if w.get("word") == WAKE_WORD), default=0.0)

    @staticmethod
    def _text_and_min_conf(result):
        text = result.get("text", "")
        words = result.get("result", [])
        return text, min((w.get("conf", 0.0) for w in words), default=0.0)

    def run(self):
        ducker = Ducker()       # COM itself lives on the shared COM worker

        recognizer = None

        def get_recognizer():
            """Build the speech recognizer the first time it's actually used."""
            nonlocal recognizer
            if recognizer is None:
                recognizer = KaldiRecognizer(self._get_model(), SAMPLE_RATE,
                                             GRAMMAR)
                recognizer.SetWords(True)
            return recognizer

        if WAKE_ENGINE == "vosk":
            recognizer = get_recognizer()      # needed for the wake word too

        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=AUDIO_FRAME,
                               dtype="int16", channels=1, device=self.device,
                               callback=self._audio_callback):
            active = False
            active_deadline = 0.0
            cooldown_until = 0.0
            # Voice-activity gate state (idle mode only)
            from collections import deque
            preroll = deque(maxlen=VAD_PREROLL)
            loud_frames = 0
            gate_open = False

            while self._running:
                data = self._audio_q.get()

                if not active:
                    # ---------------- IDLE: wait for the wake word ---------
                    if manual_wake.is_set():
                        manual_wake.clear()
                        active = True
                        gate_open = False
                        loud_frames = 0
                        preroll.clear()
                        active_deadline = time.time() + COMMAND_LISTEN_SECONDS
                        ducker.duck(DUCK_LEVEL)
                        self.gui_queue.put("show")
                        get_recognizer().Reset()
                        continue
                    if time.time() < cooldown_until:
                        continue

                    woke = False
                    pcm = np.frombuffer(data, dtype=np.int16)

                    if WAKE_ENGINE == "oww":
                        # openWakeWord is tiny and keeps its own audio buffer,
                        # so it is fed every frame (no gating).
                        if len(pcm) == AUDIO_FRAME:
                            scores = self.wake.predict(pcm)
                            woke = scores.get(WAKE_MODEL, 0.0) >= WAKE_THRESHOLD
                    else:
                        # --- CPU saver: only run the speech decoder when there
                        # is actually sound. This is where nearly all the CPU
                        # goes, so skipping silence is the big win.
                        rms = float(np.sqrt(np.mean(
                            pcm.astype(np.float32) ** 2))) if pcm.size else 0.0
                        if rms >= VAD_RMS:
                            loud_frames = VAD_HANGOVER
                        elif loud_frames > 0:
                            loud_frames -= 1

                        if loud_frames == 0:
                            preroll.append(data)      # keep a short lead-in
                            if gate_open:
                                # Sound just ended: FLUSH what was said. The
                                # wake word lives in this final result, so it
                                # must be read before resetting.
                                gate_open = False
                                result = json.loads(recognizer.FinalResult())
                                woke = (self._wake_conf(result)
                                        >= WAKE_CONFIDENCE)
                                recognizer.Reset()
                            if not woke:
                                continue
                        else:
                            if not gate_open:
                                # Sound started — replay the buffered lead-in
                                # so the first syllable isn't clipped.
                                gate_open = True
                                for chunk in preroll:
                                    recognizer.AcceptWaveform(chunk)
                                preroll.clear()
                            # A pause inside speech also produces a result.
                            if recognizer.AcceptWaveform(data):
                                result = json.loads(recognizer.Result())
                                woke = (self._wake_conf(result)
                                        >= WAKE_CONFIDENCE)

                    if woke:
                        active = True
                        gate_open = False
                        loud_frames = 0
                        preroll.clear()
                        active_deadline = time.time() + COMMAND_LISTEN_SECONDS
                        self.gui_queue.put("show")
                        ducker.duck(DUCK_LEVEL)      # lower the music
                        get_recognizer().Reset()     # loads the model if needed
                else:
                    # ---------------- ACTIVE: capture the command ----------
                    handled = False
                    if recognizer.AcceptWaveform(data):
                        result = json.loads(recognizer.Result())
                        text, min_conf = self._text_and_min_conf(result)
                        # ignore leftovers of the wake phrase
                        clean = text and WAKE_WORD not in text and "hey" not in text
                        if clean and min_conf >= COMMAND_CONFIDENCE:
                            handled = execute_command(text)

                    if handled or time.time() >= active_deadline:
                        active = False
                        ducker.restore()             # bring the music back
                        recognizer.Reset()
                        if self.wake is not None:
                            self.wake.reset()
                        cooldown_until = time.time() + WAKE_COOLDOWN
                        self.gui_queue.put("hide")

    def stop(self):
        self._running = False


# ===========================================================================
#  GLOBAL KEY WATCHER: triple-* to wake from anywhere (even lock screen)
# ===========================================================================

class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_uint32),
        ("scanCode", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


# LRESULT, WPARAM, LPARAM are all pointer-sized on x64
_LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,        # LRESULT (pointer-sized)
    ctypes.c_int,            # nCode
    ctypes.c_size_t,         # WPARAM (UINT_PTR, pointer-sized)
    ctypes.c_void_p          # LPARAM (LONG_PTR, pointer-sized)
)


class GlobalKeyWatcher(threading.Thread):
    """
    Watches for Ctrl+Shift+B pressed anywhere — even from the Windows lock /
    logon screen. When detected it wakes the audio listener and shows the
    overlay.

    Uses a low-level keyboard hook (WH_KEYBOARD_LL) which works without a
    window and doesn't need focus. A message pump runs on this thread to
    keep the hook alive.
    """

    def __init__(self, gui_queue):
        super().__init__(daemon=True)
        self.gui_queue = gui_queue
        self._ctrl = False
        self._shift = False

    def _callback(self, nCode, wParam, lParam):
        try:
            if nCode >= 0:
                struct = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT))
                vk = struct.contents.vkCode
                down = wParam in (0x0100, 0x0104)   # WM_KEYDOWN or WM_SYSKEYDOWN

                if vk in (VK_LCONTROL, VK_RCONTROL):
                    self._ctrl = down
                elif vk in (VK_LSHIFT, VK_RSHIFT):
                    self._shift = down
                elif vk == VK_B and down and self._ctrl and self._shift:
                    manual_wake.set()
                    self.gui_queue.put("show")
        except Exception:
            pass
        return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

def run(self):
        proc_ref = _LowLevelKeyboardProc(self._callback)
        user32 = ctypes.windll.user32
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        # CallNextHookEx needs correct argtypes for 64-bit lParam
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        hook = user32.SetWindowsHookExW(
            13,                                          # WH_KEYBOARD_LL
            proc_ref,
            None,                                          # hmod = NULL
            0,
        )
        if not hook:
            print("WARNING: Global hotkey hook failed — Ctrl+Shift+B won't work")
            return
        msg = wintypes.MSG()
        while True:
            ret = ctypes.windll.user32.GetMessageW(
                ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            ctypes.windll.user32.TranslateMessage(msg)
            ctypes.windll.user32.DispatchMessageW(msg)


# ===========================================================================
#  MAIN
# ===========================================================================

def poll_queue(root, overlay, gui_queue):
    """GUI thread: drain listener events and show/hide the overlay."""
    try:
        while True:
            event = gui_queue.get_nowait()
            if event == "show":
                overlay.show()
            elif event == "hide":
                overlay.hide()
    except queue.Empty:
        pass
    root.after(100, poll_queue, root, overlay, gui_queue)


def lower_priority():
    """
    Run below normal priority so BOBO never competes with games or other apps
    for CPU. It only needs to react in a fraction of a second, so this costs
    nothing in practice.
    """
    try:
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(),
                             BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass


def main():
    lower_priority()
    add_to_startup()
    scheduler()                 # start the (idle) timer thread
    start_web_server()          # Wi-Fi remote

    root = tk.Tk()
    overlay = Overlay(root)

    gui_queue = queue.Queue()
    try:
        listener = Listener(gui_queue)
    except FileNotFoundError as e:
        print(e)
        return
    listener.start()
    GlobalKeyWatcher(gui_queue).start()

    wake_name = "hey bobo" if WAKE_ENGINE == "vosk" else WAKE_MODEL
    print(f"BOBO is listening. Wake word: \"{wake_name}\"")
    print(f"Press {WAKE_HOTKEY_LABEL} to wake BOBO from anywhere, even the lock screen")

    root.after(100, poll_queue, root, overlay, gui_queue)
    try:
        root.mainloop()
    finally:
        listener.stop()


if __name__ == "__main__":
    main()
