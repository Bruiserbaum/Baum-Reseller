# Baum Reseller

A Windows desktop app for managing reselling inventory across **eBay**, **Mercari**, and **Poshmark**.

Track every item from purchase to sale — with cross-platform listing detection, bin tracking, automated alerts, profit reporting, and cloud backup.

<img width="1338" height="846" alt="image" src="https://github.com/user-attachments/assets/54453a36-4568-41f8-97f5-c1afed3d5d19" />

---

## Features

### Inventory Management
- Add items with title, description, category, bin location, purchase cost, and notes
- Auto-save — every field saves automatically as you type (no Save button needed)
- Mark items as **Missing** (with notes) and resolve them when found
- Search and filter by platform, category, or keyword

### Platform Sync
- Connect eBay (OAuth API), Mercari, and Poshmark (browser automation)
- Sync listings from all platforms with one click or on a schedule
- Automatic cross-platform deduplication via perceptual image hashing — see when the same item is listed on multiple platforms

### Smart Alerts
The app runs background checks and notifies you when:
- **Unshipped sale** — a sale was recorded 3+ days ago with no shipment marked
- **Still listed** — an item sold on one platform is still active on another
- **Missing item** — any item you've flagged as missing

Alerts show as Windows toast notifications and appear in the in-app **Alerts** panel with one-click actions (Mark Shipped, Mark Found, Dismiss).

### Reports
- Monthly and yearly sales summaries
- Per-platform revenue and profit bar charts
- Export professional PDF reports with item-level breakdown and platform totals

### Backup & Restore
- Connect Google Drive for automatic cloud backups
- Set a backup schedule (daily, weekly, monthly)
- Export/import local `.zip` backups — full restore in seconds after a fresh install

### Auto-Update
- Checks GitHub Releases on startup
- Downloads and applies updates automatically, then restarts the app

---

## Installation

Download the latest `BaumResellerSetup.exe` from the [Releases](../../releases/latest) page and run it. No Python required.

---

## Setup After Installing

### 1. eBay
1. Go to the [eBay Developer Program](https://developer.ebay.com) and create an app
2. Copy your **Client ID** and **Client Secret**
3. In Baum Reseller → Settings → eBay row → enter credentials → **Save** → **Test**

### 2. Mercari & Poshmark
1. In Settings, find the Mercari or Poshmark row
2. Enter your **email** and **password** → **Save** → **Test**
3. These use browser automation — the app opens a headless browser to log in and fetch your listings

### 3. Google Drive Backup
1. Go to [Google Cloud Console](https://console.cloud.google.com) → create a project → enable the Drive API
2. Create an OAuth credential (Desktop app) → download `credentials.json`
3. Place `credentials.json` at `%USERPROFILE%\.baum-reseller\gdrive_creds.json`
4. In Settings → **Connect Google Drive** — a browser window will open to authorize

---

## Building from Source

### Prerequisites
- Python 3.12+
- [Inno Setup 6](https://jrsoftware.org/isdownload.php)

### Steps
```bat
git clone https://github.com/Bruiserbaum/Baum-Reseller.git
cd Baum-Reseller
py -m pip install -r requirements.txt
build.bat
```

The installer will be output to `dist\BaumResellerSetup.exe`.

---

## Data Storage

All data is stored locally at `%USERPROFILE%\.baum-reseller\`:

| Path | Contents |
|---|---|
| `baum_reseller.db` | SQLite database (items, listings, sales, settings) |
| `images\` | Downloaded listing photos |
| `gdrive_creds.json` | Google Drive OAuth credentials (you provide) |
| `gdrive_token.pkl` | Google Drive auth token (auto-generated) |

---

## Development

```bat
py -m pip install -r requirements.txt
py main.py
```

### Project Structure

```
Baum-Reseller/
├── main.py                      # Entry point
├── version.py                   # App version + GitHub repo
├── app/
│   ├── main_window.py           # Main window + sidebar navigation
│   ├── database/
│   │   ├── connection.py        # SQLite setup + migrations
│   │   └── models.py            # All DB queries
│   ├── views/
│   │   ├── inventory_view.py    # Inventory list + filters
│   │   ├── item_detail_view.py  # Item edit dialog + auto-save
│   │   ├── reports_view.py      # Sales table + platform charts + PDF export
│   │   ├── settings_view.py     # Credentials, sync, update, backup
│   │   └── notifications_view.py# Alerts panel
│   ├── services/
│   │   ├── ebay_service.py      # eBay OAuth API
│   │   ├── mercari_service.py   # Mercari browser automation
│   │   ├── poshmark_service.py  # Poshmark browser automation
│   │   ├── sync_service.py      # Sync orchestrator
│   │   ├── image_service.py     # Image download + perceptual hashing
│   │   ├── notification_service.py # Background alert checks
│   │   ├── backup_service.py    # Google Drive + local backup
│   │   ├── report_service.py    # PDF generation
│   │   └── updater_service.py   # GitHub auto-updater
│   └── utils/
│       └── auto_save.py         # Debounced auto-save helper
├── assets/
│   └── style.qss                # Dark theme stylesheet
├── installer/
│   └── setup.iss                # Inno Setup installer script
├── .github/
│   └── workflows/
│       └── build-release.yml    # CI: auto-build installer on release tag
└── requirements.txt
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.
