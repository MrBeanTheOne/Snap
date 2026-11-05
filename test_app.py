"""Self-check for the 2026-11-03 audit fixes (findings 2-18). Run: python test_app.py"""
import hashlib
import http.client
import io
import json
import os
import ssl
import subprocess
import tempfile
import threading
import time
import types
import urllib.error
import urllib.parse
import zipfile

os.environ["APPDATA"] = tempfile.mkdtemp()  # before import: never touch the real state/pkgs
# the update stage + swap batch live in TEMP: isolate it, and make it a hostile path
os.environ["TEMP"] = os.path.join(tempfile.mkdtemp(), "Éric 100%")
os.makedirs(os.environ["TEMP"])

import app  # noqa: E402

# no background threads: workers would really download, the loop/update check hit the network
REAL_LOOP = app.Api._watch_loop
app.Api._worker = app.Api._watch_loop = app.Api._check_update_bg = lambda self: None


class SyncThread:  # run the watch threads inline so the test is deterministic
    def __init__(self, target, args=(), daemon=None):
        self.run = lambda: target(*args)

    def start(self):
        self.run()


# only app's view of threading: library's server and subprocess need real threads
app.threading = types.SimpleNamespace(Thread=SyncThread, Lock=threading.Lock, Timer=threading.Timer)
feed = {}  # url → list of video ids, an Exception to raise, or None (breaks _check_watch)


def fake_flat_info(url, items=None, progress=False):
    r = feed[url]
    if isinstance(r, Exception):
        raise r
    return r and {"title": "Src", "entries": [{"id": v, "url": f"https://youtu.be/{v}", "title": v} for v in r]}


api = app.Api()
api._flat_info = fake_flat_info
backlog = [f"old{i:08}" for i in range(15)]

# 2. playlist, auto on: a failed seed must not arm the watch
pl = "https://www.youtube.com/playlist?list=PLx"
feed[pl] = OSError("network down")
wid = api.add_watch(pl, "", "mp3", 1080, False, True)
w = api._watch(wid)
assert w["seeded"] == [] and w["seen"] == [] and not api.jobs, w
feed[pl] = backlog
api._check_watch(w)  # the retry seeds — the backlog is NOT new
assert w["seeded"] == [pl] and len(w["seen"]) == 15 and not api.jobs and w["title"] == "Src", w
feed[pl] = backlog + ["new00000001"]
api._check_watch(w)
assert [j["url"] for j in api.jobs] == ["https://youtu.be/new00000001"], api.jobs

# 2. channel: a newly enabled tab seeds on its own, even after a failed first attempt
ch = "https://www.youtube.com/@chan"
feed[ch + "/videos"] = backlog
cid = api.add_watch(ch, "Chan", "mp4", 1080, False, True, ["videos"])
c = api._watch(cid)
feed[ch + "/shorts"] = OSError("tab fetch failed")
api.update_watch(cid, "mp4", 1080, False, ["videos", "shorts"])
assert c["seeded"] == [ch + "/videos"] and len(api.jobs) == 1, c
feed[ch + "/shorts"] = [f"sht{i:08}" for i in range(15)]
api._check_watch(c)
assert ch + "/shorts" in c["seeded"] and len(api.jobs) == 1, api.jobs
api.update_watch(cid, "mp4", 1080, False, ["videos"])  # dropping a tab disarms it
assert c["seeded"] == [ch + "/videos"], c

# 6. a watch already being checked is skipped, not checked twice
feed[pl] = backlog + ["new00000001", "new00000002"]
api._wlocks[wid].acquire()
api._check_watch(w)
api._wlocks[wid].release()
assert len(api.jobs) == 1, api.jobs

# 5. one watch blowing up must not stop the loop from checking the rest
class Stop(Exception):
    pass


def stop(_):
    raise Stop


feed[pl], feed[ch + "/videos"] = None, backlog + ["new00000003"]  # None → AttributeError in the check
real_time = app.time
app.time = types.SimpleNamespace(strftime=real_time.strftime, sleep=stop)  # one loop pass, then out
try:
    REAL_LOOP(api)
except Stop:
    pass
finally:
    app.time = real_time
