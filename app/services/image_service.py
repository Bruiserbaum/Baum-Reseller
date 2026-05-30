import os
import threading
import requests
import imagehash
from PIL import Image
from io import BytesIO

IMAGES_DIR = os.path.join(os.path.expanduser("~"), ".baum-reseller", "images")


def download_image(url: str, item_id: int, filename: str) -> tuple[str, str]:
    """Download image, return (local_path, perceptual_hash)."""
    os.makedirs(os.path.join(IMAGES_DIR, str(item_id)), exist_ok=True)
    dest = os.path.join(IMAGES_DIR, str(item_id), filename)

    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    img = Image.open(BytesIO(resp.content)).convert("RGB")
    img.save(dest, "JPEG", quality=85)

    phash = str(imagehash.phash(img))
    return dest, phash


def compute_hash_for_file(path: str) -> str:
    img = Image.open(path).convert("RGB")
    return str(imagehash.phash(img))


def find_duplicate_items(item_id: int) -> list[int]:
    """Compare this item's image hashes against all others; return matching item IDs."""
    from app.database.models import get_item, get_items_by_hash

    item = get_item(item_id)
    if not item:
        return []

    duplicates = set()
    for img in item.get("images", []):
        h = img.get("image_hash", "")
        if h:
            for match in get_items_by_hash(h):
                mid = match["item_id"]
                if mid != item_id:
                    duplicates.add(mid)
    return list(duplicates)
