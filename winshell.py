"""Windows shell integration: snap:// protocol, autostart, tray icon,
single-instance handoff, clipboard."""
import ctypes
import json
import os
import sys
import time
import urllib.parse
import winreg

import pystray
from PIL import Image

import updater

SHOW_FLAG = os.path.join(updater.STATE_DIR, "show.flag")  # second launch asks the first to show itself


def _entry_cmd(args):
    """Command line that relaunches this app, frozen or from source."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {args}'
    return f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}" {args}'


def register_protocol():
    """snap:// links (e.g. from the bookmarklet) open in Snap. HKCU — no admin needed."""
    root = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\snap")
    winreg.SetValueEx(root, None, 0, winreg.REG_SZ, "URL:Snap")
    winreg.SetValueEx(root, "URL Protocol", 0, winreg.REG_SZ, "")
    cmdk = winreg.CreateKey(root, r"shell\open\command")
    winreg.SetValueEx(cmdk, None, 0, winreg.REG_SZ, _entry_cmd('"%1"'))


def url_from_argv():
    for a in sys.argv[1:]:
        if a.startswith("snap://"):
            u = urllib.parse.unquote(a[len("snap://"):]).rstrip("/")
            return u if u.startswith("http") else "https://" + u
    return ""


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "Snap", 0, winreg.REG_SZ, _entry_cmd("--tray"))
        else:
            try:
                winreg.DeleteValue(k, "Snap")
            except FileNotFoundError:
                pass


def claim_single_instance():
    """True if we're the only Snap. Otherwise hand any snap:// URL to the running
    instance (which shows itself) and return False so the caller exits."""
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW(None, False, "Global\\SnapDownloaderApp")
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        os.makedirs(updater.STATE_DIR, exist_ok=True)
        with open(SHOW_FLAG, "w", encoding="utf-8") as f:
            f.write(url_from_argv())
        return False
    try:
        os.remove(SHOW_FLAG)  # stale flag from a previous run
    except OSError:
        pass
    return True


def show_watcher(window):
    """Poll for the flag a second launch leaves behind; show the window (and fetch its URL)."""
    while True:
        time.sleep(1)
        if os.path.exists(SHOW_FLAG):
            try:
                with open(SHOW_FLAG, encoding="utf-8") as f:
                    url = f.read().strip()
                os.remove(SHOW_FLAG)
            except OSError:
                url = ""
            window.show()
            window.restore()
            if url.startswith("http"):
                window.evaluate_js(f"url.value = {json.dumps(url)}; showTab('grab'); fetchInfo();")


def make_tray(window, api, icon_path):
    img = Image.open(icon_path)

    def show(icon, item):
        window.show()
        window.restore()

    def quit_(icon, item):
        api.settings["tray"] = False  # let the close actually close
        icon.stop()
        window.destroy()

    icon = pystray.Icon("Snap", img, "Snap", menu=pystray.Menu(
        pystray.MenuItem("Open Snap", show, default=True),
        pystray.MenuItem("Quit", quit_),
    ))
    icon.run_detached()
    return icon


def read_clipboard():
    # ctypes reads the clipboard in ~0ms; a PowerShell round-trip costs ~0.5s per window focus
    try:
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        u.GetClipboardData.restype = ctypes.c_void_p
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = [ctypes.c_void_p]
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        text = ""
        if u.OpenClipboard(0):
            h = u.GetClipboardData(13)  # CF_UNICODETEXT
            if h:
                p = k.GlobalLock(h)
                if p:
                    text = ctypes.c_wchar_p(p).value or ""
                    k.GlobalUnlock(h)
            u.CloseClipboard()
        return text.strip().splitlines()[0] if text.strip() else ""
    except Exception:
        return ""
