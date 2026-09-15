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

APP_VERSION = "4.10.2"
REPO = "MrBeanTheOne/snap"
STATE_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Snap")
PKG_DIR = os.path.join(STATE_DIR, "pkgs")  # in-app yt-dlp updates land here, shadowing the bundled copy


def apply_pending():
    """Finish a pending yt-dlp update. Must run BEFORE yt_dlp is imported."""
    if os.path.isdir(PKG_DIR + ".new"):
        shutil.rmtree(PKG_DIR, ignore_errors=True)
        os.replace(PKG_DIR + ".new", PKG_DIR)
    if os.path.isdir(PKG_DIR):
        sys.path.insert(0, PKG_DIR)


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
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assets = json.load(r).get("assets") or []
        url = next(a["browser_download_url"] for a in assets if a["name"].endswith(".zip"))
        inst = os.path.dirname(sys.executable)
        stage = _stage_release(url, inst)
        _spawn_apply(stage, inst)
        return {"restart": True}
    except Exception:
        # private repo / no assets / read-only install dir — the release page still works
        webbrowser.open(f"https://github.com/{REPO}/releases/latest")
        return {"manual": True}


def _stage_release(url, inst):
    """Download and unpack the release zip into <install>/_update; returns the new app root."""
    stage = os.path.join(inst, "_update")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    zf = os.path.join(stage, "snap.zip")
    with urllib.request.urlopen(url, timeout=600) as r, open(zf, "wb") as f:
        shutil.copyfileobj(r, f)
    zipfile.ZipFile(zf).extractall(stage)
    os.remove(zf)
    src = os.path.join(stage, "Snap")  # CI zips dist/Snap → archive root is Snap/
    if not os.path.isfile(os.path.join(src, "Snap.exe")):
        raise RuntimeError("unexpected zip layout")
    return src


def _spawn_apply(src, inst, exe="Snap.exe"):
    """Detached batch: wait for this process to exit, move the staged build in, relaunch."""
    bat = os.path.join(os.environ.get("TEMP", inst), "snap_update.bat")
    launch = f'start "" "{os.path.join(inst, exe)}"' if exe else ""
    with open(bat, "w", encoding="ascii", errors="replace") as f:
        f.write(f"""@echo off
timeout /t 2 /nobreak >nul
robocopy "{src}" "{inst}" /e /move /r:20 /w:1 >nul
rmdir /s /q "{os.path.dirname(src)}"
{launch}
del "%~f0"
""")
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000008)  # CREATE_NO_WINDOW | DETACHED
