# BOBO — Engineering Roadmap

## Current public source milestone: v0.1.0
- [x] Hotkey logic restored; Python AST-based regression tests.
- [x] Fail-closed web remote, unpredictable session key and signed expiring cookies.
- [x] Linux CI for Python syntax, security and hotkey regressions.
- [x] Windows runner native Win32 API smoke and Python regressions (availability may be restricted in hosted environments).
- [ ] **Manual Windows 10/11 acceptance**: physical Ctrl+Shift+B, audio microphone capture, waking, screen control, battery/brightness, Wi-Fi remote and firewall.
- [ ] Full integration tests with actual sound card / desktop and OS UI session.
- [ ] Split the monolithic app into testable audio, web, UI and Win32 adapter modules.
- [ ] Add real HTTPS reverse-proxy guidance for advanced LAN-only setups.
- [ ] Distribute signed EXE/installer after security and hardware acceptance.

### Definition of done for a stable public release
A Windows physical-machine checklist passed, reproducible build, documented supported OS versions and media/hardware limitations, dependency audit, upgrade/rollback guide and release notes.

### Status disclaimer
A successful hosted-runner CI job verifies only the tested paths, not a user's physical microphone, Bluetooth, media session, locked desktop or whole PC.
