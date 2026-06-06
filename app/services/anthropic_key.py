"""
Secure storage for the Anthropic API key.

Uses the OS keyring (Windows Credential Manager, macOS Keychain, etc.)
so the key never touches the database or any plain-text file on disk.
"""
import keyring

_SERVICE = "baum-reseller-anthropic"
_ACCOUNT = "api_key"


def get_key() -> str | None:
    """Return the stored API key, or None if not set."""
    val = keyring.get_password(_SERVICE, _ACCOUNT)
    return val.strip() if val and val.strip() else None


def set_key(api_key: str) -> None:
    """Store the API key in the system keyring."""
    keyring.set_password(_SERVICE, _ACCOUNT, api_key.strip())


def clear_key() -> None:
    """Remove the stored API key (ignores errors if not set)."""
    try:
        keyring.delete_password(_SERVICE, _ACCOUNT)
    except Exception:
        pass


def has_key() -> bool:
    """Return True if a non-empty API key is stored."""
    return bool(get_key())


def masked(key: str | None) -> str:
    """Return a display-safe masked version like 'sk-ant-…a1b2c3d4'."""
    if not key or len(key) < 12:
        return "not set"
    return f"{key[:10]}…{key[-8:]}"
