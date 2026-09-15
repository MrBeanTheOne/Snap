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
    """Cached thumbnail: embedded art for audio, a frame grab for video."""
    os.makedirs(COVER_DIR, exist_ok=True)
    st = os.stat(path)
    key = hashlib.md5(f"{path}|{st.st_mtime_ns}".encode()).hexdigest()
    out = os.path.join(COVER_DIR, key + ".jpg")
    if os.path.exists(out):
        return out
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
    return out if os.path.exists(out) else ""


_PORT = None
_GET_OUT = None


class _MediaHandler(http.server.BaseHTTPRequestHandler):
    """Serves /covers/<file> and /media/<path under out_dir> with Range support (seeking)."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = urllib.parse.unquote(self.path)
        if p.startswith("/covers/"):
            fp = os.path.join(COVER_DIR, os.path.basename(p[len("/covers/"):]))
        elif p.startswith("/media/"):
            root = os.path.normpath(_GET_OUT())
            fp = os.path.normpath(os.path.join(root, p[len("/media/"):].lstrip("/")))
            if not fp.startswith(root):  # no path traversal
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


def ensure_server(get_out_dir):
    """Start (once) the localhost media server; returns its port."""
    global _PORT, _GET_OUT
    _GET_OUT = get_out_dir
    if _PORT is None:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MediaHandler)
        _PORT = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    return _PORT


def scan(out_dir, ffmpeg):
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
            cover = _cover(path, ext, ffmpeg)
            rel = (folder + "/" if folder else "") + fn
            items.append({
                "name": os.path.splitext(fn)[0],
                "path": path,
                "folder": folder,
                "ext": ext[1:],
                "video": ext in VIDEO,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "cover_url": f"http://127.0.0.1:{_PORT}/covers/{os.path.basename(cover)}" if cover else "",
                "media_url": f"http://127.0.0.1:{_PORT}/media/" + urllib.parse.quote(rel),
            })
    items.sort(key=lambda x: -x["mtime"])
    return items
