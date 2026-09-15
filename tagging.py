"""Best-effort music tagging: MusicBrainz text lookup applied with mutagen.
ponytail: AcoustID fingerprinting (needs an API key + bundled fpcalc) is the upgrade
if title-based matching ever misses too often."""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import updater

_LOCK = threading.Lock()
_LAST = [0.0]
_UA = f"Snap/{updater.APP_VERSION} (https://github.com/{updater.REPO})"


def mb_lookup(artist, title):
    """Best MusicBrainz match for a track, or None. Globally throttled to their 1 req/s limit."""
    q = f'recording:"{title}"' + (f' AND artist:"{artist}"' if artist else "")
    url = "https://musicbrainz.org/ws/2/recording?fmt=json&limit=1&query=" + urllib.parse.quote(q)
    with _LOCK:
        wait = 1.1 - (time.time() - _LAST[0])
        if wait > 0:
            time.sleep(wait)
        _LAST[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            recs = json.load(r).get("recordings") or []
    except urllib.error.HTTPError as e:
        if e.code != 503:  # MB throttles with transient 503s — one retry
            raise
        time.sleep(2)
        with urllib.request.urlopen(req, timeout=10) as r:
            recs = json.load(r).get("recordings") or []
    if not recs or int(recs[0].get("score", 0)) < 90:
        return None
    rec = recs[0]
    rel = (rec.get("releases") or [{}])[0]
    tags = {
        "title": rec.get("title"),
        "artist": (rec.get("artist-credit") or [{}])[0].get("name"),
        "album": rel.get("title"),
        "date": (rel.get("date") or "")[:4],
    }
    return {k: v for k, v in tags.items() if v} or None


def apply_tags(path, tags):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        from mutagen.easyid3 import EasyID3
        f = EasyID3(path)
    elif ext == ".m4a":
        from mutagen.easymp4 import EasyMP4
        f = EasyMP4(path)
    elif ext == ".flac":
        from mutagen.flac import FLAC
        f = FLAC(path)
    elif ext == ".opus":
        from mutagen.oggopus import OggOpus
        f = OggOpus(path)
    else:
        return
    for k, v in tags.items():
        f[k] = v
    f.save()


def tag_from_title(path, video_title):
    """Parse 'Artist - Title (junk)' from a video title, look it up, tag the file."""
    clean = re.sub(r"[\(\[][^)\]]*[\)\]]", "", video_title).strip()
    artist, _, title = clean.partition(" - ")
    if not title:
        artist, title = "", clean
    tags = mb_lookup(artist.strip(), title.strip())
    if tags:
        apply_tags(path, tags)
