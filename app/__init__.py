import logging
import os
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


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.secret_key = Config.SECRET_KEY

    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "flask_session"
    )
    app.config["SESSION_PERMANENT"] = True
    app.config["SESSION_USE_SIGNER"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True

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

    @app.before_request
    def _start_meeting_sync():
        start_background_sync(app)

    return app
