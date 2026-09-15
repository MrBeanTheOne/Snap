import glob
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

import updater

updater.apply_pending()  # must run before yt_dlp is imported so a downloaded update wins

import webview
import yt_dlp
from yt_dlp.postprocessor.metadataparser import MetadataParserPP

import library
import tagging
import winshell

# When frozen by PyInstaller, bundled files live in _MEIPASS (onefile) or next to the exe (onedir)
BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(updater.STATE_DIR, "state.json")

DEFAULT_SETTINGS = {
    "workers": 3,          # parallel downloads (reducing takes effect on restart)
    "watch_interval": 900,  # seconds between channel checks
    "clip_auto": True,      # auto-fetch clipboard links
    "def_mode": "mp4",
    "def_quality": 1080,
    "def_subs": False,
    "mp3_bitrate": 192,
    "page_size": 100,  # channel videos per fetch; 0 = everything at once
    "organize": True,  # playlist/channel downloads go into a subfolder named after the source
    "notify": True,    # toast when downloads finish / watches find new videos
    "tray": True,      # close button hides to tray, watches keep running
    "min_tray": False,  # minimize button also hides to tray
    "cookies": False,   # use the Edge browser's YouTube login (age-restricted/members videos)
    "autostart": False,  # launch minimized to tray when Windows starts
    "sub_lang": "en",   # subtitle language(s): en / fr / en,fr / all
    "mb_tag": True,     # look up proper artist/album/year tags on MusicBrainz
}
VIDEO_MODES = ("mp4", "mkv")
TAGGABLE = ("mp3", "m4a", "flac", "opus")  # wav is tagless PCM
AUDIO_FMT = {  # prefer streams that avoid a lossy re-encode
    "m4a": "bestaudio[ext=m4a]/bestaudio/best",
    "opus": "bestaudio[acodec=opus]/bestaudio/best",
}


def _ts(s):
    """'1:23' / '83' / '1:02:03' → seconds; None when empty or unparseable."""
    s = str(s or "").strip()
    if not s:
        return None
    try:
        t = 0.0
        for p in s.split(":"):
            t = t * 60 + float(p)
        return t
    except ValueError:
        return None


def _find_ffmpeg():
    bundled = os.path.join(BASE, "ffmpeg.exe")
    if os.path.exists(bundled):
        return bundled
    found = shutil.which("ffmpeg")
    if found:
        return found
    # winget install modifies PATH for future shells only; search its package dir
    pkgs = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    hits = glob.glob(os.path.join(pkgs, "Gyan.FFmpeg*", "**", "ffmpeg.exe"), recursive=True)
    return hits[0] if hits else None


FFMPEG = _find_ffmpeg()
# YouTube extraction needs a JS runtime — a deno.exe ships in the frozen build so
# installs work on machines with neither Node nor Deno; dev runs use whatever's on PATH
_BUNDLED_DENO = os.path.join(BASE, "deno.exe")
if os.path.exists(_BUNDLED_DENO):
    JS_RUNTIMES = {"deno": {"path": _BUNDLED_DENO}}
else:
    JS_RUNTIMES = {"deno": {}, "node": {}}
TERMINAL = ("done", "error", "cancelled")


class _FetchLogger:
    """Counts yt-dlp's 'Downloading page N' lines so the UI can show fetch progress."""

    def __init__(self, api):
        self.api = api

    def debug(self, msg):
        m = re.search(r"Downloading page (\d+)", str(msg))
        if m:
            self.api.fetch_pages = int(m.group(1))

    def info(self, msg):
        self.debug(msg)

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


