"""
Shared fixtures: a Flask app on a throwaway SQLite file, a test client, a signed-in user and
a SQL statement counter. Nothing here talks to Microsoft, Zoom, OpenAI or the real database.
"""
import os
from datetime import datetime

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

os.environ.setdefault("MEETING_SYNC_ENABLED", "false")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
os.environ["ADMIN_EMAILS"] = "admin@cloudfuze.com"
os.environ["MANAGER_EMAILS"] = ""
os.environ["ORG_DOMAIN"] = "cloudfuze.com"


@pytest.fixture(scope="function")
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    from config import Config
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(tmp_path / "uploads"))

    from flask_session import Session

    from app import create_app, routes

    flask_app = create_app()
    flask_app.config.update(TESTING=True, SESSION_FILE_DIR=str(tmp_path / "sessions"))
    Session(flask_app)  # session files go to the temp folder, never the repo's flask_session/

    monkeypatch.setattr(routes, "get_token", lambda: "test-token")
    monkeypatch.setattr(routes, "get_app_token", lambda: None)
    yield flask_app

    from app import db
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def db_session(app):
    from app import db
    with app.app_context():
        yield db.session


@pytest.fixture
def client(app):
    return app.test_client()


def sign_in(client, email: str, name: str = "Test User") -> None:
    with client.session_transaction() as sess:
        sess["user"] = {"preferred_username": email, "name": name}


@pytest.fixture
def signed_in():
    return sign_in


@pytest.fixture
def count_queries():
    """Usage: with count_queries() as n: ...; n["count"] -> SQL statements executed."""
    from contextlib import contextmanager

    @contextmanager
    def _counter():
        state = {"count": 0}

        def _inc(*_args, **_kwargs):
            state["count"] += 1

        event.listen(Engine, "before_cursor_execute", _inc)
        try:
            yield state
        finally:
            event.remove(Engine, "before_cursor_execute", _inc)

    return _counter


def make_user(email: str, name: str = "", last_login: datetime | None = None):
    from app.models import User
    now = last_login or datetime(2026, 10, 1, 12, 0)
    return User(email=email, name=name or email.split("@")[0], first_login=now,
                last_login=now, login_count=1)
