import base64
import logging
import time

import requests

from config import Config

logger = logging.getLogger(__name__)

_token_cache = {"token": None, "expires_at": 0.0}


def get_zoom_access_token():
    """
    Get a Zoom Server-to-Server OAuth access token.
    Caches the token and refreshes it 60 seconds before expiry.
    Returns None if Zoom credentials are not configured.
    """
    if not (Config.ZOOM_ACCOUNT_ID and Config.ZOOM_CLIENT_ID and Config.ZOOM_CLIENT_SECRET):
        logger.warning("Zoom credentials not configured (ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET).")
        return None

    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    credentials = base64.b64encode(
        f"{Config.ZOOM_CLIENT_ID}:{Config.ZOOM_CLIENT_SECRET}".encode()
    ).decode()

    try:
        resp = requests.post(
            "https://zoom.us/oauth/token",
            params={"grant_type": "account_credentials", "account_id": Config.ZOOM_ACCOUNT_ID},
            headers={"Authorization": f"Basic {credentials}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = now + data.get("expires_in", 3600)
        logger.info("Zoom access token acquired (expires in %ds).", data.get("expires_in", 3600))
        return _token_cache["token"]
    except Exception as e:
        logger.error("Failed to get Zoom access token: %s", e)
        return None
