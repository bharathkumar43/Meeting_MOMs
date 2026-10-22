import threading

import msal
from flask import current_app, g, session, redirect, url_for, request
from config import Config

# MSAL authority/discovery metadata, shared across requests. Without it every
# ConfidentialClientApplication re-downloads it (~300 ms per construction).
_HTTP_CACHE: dict = {}

# One long-lived client for the app-only (client credentials) token; MSAL keeps that
# token in its in-memory cache and only calls Azure again near expiry.
_APP_ONLY_CLIENT = None
_APP_ONLY_LOCK = threading.Lock()


def _build_msal_app(cache=None):
    return msal.ConfidentialClientApplication(
        Config.AZURE_CLIENT_ID,
        authority=Config.AUTHORITY,
        client_credential=Config.AZURE_CLIENT_SECRET,
        token_cache=cache,
        http_cache=_HTTP_CACHE,
    )


def _load_cache():
    cache = msal.SerializableTokenCache()
    if session.get("token_cache"):
        cache.deserialize(session["token_cache"])
    return cache


def _save_cache(cache):
    if cache.has_state_changed:
        session["token_cache"] = cache.serialize()


def get_auth_url() -> str:
    """Start an auth-code flow (state + PKCE + nonce) bound to this browser session."""
    app = _build_msal_app()
    flow = app.initiate_auth_code_flow(
        scopes=Config.SCOPES,
        redirect_uri=Config.REDIRECT_URI,
    )
    session["auth_flow"] = flow
    return flow["auth_uri"]


def process_auth_callback() -> dict | None:
    """Redeem the code only for the flow this session started, then start a fresh session."""
    flow = session.pop("auth_flow", None)
    if not flow or not request.args.get("code"):
        return None

    cache = msal.SerializableTokenCache()  # never merge into a previous user's cache
    app = _build_msal_app(cache=cache)
    try:
        result = app.acquire_token_by_auth_code_flow(flow, request.args.to_dict())
    except ValueError:  # state mismatch or malformed response
        return None
    if "access_token" not in result:
        return None

    accounts = app.get_accounts()
    session.clear()
    session["user"] = result.get("id_token_claims")
    session["home_account_id"] = accounts[0]["home_account_id"] if accounts else None
    session["token_cache"] = cache.serialize()
    # New session id after login, so a pre-login session id can't be reused
    regenerate = getattr(current_app.session_interface, "regenerate", None)
    if regenerate:
        regenerate(session)
    return result


def get_token():
    """Get a valid access token, refreshing if necessary. Memoized per request."""
    if "graph_user_token" in g:
        return g.graph_user_token
    g.graph_user_token = _get_token_uncached()
    return g.graph_user_token


def _get_token_uncached():
    cache = _load_cache()
    app = _build_msal_app(cache=cache)

    # Use only the account that signed in to this session, never just the first cached one
    wanted = session.get("home_account_id")
    accounts = [a for a in app.get_accounts() if wanted and a.get("home_account_id") == wanted]
    if not accounts:
        return None

    result = app.acquire_token_silent(
        scopes=Config.SCOPES,
        account=accounts[0],
    )

    _save_cache(cache)

    if result and "access_token" in result:
        return result["access_token"]
    return None


def get_app_token():
    """
    Get an application-level access token using Client Credentials flow.
    This token can access any user's data (for admin monitoring).
    Requires Application permissions configured in Azure AD.
    """
    global _APP_ONLY_CLIENT
    with _APP_ONLY_LOCK:
        if _APP_ONLY_CLIENT is None:
            _APP_ONLY_CLIENT = _build_msal_app()
        app = _APP_ONLY_CLIENT
    result = app.acquire_token_for_client(
        scopes=[Config.APP_SCOPE],
    )
    if result and "access_token" in result:
        return result["access_token"]
    return None


def is_authenticated():
    """Check if the current session has a valid user."""
    return get_token() is not None


def logout():
    """Clear the session."""
    session.clear()
