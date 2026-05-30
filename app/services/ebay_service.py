"""
eBay integration — uses the eBay Browse API (OAuth2).
Credentials stored via keyring under service 'baum-reseller-ebay'.
"""
import keyring
import requests

SERVICE = "baum-reseller-ebay"


class EbayService:
    def get_credentials(self) -> dict:
        import json
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, client_id: str, client_secret: str):
        import json
        keyring.set_password(SERVICE, "credentials",
                             json.dumps({"client_id": client_id, "client_secret": client_secret}))

    def _get_token(self) -> str:
        creds = self.get_credentials()
        if not creds:
            raise ValueError("eBay credentials not configured.")
        resp = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            auth=(creds["client_id"], creds["client_secret"]),
            data={"grant_type": "client_credentials",
                  "scope": "https://api.ebay.com/oauth/api_scope"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def test_connection(self) -> tuple[bool, str]:
        try:
            self._get_token()
            return True, "Connected"
        except Exception as e:
            return False, str(e)

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        # TODO: implement with eBay Sell Inventory API
        # Requires seller OAuth scope; placeholder returns empty list
        return []
