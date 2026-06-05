import os
import sys
import subprocess
import threading
import requests
from packaging.version import Version

from version import VERSION, GITHUB_REPO


def get_latest_release() -> dict | None:
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def check_for_update() -> tuple[bool, str]:
    release = get_latest_release()
    if not release:
        return False, VERSION
    tag = release.get("tag_name", "").lstrip("v")
    try:
        if Version(tag) > Version(VERSION):
            return True, tag
    except Exception:
        pass
    return False, tag


def find_update_asset(release: dict) -> dict | None:
    """Return the best downloadable asset from a release — prefers .exe installer, falls back to .zip."""
    assets = release.get("assets", [])
    for asset in assets:
        if asset["name"].endswith("Setup.exe"):
            return asset
    for asset in assets:
        if asset["name"].endswith(".exe"):
            return asset
    for asset in assets:
        if asset["name"].endswith(".zip"):
            return asset
    return None


def download_and_apply_update(asset_url: str, asset_name: str,
                               progress_cb=None, done_cb=None):
    """
    Download the update asset and apply it.

    For .exe (Inno Setup installer): downloaded to a temp dir, then launched
    with /VERYSILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS so the installer
    handles closing the app, replacing files, and restarting — no manual steps.

    For .zip (legacy fallback): extract over the install dir and restart.
    """
    def _worker():
        try:
            tmp_dir = os.path.join(os.path.expanduser("~"), ".baum-reseller", "update")
            os.makedirs(tmp_dir, exist_ok=True)
            tmp_file = os.path.join(tmp_dir, asset_name)

            # Download with progress
            resp = requests.get(asset_url, stream=True, timeout=120)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp_file, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total:
                        progress_cb(int(downloaded / total * 100))

            if asset_name.endswith(".exe"):
                # Inno Setup installer — launch silently; it closes + restarts the app
                subprocess.Popen(
                    [
                        tmp_file,
                        "/VERYSILENT",
                        "/CLOSEAPPLICATIONS",
                        "/RESTARTAPPLICATIONS",
                        "/NORESTART",
                    ],
                    close_fds=True,
                )
                if done_cb:
                    done_cb(True, None)
                # Brief pause so the installer process starts, then exit this instance
                import time; time.sleep(2)
                sys.exit(0)
            else:
                # Zip fallback: extract and restart manually
                import zipfile, shutil
                app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
                with zipfile.ZipFile(tmp_file, "r") as zf:
                    zf.extractall(app_dir)
                os.remove(tmp_file)
                if done_cb:
                    done_cb(True, None)
                python = sys.executable
                os.execv(python, [python] + sys.argv)

        except Exception as e:
            if done_cb:
                done_cb(False, str(e))

    threading.Thread(target=_worker, daemon=True).start()
