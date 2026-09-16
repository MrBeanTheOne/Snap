# Third-party notices

Snap bundles the following third-party components. Each remains under its own
license; nothing in Snap's license restricts your rights under them. Copies of
the license texts are available at the links below and from each project.

## Bundled Python packages

| Component | Version | License | Source |
|---|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 2026.8.19 | Unlicense (public domain) | github.com/yt-dlp/yt-dlp |
| [yt-dlp-ejs](https://github.com/yt-dlp/ejs) | 0.8.0 | Unlicense / MIT / ISC | github.com/yt-dlp/ejs |
| [pywebview](https://github.com/r0x0r/pywebview) | 6.2.1 | BSD 3-Clause | github.com/r0x0r/pywebview |
| [pystray](https://github.com/moses-palmer/pystray) | 0.19.5 | LGPL-3.0 | github.com/moses-palmer/pystray |
| [Pillow](https://github.com/python-pillow/Pillow) | 12.2.0 | MIT-CMU (HPND) | github.com/python-pillow/Pillow |
| [mutagen](https://github.com/quodlibet/mutagen) | 1.48.1 | GPL-2.0-or-later | github.com/quodlibet/mutagen |
| [certifi](https://github.com/certifi/python-certifi) | 2026.4.22 | MPL-2.0 | github.com/certifi/python-certifi |
| [requests](https://github.com/psf/requests) | 2.34.2 | Apache-2.0 | github.com/psf/requests |
| [urllib3](https://github.com/urllib3/urllib3) | 2.7.0 | MIT | github.com/urllib3/urllib3 |
| [websockets](https://github.com/python-websockets/websockets) | 15.0.1 | BSD 3-Clause | github.com/python-websockets/websockets |

## Bundled executables (invoked as separate programs)

| Component | License | Source |
|---|---|---|
| [FFmpeg](https://ffmpeg.org) (ffmpeg.exe, ffprobe.exe) | GPL-2.0-or-later build | ffmpeg.org — sources at github.com/FFmpeg/FFmpeg; Windows builds: gyan.dev/ffmpeg / github.com/GyanD/codexffmpeg |
| [Deno](https://deno.land) (deno.exe) | MIT | github.com/denoland/deno |

FFmpeg is used unmodified and invoked as a separate process. Its complete
corresponding source code is available from the links above. The FFmpeg
license text: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html

## Runtime

| Component | License |
|---|---|
| [Python](https://python.org) (embedded by PyInstaller) | PSF-2.0 |
| [PyInstaller](https://pyinstaller.org) bootloader | GPL-2.0 with bootloader exception (permits any-license apps) |
| [Microsoft Edge WebView2](https://developer.microsoft.com/microsoft-edge/webview2/) loader | Microsoft redistributable license |

## Notes on copyleft components

- **mutagen** (GPL-2.0-or-later) and **pystray** (LGPL-3.0) are included in
  unmodified form. Their license texts apply to those components in full:
  GPL-2.0: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html ·
  LGPL-3.0: https://www.gnu.org/licenses/lgpl-3.0.html
- Snap's own code is separately licensed (see LICENSE). If you wish to
  exercise GPL/LGPL rights over those components, obtain them from their
  upstream sources linked above.
