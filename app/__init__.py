import logging
import os
import re
from urllib.parse import urlsplit
from flask import Flask
from flask_compress import Compress
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from config import Config

logging.basicConfig(
    level=logging.DEBUG if os.getenv("FLASK_DEBUG", "false").lower() == "true" else logging.INFO
)
# These libraries log full HTTP/token-request payloads at DEBUG — slow and noisy
for _noisy in ("msal", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

db = SQLAlchemy()
migrate = Migrate()
compress = Compress()


def _weak_secret_key(key: str) -> bool:
    key = key or ""
    return len(key) < 32 or bool(re.search(r"change|your-|here|example|secret-key", key, re.I))


def _is_local(redirect_uri: str) -> bool:
    return (urlsplit(redirect_uri).hostname or "") in ("localhost", "127.0.0.1", "::1")


def create_app():
    # A deployed app (any non-localhost REDIRECT_URI) must not run with the default,
    # a placeholder or a short key
    if not _is_local(Config.REDIRECT_URI) and _weak_secret_key(Config.SECRET_KEY):
        raise RuntimeError(
            "FLASK_SECRET_KEY is missing or weak. Set a long random value, e.g. "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    app = Flask(__name__)
    app.config.from_object(Config)
    app.secret_key = Config.SECRET_KEY

    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "flask_session"
    )
    app.config["SESSION_PERMANENT"] = True
    app.config["SESSION_USE_SIGNER"] = True
    # SESSION_COOKIE_SAMESITE / _SECURE / _HTTPONLY come from Config (env-driven)

    Session(app)
    db.init_app(app)
    migrate.init_app(app, db)
    # gzip/brotli/zstd for HTML, CSS, JS and JSON (gunicorn has no proxy in front to do it);
    # videos, documents and images are sent as is
    compress.init_app(app)

    # Browsers cache static files for a year; the ?v=<mtime> added below changes
    # whenever a file changes, so edits still show up immediately.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000

    @app.url_defaults
    def _static_cache_bust(endpoint, values):
        if endpoint == "static" and "filename" in values and "v" not in values:
            path = os.path.join(app.static_folder, values["filename"])
            try:
                values["v"] = int(os.stat(path).st_mtime)
            except OSError:
                pass

    from app.routes import main_bp
    app.register_blueprint(main_bp)

    from app import models  # noqa: F401

    with app.app_context():
        db.create_all()

    from app.meeting_sync import start_background_sync

    @app.after_request
    def _security_headers(response):
        # Frame/MIME/referrer hardening; no script-src rule, so existing inline scripts still run
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy", "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        )
        return response

    @app.before_request
    def _start_meeting_sync():
        start_background_sync(app)

    return app
