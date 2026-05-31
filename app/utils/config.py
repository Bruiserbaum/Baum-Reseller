"""
Persistent app configuration at ~/.baum-reseller/config.json.

This file sits in the user's home directory — completely separate from the
app install directory — so it is NEVER touched by installer upgrades or
uninstalls. Use it for non-sensitive settings that must survive reinstalls.

Passwords are NOT stored here; they go to Windows Credential Manager via keyring.
"""
import json
import os

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".baum-reseller", "config.json")


def load() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get(key: str, default=None):
    return load().get(key, default)


def set_value(key: str, value):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    data = load()
    data[key] = value
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def set_many(updates: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    data = load()
    data.update(updates)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
