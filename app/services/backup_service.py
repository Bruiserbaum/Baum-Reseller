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
IMAGES_DIR = os.path.join(os.path.expanduser("~"), ".baum-reseller", "images")


# ── Local export / import ─────────────────────────────────────────────────

def export_to_zip(dest_path: str) -> str:
    """Export entire database + images to a zip archive."""
    data = {}
    with get_connection() as conn:
        for table in ("items", "listings", "images", "sales", "settings"):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            data[table] = [dict(r) for r in rows]

    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("db_export.json", json.dumps(data, indent=2))
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
        data = json.loads(zf.read("db_export.json"))
        zf.extractall(os.path.dirname(IMAGES_DIR))

    with get_connection() as conn:
        for table in ("sales", "images", "listings", "items", "settings"):
            conn.execute(f"DELETE FROM {table}")
        for row in data.get("items", []):
            cols = ", ".join(row.keys())
            placeholders = ", ".join("?" * len(row))
            conn.execute(f"INSERT OR REPLACE INTO items ({cols}) VALUES ({placeholders})", list(row.values()))
        for row in data.get("listings", []):
            cols = ", ".join(row.keys())
            placeholders = ", ".join("?" * len(row))
            conn.execute(f"INSERT OR REPLACE INTO listings ({cols}) VALUES ({placeholders})", list(row.values()))
        for row in data.get("images", []):
            cols = ", ".join(row.keys())
            placeholders = ", ".join("?" * len(row))
            conn.execute(f"INSERT OR REPLACE INTO images ({cols}) VALUES ({placeholders})", list(row.values()))
        for row in data.get("sales", []):
            cols = ", ".join(row.keys())
            placeholders = ", ".join("?" * len(row))
            conn.execute(f"INSERT OR REPLACE INTO sales ({cols}) VALUES ({placeholders})", list(row.values()))
        for row in data.get("settings", []):
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                         (row["key"], row["value"]))


# ── Google Drive ───────────────────────────────────────────────────────────

def get_drive_service():
    """Return an authenticated Google Drive service or raise if not configured."""
    import pickle
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/drive.file"]
    creds_path = os.path.join(os.path.expanduser("~"), ".baum-reseller", "gdrive_creds.json")
    token_path = os.path.join(os.path.expanduser("~"), ".baum-reseller", "gdrive_token.pkl")

    creds = None
    if os.path.exists(token_path):
        with open(token_path, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "wb") as f:
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
