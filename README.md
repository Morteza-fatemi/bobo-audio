# BOBO

Offline voice assistant and Wi‑Fi media remote for Windows.

BOBO listens for a configurable wake word, exposes a password-protected local web remote, and controls media, volume, brightness, clipboard, timers, and an optional live-screen preview. It is designed for personal, trusted-network use—not as a public internet service.

## What is public

This repository contains the safe, runnable application code and hardware-oriented test scripts. Credentials, session keys, local logs, model weights, and machine-specific state are intentionally excluded.

## Requirements

- Windows 10/11
- Python 3.10+
- A microphone and (optionally) a Vosk model directory
- A private/trusted Wi‑Fi network for phone control

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For the default `vosk` wake engine, place the Vosk model in `model\` as documented by the configuration comments in `app.py`. Do not commit model weights.

## Run

```powershell
python app.py
```

By default the assistant runs **without any web remote**. Voice commands work normally. To enable the local web remote, create a local-only `web_password.txt` file next to `app.py` containing a unique password of at least 12 characters. Restart BOBO; the server listens only on `127.0.0.1` until you deliberately change `WEB_BIND`.

## Configuration

Configuration is intentionally kept near the top of `app.py` so a local installation can be understood without hidden configuration. Important settings include:

- `WEB_BIND`: defaults to `127.0.0.1`. Change to `0.0.0.0` **only** if a phone must reach the remote on a trusted private Wi-Fi, with a strong password and a private-only firewall rule.
- `SCREEN_VIEW`: defaults to `False` because screenshots may reveal private information. Enable only with full understanding of the risk.
- `WEB_SESSION_DAYS`: defaults to **1 day**, down from 30; the server verifies the timestamp and signed token on every request (not just browser expiry).
- `WAKE_ENGINE`: choose the supported wake-word engine.

The `web_password.txt` file is **required** to enable the web remote (or set a 12+ character `WEB_PASSWORD` locally), and must never be committed. Do not set passwords in a public fork. A persistent random `.bobo_session_key` is created with restrictive permissions where supported; if storage fails or the key is malformed, web remote startup is refused rather than signing sessions with a predictable key.

## Security model and limitations

The remote uses plain HTTP on the local network, so the password and session cookie are **not encrypted in transit**. It is not suitable for public Wi-Fi or internet exposure. Treat LAN access as an explicit trusted-network opt-in. Screen previews can reveal everything visible on the PC. Use a unique local password, keep the firewall rule private-only, bind to localhost when remote control is unnecessary, and disable `SCREEN_VIEW` when possible.

The project currently targets Windows APIs and media sessions. Hardware-dependent microphone, screen-capture, brightness, and media behavior can vary by machine and driver. No production-scale or internet-facing guarantees are claimed.

## Testing and quality checks

The repository includes focused Windows/hardware test scripts for microphone levels, WASAPI input, and hook behavior. Platform-independent CI validates syntax, hotkey behavior, remote default isolation, secret persistence and failure behavior on every push and pull request.

Run a local syntax check with:

```powershell
python -m compileall -q app.py debug_listen.py test_hook_debug.py test_mic_levels.py test_wasapi.py test_windows_mic.py
```

## Secure remote setup (Windows)

1. Run BOBO normally: offline voice commands are available while the web remote stays disabled without credentials.
2. Create `web_password.txt` alongside `app.py` with a **unique random password of at least 12 characters**. Store the file locally; do not commit it.
3. Restart BOBO. On this PC only, open `http://127.0.0.1:49731/`.
4. Optional phone access: set `WEB_BIND = "0.0.0.0"` only on private Wi-Fi, allow the port only on private Windows Firewall profiles, and keep `SCREEN_VIEW = False` unless required. Do not expose the port to the internet.
5. To disable remote access again, set `WEB_BIND = "127.0.0.1"` or remove the local password file and restart. Changing the password invalidates existing signed cookies. Sessions also expire server-side after their configured lifetime. The offline assistant remains operational.

**Compatibility note:** Existing setups with no password or a password shorter than 12 characters will no longer start the web remote until a strong password is configured. This is intentional to prevent unauthenticated control. Windows hardware and real LAN behavior still require manual validation.

## Architecture at a glance

```text
Microphone → wake-word listener → command dispatcher → Windows media / display APIs
                                      └──────────────→ local HTTP remote
                                                        └→ signed session cookie
```

The listener, command dispatcher, scheduler, Windows integration workers, and HTTP remote are kept as explicit components in `app.py` to make the personal tool easy to inspect and modify.

## Roadmap

- Add optional HTTPS/reverse-proxy guidance for advanced local deployments.
- Separate platform integrations behind testable adapters.
- Add deterministic unit tests for command parsing and session policy.
- Improve packaging and first-run model setup.

## License

MIT. See [LICENSE](LICENSE).
