"""Scan the download folder into a browsable library with cached cover art,
served to the UI over a local-only HTTP server (the webview can't load file:// media)."""
import base64
import hashlib
import http.server
import mimetypes
import os
import subprocess
import threading
import urllib.parse

import updater

EXTS = {".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".flac", ".wav", ".opus"}
VIDEO = {".mp4", ".mkv", ".webm"}
COVER_DIR = os.path.join(updater.STATE_DIR, "covers")


def _embedded_art(path, ext):
    if ext == ".mp3":
        from mutagen.id3 import ID3
        for tag in ID3(path).getall("APIC"):
            return tag.data
    elif ext == ".m4a":
        from mutagen.mp4 import MP4
        c = (MP4(path).tags or {}).get("covr")
        return bytes(c[0]) if c else None
    elif ext == ".flac":
        from mutagen.flac import FLAC
        pics = FLAC(path).pictures
        return pics[0].data if pics else None
    elif ext == ".opus":
        from mutagen.flac import Picture
        from mutagen.oggopus import OggOpus
        b64 = OggOpus(path).get("metadata_block_picture")
        return Picture(base64.b64decode(b64[0])).data if b64 else None
    return None


def _cover(path, ext, ffmpeg):
    """Cached thumbnail: embedded art for audio, a frame grab for video; "" when there's none."""
    os.makedirs(COVER_DIR, exist_ok=True)
    st = os.stat(path)
    key = hashlib.md5(f"{path}|{st.st_mtime_ns}".encode()).hexdigest()
    out = os.path.join(COVER_DIR, key + ".jpg")
    miss = os.path.join(COVER_DIR, key + ".none")  # negative cache — mtime in the key retries edits
    if os.path.exists(out):
        return out
    if os.path.exists(miss) or (ext in VIDEO and not ffmpeg):
        return ""
    try:
        if ext in VIDEO:
            for seek in ("3", "0"):  # a clip shorter than the seek yields no frame — retry at 0
                subprocess.run([ffmpeg, "-y", "-ss", seek, "-i", path, "-frames:v", "1",
                                "-vf", "scale=320:-2", out],
                               capture_output=True, timeout=20, creationflags=0x08000000)
                if os.path.exists(out):
                    break
        else:
            data = _embedded_art(path, ext)
            if data:
                with open(out, "wb") as f:
                    f.write(data)
    except Exception:
        pass
    if os.path.exists(out):
        return out
    open(miss, "w").close()
    return ""


_PORT = None
_GET_OUT = None
_FFMPEG = None


def _resolve(rel):
    """URL path → file under the download folder, or None if it escapes it."""
    root = os.path.normpath(_GET_OUT())
    fp = os.path.normpath(os.path.join(root, rel.lstrip("/")))
    try:  # commonpath, not startswith: '…\Snap-evil' starts with '…\Snap'
        return fp if os.path.commonpath([root, fp]) == root else None
    except ValueError:  # another drive
        return None


class _MediaHandler(http.server.BaseHTTPRequestHandler):
    """Serves /cover/<path> (made on first request) and /media/<path> with Range support
    (seeking); both paths are relative to out_dir."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if p.startswith("/cover/"):
            # made HERE, not in scan(): the <img> is lazy, so only cards scrolled into view
            # pay for an ffmpeg frame grab, each on its own server thread
            src = _resolve(p[len("/cover/"):])
            fp = src and os.path.isfile(src) and _cover(src, os.path.splitext(src)[1].lower(), _FFMPEG)
            if not fp:
                return self.send_error(404)  # the card falls back to its format tile
        elif p.startswith("/media/"):
            fp = _resolve(p[len("/media/"):])
            if not fp:  # no path traversal
                return self.send_error(403)
        else:
            return self.send_error(404)
        if not os.path.isfile(fp):
            return self.send_error(404)
        size = os.path.getsize(fp)
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            s, _, e = rng[len("bytes="):].partition("-")
            start = int(s) if s else max(0, size - int(e))
            if s and e:
                end = min(int(e), size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", mimetypes.guess_type(fp)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        try:
            with open(fp, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # player seeked / closed mid-stream


def ensure_server(get_out_dir, ffmpeg):
    """Start (once) the localhost media server; returns its port."""
    global _PORT, _GET_OUT, _FFMPEG
    _GET_OUT, _FFMPEG = get_out_dir, ffmpeg
    if _PORT is None:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MediaHandler)
        _PORT = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    return _PORT


def scan(out_dir):
    """Walk + stat only — covers come later, per visible card, from /cover/."""
    items = []
    if not os.path.isdir(out_dir):
        return items
    for root, _dirs, files in os.walk(out_dir):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in EXTS:
                continue
            path = os.path.join(root, fn)
            try:
                st = os.stat(path)
            except OSError:
                continue
            folder = os.path.relpath(root, out_dir)
            folder = "" if folder == "." else folder.replace("\\", "/")
            rel = urllib.parse.quote((folder + "/" if folder else "") + fn)
            items.append({
                "name": os.path.splitext(fn)[0],
                "path": path,
                "folder": folder,
                "ext": ext[1:],
                "video": ext in VIDEO,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                # ?v=mtime: an edited file must not show the browser-cached old cover
                "cover_url": f"http://127.0.0.1:{_PORT}/cover/{rel}?v={st.st_mtime_ns}",
                "media_url": f"http://127.0.0.1:{_PORT}/media/{rel}",
            })
    items.sort(key=lambda x: -x["mtime"])
    return items
