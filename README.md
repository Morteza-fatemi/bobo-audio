# BOBO — Offline Voice Assistant + Wi-Fi Web Remote (Windows)

Always-on background assistant. Say **"Hey Bobo"** → a Siri-like glowing 3-D orb
appears at the top of the screen, the music volume ducks so your command is
heard clearly, the command runs, then the volume is restored.

It also serves a **modern web remote on your Wi-Fi**, so you can control media
from your phone's browser.

| Say (after "Hey Bobo")     | Action                                  |
|----------------------------|-----------------------------------------|
| "sleep pc" / "screen off"  | Turns the **monitor** off (PC stays on) |
| "play music" / "play"      | Media Play/Pause                        |
| "stop" / "pause"           | Media Play/Pause                        |
| "next" / "next song"       | Next track                              |
| "back" / "previous"        | Previous track                          |

---

## 1. Install
```powershell
cd D:\BOBO
pip install -r requirements.txt
```
The `model` folder (Vosk `vosk-model-small-en-us-0.15`) must be in `D:\BOBO`.

## 2. Run
```powershell
python app.py          # shows messages while testing
pythonw app.py         # fully hidden, no console window
```
At startup it prints the remote URL, registers itself for Windows auto-start,
and starts listening.

---

## Wi-Fi Web Remote  📱
When the app starts it prints something like:
```
Web remote ready:  http://192.168.100.26:49731/
```
Open that URL in your **phone's browser** (same Wi-Fi). The panel gives you:

* **Now Playing** — the real track title and artist from Windows, with a live
  status dot. The main button shows a **play** icon when paused and a **pause**
  icon when playing.
* **Media** — Previous / Play-Pause / Next. These drive the **Windows media
  session**, so they work with players that ignore the keyboard media keys
  (Telegram, some browsers), falling back to media keys when needed.
* **Volume** — a mute button, **− / +** step buttons, and a slider that sets the
  exact Windows volume.
* **Brightness** — same controls for the laptop screen brightness.
* **Send to PC clipboard** — type text on your phone, tap **Send**, then press
  **Ctrl+V** on the laptop. Full Unicode (Persian, emoji) supported.
* **Timers** — "pause the music in 10 minutes", "turn the screen off in 20".
  Pick an action (Pause music / Screen off / Mute) and a delay, tap **Add
  timer**, and it counts down live. Cancel any timer with the **×**.
  *Pause* only ever pauses — a timer can never accidentally start music.
* **Battery** — live percentage and charging state in the header.
* **Live Screen** — toggle on to watch your laptop's screen, with a fullscreen
  button. It refreshes about once a second and **pauses automatically when the
  page isn't visible**, to save phone battery and PC CPU.
* **Turn Screen Off**

The page is built for phones: no double-tap zoom, large touch targets, real SVG
icons, full keyboard/screen-reader labels, and it respects
`prefers-reduced-motion`.

### Password / sign-in
The remote is protected by a password stored in **`web_password.txt`** next to
`app.py` (one line, just the password).

* You sign in **once per device** — a signed, `HttpOnly` cookie keeps you logged
  in for 30 days, so the phone never asks again.
* The password is **never put in the URL**, so it can't leak into browser
  history or logs.
* 5 wrong attempts locks that device out for 30 seconds.
* **To change it:** edit `web_password.txt` and restart. Changing the password
  automatically signs out every device.
* **To remove protection:** delete `web_password.txt` (not recommended).
* Delete `.bobo_session_key` to force every device to sign in again.

### Remote settings (top of app.py)
* `WEB_PORT = 49731` — change the port if you like.
* `WEB_BIND = "0.0.0.0"` — reachable over Wi-Fi. Set to `"127.0.0.1"` for
  **this PC only**.
* `WEB_SESSION_DAYS = 30` — how long a signed-in device is remembered.
* `SCREEN_VIEW = True` — set `False` to remove the live-screen feature entirely.
* `SCREEN_WIDTH = 760`, `SCREEN_QUALITY = 55`, `SCREEN_REFRESH_MS = 1000` —
  preview size / quality / refresh rate.