assert w["error"] and len(api.jobs) == 2, (w["error"], api.jobs)

# 6. concurrent enqueues get unique ids
ts = [threading.Thread(target=lambda: [api.enqueue(f"u{i}", "mp3", 0, "t") for i in range(25)]) for _ in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
ids = [j["id"] for j in api.jobs]
assert len(ids) == len(set(ids)) == 202, len(ids)

# 3 + 4. the queue survives a restart; the save went through temp + replace
api.jobs[1]["status"] = "cancelling"
api.jobs[1]["cancel"] = True
api.jobs[2]["status"] = "done"
api._save_raw()
assert not os.path.exists(app.STATE_FILE + ".tmp")
again = app.Api()
assert again.q.qsize() == 200 and again.jobs[-1]["status"] == "queued", again.q.qsize()
assert sorted(j["status"] for j in again.jobs)[:2] == ["cancelled", "done"]

# 4. an unreadable state file is set aside, never overwritten by the next save
with open(app.STATE_FILE, "w") as f:
    f.write('{"watches": [')
app.Api()._save_raw()
with open(app.STATE_FILE + ".bad") as f:
    assert f.read() == '{"watches": ['

# 7 + 8. quotes are escaped, MB goes through updater's opener, and an MB failure
# still writes the karaoke marker + title-order tags
queries, applied = [], []


def mb_down(req, timeout):
    queries.append(urllib.parse.unquote(req.full_url.split("query=")[1]))
    raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)


app.updater._urlopen, real_urlopen = mb_down, app.updater._urlopen
app.tagging.apply_tags = lambda p, t: applied.append(t)
song = os.path.join(tempfile.mkdtemp(), "x.mp4")
open(song, "w").close()
out = app.tagging.tag_from_title(song, 'David Bowie - "Heroes" (Karaoke Version)')
assert queries == [r'recording:"\"Heroes\"" AND artist:"David Bowie"'], queries  # one try, then fallback
assert applied == [{"artist": "David Bowie", "title": '"Heroes" (Karaoke)', "genre": "Karaoke"}], applied
assert os.path.basename(out) == "David Bowie - _Heroes_ (Karaoke).mp4", out
app.updater._urlopen = real_urlopen

# 9. a certifi verify failure retries on the system store; other errors don't
calls = []


def fake_open(req, timeout, context=None):
    calls.append(context)
    if context is not None:
        raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer"))
    return "ok"


app.updater.urllib.request.urlopen, real_open = fake_open, app.updater.urllib.request.urlopen
assert app.updater._urlopen("https://x", 1) == "ok" and calls[0] is not None and calls[1] is None
app.updater.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("refused"))
try:
    app.updater._urlopen("https://x", 1)
    raise AssertionError("non-cert error must not retry silently")
except urllib.error.URLError:
    pass
app.updater.urllib.request.urlopen = real_open

# 11. downloads are sha256-checked before anything is unpacked
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("yt_dlp/__init__.py", "")
whl = buf.getvalue()


def pypi(digest):
    meta = {"urls": [{"filename": "x.whl", "url": "W", "digests": {"sha256": digest}}]}
    return lambda u, timeout: io.BytesIO(json.dumps(meta).encode() if u.endswith("/json") else whl)


app.updater._urlopen = pypi("0" * 64)
assert "sha256" in app.updater.update_ytdlp().get("error", "")
assert not os.path.exists(app.updater.PKG_DIR + ".new")
app.updater._urlopen = pypi(hashlib.sha256(whl).hexdigest())
assert app.updater.update_ytdlp() == {"ok": True}


class Resp(io.BytesIO):
    headers = {}


buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("Snap/Snap.exe", os.urandom(1 << 20))  # incompressible: clears the 1MB truncation guard
rel = buf.getvalue()
app.updater._urlopen = lambda u, timeout: Resp(rel)
try:
    app.updater._stage_release("Z", "", "sha256:" + "0" * 64)
    raise AssertionError("bad digest must not unpack")
except RuntimeError as e:
    assert "sha256" in str(e), e
