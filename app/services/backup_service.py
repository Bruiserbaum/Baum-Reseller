"""
Backup / restore to Google Drive.
Backup format: a zip containing db_export.json + images/.
"""
import json
import os
import zipfile
import datetime
import threading

from app.database.connection import DB_PATH, get_connection
from app.database.models import get_setting, set_setting

BACKUP_FILENAME = "baum_reseller_backup.zip"
_BAUM_DIR  = os.path.join(os.path.expanduser("~"), ".baum-reseller")
IMAGES_DIR = os.path.join(_BAUM_DIR, "images")
_CONFIG_PATH = os.path.join(_BAUM_DIR, "config.json")


# ── Local export / import ─────────────────────────────────────────────────

def export_to_zip(dest_path: str) -> str:
    """Export entire database + images to a zip archive."""
    data = {}
    with get_connection() as conn:
        for table in ("items", "listings", "images", "sales", "settings", "notifications"):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            data[table] = [dict(r) for r in rows]

    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("db_export.json", json.dumps(data, indent=2))

        # Include app config (Poshmark email, etc.)
        if os.path.isfile(_CONFIG_PATH):
            zf.write(_CONFIG_PATH, "config.json")

        # Include any locally-cached images
        if os.path.isdir(IMAGES_DIR):
            for root, _, files in os.walk(IMAGES_DIR):
                for fname in files:
                    full = os.path.join(root, fname)
                    arc = os.path.relpath(full, os.path.dirname(IMAGES_DIR))
                    zf.write(full, arc)
    return dest_path


def import_from_zip(src_path: str):
    """Restore database and images from a zip archive."""
    with zipfile.ZipFile(src_path, "r") as zf:
        names = zf.namelist()
        data = json.loads(zf.read("db_export.json"))

        # Extract everything except db_export.json to the .baum-reseller dir
        for name in names:
            if name == "db_export.json":
                continue
            dest = os.path.join(_BAUM_DIR, name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(name) as src, open(dest, "wb") as dst:
                dst.write(src.read())

    def _insert(conn, table: str, rows: list):
        for row in rows:
            cols = ", ".join(row.keys())
            placeholders = ", ".join("?" * len(row))
            conn.execute(
                f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )

    with get_connection() as conn:
        # Delete in dependency order: child rows first, then parents.
        # notifications + sales + images + listings reference items,
        # so items must be cleared last.
        for table in ("notifications", "sales", "images", "listings", "items", "settings"):
            conn.execute(f"DELETE FROM {table}")

        _insert(conn, "items",    data.get("items",    []))
        _insert(conn, "listings", data.get("listings", []))
        _insert(conn, "images",   data.get("images",   []))
        _insert(conn, "sales",    data.get("sales",    []))
        # notifications may be absent in backups made before v1.5.10
        _insert(conn, "notifications", data.get("notifications", []))
        for row in data.get("settings", []):
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                         (row["key"], row["value"]))

        # Fix absolute image paths that came from a different machine / username.
        # Images are extracted to IMAGES_DIR; the stored local_path might reference
        # a different home directory (e.g. C:\Users\OldUser\...).  Re-anchor any
        # path that no longer exists by reconstructing it from IMAGES_DIR.
        rows = conn.execute(
            "SELECT id, local_path FROM images WHERE local_path != '' AND local_path IS NOT NULL"
        ).fetchall()
        for img_id, lp in rows:
            if lp and not os.path.exists(lp):
                # Extract the portion after the last 'images' directory segment
                norm = lp.replace("\\", "/")
                marker = "/images/"
                idx = norm.rfind(marker)
                if idx != -1:
                    rel = norm[idx + len(marker):]   # e.g. "123/photo.jpg"
                    new_path = os.path.join(IMAGES_DIR, *rel.split("/"))
                    if os.path.exists(new_path):
                        conn.execute(
                            "UPDATE images SET local_path = ? WHERE id = ?",
                            (new_path, img_id),
                        )


# ── Google Drive ───────────────────────────────────────────────────────────

GDRIVE_CREDS_PATH = os.path.join(os.path.expanduser("~"), ".baum-reseller", "gdrive_creds.json")
GDRIVE_TOKEN_PATH = os.path.join(os.path.expanduser("~"), ".baum-reseller", "gdrive_token.pkl")


def get_drive_service():
    """Return an authenticated Google Drive service or raise if not configured."""
    import pickle
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/drive.file"]

    if not os.path.exists(GDRIVE_CREDS_PATH):
        raise FileNotFoundError(
            "Google Drive credentials file not found.\n\n"
            "To connect Google Drive you need an OAuth2 client credentials file "
            "from Google Cloud Console:\n"
            "  1. Go to console.cloud.google.com → APIs & Services → Credentials\n"
            "  2. Create an OAuth 2.0 Client ID (Desktop app)\n"
            "  3. Download the JSON file\n"
            "  4. Click 'Browse for credentials file' and select it\n\n"
            f"Expected location:\n  {GDRIVE_CREDS_PATH}"
        )

    creds = None
    if os.path.exists(GDRIVE_TOKEN_PATH):
        with open(GDRIVE_TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GDRIVE_CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(GDRIVE_TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)

    return build("drive", "v3", credentials=creds)


def upload_backup_to_drive(local_zip: str, done_cb=None):
    def _worker():
        try:
            from googleapiclient.http import MediaFileUpload
            service = get_drive_service()

            query = f"name='{BACKUP_FILENAME}' and trashed=false"
            results = service.files().list(q=query, fields="files(id)").execute()
            existing = results.get("files", [])

            media = MediaFileUpload(local_zip, mimetype="application/zip", resumable=True)
            if existing:
                service.files().update(
                    fileId=existing[0]["id"], media_body=media
                ).execute()
            else:
                service.files().create(
                    body={"name": BACKUP_FILENAME}, media_body=media
                ).execute()

            set_setting("last_backup_time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            if done_cb:
                done_cb(True, None)
        except Exception as e:
            if done_cb:
                done_cb(False, str(e))

    threading.Thread(target=_worker, daemon=True).start()


def download_backup_from_drive(dest_zip: str, done_cb=None):
    def _worker():
        try:
            from googleapiclient.http import MediaIoBaseDownload
            import io
            service = get_drive_service()

            query = f"name='{BACKUP_FILENAME}' and trashed=false"
            results = service.files().list(q=query, fields="files(id)").execute()
            files = results.get("files", [])
            if not files:
                raise FileNotFoundError("No backup found on Google Drive.")

            request = service.files().get_media(fileId=files[0]["id"])
            with open(dest_zip, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()

            if done_cb:
                done_cb(True, None)
        except Exception as e:
            if done_cb:
                done_cb(False, str(e))

    threading.Thread(target=_worker, daemon=True).start()
