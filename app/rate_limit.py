"""
Per-user request limits (by_ip=True keys on the client IP instead), shared by all workers through the
database. Fixed windows: Config.RATE_LIMITS maps a limit name to (requests, seconds).
"""
import logging
import random
import time
from functools import wraps

from flask import Response, flash, jsonify, redirect, request, session, url_for
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import RateLimitCounter
from config import Config

logger = logging.getLogger(__name__)

_PRUNE_AFTER_SECONDS = 86400
_JSON_LIMITS = {"parse_file", "upload_video", "upload_transcript", "analyze"}  # fetch() callers


def hit(name: str, who: str) -> bool:
    """
    Count one request for `who` under limit `name`; False once the window's limit is used up.
    One atomic UPDATE (no lost counts across workers), on its own connection so it never
    commits the request's pending work. If the database errors, the request is allowed.
    """
    limit, window = Config.RATE_LIMITS[name]
    now = int(time.time())
    window_start = now - now % window
    key = f"{name}:{(who or 'anonymous').lower()}"[:320]
    table = RateLimitCounter.__table__
    try:
        with db.engine.begin() as conn:
            bumped = conn.execute(
                update(table)
                .where(table.c.key == key, table.c.window_start == window_start,
                       table.c.count < limit)
                .values(count=table.c.count + 1)
            ).rowcount
            if bumped:
                allowed = True
            elif conn.execute(select(table.c.count).where(
                    table.c.key == key, table.c.window_start == window_start)).first():
                allowed = False  # the row exists and is at the limit
            else:
                allowed = _insert_first(conn, table, key, window_start, limit)
        if random.random() < 0.01:
            _prune(now)
        return allowed
    except Exception:
        logger.exception("Rate limit check failed for %s; allowing the request", name)
        return True


def _insert_first(conn, table, key: str, window_start: int, limit: int) -> bool:
    """First request of a window; if another worker inserted it first, count via UPDATE."""
    try:
        with conn.begin_nested():
            conn.execute(insert(table).values(key=key, window_start=window_start, count=1))
        return True
    except IntegrityError:
        return bool(conn.execute(
            update(table)
            .where(table.c.key == key, table.c.window_start == window_start, table.c.count < limit)
            .values(count=table.c.count + 1)
        ).rowcount)


def _prune(now: int) -> None:
    table = RateLimitCounter.__table__
    try:
        with db.engine.begin() as conn:
            conn.execute(delete(table).where(table.c.window_start < now - _PRUNE_AFTER_SECONDS))
    except Exception:
        logger.exception("Rate-limit pruning failed")


def _caller() -> str:
    user = session.get("user") or {}
    return (user.get("preferred_username") or user.get("upn") or "").lower()


def limited(name: str, by_ip: bool = False):
    """Decorator: refuse the request with 429 once the caller used up limit `name`."""
    def wrap(view):
        @wraps(view)
        def decorated(*args, **kwargs):
            who = (request.remote_addr or "") if by_ip else _caller()
            if not hit(name, who):
                logger.warning("Rate limit %s reached for %s", name, who)
                message = "Too many requests. Please wait a while and try again."
                if name in _JSON_LIMITS or request.is_json:
                    return jsonify({"status": "error", "message": message, "error": message}), 429
                if request.method == "GET":
                    return Response(message, status=429, mimetype="text/plain")
                flash(message, "warning")  # form pages: back to the dashboard with a notice
                return redirect(url_for("main.dashboard"), code=303)
            return view(*args, **kwargs)
        return decorated
    return wrap