assert app.updater._stage_release("Z", "", "sha256:" + hashlib.sha256(rel).hexdigest()).endswith("Snap")
app.updater._urlopen = real_urlopen

# 10 + 11. the swap: non-ASCII/% paths survive, /mir stays inside _internal, unins000.* survive
inst = os.path.join(os.environ["TEMP"], "inst", "Snap")
stage = os.path.join(os.environ["TEMP"], "stage")
src = os.path.join(stage, "Snap")
for d, files in ((inst, {"Snap.exe": "old build", "unins000.exe": "u", "_internal/old.py": ""}),
                 (src, {"Snap.exe": "new", "_internal/new.py": ""})):
    for n, body in files.items():
        os.makedirs(os.path.dirname(os.path.join(d, n)), exist_ok=True)
        with open(os.path.join(d, n), "w") as f:
            f.write(body)
app.updater._spawn_apply(src, inst, exe="")  # exe="" → no relaunch
for _ in range(150):
    if not os.path.exists(stage):
        break
    time.sleep(0.2)
assert not os.path.exists(stage), "swap batch never finished"
assert os.listdir(os.path.join(inst, "_internal")) == ["new.py"]
with open(os.path.join(inst, "Snap.exe")) as f:
    assert f.read() == "new"
assert os.path.exists(os.path.join(inst, "unins000.exe"))

# 12. memory keeps the last 200 finished jobs, every unfinished one, in order
for j in api.jobs[:-1]:
    j["status"] = "done"
api._trim_jobs()
assert len(api.jobs) == 201 and api.jobs[-1]["status"] == "queued" and api.jobs[0]["id"] == 1, len(api.jobs)

# 13 + 14a. covers: negative-cached, and /media/ + /cover/ can't reach a sibling folder
lib = os.path.join(tempfile.mkdtemp(), "Snap")
os.makedirs(lib)
with open(os.path.join(lib, "a.mp3"), "wb") as f:
    f.write(b"not really an mp3")
calls = []
real_art = app.library._embedded_art
app.library._embedded_art = lambda p, e: calls.append(p)
assert app.library._cover(os.path.join(lib, "a.mp3"), ".mp3", None) == ""
assert app.library._cover(os.path.join(lib, "a.mp3"), ".mp3", None) == "" and len(calls) == 1, calls
app.library._embedded_art = real_art
app.library._GET_OUT = lambda: lib
assert app.library._resolve("../Snap-evil/x") is None and app.library._resolve("../../x") is None
assert app.library._resolve("sub/a.mp3") == os.path.join(lib, "sub", "a.mp3")

# 14b + c. the media server needs the session token, and answers bad Ranges with 416
port = app.library.ensure_server(lambda: lib, None)


def get(path, rng=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path, headers={"Range": rng} if rng else {})
    r = c.getresponse()
    return r.status, r.read()


tok = app.library._TOKEN
assert get("/media/a.mp3")[0] == 404  # what another web page / DNS rebinding would try
assert get(f"/{tok}/media/a.mp3") == (200, b"not really an mp3")
assert get(f"/{tok}/media/a.mp3", "bytes=0-2") == (206, b"not")
assert get(f"/{tok}/media/a.mp3", "bytes=-3") == (206, b"mp3")
for bad in ("bytes=0-1,4-5", "bytes=x-1", "bytes=999-", "bytes=-"):
    assert get(f"/{tok}/media/a.mp3", bad)[0] == 416, bad
assert get(f"/{tok}/media/a.mp3")[0] == 200  # server survived all of the above

# 15. converting never clobbers (here: the source itself), and a tagged file keeps its tags
out = tempfile.mkdtemp()
api.out_dir = out
tagged, plain = os.path.join(out, "tagged.mp3"), os.path.join(out, "plain.flac")
subprocess.run([app.FFMPEG, "-f", "lavfi", "-i", "sine=d=1", "-metadata", "artist=Me",
                "-metadata", "title=Mine", tagged], capture_output=True, check=True)
subprocess.run([app.FFMPEG, "-f", "lavfi", "-i", "sine=d=1", plain], capture_output=True, check=True)
titles = []
real_tag = app.tagging.tag_from_title
app.tagging.tag_from_title = lambda p, t, r="": titles.append(t) or p
for src in (tagged, plain):
    api._convert({"url": src, "mode": "mp3", "title": os.path.basename(src), "cancel": False})
