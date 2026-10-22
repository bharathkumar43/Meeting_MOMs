"""
Per-user request limits (per client IP for sign-in), shared by all workers through the
database. Fixed windows: Config.RATE_LIMITS maps a limit name to (requests, seconds).
"""
import logging
import random
import time
from functools import wraps

from flask import Response, flash, jsonify, redirect, request, session, url_for
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import RateLimitCounter
from config import Config

logger = logging.getLogger(__name__)

_PRUNE_AFTER_SECONDS = 86400
_JSON_LIMITS = {"parse_file", "upload_video", "upload_transcript", "analyze"}  # fetch() callers


def hit(name: str, who: str) -> bool:
    """Count one request for `who` under limit `name`; False once the window's limit is used up."""
    limit, window = Config.RATE_LIMITS[name]
    now = int(time.time())
    window_start = now - now % window
    key = f"{name}:{(who or 'anonymous').lower()}"[:320]
    for _ in range(2):  # a concurrent first insert for the same window wins once; retry as update
        row = db.session.get(RateLimitCounter, (key, window_start))
        if row is None:
            row = RateLimitCounter(key=key, window_start=window_start, count=0)
            db.session.add(row)
        if row.count >= limit:
            db.session.rollback()
            return False
        row.count += 1
        try:
            db.session.commit()
            break
        except IntegrityError:
            db.session.rollback()
    if random.random() < 0.01:
        _prune(now)
    return True


def _prune(now: int) -> None:
    try:
        RateLimitCounter.query.filter(
            RateLimitCounter.window_start < now - _PRUNE_AFTER_SECONDS).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()
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
