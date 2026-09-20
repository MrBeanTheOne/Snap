"""Self-update plumbing: yt-dlp from PyPI wheels, the app itself from GitHub."""
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import webbrowser
import zipfile

APP_VERSION = "4.10.10"
REPO = "MrBeanTheOne/Snap"
STATE_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Snap")
PKG_DIR = os.path.join(STATE_DIR, "pkgs")  # in-app yt-dlp updates land here, shadowing the bundled copy
PKG_STAMP = os.path.join(PKG_DIR, ".app_version")  # which build this override was fetched for
# ponytail: one update at a time, so a module global is enough — the UI polls it
PROGRESS = {"pct": 0, "stage": ""}


def apply_pending():
    """Finish a pending yt-dlp update, and retire one this build has outgrown.
    Must run BEFORE yt_dlp is imported."""
    if os.path.isdir(PKG_DIR + ".new"):
        shutil.rmtree(PKG_DIR, ignore_errors=True)
        os.replace(PKG_DIR + ".new", PKG_DIR)
        try:
            with open(PKG_STAMP, "w", encoding="ascii") as f:
                f.write(APP_VERSION)
        except OSError:
            pass  # unstamped just means the next launch re-checks against the bundle
    elif os.path.isdir(PKG_DIR) and _stamp() != APP_VERSION:
        # Every release bundles the newest yt-dlp CI could pip install, so an override
        # fetched for an older build is the stale one — left on sys.path it silently
        # DOWNGRADES yt-dlp on every app update. Retire it; the bundled copy wins.
        shutil.rmtree(PKG_DIR, ignore_errors=True)
    if os.path.isdir(PKG_DIR):
        sys.path.insert(0, PKG_DIR)


def _stamp():
    try:
        with open(PKG_STAMP, encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        return ""  # pre-4.10.9 override: no stamp, so it gets retired once


def _v(s):
    # "2026.08.19" vs PyPI's "2026.8.19" — compare numerically
    return [int(p) for p in s.split(".") if p.isdigit()]


def check_update():
    import yt_dlp

    out = {}
    try:
        with urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=10) as r:
            latest = json.load(r)["info"]["version"]
        cur = yt_dlp.version.__version__
        out.update({"current": cur, "latest": latest, "available": _v(latest) > _v(cur)})
    except Exception as e:
        return {"error": str(e)[:120]}
    for api_url, key in ((f"https://api.github.com/repos/{REPO}/releases/latest", "tag_name"),
                         (f"https://api.github.com/repos/{REPO}/tags", "name")):
        try:
            req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.load(r)
            tag = (data[0] if isinstance(data, list) else data)[key].lstrip("v")
            out.update({"app_current": APP_VERSION, "app_latest": tag,
                        "app_available": _v(tag) > _v(APP_VERSION)})
            break
        except Exception:
            continue  # no releases/tags yet or offline — the yt-dlp result is still useful
    return out


def update_ytdlp():
    # ponytail: wheels are zips and yt-dlp is pure Python — no pip, so this works in the frozen exe;
    # a future yt-dlp release adding a compiled dependency would need a full app rebuild instead
    new = PKG_DIR + ".new"
    try:
        shutil.rmtree(new, ignore_errors=True)
        os.makedirs(new)
        for pkg in ("yt-dlp", "yt-dlp-ejs"):
            with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=10) as r:
                meta = json.load(r)
            whl = next(u["url"] for u in meta["urls"] if u["filename"].endswith(".whl"))
            with urllib.request.urlopen(whl, timeout=120) as r:
                zipfile.ZipFile(io.BytesIO(r.read())).extractall(new)
        return {"ok": True}  # swapped into pkgs/ by apply_pending() on next launch
    except Exception as e:
        shutil.rmtree(new, ignore_errors=True)
        return {"error": str(e)[:120]}