assert os.path.exists(os.path.join(out, "tagged (1).mp3")) and os.path.getsize(tagged) > 0
assert titles == ["plain"], titles  # tagged.mp3 skipped; no '.flac' in the guess
app.tagging.tag_from_title = real_tag

# 16. a redo replaces the file the same video made before; subtitle sidecars follow the rename
app.updater._urlopen = mb_down  # no MB: the title-order fallback names it
d = tempfile.mkdtemp()
prev = os.path.join(d, "David Bowie - Heroes.mp3")
for n in ("Heroes video.mp3", "Heroes video.en.vtt", "David Bowie - Heroes.mp3"):
    open(os.path.join(d, n), "w").close()
fresh_dl = os.path.join(d, "Heroes video.mp3")
assert app.tagging.tag_from_title(fresh_dl, "David Bowie - Heroes") == fresh_dl  # not a redo: keep both
assert app.tagging.tag_from_title(fresh_dl, "David Bowie - Heroes", prev) == prev
assert sorted(os.listdir(d)) == ["David Bowie - Heroes.en.vtt", "David Bowie - Heroes.mp3"], os.listdir(d)
app.updater._urlopen = real_urlopen

# 17. a synced playlist over 3000 items doesn't re-trigger its oldest ones
big = "https://www.youtube.com/playlist?list=PLbig"
feed[big] = [f"big{i:08}" for i in range(3500)]
b = api._watch(api.add_watch(big, "", "mp3", 1080, False, False))
api._check_watch(b)
assert len(b["seen"]) == 3500 and b["pending"] == [], (len(b["seen"]), len(b["pending"]))

# 18. paging counts raw items (unavailable videos come back as None); any scheme is a link
asked = []
api._flat_info = lambda u, items=None, progress=False: asked.append(u) or {
    "_type": "playlist", "title": "T", "entries": [{"url": "a", "title": "a"}, None, {"url": "c", "title": "c"}]}
r = api.get_info("https://www.youtube.com/@chan", "videos", 6, 3)
assert r["more"] and r["next"] == 9 and len(r["entries"]) == 2, r
api.get_info("ftp://example.com/x.mp4")
assert asked[-1] == "ftp://example.com/x.mp4", asked  # not a YouTube search for 'ftp://…'

# 19. same title, different video: the newcomer gets its id instead of being skipped as
# "already downloaded"; the video that owns the name (this session or history) keeps it
d = tempfile.mkdtemp()
t = os.path.join(d, "%(title)s.%(ext)s")
v = lambda vid, title="sample this | x": {"title": title, "mode": "mp4", "url": f"https://youtu.be/{vid}"}
assert api._unique_name(v("AAAAAAAAAAA"), t) == t
assert api._unique_name(v("BBBBBBBBBBB"), t) == os.path.join(d, "%(title)s [%(id)s].%(ext)s")
assert api._unique_name(v("AAAAAAAAAAA"), t) == t  # the same video again: plain name, as before
api.jobs.append({"id": 9000, "status": "done", "url": "https://youtu.be/CCCCCCCCCCC", "title": "old",
                 "filepath": os.path.join(d, "Old Title.mp4")})
assert api._unique_name(v("DDDDDDDDDDD", "Old Title"), t) != t  # history owns it
assert api._unique_name(v("CCCCCCCCCCC", "Old Title"), t) == t

# 20. retrying a failed download replaces its row; redoing a finished one keeps it
fid = api.enqueue("https://youtu.be/EEEEEEEEEEE", "mp3", 1080, "f")
api._job(fid)["status"] = "error"
n = len(api.jobs)
api.redo(fid)
assert api._job(fid) is None and len(api.jobs) == n and api.jobs[-1]["url"].endswith("EEEEEEEEEEE")
did = api.enqueue("https://youtu.be/FFFFFFFFFFF", "mp3", 1080, "g")
api._job(did)["status"] = "done"
api.redo(did)
assert api._job(did) and len(api.jobs) == n + 2
print("ok")