> ## ⚠️ Security — please read
> **This runs over plain HTTP, not HTTPS.** On the local network the password
> and the screen images travel **unencrypted**. That means:
>
> * **Use a password you don't use anywhere else.** Never reuse an email,
>   banking, or social-media password here — someone sniffing the Wi-Fi could
>   read it in cleartext.
> * Without `web_password.txt`, *anyone on the same Wi-Fi* can **watch your
>   screen**, see whatever you have open, control your media, and turn your
>   display off.
> * Never run this on public / shared Wi-Fi (cafés, hotels, dorms, offices).
>   On an untrusted network use `WEB_BIND = "127.0.0.1"` or
>   `SCREEN_VIEW = False`.
>
> If Windows Firewall asks to allow Python on the network, allow it for
> **Private networks only** — otherwise your phone can't reach it.

---

## Wake word
Default is **"Hey Bobo"** using Vosk (`WAKE_ENGINE = "vosk"`).

Switch engines at the top of `app.py`:
```python
WAKE_ENGINE = "vosk"   # "hey bobo"
# WAKE_ENGINE = "oww"  # openWakeWord: better over loud music
```
`"oww"` is more robust while music plays, but only supports ready-made phrases
(`WAKE_MODEL`): `"alexa"`, `"hey_jarvis"`, `"hey_mycroft"`, `"hey_rhasspy"` —
it has no "bobo" model without custom training.

### Tuning
* `WAKE_CONFIDENCE = 0.80` (vosk) — raise if it false-triggers, lower (0.65) if
  it sometimes ignores you.
* `WAKE_THRESHOLD = 0.4` (oww) — lower (0.3) to hear you better over music.
* `DUCK_LEVEL = 0.15` — volume while listening for a command.
* `COMMAND_LISTEN_SECONDS = 6` — how long it listens after waking.
* `INPUT_DEVICE_INDEX = None` — set a number to force a specific microphone.

---

## Performance — how light is it?
Measured on this PC (20 cores), idle in the background:

| | |
|---|---|
| **CPU (quiet room)** | ~0.4 % of total CPU |
| **RAM** | ~220 MB |
| **Priority** | Below-normal, so it never competes with games |

Where the RAM goes: the **Vosk speech model is ~115 MB** — that is the price of
a custom "Hey Bobo" wake word. Everything else (Python, numpy, Pillow, tkinter,
the web server) is small.

Three things keep it light:
1. **Silence gate** — audio quieter than `VAD_RMS` is never fed to the speech
   decoder. In a quiet room that skips ~100 % of the decoding work. (Measured:
   ambient noise here is RMS 7 vs a threshold of 300.)
2. **Below-normal process priority.**
3. **No polling** — the scheduler sleeps until the next timer is due, the screen
   preview stops when the phone's page isn't visible, and status is cached.

> **Don't switch to `WAKE_ENGINE = "oww"` for performance.** It is *heavier*:
> openWakeWord costs ~155 MB (onnxruntime + scipy + sklearn) on top of Vosk.
> The default `"vosk"` / "Hey Bobo" setup is the lightest configuration.

### If you want it lighter still
* `VAD_RMS = 300` — raise it (e.g. 500) if your room is noisy and you want the
  decoder to stay off more of the time.
* `SCREEN_VIEW = False` — drops the screen-capture code path entirely.
* `Overlay.FRAMES = 40` — lower it to save a few MB of animation frames.

## Build a hidden .exe
```powershell
pip install pyinstaller
pyinstaller --noconsole --onefile --name BOBO app.py
```
Copy the `model\` folder next to `dist\BOBO.exe`, then run it once.

## Notes
* **Screen off:** moving the mouse / pressing a key turns the display back on.
* **Media keys** need a player that responds to them (Spotify, browser video…).
* **Uninstall auto-start:** delete `BOBO_Voice_Assistant` under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (regedit).
* **Stop the app:** end the `python`/`pythonw`/`BOBO.exe` task in Task Manager.