class Api:
    def __init__(self):
        self.lock = threading.Lock()
        self.q = queue.Queue()
        self.fetch_pages = 0
        self.downloaded = {}  # video id → date; survives clear_history
        self.tray = None  # set once the tray icon is up
        self.paused = False
        self._batch = {"done": 0, "error": 0}  # since the last queue-empty toast
        self.out_dir = os.path.join(os.path.expanduser("~"), "Downloads", "Snap")
        self.jobs = []
        self.watches = []
        self.settings = dict(DEFAULT_SETTINGS)
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
            self.out_dir = state.get("out_dir", self.out_dir)
            self.jobs = state.get("history", [])
            self.watches = state.get("watches", [])
            self.downloaded = state.get("downloaded", {})
            self.settings.update({k: v for k, v in state.get("settings", {}).items() if k in DEFAULT_SETTINGS})
        except Exception:
            pass
        # migrate pre-tabs watches: url used to carry a /videos suffix
        for w in self.watches:
            w.setdefault("tabs", ["videos"])
            w["url"] = re.sub(r"/(videos|shorts|streams|live)/?$", "", w["url"].rstrip("/"))
        self._nworkers = max(1, min(8, int(self.settings["workers"])))
        for _ in range(self._nworkers):
            threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def _save(self):
        # best-effort — a failed save must never kill a worker or watch thread
        try:
            self._save_raw()
        except Exception:
            pass

    def _save_raw(self):
        with self.lock:
            os.makedirs(updater.STATE_DIR, exist_ok=True)
            history = [j for j in self.jobs if j["status"] in TERMINAL][-200:]
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {"out_dir": self.out_dir, "history": history, "watches": self.watches,
                     "settings": self.settings, "downloaded": self.downloaded}, f
                )

    # ---- called from JS ----

    def get_info(self, url, tab="videos", offset=0, count=100):
        url = url.strip()
        items = None
        search = not re.match(r"https?://", url)
        if search:
            url = f"ytsearch12:{url}"  # plain words → top YouTube results in the picker
        channel = not search and self._is_channel(url) and "/playlists" not in url
        if channel:
            # a channel root makes yt-dlp walk every tab (slow) and returns tab playlists,
            # not videos — fetch one tab (UI pills pick which); count=0 loads everything
            url = re.sub(r"/(videos|shorts|streams|live)/?$", "", url.rstrip("/"))
            url += "/" + (tab if tab in ("videos", "shorts", "streams") else "videos")
            if count:
                items = f"{offset + 1}:{offset + count}"
        self.fetch_pages = 0
        try:
            info = self._flat_info(url, items=items, progress=True)
        except Exception as e:
            return {"error": str(e)}

        if info.get("_type") == "playlist":
            entries = [
                {
                    "url": e.get("url") or e.get("webpage_url"),
                    "title": e.get("title", "?"),
                    "thumb": (e.get("thumbnails") or [{}])[-1].get("url", ""),
                    "dl": self.downloaded.get(self._dlkey(e.get("url") or "")),
                }
                for e in info.get("entries", [])
                if e
            ]
            return {
                "playlist": True,
                "search": search,
                "title": info.get("title", "Playlist"),
                "entries": entries,
                "more": bool(channel and count and len(entries) == count),
            }
        heights = sorted(
            {f["height"] for f in info.get("formats", []) if f.get("height")},
            reverse=True,
        )
        return {
            "playlist": False,
            "title": info.get("title", "?"),
            "thumbnail": info.get("thumbnail"),
            "heights": heights or [1080, 720, 480],
            "dl": self.downloaded.get(self._dlkey(url)),
        }

    @staticmethod
    def _dlkey(url):
        # ponytail: YouTube video id as identity; raw URL for other sites.
        # title-similarity matching is the upgrade if re-uploads become a problem
        m = re.search(r"(?:v=|youtu\.be/|/shorts/)([\w-]{11})", url or "")
        return m.group(1) if m else (url or "")

    def clear_downloaded(self):
        self.downloaded = {}
        self._save()

    @staticmethod
    def _folder_name(name):
        # strip the channel-tab suffix and Windows-hostile characters
        name = re.sub(r" - (Videos|Shorts|Live)$", "", str(name or ""))
        return re.sub(r'[<>:"/\\|?*]', "", name).strip(". ")[:80]

    def enqueue(self, url, mode, quality, title, subs=False, thumb="", folder="", clip_a="", clip_b=""):
        job = {
            "id": max((j["id"] for j in self.jobs), default=-1) + 1,
            "title": title or url,
            "url": url,
            "mode": mode,
            "quality": int(quality or 1080),
            "subs": bool(subs),
            "thumb": thumb or "",
            "folder": self._folder_name(folder),
            "clip_a": str(clip_a or "").strip(),
            "clip_b": str(clip_b or "").strip(),
            "status": "queued",
            "percent": 0,
            "speed": "",
            "eta": "",
            "error": "",
            "filepath": "",
            "cancel": False,
        }
        self.jobs.append(job)
        self.q.put(job)
        return job["id"]

    def redo(self, job_id):
        j = self._job(job_id)
        if j:
            self.enqueue(j["url"], j["mode"], j["quality"], j["title"], j["subs"], j.get("thumb", ""),
                         j.get("folder", ""), j.get("clip_a", ""), j.get("clip_b", ""))

    def cancel(self, job_id):
        j = self._job(job_id)
        if j and j["status"] not in TERMINAL:
            j["cancel"] = True
            j["status"] = "cancelling"

    def get_jobs(self):
        # ponytail: JS polls this every 500ms — push via evaluate_js if it ever feels laggy
        return self.jobs

    def toggle_pause(self):
        self.paused = not self.paused
        return self.paused

    def get_paused(self):
        return self.paused

    def clear_history(self):
        self.jobs = [j for j in self.jobs if j["status"] not in TERMINAL]
        self._save()

    def get_clipboard(self):
        return winshell.read_clipboard()

    def get_out_dir(self):
        return self.out_dir

    def get_pending_url(self):
        u = getattr(self, "pending_url", "")
        self.pending_url = ""
        return u

    def get_settings(self):
        return self.settings

    def set_settings(self, patch):
        clean = {k: patch[k] for k in DEFAULT_SETTINGS if k in patch}
        for k in ("workers", "watch_interval", "def_quality", "mp3_bitrate", "page_size"):
            if k in clean:
                clean[k] = int(clean[k])
        if "workers" in clean:
            clean["workers"] = max(1, min(8, clean["workers"]))
        if "autostart" in clean:
            try:
                winshell.set_autostart(bool(clean["autostart"]))
            except Exception:
                clean.pop("autostart")
        self.settings.update(clean)
        # more workers take effect immediately; fewer only on restart (threads can't be reaped)
        while self._nworkers < self.settings["workers"]:
            threading.Thread(target=self._worker, daemon=True).start()
            self._nworkers += 1
        self._save()
        return self.settings

    def choose_folder(self):
        result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        if result:
            self.out_dir = result[0]
            self._save()
        return self.out_dir

    def open_folder(self):
        os.makedirs(self.out_dir, exist_ok=True)
        os.startfile(self.out_dir)

    def open_file(self, job_id):
        j = self._job(job_id)
        if j and j["filepath"] and os.path.exists(j["filepath"]):
            os.startfile(j["filepath"])

    # ---- library ----

    def get_library(self):
        library.ensure_server(lambda: self.out_dir)
        return library.scan(self.out_dir, FFMPEG)

    def reveal(self, path):
        if os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", path], creationflags=0x08000000)

    # ---- yt-dlp updater ----

    def get_version(self):
        return yt_dlp.version.__version__

    def get_app_version(self):
        return updater.APP_VERSION

    def check_update(self):
        return updater.check_update()

    def update_ytdlp(self):
        return updater.update_ytdlp()

    def update_app(self):
        return updater.update_app()

    def restart_app(self):
        # the updater's batch script waits for this process to die, swaps files, relaunches
        threading.Timer(0.6, webview.windows[0].destroy).start()

    # ---- channel watch ----

    @staticmethod
    def _is_channel(url):
        return "youtube.com" in url and any(p in url for p in ("/@", "/channel/", "/c/", "/user/"))

    def _watch_urls(self, w, tabs=None):
        if self._is_channel(w["url"]):
            return [w["url"].rstrip("/") + "/" + t for t in (tabs or w.get("tabs", ["videos"]))]
        return [w["url"]]

    def add_watch(self, url, title, mode, quality, subs, auto, tabs=None):
        url = re.sub(r"/(videos|shorts|streams|live)/?$", "", url.strip().rstrip("/"))
        tabs = [t for t in (tabs or []) if t in ("videos", "shorts", "streams")] or ["videos"]
        w = {
            "id": max((x["id"] for x in self.watches), default=-1) + 1,
            "url": url,
            "title": title or url,
            "mode": mode,
            "quality": int(quality or 1080),
            "subs": bool(subs),
            "auto": bool(auto),
            "tabs": tabs,
            "thumb": "",
            "seen": [],
            "pending": [],
            "checked": "",
            "error": "",
        }
        self.watches.append(w)
        # seed: current uploads count as already-seen, only future ones trigger
        threading.Thread(target=self._check_watch, args=(w, True), daemon=True).start()
        return w["id"]

    def remove_watch(self, watch_id):
        self.watches = [w for w in self.watches if w["id"] != watch_id]
        self._save()

    def update_watch(self, watch_id, mode, quality, subs, tabs):
        w = self._watch(watch_id)
        if not w:
            return
        added = [t for t in tabs if t not in w.get("tabs", ["videos"])]
        w.update({"mode": mode, "quality": int(quality or 1080), "subs": bool(subs), "tabs": tabs})
        if added and self._is_channel(w["url"]):
            # seed newly enabled tabs so existing uploads don't flood pending/auto-download
            threading.Thread(target=self._seed_tabs, args=(w, added), daemon=True).start()
        self._save()

    def _seed_tabs(self, w, tabs):
        for u in self._watch_urls(w, tabs):
            try:
                info = self._flat_info(u, items='1:15')
                for e in info.get("entries", []) or []:
                    vid = e and (e.get("id") or e.get("url"))
                    if vid and vid not in w["seen"]:
                        w["seen"].append(vid)
            except Exception:
                pass
        self._save()

    def toggle_auto(self, watch_id):
        w = self._watch(watch_id)
        if w:
            w["auto"] = not w["auto"]
            self._save()

    def check_watch_now(self, watch_id):
        w = self._watch(watch_id)
        if w:
            threading.Thread(target=self._check_watch, args=(w,), daemon=True).start()

    def download_pending(self, watch_id, url):
        w = self._watch(watch_id)
        if not w:
            return
        for p in list(w["pending"]):
            if url in ("*", p["url"]):
                self.enqueue(p["url"], w["mode"], w["quality"], p["title"], w["subs"], folder=w["title"])
                w["pending"].remove(p)
        self._save()

    def get_watches(self):
        return self.watches

    # ---- internals ----

    def _flat_info(self, url, items=None, progress=False):
        opts = {
            "quiet": True,
            "noplaylist": False,
            "extract_flat": "in_playlist",
            "js_runtimes": JS_RUNTIMES,
            # preview needs titles/thumbs/heights only — skipping manifest fetches saves ~0.5s
            "extractor_args": {"youtube": {"skip": ["dash", "hls"]}},
        }
        if items:
            opts["playlist_items"] = items  # "start:end" range
        if progress:
            opts["logger"] = _FetchLogger(self)
        return self._extract(opts, url, download=False)

    def get_fetch_pages(self):
        return self.fetch_pages

    def _extract(self, opts, url, download):
        if self.settings.get("cookies"):
            # Edge's YouTube login; Chrome's app-bound encryption blocks yt-dlp
            opts["cookiesfrombrowser"] = ("edge",)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=download)
        except yt_dlp.utils.DownloadError as e:
            if "Sign in to confirm" not in str(e):
                raise
            # ponytail: IP bot-check fallback — mweb/android get through but cap ~360p;
            # browser cookies (signed-in YouTube session) would restore full quality
            opts = {**opts, "extractor_args": {"youtube": {"player_client": ["mweb", "android"]}}}
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=download)

    def _job(self, job_id):
        return next((j for j in self.jobs if j["id"] == job_id), None)

    def _watch(self, watch_id):
        return next((w for w in self.watches if w["id"] == watch_id), None)

    def _watch_loop(self):
        while True:
            time.sleep(self.settings["watch_interval"])
            for w in list(self.watches):
                self._check_watch(w)

    def _notify(self, title, msg):
        if not self.settings.get("notify", True):
            return
        try:
            if self.tray:
                self.tray.notify(msg, title)
        except Exception:
            pass

    def _check_watch(self, w, seed=False):
        errs = []
        new = 0
        for u in self._watch_urls(w):
            try:
                # channels list newest first (15 is plenty); playlists append at the
                # BOTTOM, so a synced playlist must be fetched in full
                items = "1:15" if self._is_channel(w["url"]) else None
                info = self._flat_info(u, items=items)
            except Exception as e:
                errs.append(str(e)[:120])
                continue
            if seed and info.get("title"):
                w["title"] = info["title"]
            if not w.get("thumb"):  # channel avatar; also backfills pre-avatar watches
                thumbs = info.get("thumbnails") or []
                pick = next((t for t in thumbs if t.get("id") == "avatar_uncropped"), None)
                w["thumb"] = (pick or (thumbs[-1] if thumbs else {})).get("url", "")
            for e in info.get("entries", []) or []:
                if not e:
                    continue
                vid = e.get("id") or e.get("url")
                if not vid or vid in w["seen"]:
                    continue
                w["seen"].append(vid)
                if seed:
                    continue
                item = {"url": e.get("url") or e.get("webpage_url"), "title": e.get("title", "?")}
                new += 1
                if w["auto"]:
                    self.enqueue(item["url"], w["mode"], w["quality"], item["title"], w["subs"], folder=w["title"])
                else:
                    w["pending"].append(item)
        if new:
            verb = "downloading" if w["auto"] else "new"
            self._notify(w["title"], f"{new} {verb} video{'s' if new != 1 else ''}")
        w["seen"] = w["seen"][-3000:]  # big enough that a large synced playlist never re-triggers
        w["checked"] = time.strftime("%H:%M")
        w["error"] = errs[0] if errs else ""
        self._save()

    def _worker(self):
        while True:
            job = self.q.get()
            while self.paused and not job["cancel"]:  # running jobs finish; queued ones wait here
                time.sleep(0.5)
            try:
                if job["cancel"]:
                    job["status"] = "cancelled"
                else:
                    self._download(job)
                    job["status"] = "done"
                    job["percent"] = 100
                    if job["url"].startswith("http"):  # local conversions don't belong in the memory
                        self.downloaded[self._dlkey(job["url"])] = time.strftime("%Y-%m-%d")
                    if len(self.downloaded) > 5000:  # ponytail: FIFO trim; enough for years
                        for k in list(self.downloaded)[:-5000]:
                            del self.downloaded[k]
            except Exception as e:
                if job["cancel"]:
                    job["status"] = "cancelled"
                else:
                    job["status"] = "error"
                    job["error"] = str(e).split(";")[0][:200]
            job["speed"] = ""
            job["eta"] = ""
            if job["status"] in ("done", "error"):
                self._batch[job["status"]] += 1
            # one summary toast when the queue drains, not one per job
            if all(j["status"] in TERMINAL for j in self.jobs):
                d, e = self._batch["done"], self._batch["error"]
                self._batch = {"done": 0, "error": 0}
                if d or e:
                    msg = f"{d} download{'s' if d != 1 else ''} finished" + (f", {e} failed" if e else "")
                    self._notify("Snap", msg)
            self._save()

    def _convert(self, job):
        """Local file → chosen format with the bundled ffmpeg. Video modes remux (-c copy)."""
        src = job["url"]
        dst = os.path.join(self.out_dir, os.path.splitext(os.path.basename(src))[0] + "." + job["mode"])
        os.makedirs(self.out_dir, exist_ok=True)
        job["status"] = "converting"
        args = [FFMPEG, "-y", "-i", src]
        if job.get("clip_a"):
            args += ["-ss", job["clip_a"]]
        if job.get("clip_b"):
            args += ["-to", job["clip_b"]]
        if job["mode"] in VIDEO_MODES:
            # ponytail: remux only — re-encode fallback if incompatible codecs turn up often
            args += ["-c", "copy"]
        else:
            args += ["-vn"] + {
                "mp3": ["-c:a", "libmp3lame", "-b:a", f"{self.settings['mp3_bitrate']}k"],
                "m4a": ["-c:a", "aac", "-b:a", "192k"],
                "opus": ["-c:a", "libopus", "-b:a", "160k"],
                "flac": ["-c:a", "flac"],
                "wav": ["-c:a", "pcm_s16le"],
            }[job["mode"]]
        args.append(dst)
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                creationflags=0x08000000)
        while proc.poll() is None:
            if job["cancel"]:
                proc.kill()
                raise yt_dlp.utils.DownloadCancelled("cancelled by user")
            time.sleep(0.3)
        if proc.returncode:
            err = (proc.stderr.read() or b"").decode(errors="replace").strip().splitlines()
            raise RuntimeError("ffmpeg: " + (err[-1] if err else "conversion failed"))
        job["filepath"] = dst
        if job["mode"] in TAGGABLE and self.settings.get("mb_tag", True):
            try:
                tagging.tag_from_title(dst, job["title"])
            except Exception:
                pass

    def _download(self, job):
        if os.path.isfile(job["url"]):
            return self._convert(job)

        def hook(d):
            if job["cancel"]:
                raise yt_dlp.utils.DownloadCancelled("cancelled by user")
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if total:
                    job["percent"] = round(d.get("downloaded_bytes", 0) / total * 100, 1)
                job["speed"] = d.get("_speed_str", "").strip()
                job["eta"] = d.get("_eta_str", "").strip()
                job["status"] = "downloading"
            elif d["status"] == "finished":
                job["status"] = "converting"
                job["speed"] = ""
                job["eta"] = ""

        out = self.out_dir
        if job.get("folder") and self.settings.get("organize", True):
            out = os.path.join(out, job["folder"])
        os.makedirs(out, exist_ok=True)
        opts = {
            "quiet": True,
            "noprogress": True,
            "noplaylist": True,
            "outtmpl": os.path.join(out, "%(title)s.%(ext)s"),
            "progress_hooks": [hook],
            "ffmpeg_location": FFMPEG,
            "js_runtimes": JS_RUNTIMES,
            # parallel fragments speed up DASH/HLS/SABR streams; no effect on plain https
            "concurrent_fragment_downloads": 4,
        }
        if job["subs"]:
            langs = self.settings.get("sub_lang", "en").split(",")
            opts.update(
                {"writesubtitles": True, "writeautomaticsub": True, "subtitleslangs": langs}
            )
        start, end = _ts(job.get("clip_a")), _ts(job.get("clip_b"))
        if start is not None or end is not None:
            opts["download_ranges"] = yt_dlp.utils.download_range_func(
                None, [(start or 0, end if end is not None else float("inf"))]
            )
            opts["force_keyframes_at_cuts"] = True  # exact cuts (re-encodes at the edges)
        if job["mode"] in VIDEO_MODES:
            q = job["quality"]
            if job["mode"] == "mp4":
                opts["format"] = (
                    f"bestvideo[height<={q}][ext=mp4]+bestaudio[ext=m4a]"
                    f"/bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"
                )
            else:
                # MKV takes any codec — the only way to get 4K+ VP9/AV1 streams
                opts["format"] = f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"
            opts["merge_output_format"] = job["mode"]
        else:
            opts["format"] = AUDIO_FMT.get(job["mode"], "bestaudio/best")
            extract = {"key": "FFmpegExtractAudio", "preferredcodec": job["mode"]}
            if job["mode"] == "mp3":
                extract["preferredquality"] = str(self.settings["mp3_bitrate"])
            if job["mode"] in TAGGABLE:
                # "Artist - Song" in the title → artist/title tags; YT Music tags pass through
                # ponytail: title-pattern heuristic — AcoustID fingerprinting if it misidentifies
                opts["writethumbnail"] = True
                opts["postprocessors"] = [
                    {
                        "key": "MetadataParser",
                        "when": "pre_process",
                        "actions": [
                            (
                                MetadataParserPP.Actions.INTERPRET,
                                "title",
                                "%(meta_artist)s - %(meta_title)s",
                            )
                        ],
                    },
                    extract,
                    {"key": "FFmpegMetadata"},
                    {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
                    {"key": "EmbedThumbnail"},
                ]
            else:
                # WAV is tagless PCM — no metadata/cover-art postprocessors
                opts["postprocessors"] = [extract]
        info = self._extract(opts, job["url"], download=True)
        rd = (info or {}).get("requested_downloads") or [{}]
        job["filepath"] = rd[0].get("filepath", "")
        if not job["thumb"]:
            job["thumb"] = (info or {}).get("thumbnail") or ""
        if job["mode"] in TAGGABLE and self.settings.get("mb_tag", True) and job["filepath"]:
            try:
                tagging.tag_from_title(job["filepath"], job["title"])
            except Exception:
                pass  # best-effort — the yt-dlp tags are already written


if __name__ == "__main__":
    if not winshell.claim_single_instance():
        sys.exit(0)  # the running instance was poked awake (with any snap:// URL)

    try:
        winshell.register_protocol()
    except Exception:
        pass

    api = Api()
    api.pending_url = winshell.url_from_argv()  # snap:// link that launched this instance
    window = webview.create_window(
        "Snap", os.path.join(BASE, "ui.html"),
        js_api=api, width=860, height=720, min_size=(600, 480),
        hidden="--tray" in sys.argv,  # autostart launches straight to the tray
    )

    def on_closing():
        if api.settings.get("tray", True):
            window.hide()
            return False  # cancel the close — keep watches/downloads running in the tray

    def on_minimized():
        if api.settings.get("min_tray", False):
            # deferred: hide() from inside the event runs on the UI thread mid-minimize
            threading.Timer(0.15, window.hide).start()

    def on_start():
        api.tray = winshell.make_tray(window, api, os.path.join(BASE, "icon.png"))
        threading.Thread(target=winshell.show_watcher, args=(window,), daemon=True).start()

    window.events.closing += on_closing
    window.events.minimized += on_minimized
    webview.start(on_start)
    if api.tray:
        api.tray.stop()
