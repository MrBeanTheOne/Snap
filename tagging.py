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
    elif ext in (".m4a", ".mp4"):
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


def _clean(t):
    """Video title → bare 'Artist - Song': drop '| channel' tails, brackets, karaoke junk."""
    t = re.sub(r"[\(\[][^)\]]*[\)\]]", "", t.split("|")[0])
    t = re.sub(r"(?i)\bin the style of\b.*|\bkaraoke( version)?\b|\binstrumental\b|\b(with )?lyrics\b", "", t)
    return " ".join(t.split()).strip(" -")


def tag_from_title(path, video_title):
    """Parse the video title, look it up, tag the file and rename it 'Artist - Title.ext'.
    Karaoke versions keep a '(Karaoke)' marker + genre so they never blend with originals.
    Returns the file's final path."""
    karaoke = "karaoke" in video_title.lower()
    clean = _clean(video_title)
    a, _, b = clean.partition(" - ")
    style = re.search(r"(?i)in the style of ([^)\]|]+)", video_title)  # Stingray/Party Tyme
    # KaraFun puts 'Song - Artist', Sing King 'Artist - Song': try both, MB's score picks
    tries = [(style.group(1), clean)] if style else []
    tries += [(a, b), (b, a)] if b else [("", clean)]
    tags = None
    for artist, title in tries:
        tags = mb_lookup(artist.strip(), title.strip())
        if tags:
            break
    if karaoke:
        tags = dict(tags or {}, genre="Karaoke")
        if "title" in tags:
            tags["title"] += " (Karaoke)"
    if not tags:
        return path
    apply_tags(path, tags)
    if "artist" not in tags or "title" not in tags:
        return path  # no confident match — keep the original name
    name = "".join("_" if c in '<>:"/\\|?*' else c for c in f"{tags['artist']} - {tags['title']}")
    new = os.path.join(os.path.dirname(path), name.strip(" .")[:150] + os.path.splitext(path)[1])
    if os.path.exists(new):
        return path  # never overwrite (same song from another channel, or same name)
    os.rename(path, new)
    return new
