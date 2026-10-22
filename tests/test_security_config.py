"""Transport and browser hardening set by the app factory."""
from config import Config


def test_session_cookie_secure_follows_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "SESSION_COOKIE_SECURE", True)
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{tmp_path / 'c.db'}")
    from app import create_app
    assert create_app().config["SESSION_COOKIE_SECURE"] is True


def test_session_cookie_secure_defaults_from_redirect_uri_scheme():
    assert Config.secure_cookie_default("https://moms.example.com/auth/callback") is True
    assert Config.secure_cookie_default("http://localhost:5100/auth/callback") is False


def test_security_headers_on_every_response(client):
    resp = client.get("/login-page")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    csp = resp.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
