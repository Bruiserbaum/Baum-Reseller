"""
eBay integration.

Public API  — client credentials OAuth (existing, for Browse API).
Seller API  — OAuth2 Authorization Code Grant required for:
                • Sell Inventory API  (active listings)
                • Sell Fulfillment API (sold orders)

Setup required in eBay Developer Portal
────────────────────────────────────────
1. Log in at https://developer.ebay.com/my/keys
2. Open your app's OAuth settings.
3. Add  http://localhost:9735/oauth/callback  as an accepted RuName / redirect URI.
4. Save your Client ID and Client Secret in Settings, then click "Authorize Seller".

Token storage: access + refresh tokens are stored via keyring so they persist
between sessions. The access token is auto-refreshed when it expires.
"""

import base64
import json
import keyring
import requests
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

SERVICE = "baum-reseller-ebay"
_SELLER_TOKEN_KEY = "seller_tokens"
_OAUTH_PORT = 9735
_REDIRECT_URI = f"http://localhost:{_OAUTH_PORT}/oauth/callback"

_SELLER_SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
])


class EbayService:
    # ── Credential storage ────────────────────────────────────────────────

    def get_credentials(self) -> dict:
        raw = keyring.get_password(SERVICE, "credentials")
        return json.loads(raw) if raw else {}

    def save_credentials(self, client_id: str, client_secret: str):
        keyring.set_password(SERVICE, "credentials",
                             json.dumps({"client_id": client_id,
                                         "client_secret": client_secret}))

    def get_seller_tokens(self) -> dict:
        raw = keyring.get_password(SERVICE, _SELLER_TOKEN_KEY)
        return json.loads(raw) if raw else {}

    def has_seller_access(self) -> bool:
        return bool(self.get_seller_tokens())

    # ── Token helpers ─────────────────────────────────────────────────────

    def _public_token(self) -> str:
        """Client credentials token — public Browse API only."""
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

    def _seller_token(self) -> str:
        """Return a valid seller access token, refreshing automatically if expired."""
        import time
        tokens = self.get_seller_tokens()
        if not tokens:
            raise ValueError(
                "Seller access not authorized — click 'Authorize Seller' in Settings."
            )
        if tokens.get("expires_at", 0) < time.time() + 60:
            new_token = self._refresh(tokens["refresh_token"])
            if new_token:
                return new_token
        return tokens["access_token"]

    def _refresh(self, refresh_token: str) -> str | None:
        import time
        creds = self.get_credentials()
        if not creds:
            return None
        try:
            resp = requests.post(
                "https://api.ebay.com/identity/v1/oauth2/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                auth=(creds["client_id"], creds["client_secret"]),
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": _SELLER_SCOPES,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            tokens = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", refresh_token),
                "expires_at": time.time() + data.get("expires_in", 7200) - 60,
            }
            keyring.set_password(SERVICE, _SELLER_TOKEN_KEY, json.dumps(tokens))
            return tokens["access_token"]
        except Exception:
            return None

    # ── OAuth flow ────────────────────────────────────────────────────────

    def start_oauth_flow(self, done_cb=None):
        """
        Launch eBay OAuth2 Authorization Code flow.
        Opens a browser window for the user to approve seller access, then
        captures the callback on localhost and exchanges the code for tokens.

        Prerequisite: http://localhost:9735/oauth/callback must be registered
        as a redirect URI in your eBay Developer Portal app settings.
        """
        creds = self.get_credentials()
        if not creds:
            if done_cb:
                done_cb(False, "Save your eBay Client ID and Secret first.")
            return
        threading.Thread(
            target=_run_oauth_flow,
            args=(creds["client_id"], creds["client_secret"], done_cb),
            daemon=True,
        ).start()

    # ── Connection test ───────────────────────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        try:
            self._public_token()
        except Exception as e:
            return False, str(e)
        if self.has_seller_access():
            return True, "Connected — seller access authorized"
        return True, "Connected (public only) — click Authorize Seller for listings"

    # ── Data fetching ─────────────────────────────────────────────────────

    def fetch_listings(self, progress_cb=None) -> list[dict]:
        """Fetch active inventory using the eBay Sell Inventory API."""
        token = self._seller_token()
        headers = {"Authorization": f"Bearer {token}"}
        listings = []
        offset, limit = 0, 200

        while True:
            resp = requests.get(
                "https://api.ebay.com/sell/inventory/v1/inventory_item",
                headers=headers,
                params={"limit": limit, "offset": offset},
                timeout=20,
            )
            if resp.status_code == 401:
                raise RuntimeError(
                    "eBay seller token expired — click 'Authorize Seller' to re-authorize."
                )
            resp.raise_for_status()
            data = resp.json()
            total = data.get("total", 0)

            for item in data.get("inventoryItems", []):
                sku = item.get("sku", "")
                product = item.get("product", {})
                listings.append({
                    "listing_id": sku,
                    "title": product.get("title", ""),
                    "price": 0.0,  # price lives on the offer, not the inventory item
                    "url": "",
                    "status": "active",
                    "listed_date": "",
                })

            offset += limit
            if progress_cb:
                progress_cb(min(85, offset * 100 // max(total, 1)))
            if offset >= total:
                break

        return listings

    def fetch_sold_orders(self, progress_cb=None) -> list[dict]:
        """Fetch fulfilled orders using the eBay Sell Fulfillment API."""
        token = self._seller_token()
        headers = {"Authorization": f"Bearer {token}"}
        orders = []
        cursor = None

        while True:
            params = {
                "limit": 200,
                "filter": "orderfulfillmentstatus:{FULFILLED}",
            }
            if cursor:
                params["after"] = cursor
            resp = requests.get(
                "https://api.ebay.com/sell/fulfillment/v1/order",
                headers=headers,
                params=params,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            for order in data.get("orders", []):
                sale_date = order.get("creationDate", "")[:10]
                for li in order.get("lineItems", []):
                    orders.append({
                        "listing_id": li.get("sku") or li.get("lineItemId", ""),
                        "title": li.get("title", ""),
                        "price": float(
                            li.get("lineItemCost", {}).get("value", 0) or 0
                        ),
                        "url": "",
                        "status": "sold",
                        "sold_date": sale_date,
                        "listed_date": "",
                    })

            cursor = data.get("next")
            if not cursor:
                break

        return orders


# ── OAuth flow implementation ─────────────────────────────────────────────────

def _run_oauth_flow(client_id: str, client_secret: str, done_cb):
    """
    Spin up a local HTTP server, open the eBay auth page, capture the code,
    exchange it for tokens, and store them in keyring.
    Runs entirely in a background thread — safe to call from the UI thread.
    """
    import time

    auth_code: list[str | None] = [None]
    auth_error: list[str | None] = [None]
    got_callback = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/oauth/callback":
                params = parse_qs(parsed.query)
                if "code" in params:
                    auth_code[0] = params["code"][0]
                    self._reply(200, "Authorization successful! You may close this tab.")
                else:
                    desc = params.get("error_description", ["Unknown eBay error"])[0]
                    auth_error[0] = desc
                    self._reply(400, f"Authorization failed: {desc}")
                got_callback.set()
            else:
                self._reply(404, "Not found")

        def _reply(self, code: int, body: str):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_):
            pass

    server = HTTPServer(("localhost", _OAUTH_PORT), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    auth_url = "https://auth.ebay.com/oauth2/authorize?" + urlencode({
        "client_id": client_id,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": _SELLER_SCOPES,
    })
    webbrowser.open(auth_url)

    got_callback.wait(timeout=180)
    server.shutdown()

    if not auth_code[0]:
        msg = auth_error[0] or "Authorization timed out or was cancelled."
        if done_cb:
            done_cb(False, msg)
        return

    # Exchange authorization code for tokens
    try:
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        resp = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic}",
            },
            data={
                "grant_type": "authorization_code",
                "code": auth_code[0],
                "redirect_uri": _REDIRECT_URI,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        tokens = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": time.time() + data.get("expires_in", 7200) - 60,
        }
        keyring.set_password(SERVICE, _SELLER_TOKEN_KEY, json.dumps(tokens))
        if done_cb:
            done_cb(True, None)
    except Exception as exc:
        if done_cb:
            done_cb(False, f"Token exchange failed: {exc}")
