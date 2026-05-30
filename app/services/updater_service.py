import os
import sys
import zipfile
import shutil
import threading
import requests
from packaging.version import Version

from version import VERSION, GITHUB_REPO


def get_latest_release() -> dict | None:
    """Return latest GitHub release info or None on failure."""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def check_for_update() -> tuple[bool, str]:
    """Returns (update_available, latest_version_string)."""
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


def download_and_apply_update(asset_url: str, progress_cb=None, done_cb=None):
    """Download zip asset, extract over current install, then restart."""
    def _worker():
        try:
            tmp_zip = os.path.join(os.path.expanduser("~"), ".baum-reseller", "update.zip")
            os.makedirs(os.path.dirname(tmp_zip), exist_ok=True)

            resp = requests.get(asset_url, stream=True, timeout=60)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp_zip, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total:
                        progress_cb(int(downloaded / total * 100))

            app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            with zipfile.ZipFile(tmp_zip, "r") as zf:
                zf.extractall(app_dir)
            os.remove(tmp_zip)

            if done_cb:
                done_cb(True, None)

            # Restart
            python = sys.executable
            os.execv(python, [python] + sys.argv)
        except Exception as e:
            if done_cb:
                done_cb(False, str(e))

    threading.Thread(target=_worker, daemon=True).start()
