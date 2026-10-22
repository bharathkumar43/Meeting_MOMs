"""Sign-in security: the callback only redeems a code for the flow this browser started, and a
login always starts a fresh session bound to the account that just signed in."""
import pytest

from app import auth

USER_A = {"preferred_username": "a@cloudfuze.com", "name": "A", "oid": "oid-a", "tid": "tid"}
USER_B = {"preferred_username": "b@cloudfuze.com", "name": "B", "oid": "oid-b", "tid": "tid"}


class FakeMsalApp:
    """Stands in for msal.ConfidentialClientApplication; no network."""

    signed_in = USER_B

    def __init__(self, cache=None):
        self.cache = cache

    def initiate_auth_code_flow(self, scopes, redirect_uri):
        return {"state": "state-1", "nonce": "nonce-1",
                "auth_uri": "https://login.example/authorize?state=state-1"}

    def get_authorization_request_url(self, scopes, redirect_uri, **kwargs):
        return "https://login.example/authorize"

    def _result(self):
        return {"access_token": "token", "id_token_claims": dict(self.signed_in)}

    def acquire_token_by_auth_code_flow(self, flow, auth_response):
        # msal raises ValueError when the returned state does not match the stored flow
        if auth_response.get("state") != flow.get("state"):
            raise ValueError("state mismatch")
        return self._result()

    def acquire_token_by_authorization_code(self, code, scopes, redirect_uri):
        return self._result()

    def get_accounts(self):
        return [{"home_account_id": "oid-a.tid"}, {"home_account_id": "oid-b.tid"}]

    def acquire_token_silent(self, scopes, account):
        return {"access_token": f"token-for-{account['home_account_id']}"}


@pytest.fixture
def fake_msal(monkeypatch):
    monkeypatch.setattr(auth, "_build_msal_app", lambda cache=None: FakeMsalApp(cache))
    monkeypatch.setattr("app.routes.record_login", lambda *a, **k: None)
    monkeypatch.setattr("app.routes.meeting_sync.start_initial_sync", lambda *a, **k: None)
    return FakeMsalApp


def test_callback_rejects_code_without_a_login_started_in_this_browser(client, fake_msal):
    # An attacker's code delivered to a browser that never visited /login must not sign it in.
    client.get("/auth/callback?code=attacker-code&state=state-1")
    with client.session_transaction() as sess:
        assert "user" not in sess


def test_callback_rejects_mismatched_state(client, fake_msal):
    client.get("/login")
    client.get("/auth/callback?code=attacker-code&state=other-state")
    with client.session_transaction() as sess:
        assert "user" not in sess


def test_callback_flow_is_single_use(client, fake_msal):
    client.get("/login")
    client.get("/auth/callback?code=c1&state=state-1")
    with client.session_transaction() as sess:
        sess.pop("user")
    client.get("/auth/callback?code=c2&state=state-1")
    with client.session_transaction() as sess:
        assert "user" not in sess


def test_login_signs_in_with_matching_state(client, fake_msal):
    resp = client.get("/login")
    assert resp.headers["Location"].startswith("https://login.example/authorize")
    resp = client.get("/auth/callback?code=c1&state=state-1")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard")
    with client.session_transaction() as sess:
        assert sess["user"]["preferred_username"] == "b@cloudfuze.com"


def test_login_starts_a_fresh_session(client, fake_msal):
    # A previous user's token cache and derived grants must not survive a new sign-in.
    with client.session_transaction() as sess:
        sess["user"] = dict(USER_A)
        sess["token_cache"] = '{"AccessToken": {"x": {"secret": "old"}}}'
        sess["recording_map"] = {"evt-1": {"kind": "api", "user_id": "u", "meeting_id": "m"}}
    old_sid = client.get_cookie("session").value
    client.get("/login")
    client.get("/auth/callback?code=c1&state=state-1")
    with client.session_transaction() as sess:
        assert sess["user"]["preferred_username"] == "b@cloudfuze.com"
        assert "recording_map" not in sess
        assert "old" not in (sess.get("token_cache") or "")
    assert client.get_cookie("session").value != old_sid


def test_delegated_token_belongs_to_the_signed_in_account(app, fake_msal):
    # Even with two accounts in the cache, Graph calls must use the session's own account.
    with app.test_request_context("/"):
        from flask import session
        session["user"] = dict(USER_B)
        session["home_account_id"] = "oid-b.tid"
        assert auth._get_token_uncached() == "token-for-oid-b.tid"


def test_no_delegated_token_when_cache_has_no_matching_account(app, fake_msal):
    with app.test_request_context("/"):
        from flask import session
        session["user"] = {"preferred_username": "c@cloudfuze.com"}
        session["home_account_id"] = "oid-c.tid"
        assert auth._get_token_uncached() is None


def test_callback_with_bad_id_token_nonce_fails_cleanly(client, fake_msal, monkeypatch):
    # msal raises RuntimeError when the id_token nonce does not match the stored flow
    def _nonce_mismatch(self, flow, auth_response):
        raise RuntimeError("nonce mismatch")
    monkeypatch.setattr(FakeMsalApp, "acquire_token_by_auth_code_flow", _nonce_mismatch)
    client.get("/login")
    resp = client.get("/auth/callback?code=c1&state=state-1")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login-page")
    with client.session_transaction() as sess:
        assert "user" not in sess
