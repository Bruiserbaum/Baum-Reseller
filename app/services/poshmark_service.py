"""
Poshmark integration — browser automation via Playwright.
Credentials stored via keyring under service 'baum-reseller-poshmark'.
"""
import keyring
import json

SERVICE = "baum-reseller-poshmark"


class PoshmarkService:
    def get_credentials(self) -> dict:
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, email: str, password: str):
        keyring.set_password(SERVICE, "credentials",
                             json.dumps({"email": email, "password": password}))

    def test_connection(self) -> tuple[bool, str]:
        try:
            creds = self.get_credentials()
            if not creds:
                return False, "No credentials saved."
            return True, "Credentials saved (not yet verified)"
        except Exception as e:
            return False, str(e)

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        # TODO: use Playwright to log in and scrape closet listings
        return []
