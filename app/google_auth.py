"""Google OAuth 2.0 helpers — per-user delegated token flow stored in Flask session."""

import logging
import time
from urllib.parse import urlencode

import requests

from config import Config

logger = logging.getLogger(__name__)

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

_GOOGLE_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/meetings.space.readonly",
]

_SESSION_ACCESS_KEY = "google_access_token"
_SESSION_REFRESH_KEY = "google_refresh_token"
_SESSION_EXPIRY_KEY = "google_token_expiry"  # Unix timestamp float


def get_google_auth_url(state: str) -> str:
    """Build the Google OAuth 2.0 authorization URL."""
    params = {
        "client_id": Config.GOOGLE_CLIENT_ID,
        "redirect_uri": Config.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(_GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # forces refresh_token on every authorization
        "state": state,
    }
    return f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    """
    Exchange an authorization code for access + refresh tokens.
    Returns the full token response dict; may contain an 'error' key on failure.
    """
    try:
        resp = requests.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": Config.GOOGLE_CLIENT_ID,
                "client_secret": Config.GOOGLE_CLIENT_SECRET,
                "redirect_uri": Config.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("exchange_code_for_tokens failed: %s", e)
        return {"error": str(e)}


def store_google_tokens(session: dict, token_data: dict) -> None:
    """Persist Google tokens into Flask session."""
    session[_SESSION_ACCESS_KEY] = token_data.get("access_token", "")
    session[_SESSION_EXPIRY_KEY] = time.time() + int(token_data.get("expires_in", 3600))
    if token_data.get("refresh_token"):
        session[_SESSION_REFRESH_KEY] = token_data["refresh_token"]


def _refresh_google_token(session: dict) -> bool:
    """
    Use the stored refresh token to obtain a new access token.
    Returns True on success, False on failure (missing refresh_token or HTTP error).
    """
    refresh_token = session.get(_SESSION_REFRESH_KEY, "")
    if not refresh_token:
        logger.warning("Google token refresh skipped: no refresh_token in session")
        return False
    try:
        resp = requests.post(
            _GOOGLE_TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": Config.GOOGLE_CLIENT_ID,
                "client_secret": Config.GOOGLE_CLIENT_SECRET,
                "grant_type": "refresh_token",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        session[_SESSION_ACCESS_KEY] = data["access_token"]
        session[_SESSION_EXPIRY_KEY] = time.time() + int(data.get("expires_in", 3600))
        logger.info("Google access token refreshed successfully.")
        return True
    except Exception as e:
        logger.warning("Google token refresh failed: %s", e)
        return False


def get_google_token(session: dict) -> str | None:
    """
    Return a valid Google access token from session, refreshing if within 60 s of expiry.
    Returns None if not authenticated or if refresh fails.
    """
    access = session.get(_SESSION_ACCESS_KEY, "")
    expiry = session.get(_SESSION_EXPIRY_KEY, 0.0)
    if not access:
        return None
    if time.time() < expiry - 60:
        return access
    if _refresh_google_token(session):
        return session.get(_SESSION_ACCESS_KEY)
    clear_google_tokens(session)
    return None


def is_google_connected(session: dict) -> bool:
    """True if a Google access token exists in session (may still be expired / refreshable)."""
    return bool(session.get(_SESSION_ACCESS_KEY))


def clear_google_tokens(session: dict) -> None:
    """Remove all Google tokens from session."""
    for key in (_SESSION_ACCESS_KEY, _SESSION_REFRESH_KEY, _SESSION_EXPIRY_KEY):
        session.pop(key, None)


def revoke_google_token(session: dict) -> None:
    """Revoke the Google token via Google's revocation endpoint and clear session."""
    token = session.get(_SESSION_ACCESS_KEY, "") or session.get(_SESSION_REFRESH_KEY, "")
    if token:
        try:
            requests.post(
                _GOOGLE_REVOKE_URL,
                params={"token": token},
                timeout=5,
            )
        except Exception as e:
            logger.debug("Google token revocation request failed (ignored): %s", e)
    clear_google_tokens(session)
