"""
Kalshi REST API client with RSA-PSS authentication.
Auth scheme: KALSHI-ACCESS-KEY header (UUID) + RSA-PSS signed timestamp+method+path.
"""

import base64
import hashlib
import time
from pathlib import Path
from typing import Any

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from config import KalshiConfig


class KalshiAuthError(Exception):
    pass


class KalshiAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class KalshiClient:
    def __init__(self, config: KalshiConfig):
        self.config = config
        self.session = requests.Session()
        self._private_key = self._load_private_key()

    def _load_private_key(self):
        path = Path(self.config.private_key_path)
        if not path.exists():
            raise KalshiAuthError(f"Private key not found at {path}")
        pem = path.read_bytes()
        try:
            return serialization.load_pem_private_key(pem, password=None)
        except Exception as e:
            raise KalshiAuthError(f"Failed to load private key: {e}") from e

    def _sign_request(self, method: str, path: str) -> dict:
        """Build RSA-PSS signed auth headers."""
        ts_ms = str(int(time.time() * 1000))
        message = ts_ms + method.upper() + path
        signature = self._private_key.sign(
            message.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.config.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "Content-Type": "application/json",
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        url = self.config.base_url + endpoint
        headers = self._sign_request(method, "/trade-api/v2" + endpoint)
        resp = self.session.request(method, url, headers=headers, **kwargs)

        if resp.status_code == 401:
            raise KalshiAuthError(
                "Authentication failed (401). Check: key ID matches private key, "
                "correct env (demo vs production), private key file is valid RSA PEM."
            )
        if not resp.ok:
            raise KalshiAPIError(resp.status_code, resp.text[:300])

        return resp.json() if resp.content else {}

    # ── Account ────────────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        """Returns available_balance_cents and portfolio_value_cents."""
        return self._request("GET", "/portfolio/balance")

    def get_positions(self) -> list[dict]:
        data = self._request("GET", "/portfolio/positions")
        return data.get("market_positions", [])

    # ── Markets ────────────────────────────────────────────────────────────────

    def get_markets(
        self,
        status: str = "open",
        limit: int = 200,
        cursor: str | None = None,
        tag: str | None = None,
    ) -> dict:
        """Paginated list of markets. Returns {markets, cursor}."""
        params: dict = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if tag:
            params["tag"] = tag
        return self._request("GET", "/markets", params=params)

    def get_all_sports_markets(self, tags: list[str]) -> list[dict]:
        """Fetch all open sports markets across given tags."""
        markets: list[dict] = []
        for tag in tags:
            cursor = None
            while True:
                resp = self.get_markets(tag=tag, cursor=cursor)
                markets.extend(resp.get("markets", []))
                cursor = resp.get("cursor")
                if not cursor:
                    break
        return markets

    def get_market(self, ticker: str) -> dict:
        return self._request("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker: str, depth: int = 10) -> dict:
        """Returns {yes: [[price, qty],...], no: [[price, qty],...]}."""
        return self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    # ── Orders ─────────────────────────────────────────────────────────────────

    def create_order(
        self,
        ticker: str,
        side: str,         # "yes" or "no"
        action: str,       # "buy" or "sell"
        order_type: str,   # "limit" or "market"
        count: int,        # number of contracts
        yes_price: int | None = None,  # cents (1–99)
        no_price: int | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        payload: dict = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "type": order_type,
            "count": count,
        }
        if yes_price is not None:
            payload["yes_price"] = yes_price
        if no_price is not None:
            payload["no_price"] = no_price
        if client_order_id:
            payload["client_order_id"] = client_order_id
        return self._request("POST", "/portfolio/orders", json=payload)

    def cancel_order(self, order_id: str) -> dict:
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    def get_orders(self, status: str = "resting") -> list[dict]:
        data = self._request("GET", "/portfolio/orders", params={"status": status})
        return data.get("orders", [])

    def get_fills(self, ticker: str | None = None) -> list[dict]:
        params = {}
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/portfolio/fills", params=params)
        return data.get("fills", [])