def update_app():
    if not getattr(sys, "frozen", False):
        try:
            src = os.path.dirname(os.path.abspath(__file__))
            out = subprocess.run(
                ["git", "-C", src, "pull", "--ff-only"],
                capture_output=True, text=True, timeout=60, creationflags=0x08000000,
            )
            if out.returncode:
                return {"error": (out.stderr or out.stdout)[:200]}
            return {"ok": True}
        except Exception as e:
            return {"error": str(e)[:120]}
    # frozen: download the latest release zip, stage it, swap after the app exits
    PROGRESS.update(pct=0, stage="checking")
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assets = json.load(r).get("assets") or []
        url = next((a["browser_download_url"] for a in assets if a["name"].endswith(".zip")), "")
    except Exception as e:
        PROGRESS.update(pct=0, stage="")
        return {"error": str(e)[:160]}
    if not url:
        # nothing installable on that release — the page is the only way through
        webbrowser.open(f"https://github.com/{REPO}/releases/latest")
        PROGRESS.update(pct=0, stage="")
        return {"manual": True}
    try:
        inst = os.path.dirname(sys.executable)
        stage = _stage_release(url, inst)
        # The bundle is only as fresh as the CI run that built it; grab the current
        # yt-dlp too so one click lands on the newest of both. Best-effort: a PyPI
        # hiccup must not cost you the app update, and the bundled copy still works.
        PROGRESS.update(pct=100, stage="ytdlp")
        update_ytdlp()
        _spawn_apply(stage, inst)
        PROGRESS.update(pct=100, stage="restarting")
        return {"restart": True}
    except Exception as e:
        # report it instead of bouncing to the browser — the reason is what you need
        PROGRESS.update(pct=0, stage="")
        return {"error": str(e)[:160]}


def _stage_release(url, inst):
    """Download and unpack the release zip into a temp stage; returns the new app root.
    Staged OUTSIDE the install dir so the mirror-copy can't eat its own source."""
    stage = os.path.join(os.environ.get("TEMP", inst), "snap_update_stage")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    zf = os.path.join(stage, "snap.zip")
    PROGRESS.update(pct=0, stage="downloading")
    got = 0
    with urllib.request.urlopen(url, timeout=600) as r, open(zf, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                PROGRESS["pct"] = got * 100 // total
    if got < 1 << 20:
        raise RuntimeError("download truncated")  # an error page unpacked as a build would brick it
    PROGRESS.update(pct=100, stage="unpacking")
    zipfile.ZipFile(zf).extractall(stage)
    os.remove(zf)
    src = os.path.join(stage, "Snap")  # CI zips dist/Snap → archive root is Snap/
    if not os.path.isfile(os.path.join(src, "Snap.exe")):
        raise RuntimeError("unexpected zip layout")
    return src


def _spawn_apply(src, inst, exe="Snap.exe"):
    """Background batch: wait for the old exe to unlock, mirror the staged build in, relaunch."""
    bat = os.path.join(os.environ.get("TEMP", inst), "snap_update.bat")
    target = os.path.join(inst, exe or "Snap.exe")
    launch = f'start "" "{os.path.join(inst, exe)}"' if exe else ""
    # Poll the exe itself: opening it for append fails while a process holds the image.
    # A fixed sleep raced our own shutdown and left robocopy retrying ERROR 32 against a
    # still-running Snap.exe. The cap stops a hung exit leaving an invisible cmd spinning.
    with open(bat, "w", encoding="ascii", errors="replace") as f:
        f.write(f"""@echo off
set /a n=0
:wait
set /a n+=1
if %n% geq 60 goto go
2>nul (>>"{target}" call ) && goto go
timeout /t 1 /nobreak >nul
goto wait
:go
robocopy "{src}" "{inst}" /mir /r:10 /w:1 >nul
rmdir /s /q "{os.path.dirname(src)}"
{launch}
del "%~f0"
""")
    # CREATE_NO_WINDOW alone: Windows IGNORES it when DETACHED_PROCESS is also set, so the
    # old 0x08000008 handed cmd its own visible console. The child outlives us either way.
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000)
