# BOBO v0.1.0 — source preview (2026-10-13)

**Scope:** public source-only preview of an offline Windows voice assistant and opt-in LAN remote. **Not** a tested Windows installer, production release or security certification.

## Included
- Configurable Vosk wake/command processing and Windows media controls.
- Restored Ctrl+Shift+B hotkey registration and hook lifecycle cleanup.
- Remote defaults fail-closed: loopback only, password required, screen view disabled unless explicitly enabled.
- Persistent unpredictable session signing, server-side expiry, password rotation invalidation.
- Platform-independent security/hotkey regression suite and Windows-native CI smoke.

## Compatibility and security
- Existing installs without a 12+ character password no longer start the LAN remote.
- Browser sessions from earlier cookie formats are invalidated.
- Remote uses plain HTTP on trusted LAN only; not suitable for public internet exposure.
- Hardware-dependent microphone/desktop/driver integration must be validated on an actual user Windows 10/11 machine.

## Source
Download this release's automatic source archive; follow [README](README.md). No compiled executable is attached. See [ROADMAP](ROADMAP.md) for packaging and remaining validation.
