import msal
from flask import session, redirect, url_for, request
from config import Config


def _build_msal_app(cache=None):
    return msal.ConfidentialClientApplication(
        Config.AZURE_CLIENT_ID,
        authority=Config.AUTHORITY,
        client_credential=Config.AZURE_CLIENT_SECRET,
        token_cache=cache,
    )


def _load_cache():
    cache = msal.SerializableTokenCache()
    if session.get("token_cache"):
        cache.deserialize(session["token_cache"])
    return cache


def _save_cache(cache):
    if cache.has_state_changed:
        session["token_cache"] = cache.serialize()


def get_auth_url():
    """Generate the Azure AD authorization URL."""
    app = _build_msal_app()
    auth_url = app.get_authorization_request_url(
        scopes=Config.SCOPES,
        redirect_uri=Config.REDIRECT_URI,
    )
    return auth_url


def process_auth_callback():
    """Exchange the authorization code for tokens."""
    cache = _load_cache()
    app = _build_msal_app(cache=cache)

    code = request.args.get("code")
    if not code:
        return None

    result = app.acquire_token_by_authorization_code(
        code,
        scopes=Config.SCOPES,
        redirect_uri=Config.REDIRECT_URI,
    )

    if "access_token" in result:
        session["user"] = result.get("id_token_claims")
        _save_cache(cache)
        return result
    return None


def get_token():
    """Get a valid access token, refreshing if necessary."""
    cache = _load_cache()
    app = _build_msal_app(cache=cache)

    accounts = app.get_accounts()
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
    app = _build_msal_app()
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
