# Changelog

## 2.0.0 — 2026-10-27

First public release.

- Windows, macOS and Linux. Data folder, tool lookup, "open folder", process tree kill and TLS certificates per system.
- Chrome, Edge, Brave, Opera, Vivaldi, Chromium and Firefox from the same extension code.
- Interface and helper messages in English and Portuguese, following the browser language.
- One-step installer (`install.cmd` / `install.sh` / `install.command`):
  - installs Python, ffmpeg, yt-dlp and Deno when missing;
  - copies the program to a fixed folder;
  - registers the helper with every browser;
  - runs a self-test.
- Firefox: asks for site access when it is missing.
- Automated tests with real HLS, HLS AES-128 and DASH downloads against a local server, resume, the full extension → helper → worker flow and locale completeness. CI on Windows, macOS and Linux.

### Validated

| Where | What |
|---|---|
| Windows 11, Python 3.13 | installer, test suite, Chrome/Edge/Firefox/Opera registration |
| CI (GitHub Actions) | Python unit tests on windows/macos/ubuntu (3.13, plus 3.9 on ubuntu) and JavaScript unit tests (Node 22) |
| Not yet validated by hand | browsers on macOS and Linux |
