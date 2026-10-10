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
    url = "https://musicbrainz.org/ws/2/recording?fmt=json&limit=10&query=" + urllib.parse.quote(q)
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
    top = recs[0]
    # a song comes back as many recordings (studio + every live bootleg): take album/year
    # from the earliest one with an official non-live, non-compilation release
    best = min((r for r in recs if int(r.get("score", 0)) >= 90 and _credit(r) == _credit(top)
                and r.get("first-release-date") and _studio(r)),
               key=lambda r: r["first-release-date"], default=None)
    tags = {"title": top.get("title"), "artist": _credit(top)}
    if best:  # none = leave album/year empty rather than tag a bootleg
        tags.update(album=_studio(best)[0].get("title"), date=best["first-release-date"][:4])
    return {k: v for k, v in tags.items() if v} or None


def _credit(rec):
    """'Mariah Carey feat. JAY-Z' — every credited artist with MB's join phrases."""
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in rec.get("artist-credit") or []).strip()


def _studio(rec):
    return [r for r in rec.get("releases") or [] if r.get("status") == "Official"
            and not (r.get("release-group") or {}).get("secondary-types")]


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
    t = re.sub(r"\s+[•–—~]\s+", " - ", t)  # CC Karaoke uses 'Artist • Song'
    return " ".join(t.split()).strip(" -")


def tag_from_title(path, video_title):
    """Parse the video title, look it up, tag the file and rename it 'Artist - Title.ext'.
    Karaoke versions keep a '(Karaoke)' marker + genre so they never blend with originals.
    Returns the file's final path."""
    low = video_title.lower()
    karaoke = "karaoke" in low or "instrumental" in low  # backing tracks either way
    clean = _clean(video_title)
    a, _, b = clean.partition(" - ")
    a, b = a.strip(), b.strip()
    style = re.search(r"(?i)in the style of ([^)\]|]+)", video_title)  # Stingray/Party Tyme
    # KaraFun puts 'Song - Artist', Sing King 'Artist - Song': try both, MB's score picks
    tries = [(style.group(1).strip(), clean)] if style else []
    tries += [(a, b), (b, a)] if b else [("", clean)]
    tags = None
    for artist, title in tries:
        tags = mb_lookup(artist, title)
        if tags:
            break
    if not tags and tries[0][0]:
        # MB lacks most brand-new releases: trust the title's own order instead
        # ponytail: KaraFun is the only reversed channel seen so far — add others here
        artist, title = (b, a) if "karafun" in low and not style else tries[0]
        tags = {"artist": artist, "title": title}
    if karaoke:
        tags = dict(tags or {}, genre="Karaoke")
        if "title" in tags:
            tags["title"] += " (Karaoke)"
    if not tags:
        return path
    apply_tags(path, tags)
    if "artist" not in tags or "title" not in tags:
        return path  # title had no artist — keep the original name
    name = "".join("_" if c in '<>:"/\\|?*' else c for c in f"{tags['artist']} - {tags['title']}")
    new = os.path.join(os.path.dirname(path), name.strip(" .")[:150] + os.path.splitext(path)[1])
    if os.path.exists(new):
        return path  # never overwrite (same song from another channel, or same name)
    os.rename(path, new)
    return new
