# Videocassete Web

**[Leia em português](README.pt-BR.md)**

A browser extension plus a small local helper that downloads streaming videos at network speed, in parallel, outside the browser. You get an MP4, or an MP3 if you only want the audio.

| What it does | How |
|---|---|
| Downloads **HLS** (`.m3u8`) and **DASH** (`.mpd`) streams and plain MP4 videos | detects the manifest in the tab, downloads the segments in parallel and merges them with ffmpeg |
| Downloads from **YouTube, Instagram, Facebook, TikTok, X, Vimeo, Threads** | hands the link to [yt-dlp](https://github.com/yt-dlp/yt-dlp) with your session cookies |
| Saves **audio only as MP3** | best audio track → MP3 V0 (~245 kbps) with title, artist and cover |
| Lists **every video in a feed** so you can pick one | "Videos on the page" section in the popup |
| **Records from the page** when downloading is not possible | `captureStream` + `MediaRecorder`, written to disk every second |

Downloads keep going after you close the tab or the browser. If one stops, **Resume** continues from the segment where it stopped. The interface follows the browser language (English or Portuguese).

Works on **Windows, macOS and Linux**, with **Chrome, Edge, Brave, Opera, Vivaldi, Chromium and Firefox**.

---

## Minimum requirements

| Item | Minimum | Notes |
|---|---|---|
| Windows | 10 (1809) or 11 | the installer uses `winget`, which ships with Windows. Without it, the installer downloads the tools directly |
| macOS | 11 Big Sur | [Homebrew](https://brew.sh) is used if present. Without it, the installer downloads the tools directly |
| Linux | any distro with glibc (Ubuntu 20.04+, Debian 11+, Fedora 36+…) | ffmpeg comes from `apt`/`dnf`/`pacman`/`zypper` (asks for `sudo`) |
| Chrome / Edge / Brave / Opera / Vivaldi | 121+ | |
| Firefox | 140+ | see [Firefox](#firefox) |
| Python | 3.9+ | **standard library only, no `pip`**. The installer installs Python if it is missing |
| Disk | 2× the video size | segments stay in a temporary folder until the merge |

The installer finds or installs everything else. You don't need to install these yourself:
- **ffmpeg** merges video and audio, decrypts HLS AES-128 and converts recordings and MP3.
- **yt-dlp** and **Deno** are needed only for YouTube and social networks.

---

## Installation (2 steps)

### 1. Run the installer

Download **`videocassete_web-<version>.zip`** from the [latest release](../../releases/latest) and extract it. Then:

| System | What to do |
|---|---|
| **Windows** | double-click **`install.cmd`** |
| **macOS** | double-click **`install.command`** (the first time: right-click → Open), or run `sh install.sh` in Terminal |
| **Linux** | in a terminal, inside the folder: `sh install.sh` |

The installer does five things:
- asks before installing what is missing (`--yes` skips the questions);
- copies the program to a fixed folder:
  - Windows: `%LOCALAPPDATA%\VideocasseteWeb\app`;
  - macOS: `~/Library/Application Support/VideocasseteWeb/app`;
  - Linux: `~/.local/share/videocassete_web/app`;
- registers the helper with every browser;
- tests the connection;
- opens the extension folder.

Afterwards you can delete the zip you downloaded.

### 2. Load the extension

**Chrome, Edge, Brave, Opera, Vivaldi**
1. Open `chrome://extensions` (`edge://extensions`, `brave://extensions`, `opera://extensions`).
2. Turn on **Developer mode**.
3. Click **Load unpacked** and select the **`app/extension`** folder that the installer opened.
4. Pin the icon: click the puzzle piece 🧩 next to the address bar, then the pin 📌.
5. Open the popup. The badge at the top must say **ready**.

**Firefox**: install the `.xpi` from the [latest release](../../releases/latest) (a click installs it). See [Firefox](#firefox).

### Updating

Download the new zip and run the installer again. Then click reload ↻ on the extension card in `chrome://extensions`. Your settings and downloads are kept.

### Uninstalling

Run `python install.py --uninstall` (Windows: `install.cmd --uninstall`), then remove the extension from the browser.

---

## Usage

### A video with HLS/DASH/MP4

1. Open the video page and **press play**. The icon shows how many streams were found.
2. Click the icon. Under **In this tab** you see each stream with its duration and qualities.
3. Choose the quality and click **Download**.
4. Follow the progress under **Downloads**. You can close the popup, the tab or the browser.
5. The file goes to your **Downloads** folder, or to the folder set in Settings.

If the video was already playing before you installed the extension, reload the page (F5) and press play again.

### YouTube

Open the video and click the icon. The card shows the title and the qualities. To save only the audio, choose **🎵 Audio only (MP3)**.

### Instagram, Facebook and other feeds

On pages with several videos, **Videos on the page** lists each one with thumbnail, profile, caption, duration and whether it is playing. Each video has these buttons:

- **👁** scrolls the page to the video and highlights it.
- **Download** (Video or 🎵 MP3) sends the post link to yt-dlp with your session. The file is named like `facebook_20261027-121154.mp4`.
- **⏺ Record** records that video from the page. Use it for posts without a link, such as ads.

Feeds only load videos near the screen. Scroll and click **↻ Refresh** to list more.

### Record from the page (plan B)

The dashed **Record the video from the page** card works with any video that plays in the tab:

- **1x:** real time, exact copy.
- **1.5x / 2x:** records faster, and ffmpeg restores the original duration at the end (re-encodes).
- **4x:** may drop frames and audio.

The recording is written to disk every second. Closing the tab keeps what was already recorded.

### Settings (bottom of the popup)

- **Default quality:** best, up to 1080p/720p/480p, or lowest.
- **Parallel connections:** 8 by default. Raise it (16–32) on fast connections. The helper lowers it automatically if the site refuses.
- **Destination folder:** empty means Downloads.
- **Download a link:** paste a `.m3u8`, `.mpd`, MP4 or a YouTube/social video link.

### Command line (without the extension)

```sh
python <app>/host/stream_worker.py --probe --url https://.../manifest.mpd
python <app>/host/stream_worker.py --url https://.../master.m3u8 --quality 720 --threads 16 --header "Cookie: ..."
python <app>/host/stream_worker.py <data>/jobs/<job-folder>     # resumes a job
```

---

## Firefox

Firefox only installs extensions signed by Mozilla. Unsigned extensions can only be loaded temporarily and disappear when Firefox restarts.

- **Users:** install the signed `.xpi` from the [Releases](../../releases/latest) page.
- **Trying the source:**
  1. open `about:debugging#/runtime/this-firefox`;
  2. click **Load Temporary Add-on…** and pick `manifest.json` in the `app/extension` folder.
- **After installing**, open the popup once. If it shows **Allow access to all sites**, click it, because Firefox does not grant site access automatically.
- **Recording from the page** mutes the tab sound while it records (a Firefox limitation of `mozCaptureStream`). The recording itself still has audio.

**Maintainers, to sign:** create free API keys at [addons.mozilla.org](https://addons.mozilla.org/developers/addon/api/key/) and run:

```sh
python tools/build_release.py
npx web-ext sign --channel unlisted --source-dir extension --api-key <JWT issuer> --api-secret <JWT secret>
```

Then attach the generated `.xpi` to the GitHub release. "Unlisted" means self-distributed, with no public listing on AMO.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Popup shows **local helper missing** | Run the installer again. On Linux with a **Snap/Flatpak** browser, native messaging may be blocked by the sandbox: use the browser from the vendor's `.deb`/`.rpm` instead. |
| "Could not establish connection. Receiving end does not exist." | The extension background code is outdated. Reload the extension in `chrome://extensions`. |
| Nothing under "In this tab" | Press play. If it was already playing, reload with F5. Or use "Videos on the page" or recording. On Firefox, check site access (see above). |
| HTTP 401/403 mid-download | The login or token expired. Keep the video page open and logged in, press play and click **Resume**: the extension sends fresh cookies and tokens. |
| "the site asked for login" (YouTube/Instagram/Facebook) | Sign in to your account in this browser and try again. |
| "media encrypted with DRM" / "ciphered segments" | The content is protected and will not be decrypted. Use the site's own download button, if you are allowed to. |
| YouTube suddenly stops working | YouTube changes often. Run the installer again: it updates yt-dlp. |
| macOS: "cannot be opened because it is from an unidentified developer" | Right-click `install.command` → Open, or run `sh install.sh` in Terminal. |

Each download has a log in `<data>/jobs/<job>/worker.log`. The log records why the server refused and the names of the headers used, never their values.

---

## Limits

- **DRM** (Netflix, Prime Video, Disney+, Globoplay…) and **media encrypted by the service** (for example Microsoft Stream/SharePoint meeting recordings with `enableEncryption`) are not decrypted or bypassed. The extension detects them from the real content and tells you. A manifest that only *announces* `<ContentProtection>` but serves clear media downloads normally.
- **Live streams:** only complete videos (VOD).
- **DASH with several periods** (stitched ads): only the first period is downloaded.
- **Chrome Web Store / Edge Add-ons:** the extension is not published there, because store policies forbid YouTube downloaders. Hence "Load unpacked".
- Many downloads in a row with your account may make YouTube, Instagram or Facebook flag it as automated.
- Downloading content may break a site's terms of service or copyright. Only use it on content you have the right to keep.

---

## How it works

```
Browser
 ├─ background.js   watches the tab requests: manifests and per-server auth headers
 ├─ popup.js        lists streams and videos, per-server cookies, settings and downloads
 ├─ scan.js         (injected) lists the page <video> elements and finds each post link
 └─ recorder.js     (injected) page recording; sends 1 s chunks to the background
        │  Native Messaging (videocassete_web)
        ▼
host/stream_host.py   receives commands, creates the job folder and starts the worker detached from the browser
        ▼
host/stream_worker.py independent process: reads the manifest, downloads in parallel, resumes, merges with ffmpeg
                      (or calls yt-dlp for YouTube and social networks)
```

**Security**
- Cookies and tokens only go to the server that owns them. The manifest token does not leak to the CDN, and each server gets its own cookies.
- They are kept in files inside the job folder and deleted when the download finishes or is cancelled.
- HTTPS certificates are always verified. On Windows, if a site's root certificate is missing, the helper asks Windows to fetch it. On macOS with the python.org Python, it uses the system certificate bundle.

| Path | Role |
|---|---|
| `extension/` | Manifest V3 extension (Chromium and Firefox from the same code), `_locales/en` and `_locales/pt_BR` |
| `host/stream_host.py` | Native Messaging host (commands and persistent recording) |
| `host/stream_worker.py` | downloader: HLS, DASH, direct MP4, yt-dlp, merge, resume |
| `host/platform_paths.py` | what changes between Windows, macOS and Linux |
| `host/i18n.py` | helper messages in English and Portuguese |
| `install.py` | installer (dependencies, copy, launcher, browser registration, self-test) |
| `tests/` | `python -m unittest discover -s tests` (real HLS/DASH/AES-128 downloads against a local server) |
| `tools/build_release.py` | builds the release zips in `dist/` |

Data folder (`<data>`):
- Windows: `%LOCALAPPDATA%\VideocasseteWeb`;
- macOS: `~/Library/Application Support/VideocasseteWeb`;
- Linux: `~/.local/share/videocassete_web`.

It holds `app/` (the installed program), `jobs/` (one folder per download), `bin/` (tools downloaded by the installer) and `tools.json`.

## License

[MIT](LICENSE)
