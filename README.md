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

The application prints the local web-remote URL at startup. Open it from a phone on the same private Wi‑Fi network.

## Configuration

Configuration is intentionally kept near the top of `app.py` so a local installation can be understood without hidden configuration. Important settings include:

- `WEB_BIND`: use `127.0.0.1` for this PC only; use `0.0.0.0` only on a trusted private network.
- `SCREEN_VIEW`: set to `False` when screen streaming is not needed.
- `WEB_SESSION_DAYS`: controls the lifetime of signed browser sessions.
- `WAKE_ENGINE`: choose the supported wake-word engine.

The optional `web_password.txt` file is local-only and must never be committed.

## Security model and limitations

The remote uses plain HTTP on the local network. It is not suitable for public Wi‑Fi or internet exposure. Screen previews can reveal everything visible on the PC. Use a unique local password, keep the firewall rule private-only, bind to localhost when remote control is unnecessary, and disable `SCREEN_VIEW` when possible.

The project currently targets Windows APIs and media sessions. Hardware-dependent microphone, screen-capture, brightness, and media behavior can vary by machine and driver. No production-scale or internet-facing guarantees are claimed.

## Testing and quality checks

The repository includes focused Windows/hardware test scripts for microphone levels, WASAPI input, and hook behavior. A platform-independent CI check validates Python syntax and repository hygiene on every push and pull request.

Run a local syntax check with:

```powershell
python -m compileall -q app.py debug_listen.py test_hook_debug.py test_mic_levels.py test_wasapi.py test_windows_mic.py
```

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
